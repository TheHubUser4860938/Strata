"""memory-core fork: the memory store, sticky blocks, injection and journaling - no GPU, no model pack.

    python -m unittest serve.test_memory -v

Runs the real server over the mock engine (like serve/test_context_overflow.py): facts remembered over
HTTP come back in recall, a chat turn is journaled, the mute header skips memory entirely, and the
injected block is byte-stable for a conversation while the store changes underneath.
"""
import http.client
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.memory import MemoryService, request_state, time_bound, tool_result
from serve.server import ByteTokenizer, Cartridges, MockEngine, Service, serve

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


class EmbedderMuteOverHttp(unittest.TestCase):
    """The arm-C A/B surface, proven offline (mock engine, stub embedder, real HTTP): one server,
    one store, one vector set - and the per-request header decides which arm answers. The response
    echoes the arm, so a venue record can be audited from its own lines rather than from the flags
    the run hoped it had set."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, "the mock answer", max_context=131072)
        cls.svc = Service(cls.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        v = [1.0, 0.0, 0.0, 0.0]
        cls.svc.memory = MemoryService(cls.tmp.name,
                                       embedder=StubEmbedder({"glimmery": v,
                                                              "zebra flibberquant gauge": v,
                                                              # a content-word SUBSET of the stored row:
                                                              # the guard folds this one away by default
                                                              "zebra flibberquant": v}))
        cls.svc.memory.remember("zebra flibberquant gauge")     # shares no word with the probe query
        cls.httpd = serve(cls.svc, port=0)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.svc.memory.close()
        cls.tmp.cleanup()

    def _get(self, qs, mute=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {"Accept": "application/json"}
        if mute:
            hdrs["X-Strata-Memory-Embedder"] = "off"
        conn.request("GET", "/memory?" + qs, headers=hdrs)
        return json.loads(conn.getresponse().read().decode())

    def test_get_search_answers_differ_by_arm_and_say_which_one(self):
        on = self._get("q=glimmery&limit=10")
        off = self._get("q=glimmery&limit=10", mute=True)
        self.assertTrue(on["hits"], "the hybrid arm finds the row only cosine can reach")
        self.assertEqual(off["hits"], [], "the lexical arm must reproduce the old store exactly")
        self.assertIs(on["embedder"], True, "the reply states which arm served it")
        self.assertIs(off["embedder"], False)

    def test_post_search_honours_the_same_header(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", "/memory", json.dumps({"op": "search", "q": "glimmery", "limit": 10}),
                     {"Content-Type": "application/json", "X-Strata-Memory-Embedder": "off"})
        self.assertEqual(json.loads(conn.getresponse().read().decode())["hits"], [])

    def _post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", "/memory", json.dumps(body), {"Content-Type": "application/json"})
        return json.loads(conn.getresponse().read().decode())

    def test_remember_guard_false_lands_the_write_the_guard_would_fold(self):
        """The venue's ingest needs this over HTTP, so it is proven over HTTP. "zebra flibberquant"
        is a content-word subset of the row already in the store, so the default path folds it into
        that row and returns the OLD id - which is exactly what shuffles a benchmark's gold map when
        the turns being written are evidence rather than proposals."""
        folded = self._post({"op": "remember", "text": "zebra flibberquant"})
        self.assertEqual(folded["deduped"], "semantic", "the guard is ON by default - the shipped path")
        landed = self._post({"op": "remember", "text": "zebra flibberquant", "guard": False})
        self.assertNotEqual(landed["id"], folded["id"], "guard=false must write the row, not fold it")
        self.assertFalse(landed.get("deduped"), "and report a fresh write, so the caller can tell")
        again = self._post({"op": "remember", "text": "zebra flibberquant", "guard": "off"})
        self.assertEqual(again["id"], landed["id"], "the string form means off too, and exact-text "
                                                    "dedup still applies - guard=false is not a bypass")

    def test_the_injection_path_respects_the_mute(self):
        msgs = [{"role": "user", "content": "glimmery?"}]
        _m, ctx_on = request_state(self.svc.memory, msgs, {"X-Strata-Memory-Scope": "default"}, {})
        _m, ctx_off = request_state(self.svc.memory, msgs, {"X-Strata-Memory-Scope": "default",
                                                           "X-Strata-Memory-Embedder": "off"}, {})
        self.assertTrue(ctx_on and ctx_on["hits"], "the hybrid arm injects the fact")
        self.assertTrue(ctx_off and not ctx_off["hits"], "the lexical arm injects nothing - and the "
                                                         "request still journals normally")


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

    def test_anthropic_dialect_injects_and_journals(self):
        # beta item B (2026-10-12): Claude Code rides /v1/messages - the harness-independence
        # claim breaks if memory only rides the OpenAI route. The prompt-size delta between a
        # request and its muted twin proves the block reached the ENGINE, not just that the
        # journal fired (mem_ctx exists even when nothing recalls).
        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The Zephyr staging mirror runs on :9099",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        msg = {"model": "m", "max_tokens": 64, "thinking": {"type": "disabled"},
               "messages": [{"role": "user", "content": "which port is the Zephyr staging mirror?"}]}
        hdr = [("X-Strata-Memory-Scope", "fw")]
        before = self.http("GET", "/memory")[1]["stats"]["turns"]
        st, body = self.http("POST", "/v1/messages", msg, headers=hdr)
        self.assertEqual(st, 200, body)
        self.assertEqual("".join(b.get("text", "") for b in body.get("content", [])), "the mock answer",
                         "the answer must survive the new wrappers untouched")
        self.assertEqual(self.http("GET", "/memory")[1]["stats"]["turns"], before + 1,
                         "the exchange was journaled")
        st, muted = self.http("POST", "/v1/messages", msg, headers=hdr + [("X-Strata-Memory", "off")])
        self.assertEqual(st, 200, muted)
        self.assertLess(muted["usage"]["input_tokens"], body["usage"]["input_tokens"],
                        "the injected block must be bytes in the prompt, not just in the journal")
        self.assertEqual(self.http("GET", "/memory")[1]["stats"]["turns"], before + 1,
                         "the mute header skips memory entirely on this dialect too")

    def test_responses_dialect_injects_and_journals(self):
        # and the Responses route (Codex CLI): same parity, same proof
        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The Quillon deploy window is 03:15 UTC",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        req = {"model": "m", "max_output_tokens": 64, "reasoning": {"effort": "none"},
               "input": [{"role": "user",
                          "content": [{"type": "input_text", "text": "when is the Quillon deploy window?"}]}]}
        hdr = [("X-Strata-Memory-Scope", "fw")]
        before = self.http("GET", "/memory")[1]["stats"]["turns"]
        st, body = self.http("POST", "/v1/responses", req, headers=hdr)
        self.assertEqual(st, 200, body)
        text = "".join(c.get("text", "") for o in body.get("output", []) for c in (o.get("content") or []))
        self.assertIn("the mock answer", text)
        self.assertEqual(self.http("GET", "/memory")[1]["stats"]["turns"], before + 1)
        st, muted = self.http("POST", "/v1/responses", req, headers=hdr + [("X-Strata-Memory", "off")])
        self.assertEqual(st, 200, muted)
        used = lambda b: (b.get("usage") or {}).get("input_tokens") or (b.get("usage") or {}).get("prompt_tokens") or 0
        self.assertLess(used(muted), used(body), "the injected block must be bytes in the prompt")
        self.assertEqual(self.http("GET", "/memory")[1]["stats"]["turns"], before + 1)

    def test_ui_page_and_script_are_served(self):
        # item D (2026-10-13): the store's own page - one route line, zero new API
        st, body = self.http("GET", "/memory-ui")
        self.assertEqual(st, 200)
        self.assertIn("<title>Strata - Memory</title>", body)
        st, body = self.http("GET", "/web/memory.js")
        self.assertEqual(st, 200, "the page's script rides the same static handler the app uses")

    def test_bad_requests_are_400(self):
        st, body = self.http("POST", "/memory", {"op": "remember", "text": ""})
        self.assertEqual(st, 400, body)
        st, body = self.http("POST", "/memory", {"op": "nope"})
        self.assertEqual(st, 400)


class MemoryTool(unittest.TestCase):
    """C (agentic recall, 2026-10-12): the model calls memory_search and the SERVER runs it and
    lets the model continue - server-side so every harness gets browsing with zero client wiring.
    Fresh fixture per test: the scripted engine hands out its rounds one request at a time."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        call = ("<tool_call>\n<function=memory_search>\n<parameter=q>\nzygost scale calibration\n"
                "</parameter>\n</function>\n</tool_call>")
        self.engine = MockEngine(tok, [call, "the mock answer after recall"], max_context=131072)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.memory = MemoryService(self.tmp.name, tool=True)
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.svc.memory.close()
        self.tmp.cleanup()

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

    def test_server_runs_the_search_and_the_model_continues(self):
        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The zygost scale reads 41.2 after calibration",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "max_tokens": 256, "reasoning_effort": "off",
                              "messages": [{"role": "user",
                                            "content": "what does memory say about the zygost scale?"}]},
                             headers=[("X-Strata-Memory-Scope", "fw")])
        self.assertEqual(st, 200, body)
        ch = body["choices"][0]
        self.assertEqual(ch["message"]["content"], "the mock answer after recall",
                         "the round-2 answer is the reply; the tool round stays internal")
        self.assertEqual(ch["finish_reason"], "stop")
        trace = ch["message"].get("strata_mcp") or []
        ran = [e for e in trace if e.get("event") == "call" and e.get("name") == "memory_search"]
        res = [e for e in trace if e.get("event") == "result" and e.get("ok")]
        self.assertTrue(ran and res, "the server ran the search itself (trace carries the round)")
        self.assertIn("zygost", res[0]["text"])
        self.assertIn("41.2", res[0]["text"], "the fact reached the model as the tool result")

    def test_muted_request_gets_no_tool(self):
        # X-Strata-Memory: off kills injection AND the tool: no mem_ctx, no gate, no trace
        st, body = self.http("POST", "/v1/chat/completions",
                             {"model": "m", "max_tokens": 64, "reasoning_effort": "off",
                              "messages": [{"role": "user", "content": "muted: no browsing today"}]},
                             headers=[("X-Strata-Memory", "off")])
        self.assertEqual(st, 200, body)
        self.assertNotIn("strata_mcp", body["choices"][0]["message"])

    def test_anthropic_route_runs_the_same_loop(self):
        # C parity (2026-10-13): Claude Code rides /v1/messages and gets the identical server-side
        # browse - the tool round stays internal, the answer is the whole reply, and usage reports
        # the FINAL round's prompt (the engine read that one, not the first).
        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The zygost scale reads 41.2 after calibration",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        st, body = self.http("POST", "/v1/messages",
                             {"model": "m", "max_tokens": 256, "thinking": {"type": "disabled"},
                              "messages": [{"role": "user",
                                            "content": "what does memory say about the zygost scale?"}]},
                             headers=[("X-Strata-Memory-Scope", "fw")])
        self.assertEqual(st, 200, body)
        self.assertEqual([b["text"] for b in body["content"] if b["type"] == "text"],
                         ["the mock answer after recall"], "round 2's answer is the whole reply")
        self.assertEqual(body["stop_reason"], "end_turn")
        self.assertFalse([b for b in body["content"] if b["type"] == "tool_use"],
                         "the internal call never leaks to the client")
        self.assertEqual(body["usage"]["input_tokens"], len(self.engine.last_prompt),
                         "input_tokens counts the final round's prompt, the one the engine read")

    def test_responses_route_runs_the_same_loop(self):
        # C parity: Codex CLI rides /v1/responses - same loop, and the assembler never sees the
        # internal call (run_with_mcp swallows the events of names it serves itself).
        st, body = self.http("POST", "/memory", {"op": "remember",
                                                 "text": "The zygost scale reads 41.2 after calibration",
                                                 "scope": "fw"})
        self.assertEqual(st, 200, body)
        st, body = self.http("POST", "/v1/responses",
                             {"model": "m", "max_output_tokens": 256, "reasoning": {"effort": "off"},
                              "input": "what does memory say about the zygost scale?"},
                             headers=[("X-Strata-Memory-Scope", "fw")])
        self.assertEqual(st, 200, body)
        texts = [c["text"] for item in body["output"] if item["type"] == "message"
                 for c in item.get("content", []) if c.get("type") == "output_text"]
        self.assertEqual(texts, ["the mock answer after recall"])
        self.assertFalse([i for i in body["output"] if i["type"] == "function_call"],
                         "no function_call item reaches the Codex client")

    def test_tool_result_shape(self):
        self.svc.memory.remember("The zygost scale reads 41.2 after calibration", scope="fw")
        r = tool_result(self.svc.memory, {"q": "zygost scale"}, scope="fw")
        self.assertTrue(r["ok"])
        d = json.loads(r["text"])
        self.assertEqual(d["found"], 1)
        self.assertIn("41.2", d["facts"][0]["text"])
        bad = tool_result(self.svc.memory, {"q": "zygost", "since": "last tuesday"}, scope="fw")
        self.assertFalse(bad["ok"], "a junk date is an honest tool error, not a widened window")
        empty = tool_result(self.svc.memory, {}, scope="fw")
        self.assertFalse(empty["ok"], "q is required")


