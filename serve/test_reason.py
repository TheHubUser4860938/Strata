"""Server-side REASON modes (the fork's DavidAU-template steering, 2026-10-17): one config switch
("reason_modes") turns the feature on, a template gate keeps it off where the template would raise,
the sticky map is per-conversation, /reason steers it over HTTP and reason_mode steers it from
inside the model's own tool loop, and off is silent and byte-identical.  No GPU, mock engine.

    python -m unittest serve.test_reason -v

The spike this suite locks in was measured first: DavidAU's template resolves its system-block
text from the RENDER CONTEXT's reasoning_effort (passing reasoning_effort="vader" injects the
Vader block with no tag in the conversation), which is what makes server-side, harness-independent
modes possible at all - the {REASON:} tag is his chat UI for the same value, and the template's
own l_car branch keeps the tag outranking the context value. The hierarchy, all of it the
template's design: client tag > conversation sticky > config default > template default - with
"off unless the switch AND the template say yes" in front of the whole ladder.
"""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.server import (ByteTokenizer, MockEngine, REASON_MODES, Service, serve,
                          apply_reason_mode, reason_conv_key, reason_mode_ok, reason_tool_result)

ROOT = Path(__file__).resolve().parents[1]


def prompt_text(tok, ids) -> str:
    """ByteTokenizer's output read back as text (one id per UTF-8 byte; >= 256 are the specials)."""
    out, buf = [], bytearray()
    for i in ids:
        if i >= 256:
            out.append(buf.decode("utf-8", "replace"))
            buf = bytearray()
            out.append(tok.SPECIALS[i - 256])
        else:
            buf.append(i)
    out.append(buf.decode("utf-8", "replace"))
    return "".join(out)


class ModeLaw(unittest.TestCase):
    """The pure parts: one list, one dict contract, one conversation key."""

    def test_the_mode_list_is_the_templates_exactly(self):
        src = (ROOT / "serve/chat_template.dau.jinja").read_text(encoding="utf-8")
        i = src.find("set reason_mods")
        if i < 0:
            self.skipTest("a stock template: the drift lock guards the DavidAU one")
        lst = src[src.index("[", i):src.index("]", i) + 1]
        self.assertEqual(set(json.loads(lst.replace("'", '"'))), set(REASON_MODES),
                         "the server's validator and the template's answer list are one law - "
                         "a DavidAU V2 that renames a mode must trip this, not leak literal text")

    def test_apply_reason_mode_contract(self):
        kw = {"reasoning_effort": "low", "enable_thinking": False}
        self.assertIs(apply_reason_mode(kw, None), kw, "no mode = the SAME dict, not a look-alike copy")
        self.assertIs(apply_reason_mode(kw, ""), kw)
        out = apply_reason_mode(kw, "vader")
        self.assertIsNot(out, kw)
        self.assertEqual(out["reasoning_effort"], "vader")
        self.assertEqual(out["enable_thinking"], False, "engine-side keys ride untouched")
        self.assertEqual(kw["reasoning_effort"], "low", "the request's own kwargs dict is not mutated")

    def test_pair_grammar_is_one_plus_two_real_modes(self):
        # the owner's ask 2026-10-17: DavidAU documents {REASON:a}{REASON:b} combos (his card's
        # pairing guide, example {REASON:007}{REASON:coder}) but the shipped template parses ONE
        # tag - measured on the GGUF-embedded template, range-read from the Hub: after_tag[1] only.
        # The fork's grammar is the same combo made mechanical server-side: "a+b", both halves real.
        self.assertTrue(reason_mode_ok("007+coder"))          # his own agentic-build combo
        self.assertTrue(reason_mode_ok("coder"))              # singles stay valid
        self.assertFalse(reason_mode_ok("007+"))              # no empty half
        self.assertFalse(reason_mode_ok("+coder"))
        self.assertFalse(reason_mode_ok("007+coder+xhigh"))   # one "+": the template knows no triple
        self.assertFalse(reason_mode_ok("007+nope"))          # both halves must answer
        self.assertFalse(reason_mode_ok("xultra"))            # the documented-but-dead name stays dead

    def test_apply_reason_mode_takes_the_reasoning_half_of_a_pair(self):
        out = apply_reason_mode({"reasoning_effort": "xhigh"}, "007+coder")
        self.assertEqual(out["reasoning_effort"], "007",
                         "the template answers to single names only - a raw pair value would raise there")

    def test_conversation_key_law(self):
        a = [{"role": "system", "content": "s"}, {"role": "user", "content": "first"},
             {"role": "assistant", "content": "x"}, {"role": "user", "content": "later"}]
        self.assertEqual(reason_conv_key(a), reason_conv_key([{"role": "user", "content": "first"}]),
                         "the key is the FIRST user message; role noise and later turns are irrelevant")
        parts = [{"role": "user", "content": [{"type": "text", "text": "first"}]}]
        self.assertEqual(reason_conv_key(a), reason_conv_key(parts),
                         "content parts concatenate to the same key across dialects")
        self.assertNotEqual(reason_conv_key(a), reason_conv_key([]),
                            "a rewritten history starts a new conversation - DavidAU's 'new chat' "
                            "advice is geometry here, not advice")


