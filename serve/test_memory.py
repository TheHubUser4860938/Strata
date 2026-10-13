"""memory-core fork: the memory store, sticky blocks, injection and journaling - no GPU, no model pack.

    python -m unittest serve.test_memory -v

Runs the real server over the mock engine (like serve/test_context_overflow.py): facts remembered over
HTTP come back in recall, a chat turn is journaled, the mute header skips memory entirely, and the
injected block is byte-stable for a conversation while the store changes underneath.
"""
import http.client
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.memory import MemoryService, request_state, time_bound
from serve.server import ByteTokenizer, MockEngine, Service, serve

ROOT = Path(__file__).resolve().parents[1]


class Store(unittest.TestCase):
    """The store and the block, without HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryService(self.tmp.name)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_remember_search_forget(self):
        r = self.mem.remember("The monitor-fw project targets MSI laptops with a broken ISP sensor", scope="fw")
        self.assertFalse(r["deduped"])
        hits = self.mem.search("which laptops does monitor-fw target", scope="fw")
        self.assertTrue(hits, "the fact should recall")
        self.assertEqual(hits[0]["id"], r["id"])
        self.assertFalse(self.mem.search("MSI broken ISP sensor", scope="user"),
                         "a project-scope fact stays out of a plain user-scope recall")

    def test_scope_isolation(self):
        self.mem.remember("fw-only fact zebraquartz", scope="fw")
        self.mem.remember("a user profile fact about aquamarine teas", scope="user")
        self.assertTrue(self.mem.search("zebraquartz", scope="fw"))
        self.assertFalse(self.mem.search("zebraquartz", scope="other"))
        self.assertTrue(self.mem.search("aquamarine teas", scope="other"),
                        "the user scope joins every project's recall")

    def test_dedup(self):
        a = self.mem.remember("Alpha Beta Gamma fact", scope="user")
        b = self.mem.remember("  alpha   beta gamma   fact  ", scope="user")
        self.assertTrue(b["deduped"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.mem.list_memories()), 1)

    def test_block_stickiness(self):
        self.mem.remember("fact about kernel uvc webcams", scope="fw")
        msgs = [{"role": "user", "content": "questions about kernel uvc webcams today"}]
        b1, hits1, _ = self.mem.block_for(msgs, "fw")
        self.assertIsNotNone(b1)
        self.assertIn("uvc", b1.lower())
        b2, _, _ = self.mem.block_for(msgs, "fw")
        self.assertEqual(b1, b2, "the same conversation keeps byte-identical bytes")
        self.mem.remember("a newer fact about dlna printers", scope="fw")
        b3, _, _ = self.mem.block_for(msgs, "fw")
        self.assertEqual(b1, b3, "the pinned block does not churn mid-conversation")
        b4, _, _ = self.mem.block_for([{"role": "user", "content": "tell me about dlna printers"}], "fw")
        self.assertIsNotNone(b4, "a new conversation recalls the store's current facts")
        self.assertIn("dlna", b4.lower())
        # no user message: nothing to recall with, the messages ride through untouched
        b5, hits5, _ = self.mem.block_for([{"role": "user", "content": "  "}], "fw")
        self.assertIsNone(b5)

    def test_request_state_off_is_identity(self):
        msgs = [{"role": "user", "content": "x"}]
        out, ctx = request_state(None, msgs, {}, {})
        self.assertIs(out, msgs)
        self.assertIsNone(ctx)

    def test_request_state_merges_into_leading_system(self):
        """The OpenCode shape: the client sends its own system at index 0 and the template allows
        exactly one system message - the block must MERGE into it, never ride beside it as a second."""
        self.mem.remember("the zebra-mango deploy window is 04:40 UTC", scope="ops")
        msgs = [{"role": "system", "content": "You are OpenCode, an agent."},
                {"role": "user", "content": "when does the zebra-mango deploy run?"}]
        out, ctx = request_state(self.mem, msgs, {"X-Strata-Memory-Scope": "ops"}, {"model": "m"})
        self.assertIsNotNone(ctx)
        self.assertEqual(len(out), 2, "still ONE system message, not two")
        self.assertEqual(out[0]["role"], "system")
        self.assertTrue(out[0]["content"].startswith("<server_memory"), "the block heads the merged turn")
        self.assertIn("You are OpenCode, an agent.", out[0]["content"], "the client's own instruction survives")
        self.assertEqual(out[1], msgs[1])

    def test_request_state_no_hit_no_lead(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "quantum basketweaving?"}]
        out, ctx = request_state(self.mem, msgs, {"X-Strata-Memory-Scope": "ops"}, {"model": "m"})
        self.assertEqual(out, msgs, "nothing recalled: the list rides through untouched")
        # but the exchange still journals: a quiet turn is tomorrow's curation source, and a
        # scope with no facts yet could never bootstrap otherwise (hits stay empty - no block rode)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["hits"], [])
        self.assertEqual(ctx["user"], "quantum basketweaving?")

    def test_request_state_no_user_text_skips_journal(self):
        # a tool-continuation request carries no user text: an answer nobody asked for is
        # curation noise, so ctx stays None and the turn never enters the queue
        msgs = [{"role": "system", "content": "sys"}, {"role": "assistant", "content": "working..."}]
        out, ctx = request_state(self.mem, msgs, {"X-Strata-Memory-Scope": "ops"}, {"model": "m"})
        self.assertIsNone(ctx)
        self.assertEqual(out, msgs)

    def test_budget_default_unlimited(self):
        # opt-in rule: no max_memories -> no cap, the path is byte-identical to every earlier release
        self.assertEqual(self.mem.max_memories, 0)
        for i in range(30):
            self.mem.remember(f"fact number {i} about widget-{i}", scope="fw")
        self.assertEqual(self.mem.stats()["memories"], 30)
        self.assertFalse(self.mem.stats()["pressure"])

    def test_budget_evicts_worst_never_recalled_first(self):
        mem = MemoryService(os.path.join(self.tmp.name, "cap"), max_memories=6)
        try:
            kept = mem.remember("a fact the recall has served", scope="fw", confidence=0.1)["id"]
            mem.conn.execute("update memories set hits=3 where id=?", (kept,))   # served: never evicted
            low = mem.remember("low confidence never seen", scope="fw", confidence=0.2)["id"]
            for i in range(5):
                mem.remember(f"solid fact {i}", scope="fw", confidence=0.9)
            # the cap is enforced on every write that sits over it: by now "low" is already gone
            # (evicted when the cap was first crossed) and this write evicts the oldest 0.9 row
            r = mem.remember("the seventh solid fact", scope="fw", confidence=0.9)
            self.assertIn("evicted", r, "the crossing write reports what it evicted")
            ids = {m["id"] for m in mem.list_memories(limit=100)}
            self.assertIn(kept, ids, "a recalled fact is never evicted without a human")
            self.assertNotIn(low, ids, "the worst zero-hits row goes first")
            self.assertEqual(len(ids), 6)
            st = mem.stats()
            self.assertEqual(st["max_memories"], 6)
            self.assertTrue(st["pressure"], "6/6 >= 80% of the cap")
        finally:
            mem.close()

    def test_budget_never_evicts_the_write_that_triggers_it(self):
        mem = MemoryService(os.path.join(self.tmp.name, "cap2"), max_memories=1)
        try:
            r = mem.remember("only fact, lowest confidence", scope="fw", confidence=0.05)
            self.assertNotIn("evicted", r, "the new row is exempt: a budget that eats its own input makes a sieve")
            self.assertEqual(len(mem.list_memories(limit=10)), 1)
        finally:
            mem.close()

    def test_invalidate_correct_version_chain(self):
        # the Zep pattern: a correction supersedes, it does not delete - recall serves only the
        # new fact, the old one survives as history pointing at its replacement
        old = self.mem.remember("Pavel works at Acme Robotics", scope="fw")["id"]
        r = self.mem.correct("Pavel works at Beta Dynamics since June", old, scope="fw")
        hits = self.mem.search("where does Pavel work", scope="fw")
        self.assertEqual([h["id"] for h in hits], [r["id"]], "recall serves the correction only")
        hist = {m["id"]: m for m in self.mem.list_memories(scope="fw", include_invalid=True)}
        self.assertIn(old, hist, "the superseded fact is still in the ledger")
        self.assertIsNotNone(hist[old]["invalid_at"])
        self.assertEqual(hist[old]["superseded_by"], r["id"])
        self.assertNotIn(old, {m["id"] for m in self.mem.list_memories(scope="fw")})
        st = self.mem.stats()
        self.assertEqual(st["invalid"], 1)
        self.assertEqual(self.mem.correct("new text for unknown old id", 999999, scope="fw")["deduped"],
                         False, "correcting a fact that is gone is a fill, not an error")

    def test_invalidate_keeps_history_out_of_dedup_and_budget(self):
        first = self.mem.remember("widget spec alpha", scope="fw")["id"]
        self.mem.invalidate(first)
        again = self.mem.remember("widget spec alpha", scope="fw")   # same text, old one retired
        self.assertFalse(again["deduped"], "an active write never dedups onto a superseded row")
        self.assertNotEqual(again["id"], first)
        self.assertEqual(len(self.mem.search("widget spec alpha", scope="fw")), 1, "one active, one history")

    def test_turn_journal(self):
        tid = self.mem.journal_turn("fw", "the question", "the answer", "m")
        pend = self.mem.pending_turns()
        self.assertEqual([t["id"] for t in pend], [tid])
        self.mem.mark_processed([tid])
        self.assertEqual(self.mem.pending_turns(), [])


class PerTurnRecall(unittest.TestCase):
    """Gate-1's fix (docs/MEMORY_CORE.md "Phase 2"): recall must FOLLOW the conversation, but the
    pinned head cannot move - at 184k tokens with 99.9% prefix reuse, one changed head byte costs a
    full prompt re-read. So fresh facts ride the tail, inside the newest user message."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryService(self.tmp.name)
        self.a = self.mem.remember("kernel uvc webcam crash traces to a broken ISP sensor", scope="user")["id"]
        self.b = self.mem.remember("dlna printer discovery speaks SSDP multicast over udp port 1900", scope="user")["id"]

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_recall_follows_without_moving_the_head(self):
        m1 = [{"role": "user", "content": "why does the kernel uvc webcam crash?"}]
        o1, c1 = request_state(self.mem, m1, {}, {})
        self.assertIn("uvc", o1[0]["content"])                       # the head block, computed fresh
        self.assertEqual(o1[1], m1[0], "the first turn carries no tail (same query as the head)")
        m2 = m1 + [{"role": "assistant", "content": "an ISP sensor bug"},
                   {"role": "user", "content": "and how does dlna printer discovery work?"}]
        o2, c2 = request_state(self.mem, m2, {}, {})
        self.assertEqual(o2[0], o1[0], "the pinned head keeps every byte - prompt checkpoints survive")
        self.assertEqual(o2[1:3], m2[0:2], "old turns ride untouched")
        self.assertTrue(o2[3]["content"].startswith("<server_memory"), "the tail block rides the newest user message")
        self.assertIn("dlna", o2[3]["content"])
        self.assertIn("and how does dlna printer discovery work?", o2[3]["content"], "the user text survives")
        self.assertEqual(o2[3]["content"].splitlines()[-1], m2[2]["content"])
        self.assertEqual(c2["hits"], [self.a, self.b], "journal credits = head facts + fresh tail facts")
        self.assertNotIn("dlna", o2[0]["content"], "the fresh fact did not leak into the stable head")

    def test_dedups_against_the_pinned_head(self):
        m1 = [{"role": "user", "content": "why does the kernel uvc webcam crash?"}]
        o1, _ = request_state(self.mem, m1, {}, {})
        m2 = m1 + [{"role": "assistant", "content": "ok"},
                   {"role": "user", "content": "same uvc webcam question again, plainer words"}]
        o2, c2 = request_state(self.mem, m2, {}, {})
        self.assertEqual(c2["hits"], [self.a], "a fact the head already carries is not re-shown in the tail")
        self.assertEqual(o2[3], m2[2], "nothing new: the newest message rides untouched")

    def test_first_turn_single_user_gets_no_second_block(self):
        msgs = [{"role": "user", "content": "dlna printer discovery details?"}]
        out, ctx = request_state(self.mem, msgs, {}, {})
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1], msgs[0])

    def test_per_turn_off_is_the_old_behaviour(self):
        self.mem.per_turn = False
        m1 = [{"role": "user", "content": "why does the kernel uvc webcam crash?"}]
        o1, _ = request_state(self.mem, m1, {}, {})
        m2 = m1 + [{"role": "assistant", "content": "ok"},
                   {"role": "user", "content": "and how does dlna printer discovery work?"}]
        o2, c2 = request_state(self.mem, m2, {}, {})
        self.assertEqual(o2[0], o1[0])
        self.assertEqual(o2[3], m2[2], "per_turn false: the newest message rides untouched")
        self.assertEqual(c2["hits"], [self.a])

    def test_retried_turn_replays_byte_identically(self):
        m = [{"role": "user", "content": "uvc webcam?"}, {"role": "assistant", "content": "sensor"},
             {"role": "user", "content": "and dlna printer discovery?"}]
        o1, c1 = request_state(self.mem, m, {}, {})
        o2, c2 = request_state(self.mem, m, {}, {})
        self.assertEqual(o1, o2, "the same turn replayed gets the same bytes")
        self.assertEqual(c1["hits"], c2["hits"])

    def test_tail_budget(self):
        small = MemoryService(self.tmp.name + "/small", turn_block_chars=600)
        small.remember("alpha zebraquartz fact " + "word " * 60, scope="user")
        small.remember("beta zebraquartz fact " + "word " * 60, scope="user")
        m = [{"role": "user", "content": "alpha zebraquartz?"}, {"role": "assistant", "content": "x"},
             {"role": "user", "content": "beta zebraquartz now?"}]
        tb, tsh = small.turn_block_for(m, "default", [])
        self.assertEqual(len(tsh), 1, "the budget keeps the ranked-first fact and drops the rest")
        self.assertLess(len(tb), 700, "the tail block stays small")
        tiny = MemoryService(self.tmp.name + "/tiny", turn_block_chars=200)
        tiny.remember("gamma zebraquartz fact " + "word " * 60, scope="user")
        tb2, tsh2 = tiny.turn_block_for(m, "default", [])
        self.assertIsNone(tb2, "a budget that fits no fact injects nothing")
        self.assertEqual(tsh2, [])
        small.close()
        tiny.close()

    def test_content_part_lists_merge(self):
        m1 = [{"role": "user", "content": "why does the kernel uvc webcam crash?"}]
        request_state(self.mem, m1, {}, {})                    # pin the head on the uvc fact first
        m = m1 + [{"role": "assistant", "content": "sensor"},
                  {"role": "user", "content": [{"type": "text", "text": "and dlna printer discovery?"}]}]
        out, ctx = request_state(self.mem, m, {}, {})
        last = out[-1]["content"]
        self.assertIsInstance(last, list)
        self.assertEqual(last[0]["text"][:14], "<server_memory")
        self.assertEqual(last[1], m[2]["content"][0])