class Cartridge(unittest.TestCase):
    """M3b tier-2 standing cartridge (2026-10-12): the standing shelves ride every prompt as its
    FIRST system bytes, and their K/V comes back from the engine session file instead of being
    re-prefilled - restore measured at ~0.1 s for 260 MB on the live box. The mock's session_file
    stands in for the disk transfer; the prefix law is checked at the token."""

    SCRIPT = ["OK", "first answer", "second answer", "muted answer", "third answer"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.slots = tempfile.TemporaryDirectory()
        self.tok = ByteTokenizer()
        self.engine = MockEngine(self.tok, list(self.SCRIPT), max_context=131072)
        self.svc = Service(self.engine, self.tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.memory = MemoryService(self.tmp.name)
        self.svc.slot_save_path = self.slots.name
        self.svc.cartridges = Cartridges(self.svc, {"enabled": True, "class_prefixes": ["USR"], "max_chars": 400})
        self.calls = {"save": 0, "restore": 0}
        orig = self.engine.session_file

        def counted(action, path):
            self.calls[action] = self.calls.get(action, 0) + 1
            return orig(action, path)
        self.engine.session_file = counted
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]
        st, body = self.http("POST", "/memory", {"op": "remember", "scope": "user", "class": "USR",
                                                 "text": "The user's launch ritual is coffee before code"})
        self.assertEqual(st, 200, body)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.svc.memory.close()
        self.tmp.cleanup()
        self.slots.cleanup()

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

    def prompt_text(self):
        return bytes(t for t in self.engine.last_prompt if t < 256).decode("utf-8", "replace")

    def ask(self, text, headers=()):
        return self.http("POST", "/v1/chat/completions",
                         {"model": "m", "max_tokens": 64, "reasoning_effort": "off",
                          "messages": [{"role": "user", "content": text}]}, headers=list(headers))

    def test_seed_then_the_standing_text_rides_every_conversation(self):
        st, body = self.ask("what is my launch ritual?")
        self.assertEqual(st, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "first answer")
        p = self.prompt_text()
        self.assertIn("STANDING MEMORY", p)
        self.assertIn("launch ritual", p, "the standing fact rides the prompt: the win is time, not magic")
        files = [f for f in os.listdir(self.slots.name) if f.startswith("MEM-CRG-")]
        self.assertEqual(len(files), 1, "the seed saved exactly one cartridge file")
        self.assertEqual(self.calls, {"save": 1, "restore": 0})
        # second fresh conversation: the slot still stands on the same bytes - no restore, no re-seed
        st, body = self.ask("second question")
        self.assertEqual(body["choices"][0]["message"]["content"], "second answer")
        self.assertIn("STANDING MEMORY", self.prompt_text())
        self.assertEqual(self.calls, {"save": 1, "restore": 0})
        st, stats = self.http("GET", "/memory")
        cart = stats["stats"]["cartridges"]
        self.assertEqual(cart["state"], "seeded")
        self.assertEqual(cart["facts"], 1)

    def test_muted_request_invalidates_and_the_next_one_restores(self):
        st, body = self.ask("first question")
        self.assertEqual(st, 200, body)
        st, body = self.ask("muted question", headers=[("X-Strata-Memory", "off")])
        self.assertEqual(st, 200, body)        # which scripted string it answers with is fixture noise;
        self.assertNotIn("STANDING MEMORY", self.prompt_text(), "memory off means the cartridge off")
        self.assertEqual(self.calls["restore"], 0)
        st, body = self.ask("third question")
        self.assertEqual(st, 200, body)
        self.assertIn("STANDING MEMORY", self.prompt_text())
        self.assertEqual(self.calls["restore"], 1, "the muted conversation replaced the slot: restore once")
        self.assertEqual(self.calls["save"], 1, "no re-seed - the file survived, only the slot moved")

    def test_cartridge_text_is_deterministic_shelved_only_and_bounded(self):
        m = self.svc.memory
        a = m.cartridge_text(["USR"])
        self.assertEqual(a, m.cartridge_text(["USR"]), "same store, same bytes, file stays valid")
        self.assertEqual(m.cartridge_text([""])[0], "", "wild facts graduate to no shelf")
        m.remember("The user's car is a maroon estate with a crooked mirror", scope="user", klass="USR")
        text, n, left = m.cartridge_text(["USR"], max_chars=180)
        self.assertEqual(n, 1, "the budget keeps the strongest fact standing")
        self.assertGreaterEqual(left, 1, "the rest are counted, never silently dropped")
        self.assertIn("coffee", text)   # conf ties: id order keeps the first row

    def test_reseed_op_reports_and_off_is_a_400(self):
        st, body = self.http("POST", "/memory", {"op": "cartridge"})
        self.assertEqual(st, 200, body)
        self.assertIn(body["cartridge"]["state"], ("seeded", "loaded"))
        self.svc.cartridges = None
        st, body = self.http("POST", "/memory", {"op": "cartridge"})
        self.assertEqual(st, 400, body)


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

    def test_ui_page_is_404_when_memory_off(self):
        st, _ = self.http("GET", "/memory-ui")
        self.assertEqual(st, 404, "no store, no page - the route hides itself")

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

    def test_a_plaintext_husk_never_outranks_a_real_ciphertext(self):
        """2026-10-14, learned the hard way on the dev box: a restart raced a seal-and-shred, a 45 KB
        plaintext husk ended up over a 9.6 MB ciphertext, and the crash rule trusted the husk - the
        store opened with ONE fact, said nothing about it, and sealed that emptiness back over the
        real store on its next close."""
        m = self._reopen()
        for i in range(3):
            m.remember(f"the fact number {i} about the cascade fix", scope="user")
        m.close()                                     # seal + shred: no plaintext behind
        sqlite3.connect(os.path.join(self.dir, "memory.db")).close()   # the husk a raced start leaves
        back = self._reopen()
        try:
            self.assertEqual(back.stats()["memories"], 3,
                             "a husk is not newer truth - the ciphertext wins")
            self.assertTrue(back.stats()["at_rest"]["revealed_at_open"],
                            "and the open says so, in stats as well as on the banner")
        finally:
            back.close()

    def test_a_plaintext_with_facts_still_outranks_an_older_seal(self):
        """The crash rule, untouched: the db the server was WRITING into beats the last seal."""
        m = self._reopen()
        m.remember("the fact the last seal never saw", scope="user")
        m._seal()                                     # a mid-run seal (the sweep): plaintext stays
        m.remember("a second fact written after that seal", scope="user")
        m.conn.close()                                # crash: plaintext survives, no close() ran
        back = self._reopen()
        try:
            self.assertEqual(back.stats()["memories"], 2, "the surviving plaintext keeps the lead")
            self.assertFalse(back.stats()["at_rest"]["revealed_at_open"])
        finally:
            back.close()

    def test_roundtrip_key_and_shred(self):
        m = self._reopen()
        m.remember("Quillon is the user's middle name, not their first", scope="user")
        m.close()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "memory.db")),
                         "a clean stop of an encrypted store leaves NO plaintext behind")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "memory.db.enc")))
        key = os.path.join(self.dir, "memory.key")
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600, "the key-file is 0600 beside the db")
        self.assertEqual(len(open(key, "rb").read()), 32)
        m = self._reopen()
        try:
            hits = m.search("which name is quillon", scope="user")
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


