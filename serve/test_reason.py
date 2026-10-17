"""Server-side REASON modes (the fork's DavidAU-template steering, 2026-10-17): the mode list is
the template's law, the sticky map is per-conversation, /reason steers it, and off is silent and
byte-identical.  No GPU, mock engine.

    python -m unittest serve.test_reason -v

The spike this suite locks in was measured first: DavidAU's template resolves its system-block
text from the RENDER CONTEXT's reasoning_effort (passing reasoning_effort="vader" injects the
Vader block with no tag in the conversation), which is what makes server-side, harness-independent
modes possible at all - the {REASON:} tag is his chat UI for the same value, and the template's
own l_car branch keeps the tag outranking the context value. The hierarchy, all of it the
template's design: client tag > conversation sticky > config default > template default.
"""
import http.client
import json
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.server import (ByteTokenizer, MockEngine, REASON_MODES, Service, serve,
                          apply_reason_mode, reason_conv_key)

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
        src = (ROOT / "serve/chat_template.jinja").read_text(encoding="utf-8")
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
        self.tpl = ChatTemplate(ROOT / "serve/chat_template.jinja")

    def test_context_mode_injects_the_block_with_no_tag_anywhere(self):
        out = self.tpl.render([{"role": "user", "content": "what is a git rebase?"}],
                              reasoning_effort="vader")
        self.assertIn("VADER", out.upper())
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
    """The whole path: HTTP control surface, mock engine, the prompt the engine actually saw."""

    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, "the mock answer", max_context=131072)
        cls.svc = Service(cls.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
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
        self.assertIsNone(body["default"])
        self.assertEqual(set(body["modes"]), set(REASON_MODES))
        prompt = self.turn("reason probe plain zqinx: one line about rebases")
        self.assertNotIn("VADER", prompt.upper())
        self.assertNotIn("CYNICAL DIAGNOSTICIAN", prompt)

    def test_sticky_last_conversation_sets_clears_and_persists_one_turn_later(self):
        conv = [{"role": "user", "content": "reason probe sticky zqiny: what is a rebase?"}]
        self.turn_msgs(conv)                                  # creates the conversation
        st, body = self.http("POST", "/reason", {"mode": "vader", "conversation": "last"})
        self.assertEqual(st, 200, body)
        # a real client resends the conversation every turn; the sticky rides its first message
        conv = conv + [{"role": "assistant", "content": "the mock answer"},
                       {"role": "user", "content": "and does it rewrite history?"}]
        prompt = self.turn_msgs(conv)
        self.assertIn("VADER", prompt.upper(), "the sticky lands on the conversation's next render")
        st, body = self.http("POST", "/reason", {"mode": "off", "conversation": "last"})
        self.assertEqual(st, 200)
        conv = conv + [{"role": "assistant", "content": "the mock answer"},
                       {"role": "user", "content": "and after clearing?"}]
        prompt = self.turn_msgs(conv)
        self.assertNotIn("VADER", prompt.upper(), "off clears at the conversation level")
        # and the law the first version of this test tripped itself: a turn WITHOUT the first
        # message is a NEW conversation and inherits nothing - DavidAU's "switch means new chat"
        # became geometry here, not advice. Steered "last" then, fresh first message: no carryover.
        self.http("POST", "/reason", {"mode": "vader", "conversation": "last"})
        no_conv = [{"role": "user", "content": "reason probe sticky zqiny2: fresh first message"}]
        prompt = self.turn_msgs(no_conv)
        self.assertNotIn("VADER", prompt.upper(), "steering is per-conversation, never ambient")

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

    def test_last_before_any_render_is_a_clean_409(self):
        with self.svc._reason_lock:
            saved, self.svc._reason_last = self.svc._reason_last, None
        try:
            st, body = self.http("POST", "/reason", {"mode": "coder", "conversation": "last"})
            self.assertEqual(st, 409, body)
        finally:
            self.svc._reason_last = saved


if __name__ == "__main__":
    unittest.main()