class RenderInjection(unittest.TestCase):
    """The spike, pinned as a test: the real template, the real file, context only."""

    def setUp(self):
        self.tpl = ChatTemplate(ROOT / "serve/chat_template.dau.jinja")

    def test_context_mode_injects_the_block_with_no_tag_anywhere(self):
        out = self.tpl.render([{"role": "user", "content": "what is a git rebase?"}],
                              reasoning_effort="vader")
        self.assertIn("DARK LORD", out.upper())
        self.assertNotIn("Reasoning effort is set to xhigh", out,
                         "the mode REPLACES the effort text at the head - it does not stack beside it")

    def test_no_mode_is_the_bytes_of_today(self):
        msgs = [{"role": "user", "content": "a plain question"}]
        plain = self.tpl.render(msgs)
        self.assertIn("xhigh", plain, "the template's own documented default (measured: it injects "
                                      "xhigh framing when no effort is passed - pre-existing behaviour, "
                                      "the feature neither adds nor hides it)")
        self.assertEqual(self.tpl.render(msgs, **apply_reason_mode({}, None)), plain,
                         "feature off = byte-identical by construction")


class ReasonOverHttp(unittest.TestCase):
    """The whole path: HTTP control surface, mock engine, the prompt the engine actually saw.
    Persona checks test for the mode block's TITLE ("DARK LORD...", "CYNICAL DIAGNOSTICIAN"), never
    the bare mode name: since v1.5 the offered reason_mode tool lists every mode name in its
    description, so a plain "VADER" in a prompt proves only that the tool menu exists - a lesson
    this suite learned from its own red run."""

    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, "the mock answer", max_context=131072)
        cls.svc = Service(cls.engine, tok, ChatTemplate(ROOT / "serve/chat_template.dau.jinja"))
        cls.svc.reason_enabled = True          # the config's "reason_modes": true, as boot would set it
        cls.httpd = serve(cls.svc, port=0)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def tearDown(self):
        self.svc.reason_default = None
        with self.svc._reason_lock:
            self.svc._reason.clear()

    def http(self, verb, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(verb, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def turn(self, text):
        return self.turn_msgs([{"role": "user", "content": text}])

    def turn_msgs(self, msgs):
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": msgs, "max_tokens": 8,
                              "reasoning_effort": "none", "strata_checkpoint": False})
        self.assertEqual(st, 200, body)
        return prompt_text(self.svc.tok, self.engine.last_prompt)

    def test_off_is_silent_and_the_control_page_reports_the_template(self):
        st, body = self.http("GET", "/reason")
        self.assertEqual(st, 200)
        self.assertEqual(body["template"], "dau")
        self.assertTrue(body["on"])
        self.assertIsNone(body["default"])
        self.assertEqual(set(body["modes"]), set(REASON_MODES))
        prompt = self.turn("reason probe plain zqinx: one line about rebases")
        self.assertNotIn("DARK LORD", prompt.upper())
        self.assertNotIn("CYNICAL DIAGNOSTICIAN", prompt)

    def test_the_master_switch_outranks_everything_and_stickies_survive_it(self):
        # "reason_modes": false (the default) outranks even a set sticky: the feature does not run.
        self.turn("reason probe switch zqon0: plan the refactor")
        st, body = self.http("POST", "/reason", {"mode": "vader", "conversation": "last"})
        self.assertEqual(st, 200, body)
        conv = [{"role": "user", "content": "reason probe switch zqon0: plan the refactor"},
                {"role": "assistant", "content": "the mock answer"},
                {"role": "user", "content": "and now?"}]
        self.assertIn("DARK LORD", self.turn_msgs(conv).upper())
        self.svc.reason_enabled = False                  # the toggle, mid-session, as a config edit + restart would
        try:
            st, body = self.http("GET", "/reason")
            self.assertFalse(body["on"])
            st, body = self.http("POST", "/reason", {"mode": "house"})
            self.assertEqual(st, 409, body)
            self.assertIn("off", body["error"]["message"])
            prompt = self.turn_msgs(conv)
            self.assertNotIn("DARK LORD", prompt.upper(), "switch off = sticky ignored, render untouched")
        finally:
            self.svc.reason_enabled = True
        self.assertIn("DARK LORD", self.turn_msgs(conv).upper(), "and switch on again: the sticky was never lost")

    def test_sticky_last_conversation_sets_clears_and_persists_one_turn_later(self):
        conv = [{"role": "user", "content": "reason probe sticky zqiny: what is a rebase?"}]
        self.turn_msgs(conv)                                  # creates the conversation
        st, body = self.http("POST", "/reason", {"mode": "vader", "conversation": "last"})
        self.assertEqual(st, 200, body)
        # a real client resends the conversation every turn; the sticky rides its first message
        conv = conv + [{"role": "assistant", "content": "the mock answer"},
                       {"role": "user", "content": "and does it rewrite history?"}]
        prompt = self.turn_msgs(conv)
        self.assertIn("DARK LORD", prompt.upper(), "the sticky lands on the conversation's next render")
        st, body = self.http("POST", "/reason", {"mode": "off", "conversation": "last"})
        self.assertEqual(st, 200)
        conv = conv + [{"role": "assistant", "content": "the mock answer"},
                       {"role": "user", "content": "and after clearing?"}]
        prompt = self.turn_msgs(conv)
        self.assertNotIn("DARK LORD", prompt.upper(), "off clears at the conversation level")
        # and the law the first version of this test tripped itself: a turn WITHOUT the first
        # message is a NEW conversation and inherits nothing - DavidAU's "switch means new chat"
        # became geometry here, not advice. Steered "last" then, fresh first message: no carryover.
        self.http("POST", "/reason", {"mode": "vader", "conversation": "last"})
        no_conv = [{"role": "user", "content": "reason probe sticky zqiny2: fresh first message"}]
        prompt = self.turn_msgs(no_conv)
        self.assertNotIn("DARK LORD", prompt.upper(), "steering is per-conversation, never ambient")

    def test_unknown_mode_is_a_400_not_a_text_leak(self):
        # DavidAU's own docs advertise xultra, which his template does NOT answer to (measured: an
        # invalid tag leaks into the prompt as literal text). The server-side door validates:
        self.turn("reason probe bad zqinz: warm the conversation")
        st, body = self.http("POST", "/reason", {"mode": "xultra", "conversation": "last"})
        self.assertEqual(st, 400, body)
        self.assertIn("xultra", body["error"]["message"])
        st, _ = self.http("POST", "/reason", {})
        self.assertEqual(st, 400)

    def test_session_default_reaches_new_conversations_and_off_restores(self):
        st, body = self.http("POST", "/reason", {"mode": "house"})   # no conversation = session default
        self.assertEqual(st, 200, body)
        self.assertEqual(body["level"], "session-default")
        prompt = self.turn("reason probe default zqonw: diagnose this bug")
        self.assertIn("CYNICAL DIAGNOSTICIAN", prompt, "a fresh conversation inherits the default")
        st, _ = self.http("POST", "/reason", {"mode": "off"})
        prompt = self.turn("reason probe default zqonx: and now")
        self.assertNotIn("CYNICAL DIAGNOSTICIAN", prompt)

    def test_a_pair_thinks_like_a_and_answers_like_b(self):
        # the block TITLES are the markers, never the bare mode names (the tool menu lesson above):
        # "OPERATIONAL AGENT v1" is the 007 construct's heading, "PROGRAMMER & UI ARCHITECT" the
        # coder's - both must be in the prompt the engine actually saw, a's before b's.
        st, body = self.http("POST", "/reason", {"mode": "007+coder"})   # pairs validate at every door
        self.assertEqual(st, 200, body)
        prompt = self.turn("reason probe pair zqonp: build the rate limiter")
        self.assertIn("OPERATIONAL AGENT", prompt, "the reasoning half frames the head like a single mode")
        self.assertIn("PROGRAMMER & UI ARCHITECT", prompt,
                      "the answer half's construct rides too - mechanically spliced, not the shipped "
                      "template's soft literal-text leak")
        self.assertLess(prompt.find("OPERATIONAL AGENT"), prompt.find("PROGRAMMER & UI ARCHITECT"),
                        "b's block lands right after a's, where the template put a's")
        st, _ = self.http("POST", "/reason", {"mode": "off"})
        prompt = self.turn("reason probe pair zqonq: and now plain")
        self.assertNotIn("OPERATIONAL AGENT", prompt, "off clears a pair like any mode")
        self.assertNotIn("PROGRAMMER & UI ARCHITECT", prompt)
        st, body = self.http("POST", "/reason", {"mode": "007+nope"})
        self.assertEqual(st, 400, body)
        st, body = self.http("POST", "/reason", {"mode": "007+coder+xhigh"})
        self.assertEqual(st, 400, body)

    def test_get_reason_carries_the_guide_and_the_pair_grammar(self):
        # the knowledge half of "adapt on the fly": the author's use-case map rides the API too,
        # so any agent can read what each construct is FOR without scraping the model card.
        st, body = self.http("GET", "/reason")
        self.assertIn("coder", body["guide"])
        self.assertTrue(any(p.startswith("007+coder") for p in body["combos"]))
        self.assertIn("reason+answer", body["pairs"])

    def test_last_before_any_render_is_a_clean_409(self):
        with self.svc._reason_lock:
            saved, self.svc._reason_last = self.svc._reason_last, None
        try:
            st, body = self.http("POST", "/reason", {"mode": "coder", "conversation": "last"})
            self.assertEqual(st, 409, body)
        finally:
            self.svc._reason_last = saved


