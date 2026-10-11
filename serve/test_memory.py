"""memory-core fork: the memory store, sticky blocks, injection and journaling - no GPU, no model pack.

    python -m unittest serve.test_memory -v

Runs the real server over the mock engine (like serve/test_context_overflow.py): facts remembered over
HTTP come back in recall, a chat turn is journaled, the mute header skips memory entirely, and the
injected block is byte-stable for a conversation while the store changes underneath.
"""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.memory import MemoryService, request_state
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
        self.assertIsNone(ctx)
        self.assertEqual(out, msgs, "nothing recalled: the list rides through untouched")

    def test_turn_journal(self):
        tid = self.mem.journal_turn("fw", "the question", "the answer", "m")
        pend = self.mem.pending_turns()
        self.assertEqual([t["id"] for t in pend], [tid])
        self.mem.mark_processed([tid])
        self.assertEqual(self.mem.pending_turns(), [])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
