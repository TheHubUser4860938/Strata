#!/usr/bin/env python3
"""Offline tests for the LongMemEval venue harness (tools/lme_harness.py, 2026-10-13).

No GPU, no downloads: a scripted MockEngine server + a real MemoryService in a temp dir,
and two synthetic questions in the benchmark's exact field shape. What this proves:
ingest lands in the question's own scope, the gold map (row id -> has_answer/session) is
right, the Primnox top-1/top-3 metrics score against it, resume skips completed work
without re-ingesting, and the metrics file aggregates with the judge disclosed.
"""
from __future__ import annotations
import http.client, json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                        # noqa: E402
from serve.frontend import ChatTemplate                        # noqa: E402
from serve.memory import MemoryService                         # noqa: E402
from serve.server import ByteTokenizer, MockEngine, Service, serve   # noqa: E402

QUESTIONS = [
    {"question_id": "q1", "question_type": "single-session-user",
     "question": "what does the zygost scale read when calibrated?", "answer": "41.2",
     "question_date": "2023-06-01 (Mon) 09:00",
     "haystack_session_ids": ["s_a", "s_b"], "answer_session_ids": ["s_a"],
     "haystack_dates": ["2023-05-02 (Tue) 10:00", "2023-05-04 (Thu) 11:00"],
     "haystack_sessions": [
         [{"role": "user", "content": "The zygost scale reads 41.2 when calibrated", "has_answer": True},
          {"role": "assistant", "content": "Noted.", "has_answer": False}],
         [{"role": "user", "content": "The weather in Osnabrick was mild", "has_answer": False},
          {"role": "assistant", "content": "Nice.", "has_answer": False}]]},
    {"question_id": "q2_abs", "question_type": "single-session-user",
     "question": "who led the expedition across the Veldt marsh?", "answer": "not mentioned",
     "question_date": "2023-06-02 (Tue) 09:00",
     "haystack_session_ids": ["s_c"], "answer_session_ids": [],
     "haystack_dates": ["2023-05-05 (Fri) 08:00"],
     "haystack_sessions": [[{"role": "user", "content": "I adopted a hamster named Pip",
                             "has_answer": False}]]},
]


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        self.engine = MockEngine(tok, "the mock answer", max_context=131072)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.memory = MemoryService(self.tmp.name)
        self.httpd = serve(self.svc, port=0)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.data = os.path.join(self.tmp.name, "q.json")
        with open(self.data, "w") as f:
            json.dump(QUESTIONS, f)
        self.out = os.path.join(self.tmp.name, "hyp.jsonl")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.svc.memory.close()
        self.tmp.cleanup()

    def argv(self, *extra):
        return ["--data", self.data, "--url", self.url, "--out", self.out,
                "--repeats", "2", "--no-judge", *extra]

    def lines(self):
        return [json.loads(l) for l in open(self.out)]

    def test_ingest_recall_and_metrics(self):
        H.main(self.argv())
        rows = self.lines()
        self.assertEqual(len(rows), 4, "two questions x two repeats")
        q1 = [r for r in rows if r["question_id"] == "q1"][0]
        self.assertEqual(q1["hypothesis"], "the mock answer")
        self.assertTrue(q1["turn_top1"], "the has_answer turn must be the first hit")
        self.assertTrue(q1["turn_top3"])
        self.assertTrue(q1["session_top1"], "the hit row maps back to its gold session")
        abstain = [r for r in rows if r["question_id"] == "q2_abs"][0]
        self.assertIsNone(abstain["turn_top1"], "abstention keeps recall nulls, not fake zeros")
        self.assertEqual(abstain["type"], "abstention")
        # the gold map persisted, and the store holds exactly the scoped rows
        maps = H.load_map(self.out + ".map")
        self.assertIn("q1", maps)
        self.assertTrue(any(v[0] for v in maps["q1"].values()), "a row is marked has_answer")
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("GET", "/memory?limit=100")
        got = json.loads(conn.getresponse().read().decode())
        scopes = {m.get("scope") for m in got["memories"]}
        self.assertIn("lme_q1", scopes, "ingest rode the same API any client uses")
        mfile = json.load(open(self.out + ".metrics.json"))
        self.assertEqual(mfile["metrics"]["overall"]["turn_top1"], 1.0)   # the only scorable question: hit
        self.assertIsNone(mfile["metrics"]["overall"]["accuracy"])        # --no-judge says so honestly
        self.assertEqual(mfile["metrics"]["judge"], "none (--no-judge)")

    def test_resume_skips_done_work_without_reingesting(self):
        H.main(self.argv("--limit", "1"))
        first = self.lines()
        self.assertTrue(all(r["question_id"] == "q1" for r in first))
        maps_n = len(H.load_map(self.out + ".map"))
        H.main(self.argv())                                      # full run now: q1 done, q2 fresh
        rows = self.lines()
        self.assertEqual(sum(1 for r in rows if r["question_id"] == "q1"), 2, "q1 not duplicated")
        self.assertEqual(sum(1 for r in rows if r["question_id"] == "q2_abs"), 2)
        self.assertEqual(len(H.load_map(self.out + ".map")), maps_n + 1, "q1 ingested once")

    def test_skip_ingest_without_map_is_an_error(self):
        with self.assertRaises(SystemExit):
            H.main(self.argv("--skip-ingest"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