class ReasonPlainTemplate(unittest.TestCase):
    """The flavor gate: on a template that does not answer to modes the feature must not exist at
    all. Measured 2026-10-17 against the stock template: a mode name in its render context raises
    its own "Unexpected reasoning effort" error when thinking is on (with thinking off it ignores
    the value) - so a stray config there is not "inert", it is every render dying. The synthetic
    template below mimics exactly that measured behaviour, so this suite catches the gate being
    removed: it is not a test of a template that could not tell the difference."""

    PLAIN = ('{% if enable_thinking is not defined or enable_thinking %}'
             '{% if reasoning_effort is defined and reasoning_effort not in '
             '("none", "low", "medium", "high", "xhigh") %}'
             '{{ raise_exception("Unexpected reasoning effort: " ~ reasoning_effort) }}'
             '{% endif %}{% endif %}'
             '{% for m in messages %}[{{ m.role }}] {{ m.content }}\n'
             '{% endfor %}{% if add_generation_prompt %}[assistant] {% endif %}')

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tpl = Path(cls.tmp.name) / "chat_template.jinja"
        tpl.write_text(cls.PLAIN, encoding="utf-8")
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, "the mock answer", max_context=131072)
        cls.svc = Service(cls.engine, tok, ChatTemplate(tpl))
        cls.svc.reason_enabled = True          # enabled on purpose: these tests police the FLAVOR gate,
        cls.httpd = serve(cls.svc, port=0)     #   which is the second gate and only reachable past the first
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def http(self, verb, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(verb, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def test_the_control_page_says_plain_and_steering_is_refused(self):
        st, body = self.http("GET", "/reason")
        self.assertEqual(st, 200)
        self.assertEqual(body["template"], "plain")
        self.assertFalse(body["on"], "a true switch on a plain template: the flavor gate still says off")
        for mode in ("vader", "off"):              # even clearing is refused: nothing was ever set
            st, body = self.http("POST", "/reason", {"mode": mode, "conversation": "last"})
            self.assertEqual(st, 409, body)
            self.assertIn("does not answer", body["error"]["message"])
        self.svc.reason_enabled = False            # and the switch-off message outranks the flavor one
        st, body = self.http("POST", "/reason", {"mode": "vader"})
        self.assertEqual(st, 409, body)
        self.assertIn("reason modes are off", body["error"]["message"])
        self.svc.reason_enabled = True

    def test_the_gate_holds_even_against_a_template_that_raises(self):
        # bypass the boot gate (which refuses a config default on plain): this is the defense in
        # the substitution itself. With the gate removed, "vader" would reach this template's
        # raise_exception and the request would die - the measured stock failure, on purpose.
        self.svc.reason_default = "vader"
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": [{"role": "user", "content": "hi there"}],
                              "max_tokens": 8, "reasoning_effort": "medium"})
        self.assertEqual(st, 200, body)            # thinking on, effort validated by the template: only
        prompt = prompt_text(self.svc.tok, self.engine.last_prompt)   # the gate keeps "vader" out of it
        self.assertIn("[user] hi there", prompt)
        self.assertNotIn("vader", prompt.lower())