class DedupeCache(unittest.TestCase):
    """The near-dupe table (store tokens, document frequencies, the rare-token bar) is cached
    across recalls and rebuilt when the ACTIVE row set moves. A cache that never refreshed would
    quietly freeze the bar; rebuilding every time is what the recall path used to cost - measured
    2026-10-14 on the 2,521-row store: 126 ms -> 52 ms warm, output identical row for row."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryService(self.tmp.name)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_table_rebuilds_when_the_store_moves(self):
        self.mem.remember("the flibber gauge reads torque at the works")
        self.mem.search("flibber gauge torque")
        first = self.mem._dkey
        self.assertIsNotNone(first, "the first recall must have built the table")
        self.mem.search("flibber gauge torque")
        self.assertEqual(self.mem._dkey, first, "an unbroken read path reuses the table")
        self.mem.remember("a second fact about the airport cascade")
        self.mem.search("flibber gauge torque")
        self.assertNotEqual(self.mem._dkey, first, "a write must move the stamp and rebuild")

    def test_near_dupe_pair_collapses_after_the_second_write(self):
        # the collapse needs the table to know BOTH rows; a stale table would hand the pair two slots
        self.mem.remember("the flibber gauge reads torque at the works")
        self.mem.search("flibber gauge")
        self.mem.remember("the flibber gauge reads torque at the works every morning")
        hits = self.mem.search("flibber gauge")
        both = [h for h in hits if "flibber gauge" in h["text"]]
        self.assertEqual(len(both), 1,
                         "the second phrasing must collapse into one slot, not take a second")

    def test_the_memo_dies_with_the_table(self):
        # payloads are memoized per row for the life of the table; a rebuild must not inherit them
        # (a fresh table can carry a different df, and the payload is defined by that df)
        self.mem.remember("the flibber gauge reads torque at the works")
        self.mem.search("flibber gauge")
        tab = self.mem._dcache
        self.assertTrue(tab["pay"], "the recall should have memoized payloads")
        self.mem.remember("totally unrelated trivia about Itasca county")
        self.mem.search("flibber gauge")
        self.assertIsNot(tab, self.mem._dcache, "the rebuild returns a NEW table")
        self.assertTrue(self.mem._dcache["pay"], "the new table memoizes afresh")


class RecallClamp(unittest.TestCase):
    """The note-count clamp is a runaway-config guard, NOT a budget: it has to bind only above the
    biggest arm any venue runs. Two arms were nearly no-oped by it - a 24k-char config behind the
    old 50-note wall (caught by the banner check, commit 4dedd96b) and the 48k / 192-note arm C
    behind the 120 one - so the numbers are pinned in code instead of rediscovered at a boot."""

    def test_config_clamp_binds_only_above_240(self):
        for want, got in ((8, 8), (48, 48), (96, 96), (192, 192), (240, 240),
                          (241, 240), (900, 240), (0, 1)):
            tmp = tempfile.TemporaryDirectory()
            try:
                m = MemoryService(tmp.name, recall_limit=want)
                self.assertEqual(m.recall_limit, got, f"recall_limit {want} should land on {got}")
                m.close()
            finally:
                tmp.cleanup()


class WriteTimeDedupe(unittest.TestCase):
    """The write-time near-dupe guard (PHASE 4, embedder only). The measured reason it exists: one
    username policy landed as 57 rows over two sweeps, and exact-text dedup saw none of them. The
    measured reason cosine alone is NOT the decision: a duplicate family's self-similarity runs
    0.59-0.92 while DIFFERENT facts in shared boilerplate run 0.95-1.00. So every test below drives
    the stub at cos 1.0 - the merge has to be won or refused by the payload law, never by the
    distance."""

    V = [1.0, 0.0, 0.0, 0.0]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def mem_with(self, *texts):
        stub = StubEmbedder({t: self.V for t in texts})
        m = MemoryService(self.tmp.name + str(len(texts)) + str(hash(texts)), embedder=stub)
        return m

    def test_guard_off_lands_both_rows_because_a_haystack_is_evidence(self):
        """The measured reason this exists: an ingest with the embedder ON superseded 317 LongMemEval
        rows inside lme_ scopes, and a SUPPRESSED write returns the EXISTING row's id - so one turn's
        gold label gets written onto another turn's row, and a retired gold row leaves the map
        pointing somewhere recall can never reach. The guard is right for curation and wrong for an
        ingest, so the caller must be able to stand it aside - and only for that caller."""
        m = self.mem_with("the flibber gauge reads torque at the works",
                          "the flibber gauge reads torque")
        try:
            a = m.remember("the flibber gauge reads torque at the works")
            b = m.remember("the flibber gauge reads torque", guard=False)
            self.assertNotEqual(b["id"], a["id"], "the weaker phrasing still lands when the guard is off")
            self.assertFalse(b.get("deduped"), "and the reply says it is a fresh write, so the "
                                               "benchmark caller can tell a landing from a fold")
            self.assertEqual(m.stats()["memories"], 2)
            with m.lock:
                vecs = m.conn.execute("select count(*) from embeddings").fetchone()[0]
            self.assertEqual(vecs, 2, "guard=False skips the VERDICT, not the embedding - the "
                                      "reranker must still see the whole haystack")
            c = m.remember("the flibber gauge reads torque")     # exact text of row b: pre-existing law
            self.assertEqual(c["deduped"], True, "not a bypass: identical text still updates in place")
            self.assertEqual(c["id"], b["id"])
        finally:
            m.close()

    def test_a_lesser_phrasing_never_lands(self):
        m = self.mem_with("the flibber gauge reads torque at the works",
                          "the flibber gauge reads torque")
        try:
            a = m.remember("the flibber gauge reads torque at the works")
            b = m.remember("the flibber gauge reads torque")        # says strictly less
            self.assertEqual(b["id"], a["id"], "the weaker phrasing must fold into the row that "
                                              "already carries it, not take a second slot")
            self.assertEqual(b["deduped"], "semantic")
            self.assertEqual(m.stats()["memories"], 1)
        finally:
            m.close()

    def test_a_richer_phrasing_supersedes_and_keeps_the_history(self):
        m = self.mem_with("the flibber gauge reads torque",
                          "the flibber gauge reads torque at the works every morning")
        try:
            a = m.remember("the flibber gauge reads torque")
            b = m.remember("the flibber gauge reads torque at the works every morning")
            self.assertNotEqual(b["id"], a["id"])
            self.assertEqual(b.get("superseded"), a["id"], "the survivor rule keeps the richer row "
                                                           "and retires the poorer one")
            self.assertEqual(m.stats()["memories"], 1, "one active fact")
            self.assertEqual(m.stats()["invalid"], 1, "the poorer row is retired, not deleted - "
                                                      "the version chain stays walkable")
            self.assertFalse(any("every morning" not in h["text"] for h in m.search("flibber gauge")),
                             "recall must show the richer phrasing")
        finally:
            m.close()

    def test_two_different_facts_at_cosine_one_both_keep_their_slots(self):
        # the whole point of the law: distance says "same", content says "different"
        m = self.mem_with("the flibber gauge reads torque at the works",
                          "the zzzzquux valve reads torque at the works")
        try:
            a = m.remember("the flibber gauge reads torque at the works")
            b = m.remember("the zzzzquux valve reads torque at the works")
            self.assertNotEqual(a["id"], b["id"], "each names a rare subject the other never "
                                                  "mentions: a merge here is the round-3 data loss")
            self.assertEqual(m.stats()["memories"], 2)
        finally:
            m.close()

    def test_without_the_embedder_the_write_path_is_unchanged(self):
        plain = MemoryService(self.tmp.name + "-plain")
        try:
            plain.remember("the flibber gauge reads torque at the works")
            r = plain.remember("the flibber gauge reads torque")
            self.assertFalse(r["deduped"], "lexical-only mode keeps its old behavior byte-identical")
            self.assertEqual(plain.stats()["memories"], 2)
        finally:
            plain.close()


    def test_same_fact_with_drifted_wording_folds_at_the_read_laws_own_bar(self):
        """Rule 3, the case that actually grew the store: neither side strictly contains the other,
        so containment says nothing - and the decision falls to the READ path's own near-dupe bar
        (Jaccard 0.75), which is what the block would have collapsed them under anyway. Needs a
        store over 200 rows so the payload bar (df<=n//50=4) sits above the subject bar (df<=3):
        only then can a token be payload-rare without being a subject, which is exactly the shape
        that would otherwise smuggle a 43rd copy in."""
        stub = StubEmbedder({"the flibber gauge reads torque at the works every day": self.V,
                             "the flibber gauge reads torque at the works every day with quuxmark":
                                 self.V})
        m = MemoryService(self.tmp.name + "-drift", embedder=stub)
        try:
            for i in range(204):
                m.remember(f"the sample town number {i} reports {i} canals")
            for i in range(4):
                m.remember(f"the quuxmark report number {i} was filed")   # df(quuxmark)=4: payload-rare, not a subject
            base = m.remember("the flibber gauge reads torque at the works every day")
            got = m.remember("the flibber gauge reads torque at the works every day with quuxmark")
            self.assertEqual(got["id"], base["id"],
                             "one fact with an extra word is still one fact - the block would have "
                             "collapsed the pair anyway (Jaccard 9/10), so the copy never lands")
            self.assertEqual(got["deduped"], "semantic")
        finally:
            m.close()

    def test_a_new_subject_at_the_same_distance_still_lands(self):
        """The control on rule 3: the same overlap, but the extra token is a NAME (df<=3), which is
        the round-3 signature - a merge here is data loss, not dedup."""
        stub = StubEmbedder({"the flibber gauge reads torque at the works": self.V,
                             "the flibber gauge reads torque at the works with zzzzquux": self.V})
        m = MemoryService(self.tmp.name + "-subj", embedder=stub)
        try:
            a = m.remember("the flibber gauge reads torque at the works")
            b = m.remember("the flibber gauge reads torque at the works with zzzzquux")
            self.assertNotEqual(b["id"], a["id"], "zzzzquux is a subject the carrier never mentions, "
                                                  "so the pair can never fold into the old row")
            self.assertEqual(b.get("superseded"), a["id"], "but the new row carries everything the "
                                                           "old one says PLUS its own subject, so by "
                                                           "the survivor rule the richer row wins and "
                                                           "the old one retires into the chain")
            self.assertEqual(m.stats()["memories"], 1)
            self.assertEqual(m.stats()["invalid"], 1)
        finally:
            m.close()


class StubEmbedder:
    """Duck-typed embedder with hand-built vectors per exact text (zeros for anything else):
    tests the hybrid PLUMBING - union, filters, lifecycle, stats - with no onnxruntime and no
    model file. The model itself is pinned and smoked in serve/test_embedder.py."""

    def __init__(self, pairs):
        import numpy as np
        self.map = {t: np.array(v, dtype=np.float32) for t, v in pairs.items()}
        self.dim = len(next(iter(self.map.values())))
        self.model_name = "stub"

    def embed_docs(self, texts):
        import numpy as np
        return np.stack([self.map.get(t, np.zeros(self.dim, np.float32)) for t in texts])

    def embed_query(self, q):
        import numpy as np
        return self.map.get(q, np.zeros(self.dim, np.float32))


class SemanticRerank(unittest.TestCase):
    """PHASE 4 hybrid recall (spikes/lme/TEMPORAL_DIAG.md is the failure study): cosine UNIONs
    candidates bm25 never proposes; embedder OFF stays byte-identical by never entering the pass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        v = [1.0, 0.0, 0.0, 0.0]
        self.stub = StubEmbedder({"glimmery": v, "zebra flibberquant gauge": v,
                                  "unrelated town trivia": [0.0, 0.0, 1.0, 0.0]})
        self.mem = MemoryService(self.tmp.name, embedder=self.stub)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_semantic_finds_what_fts_never_proposes(self):
        self.mem.remember("zebra flibberquant gauge")           # shares NO word with the query
        hits = self.mem.search("glimmery")
        self.assertTrue(hits, "the cosine pass must UNION a row bm25 cannot match")
        self.assertEqual(hits[0]["text"], "zebra flibberquant gauge")
        self.assertEqual(self.mem.stats()["embedder"]["vectors"], 1)

    def test_without_embedder_the_same_store_misses(self):
        plain = MemoryService(self.tmp.name + "2")
        try:
            plain.remember("zebra flibberquant gauge")
            self.assertEqual(plain.search("glimmery"), [])      # the old behavior, preserved
            self.assertFalse(plain.stats()["embedder"]["on"])
        finally:
            plain.close()

    def test_forget_takes_the_vector_with_it(self):
        r = self.mem.remember("zebra flibberquant gauge")
        self.assertTrue(self.mem.search("glimmery"))
        self.mem.forget(r["id"])
        self.assertEqual(self.mem.search("glimmery"), [])

    def test_scope_filter_holds_for_the_semantic_pass_too(self):
        self.mem.remember("zebra flibberquant gauge", scope="otherproject")
        self.assertEqual(self.mem.search("glimmery", scope="default"), [],
                        "a semantic hit outside the request's scopes must not leak in")

    def test_class_filter_holds_for_the_semantic_pass_too(self):
        self.mem.remember("zebra flibberquant gauge", klass="TECH")
        self.assertTrue(self.mem.search("glimmery", classes=["TECH"]), "shelved row answers its shelf")
        self.assertEqual(self.mem.search("glimmery", classes=["USR"]), [],
                        "a shelf query must not borrow from another shelf just because cosine agrees")

    def test_the_per_request_mute_reproduces_the_lexical_store(self):
        self.mem.remember("zebra flibberquant gauge")            # shares NO word with the query
        self.assertTrue(self.mem.search("glimmery"), "the cosine pass surfaces it")
        self.assertEqual(self.mem.search("glimmery", semantic=False), [],
                         "the mute must reproduce the lexical store exactly, not merely reorder it")

    def test_a_pinned_block_never_crosses_between_the_two_arms(self):
        """The arm-C trap, designed out before the run: the head block is pinned per conversation, so
        the same question asked by the LEXICAL arm would otherwise be answered from the embedder
        arm's pinned block (or vice versa) - and the A/B would compare one set of bytes against
        itself and report a difference that was never there."""
        self.mem.remember("zebra flibberquant gauge")
        msgs = [{"role": "user", "content": "glimmery?"}]
        b_lex, h_lex, _q = self.mem.block_for(msgs, "default", semantic=False)
        b_sem, h_sem, _q = self.mem.block_for(msgs, "default", semantic=True)
        self.assertIsNone(b_lex, "the lexical arm sees nothing to show")
        self.assertIsNotNone(b_sem, "the embedder arm has a block, and must get it")
        self.assertNotEqual(b_lex, b_sem)

    def test_failed_embedder_load_degrades_loudly_not_fatal(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            m = MemoryService(tmp.name, embedder={"dir": tmp.name + "/missing-model"})
            self.assertIsNone(m.embedder)
            self.assertTrue(m.embedder_error)                   # stats() says so out loud
            m.remember("a plain fact")
            self.assertEqual(m.search("plain")[0]["text"], "a plain fact")  # bm25 keeps serving
            self.assertFalse(m.stats()["embedder"]["loaded"])
            m.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