class ServerMemory(unittest.TestCase):
    """The whole path: real HTTP server, mock engine, memory on."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, "the mock answer", max_context=131072)
        cls.svc = Service(cls.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        cls.svc.memory = MemoryService(cls.tmp.name)
        cls.httpd = serve(cls.svc, port=0)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.svc.memory.close()
        cls.tmp.cleanup()

    def http(self, verb, path, body=None, headers=()):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {"Content-Type": "application/json"}
        hdrs.update({k: v for k, v in headers})
        conn.request(verb, path, json.dumps(body) if body is not None else None, hdrs)
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def test_memory_endpoints_and_chat_journal(self):
        st, body = self.http("GET", "/memory")
        self.assertEqual(st, 200)
        self.assertTrue(body["stats"]["inject"])

        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The Strata dev port policy: :8005 is the modded build",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        fid = body["id"]

        st, body = self.http("GET", "/memory?q=which+port+is+the+modded+build&scope=fw")
        self.assertEqual(st, 200)
        self.assertTrue(body["hits"], "the fact should recall over HTTP")
        self.assertEqual(body["hits"][0]["id"], fid)

        before = self.http("GET", "/memory")[1]["stats"]["turns"]
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": [{"role": "user",
                                                          "content": "which port runs the modded strata build?"}],
                              "max_tokens": 64, "reasoning_effort": "off"},
                             headers=[("X-Strata-Memory-Scope", "fw")])
        self.assertEqual(st, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "the mock answer")
        after = self.http("GET", "/memory")[1]["stats"]["turns"]
        self.assertEqual(after, before + 1, "the exchange was journaled (which also proves the block injected)")

        # the mute header: no journal for this exchange
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": [{"role": "user", "content": "muted question"}],
                              "max_tokens": 64, "reasoning_effort": "off"},
                             headers=[("X-Strata-Memory", "off")])
        self.assertEqual(st, 200)
        self.assertEqual(self.http("GET", "/memory")[1]["stats"]["turns"], after)

        # hand-posted turn (the eval harness route) and the pending list
        st, body = self.http("POST", "/memory", {"op": "turn", "scope": "fw",
                                                 "user": "hand question", "assistant": "hand answer"})
        self.assertEqual(st, 200)
        st, body = self.http("POST", "/memory", {"op": "turns"})
        self.assertTrue(any(t["user"] == "hand question" for t in body["turns"]))

        st, body = self.http("POST", "/memory", {"op": "forget", "id": fid})
        self.assertTrue(body["forgotten"])
        st, body = self.http("GET", "/memory?q=which+port+is+the+modded+build&scope=fw")
        self.assertFalse([h for h in body["hits"] if h["id"] == fid], "forgotten facts stop recalling")

    def test_opencode_shape_over_http(self):
        """The regression that the real harness hit: a client system message at index 0 plus a recalled
        fact. Before the merge fix the Qwen template raised "System message must be at the beginning"
        and every such request was a 400."""
        st, body = self.http("POST", "/memory", {"op": "remember", "scope": "ops",
                                                 "text": "the zebra-mango deploy window is 04:40 UTC"})
        self.assertEqual(st, 200, body)
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": [{"role": "system", "content": "You are OpenCode."},
                                                         {"role": "user",
                                                          "content": "when does the zebra-mango deploy run?"}],
                              "max_tokens": 64, "reasoning_effort": "off"},
                             headers=[("X-Strata-Memory-Scope", "ops")])
        self.assertEqual(st, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "the mock answer")

    def test_bad_requests_are_400(self):
        st, body = self.http("POST", "/memory", {"op": "remember", "text": ""})
        self.assertEqual(st, 400, body)
        st, body = self.http("POST", "/memory", {"op": "nope"})
        self.assertEqual(st, 400)


class MemoryOff(unittest.TestCase):
    """With the store off, the endpoints say so and chat behaves exactly as before."""

    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.svc = Service(MockEngine(tok, "the mock answer", max_context=131072), tok,
                          ChatTemplate(ROOT / "serve/chat_template.jinja"))
        cls.httpd = serve(cls.svc, port=0)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

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

    def test_memory_404_and_plain_chat(self):
        st, body = self.http("GET", "/memory")
        self.assertEqual(st, 404)
        self.assertIn("memory is off", body["error"]["message"])
        st, body = self.http("POST", "/memory", {"op": "remember", "text": "x"})
        self.assertEqual(st, 404)
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "messages": [{"role": "user", "content": "hi"}],
                              "max_tokens": 64, "reasoning_effort": "off"})
        self.assertEqual(st, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "the mock answer")


class ClassAxis(unittest.TestCase):
    """PHASE 4.5: call numbers (class = WHAT, scope = WHO), prefix-filtered recall, near-dupe
    suppression at injection, and the honest shown-count header."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryService(self.tmp.name)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_class_defaults_empty_and_search_reports_it(self):
        self.mem.remember("the zebra-mango deploy window is 04:40 UTC", scope="ops")
        hits = self.mem.search("zebra-mango deploy window", scope="ops")
        self.assertEqual(hits[0]["class"], "", "unclassified is the default, wild by design")

    def test_filter_prefixes_and_wild_behaviour(self):
        self.mem.remember("generate.cpp owns the decode loop", scope="ops", klass="STR")
        self.mem.remember("generate.cpp also submits the overlap signal", scope="ops", klass="STR/GEN")
        self.mem.remember("the recall block budget is a shelf decision", scope="ops", klass="MEM")
        self.mem.remember("KTNC is licensed to Falls City, Nebraska", scope="ops", klass="EVAL")
        self.mem.remember("the stray fact nobody shelved", scope="ops")
        got = lambda q, **kw: {h["text"] for h in self.mem.search(q, scope="ops", **kw)}
        self.assertEqual(got("generate.cpp decode overlap signal", classes=["STR"]),
                         {"generate.cpp owns the decode loop",
                          "generate.cpp also submits the overlap signal"},
                         "a prefix pulls its whole subtree (STR takes STR/GEN)")
        self.assertEqual(got("recall block budget shelf", classes=["MEM"]),
                         {"the recall block budget is a shelf decision"})
        self.assertEqual(got("generate.cpp recall licensed", classes=["MEM", "EVAL"]),
                         {"the recall block budget is a shelf decision",
                          "KTNC is licensed to Falls City, Nebraska"},
                         "multiple prefixes OR")
        unfiltered = got("generate.cpp recall licensed stray")
        self.assertIn("the stray fact nobody shelved", unfiltered,
                      "wild facts ride EVERY unfiltered request")
        self.assertNotIn("the stray fact nobody shelved",
                         got("generate.cpp recall licensed stray", classes=["STR"]),
                      "a request that names shelves deliberately leaves wild facts out")

    def test_class_validation_and_quiet_drop(self):
        from serve.memory import class_name
        self.assertEqual(class_name(" mem/crg.3 "), "MEM/CRG.3")
        with self.assertRaises(ValueError):
            self.mem.remember("junk shelf", klass="engine internals")
        self.assertEqual(class_name("engine internals", quiet=True), "",
                         "headers may carry anything: junk drops, never raises")

    def test_correct_inherits_the_shelf(self):
        a = self.mem.remember("the widget greemak works at Acme Foundry", scope="user", klass="STR")
        b = self.mem.correct("the widget greemak works at Beta Foundry", a["id"], scope="user")
        shelved = {m["id"]: m["class"] for m in self.mem.list_memories(scope="user", limit=50)}
        self.assertEqual(shelved[b["id"]], "STR", "a correction never re-shelves its fact")

    def test_remember_dedup_fills_but_never_reshelves(self):
        r = self.mem.remember("the fallow pit yields blue slate", scope="ops", klass="MEM")
        d = self.mem.remember("the fallow pit yields blue slate", scope="ops", klass="EVAL")
        self.assertTrue(d["deduped"])
        shelved = {m["id"]: m["class"] for m in self.mem.list_memories(scope="ops", limit=50)}
        self.assertEqual(shelved[r["id"]], "MEM", "the shelf is a decision, not a guess to churn")
        e = self.mem.remember("the hollow pit yields grey slate", scope="ops")   # different fact
        e2 = self.mem.remember("the hollow pit yields grey slate", scope="ops", klass="HW")
        shelved = {m["id"]: m["class"] for m in self.mem.list_memories(scope="ops", limit=50)}
        self.assertEqual(shelved[e2["id"]], "HW", "an empty shelf DOES fill on the next learning")

    def test_classify_moves_a_fact_and_history_keeps_its_shelf(self):
        a = self.mem.remember("the quarry seam runs under Route 9", scope="ops")
        self.assertTrue(self.mem.classify(a["id"], "HW"))
        self.assertEqual(self.mem.list_memories(scope="ops", limit=50)[0]["class"], "HW")
        b = self.mem.correct("the quarry seam runs under Route 19", a["id"], scope="ops")
        self.assertTrue(self.mem.classify(a["id"], "OPS"))          # re-shelve the SUPERSEDED row
        hist = {m["id"]: m["class"] for m in
                self.mem.list_memories(scope="ops", limit=50, include_invalid=True)}
        self.assertEqual(hist[a["id"]], "OPS", "history keeps its shelf marks")
        self.assertEqual(hist[b["id"]], "HW", "the live row inherited at correct-time, untouched")

    def test_recall_suppresses_near_dupes_and_prefers_the_fact(self):
        # the five-Qwen-cluster shape, from the live store 2026-10-12: paraphrases whose shared
        # identity tokens are NOT rare (they appear in every copy) - the v1 absolute-bar blind spot
        for t in ("Strata runs Qwen3.8-Flash-Next as its single supported model in many quantized sizes",
                  "Strata runs Qwen3.8-Flash-Next exclusively in various compressed sizes and versions",
                  "the Qwen3.8-Flash-Next model is Strata's one base model, offered in several quants",
                  "the zebra-mango deploy window is 04:40 UTC"):
            self.mem.remember(t, scope="ops")
        hits = self.mem.search("which model does strata run quantized sizes", scope="ops")
        qwen = [h for h in hits if "flash-next" in h["text"].lower()]
        self.assertEqual(len(qwen), 1, "three phrasings, one slot")
        # question and its answer collide as near-dupes: the FACT wins the slot, whoever ranked first
        self.mem.remember("In which Nebraska town is QZZXQ licensed to broadcast?", scope="ops")
        self.mem.remember("QZZXQ is licensed to Frisco, Nebraska to broadcast", scope="ops")
        hits = self.mem.search("which Nebraska town QZZXQ licensed broadcast", scope="ops")
        zq = [h["text"] for h in hits if "qzzxq" in h["text"].lower()]
        self.assertEqual(zq, ["QZZXQ is licensed to Frisco, Nebraska to broadcast"],
                         "an injected question answers nothing: the declarative bumps it")

    def test_dedup_recall_off_keeps_both(self):
        off = MemoryService(self.tmp.name + "/off", dedup_recall=False)
        try:
            off.remember("Strata runs Qwen3.8-Flash-Next as its single supported model", scope="ops")
            off.remember("Strata runs Qwen3.8-Flash-Next in various quantized sizes", scope="ops")
            hits = off.search("strata qwen3.8 flash-next model", scope="ops")
            self.assertEqual(len([h for h in hits if "flash-next" in h["text"].lower()]), 2,
                             "dedup_recall=False is the old behaviour, byte-for-byte")
        finally:
            off.close()

    def test_dedupe_bar_capped_on_a_wide_store(self):
        # the 2026-10-12 airport cascade in miniature: at 300 rows the uncapped relative bar is
        # exactly 6 - the document frequency of the six airport rows' OWN connectives - so the old
        # rule called faa/location/identifier/municipal/airport payload and collapsed six DIFFERENT
        # facts into one slot. The capped bar (5) excludes them: subjects differ, all six survive.
        wide = MemoryService(self.tmp.name + "/wide")
        try:
            for i in range(294):
                wide.remember(f"filler row number {i} keeps the ledger wide zq{i}", scope="ops")
            for subj, ident in (("Westhope", "D64"), ("Pruetz", "5K9"), ("Arthur", "1A2"),
                                ("Lakebird", "Q77"), ("Canyon", "M31"), ("Prairie", "T88")):
                wide.remember(f"The FAA location identifier for {subj} Municipal Airport is {ident}.",
                              scope="ops")
            hits = wide.search("faa location identifier municipal airport", scope="ops", limit=8)
            air = [h for h in hits if "faa" in h["text"].lower()]
            self.assertEqual(len(air), 6,
                             "six subjects sharing six-row connectives must all keep their slots")
        finally:
            wide.close()

    def test_search_date_index_since_until(self):
        # the Primnox date-index lesson (2026-10-12): "when did we decide X" is an episodic
        # question, and every row already carries its created_at stamp - search() now bounds on it
        old = self.mem.remember("the standup agenda moved to the lake cabin", scope="ops")
        with self.mem.lock:
            self.mem.conn.execute("update memories set created_at = created_at - 864000 * 10"
                                  " where id = ?", (old["id"],))
            self.mem.conn.commit()
        new = self.mem.remember("the standup agenda moved back to the office tower", scope="ops")
        now = time.time()
        self.assertEqual([h["id"] for h in self.mem.search("standup agenda", scope="ops",
                                                           until=now - 864000 * 5)], [old["id"]])
        self.assertEqual([h["id"] for h in self.mem.search("standup agenda", scope="ops",
                                                           since=now - 3600)], [new["id"]])
        self.assertEqual({h["id"] for h in self.mem.search("standup agenda", scope="ops")},
                         {old["id"], new["id"]}, "no bounds is byte-identical to the old window")

    def test_time_bound_parses_dates_and_rejects_junk(self):
        self.assertIsNone(time_bound(None))
        self.assertIsNone(time_bound(""))
        self.assertEqual(time_bound("1000.5"), 1000.5)
        self.assertEqual(time_bound("2026-01-01"), 1767225600.0)
        self.assertEqual(time_bound("2026-01-01T00:00:00"), 1767225600.0)
        with self.assertRaises(ValueError):
            time_bound("last tuesday")

    def test_block_header_shows_the_sample_honestly(self):
        for i in range(12):
            self.mem.remember(f"fact {i} about the flibber-gauge instrument number {i}", scope="ops")
        block, shown, _ = self.mem.block_for(
            [{"role": "user", "content": "flibber gauge instrument facts"}], "ops")
        self.assertIn(f'shown="{len(shown)} of 12 active"', block.splitlines()[0],
                      "the model can tell a slice from the whole store")

    def test_request_state_class_header(self):
        self.mem.remember("the flibber gauge reads torque in newton meters", scope="ops", klass="HW")
        self.mem.remember("the flibber gauge committee meets in Itasca County", scope="ops", klass="EVAL")
        msgs = [{"role": "user", "content": "what does the flibber gauge read in?"}]
        out, ctx = request_state(self.mem, msgs,
                                 {"X-Strata-Memory-Scope": "ops", "X-Strata-Memory-Class": "HW"},
                                 {"model": "m"})
        self.assertIn("newton meters", out[0]["content"])
        self.assertNotIn("Itasca", out[0]["content"], "the class header keeps committee trivia out")
        out2, _ = request_state(self.mem, msgs,
                                {"X-Strata-Memory-Scope": "ops", "X-Strata-Memory-Class": "junk shelf!"},
                                {"model": "m"})
        self.assertIn("Itasca", out2[0]["content"],
                      "junk in the class header drops the filter - never breaks the request")