class ReasonToolLoop(unittest.TestCase):
    """v1.5 (2026-10-17), the user's ask made real: the model calls reason_mode and the SERVER runs
    it - the memory_search pattern - so a mode change lands in the NEXT TOOL ROUND of the same
    turn, with no HTTP call and no client wiring, on any harness whose tools the server executes.
    Mock engine scripts the two rounds: a tool call, then the answer after it."""

    def setUp(self):
        tok = ByteTokenizer()
        call = ("<tool_call>\n<function=reason_mode>\n<parameter=mode>\nvader\n</parameter>\n"
                "</function>\n</tool_call>")
        self.engine = MockEngine(tok, [call, "the mock answer after steering"], max_context=131072)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.dau.jinja"))
        self.svc.reason_enabled = True
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def http(self, verb, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(verb, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def test_the_model_steers_itself_and_the_next_round_lands_in_the_same_turn(self):
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "max_tokens": 256, "reasoning_effort": "off",
                              "messages": [{"role": "user",
                                            "content": "reason probe loop zqoa: plan this refactor"}]})
        self.assertEqual(st, 200, body)
        ch = body["choices"][0]
        self.assertEqual(ch["message"]["content"], "the mock answer after steering",
                         "the round-2 answer is the reply; the steering round stays internal")
        self.assertEqual(ch["finish_reason"], "stop")
        trace = ch["message"].get("strata_mcp") or []
        ran = [e for e in trace if e.get("event") == "call" and e.get("name") == "reason_mode"]
        res = [e for e in trace if e.get("event") == "result" and e.get("ok")]
        self.assertTrue(ran and res, "the server ran the mode change itself (the trace carries the round)")
        st, body = self.http("GET", "/reason")      # pinned to THIS conversation, no "last" race: the
        self.assertTrue(any(m == "vader" for _, m in body["sticky"]),   # loop held the messages
                        "the self-steered mode is the same sticky the HTTP door would have set")
        prompt = prompt_text(self.svc.tok, self.engine.last_prompt)     # round 2's actual prompt
        self.assertIn("DARK LORD", prompt.upper(), "the mode rode the very next render: same turn")
        self.assertIn("reason_mode", prompt, "and the tool was offered to the model in the first place")

    def test_the_tool_is_never_offered_with_the_switch_off(self):
        self.svc.reason_enabled = False
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "max_tokens": 64, "reasoning_effort": "off",
                              "messages": [{"role": "user", "content": "reason probe no-tool zqob: hi"}]})
        self.assertEqual(st, 200, body)              # the scripted call round never runs: with the tool
        prompt = prompt_text(self.svc.tok, self.engine.last_prompt)   # not offered, round 1 IS the only round
        self.assertNotIn("reason_mode", prompt, "switch off: no tool joins the list, the prompt is clean")