class EncryptionAtRest(unittest.TestCase):
    """M2 job 6: key-file encryption, honest about its threat model - ciphertext when STOPPED,
    plaintext while running, loud failure on tamper, and an optional scrypt-stretched passphrase
    that never enters the config file or git."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _reopen(self, **kw):
        return MemoryService(self.dir, encrypt=True, **kw)

    def test_roundtrip_key_and_shred(self):
        m = self._reopen()
        m.remember("Eugene is the user's middle name, not their first", scope="user")
        m.close()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "memory.db")),
                         "a clean stop of an encrypted store leaves NO plaintext behind")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "memory.db.enc")))
        key = os.path.join(self.dir, "memory.key")
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600, "the key-file is 0600 beside the db")
        self.assertEqual(len(open(key, "rb").read()), 32)
        m = self._reopen()
        try:
            hits = m.search("which name is eugene", scope="user")
            self.assertTrue(hits, "the store came back through the seal intact")
            self.assertIn("middle name", hits[0]["text"])
        finally:
            m.close()

    def test_tamper_fails_loudly_not_empty(self):
        m = self._reopen()
        m.remember("the sealed store never opens quietly wrong", scope="user")
        m.close()
        blob = bytearray(open(os.path.join(self.dir, "memory.db.enc"), "rb").read())
        blob[-33] ^= 1                                    # flip one ciphertext byte (just before the tag)
        open(os.path.join(self.dir, "memory.db.enc"), "wb").write(bytes(blob))
        with self.assertRaises(ValueError, msg="a flipped byte must fail the MAC, not decrypt to mush"):
            self._reopen()

    def test_wrong_key_fails_loudly(self):
        m = self._reopen()
        m.remember("a lost key must not look like an empty store", scope="user")
        m.close()
        open(os.path.join(self.dir, "memory.key"), "wb").write(b"\x01" * 32)
        with self.assertRaises(ValueError):
            self._reopen()

    def test_passphrase_is_a_second_factor(self):
        os.environ["STRATA_TEST_PW"] = "zippel-township-1898"
        try:
            m = self._reopen(passphrase_env="STRATA_TEST_PW")
            m.remember("the passphrase never enters the config file or git", scope="user")
            m.close()
            with self.assertRaises(ValueError):
                self._reopen()                            # key alone: wrong (no passphrase in env)
            m = self._reopen(passphrase_env="STRATA_TEST_PW")
            try:
                self.assertTrue(m.search("passphrase config file git", scope="user"))
            finally:
                m.close()
        finally:
            del os.environ["STRATA_TEST_PW"]

    def test_periodic_snapshot_keeps_plaintext_for_crash_recovery(self):
        m = self._reopen()
        try:
            for i in range(50):                           # the 50-write seal cadence
                m.remember(f"snapshot probe number {i} for the seal cadence", scope="user")
            self.assertTrue(os.path.exists(os.path.join(self.dir, "memory.db.enc")),
                            "50 writes sealed a snapshot")
            self.assertTrue(os.path.exists(os.path.join(self.dir, "memory.db")),
                            "and the plaintext stayed: a crash mid-run loses at most 49 writes")
        finally:
            m.close()

    def test_stats_report_at_rest_and_by_class(self):
        m = self._reopen()
        try:
            m.remember("generate.cpp owns the decode loop", scope="ops", klass="STR")
            s = m.stats()
            self.assertEqual(s["by_class"], {"STR": 1})
            self.assertEqual(s["at_rest"]["mode"], "key-file")
            self.assertTrue(s["at_rest"]["plaintext_while_running"])
        finally:
            m.close()
        plain = MemoryService(os.path.join(self.dir, "plain"))
        try:
            self.assertEqual(plain.stats()["at_rest"], "off")
        finally:
            plain.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