class ReasonClearAndPrecedence(unittest.TestCase):
    """The audit suite (2026-10-17, fresh eyes on the merged feature): the ladder is
    sticky > session default > the template's own framing, and 'off' hands the choice back to the
    NEXT level - not to plain. On a DavidAU install that defaults to ultra, a conversation-level
    clear means ULTRA next render, and the tool's message now says exactly that (the message and
    the description said 'plain' before the audit; the behavior was always right). Pinned beside
    it: a client's own {REASON:} tag outranks every server level (the template's l_car rule), a
    request's own reason_mode tool shadows the server's (one name, one owner), and #458's split
    render carries the mode at the head while the tail keeps the request's own effort."""

    def _boot(self, default=None):
        self.engine = MockEngine(ByteTokenizer(), ["ok"], max_context=131072)
        self.svc = Service(self.engine, self.engine.tok,
                           ChatTemplate(ROOT / "serve/chat_template.dau.jinja"))
        self.svc.reason_enabled = True
        self.svc.reason_default = default
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def http(self, verb, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(verb, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def render(self, text="a question about the zibble tariff"):
        return self.svc.render_prompt([{"role": "user", "content": text}], [], {})

    def test_off_at_conversation_level_falls_to_the_session_default(self):
        self._boot(default="house")
        text = "which widget factory beats the zibble tariff"
        st, r = self.http("POST", "/reason", {"mode": "vader", "conversation": text})
        self.assertEqual(st, 200, r)
        out = self.render(text).upper()
        self.assertIn("DARK LORD", out)
        self.assertNotIn("CYNICAL DIAGNOSTICIAN", out)
        st, r = self.http("POST", "/reason", {"mode": "off", "conversation": text})
        self.assertEqual(st, 200, r)
        out = self.render(text).upper()
        self.assertIn("CYNICAL DIAGNOSTICIAN", out,
                      "clearing the sticky hands the choice to the session default - not to plain")
        self.assertNotIn("DARK LORD", out)
        st, r = self.http("POST", "/reason", {"mode": "off"})      # session level: the default goes too
        self.assertEqual(st, 200, r)
        out = self.render(text)
        self.assertNotIn("CYNICAL DIAGNOSTICIAN", out.upper())
        self.assertNotIn("DARK LORD", out.upper())
        self.assertIn("Reasoning effort is set to xhigh", out,
                      "both levels cleared is the ONLY way to the template's own framing (measured: xhigh)")

    def test_the_tool_clear_message_names_what_will_actually_render(self):
        self._boot(default="house")
        msgs = [{"role": "user", "content": "steer me then let me go back"}]
        r = reason_tool_result(self.svc, msgs, {"mode": "vader"})
        self.assertTrue(r["ok"])
        r = reason_tool_result(self.svc, msgs, {"mode": "off"})
        self.assertIn("house", r["text"], "the message names the default that now frames the render")
        self.assertNotIn("plain", r["text"], "no false promise of plain on a box that defaults to a mode")
        self.svc.reason_default = None                              # only a bare box really returns to plain
        reason_tool_result(self.svc, msgs, {"mode": "vader"})
        r = reason_tool_result(self.svc, msgs, {"mode": "off"})
        self.assertIn("plain", r["text"])

    def test_the_client_tag_beats_the_sticky(self):
        self._boot()                                                # no default: sticky vs tag, clean fight
        text = "answer with the house method {REASON:house}"
        st, r = self.http("POST", "/reason", {"mode": "vader", "conversation": text})
        self.assertEqual(st, 200, r)
        out = self.render(text).upper()
        self.assertIn("CYNICAL DIAGNOSTICIAN", out)
        self.assertNotIn("DARK LORD", out,
                         "the tag is the template's own mechanism and outranks the context value "
                         "(the l_car rule): client tag > server stickies, always")

    def test_the_split_render_carries_mode_at_head_and_effort_at_tail(self):
        self._boot(default="vader")
        self.svc.effort_end = True
        out = self.svc.render_prompt([{"role": "user", "content": "solve the platt transform"}], [],
                                     {"reasoning_effort": "low", "enable_thinking": True})
        self.assertIn("DARK LORD", out.upper(), "the checkpoint head renders with the mode text")
        self.assertIn("Reasoning effort is set to low", out,
                      "#458 keeps the request's own level in the tail - a mode never steals the effort")


if __name__ == "__main__":
    unittest.main()
