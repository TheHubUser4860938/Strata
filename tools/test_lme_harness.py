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

    def test_official_checker_templates(self):
        # the judge must be the benchmark's own checker, chosen by question type
        p = H.anscheck_prompt("temporal-reasoning", "Q", "A", "R", False)
        self.assertIn("off-by-one", p)
        self.assertTrue(p.endswith("Answer yes or no only."))
        self.assertIn("off-by-one", p)
        self.assertIn("rubric", H.anscheck_prompt("single-session-preference", "Q", "A", "R", False))
        self.assertIn("previous information", H.anscheck_prompt("knowledge-update", "Q", "A", "R", False))
        self.assertIn("unanswerable", H.anscheck_prompt("single-session-user", "Q", "A", "R", True))
        # the oracle's 32 counting questions carry answer as an int (crash of the second run)
        self.assertIn("Correct Answer: 42",
                      H.anscheck_prompt("single-session-user", "Q", 42, "There are 42", False))
        # ... and scrub met the same ints at the line-write (crash of the third run); an answer
        # of 0 must survive too - the old (s or "") would have stored it as empty
        self.assertEqual(H.scrub(42), "42")
        self.assertEqual(H.scrub(0), "0")
        self.assertEqual(H.scrub("a<|b|>c"), "a< |b| >c")
        with self.assertRaises(ValueError):
            H.anscheck_prompt("made-up-type", "Q", "A", "R", False)

    def test_skip_ingest_without_map_is_an_error(self):
        with self.assertRaises(SystemExit):
            H.main(self.argv("--skip-ingest"))

    def test_long_turns_chunk_under_the_store_limit(self):
        # the venue crash, 2026-10-13 (full oracle, question 16): LongMemEval haystack turns -
        # mostly assistant answers - run past the store's 4,000-char single-fact cap and
        # remember refused mid-ingest (HTTP 400). The harness splits long turns at word
        # boundaries; every chunk carries the turn's gold, and NO content is lost.
        long = "".join(f"note{i:04d} alpha beta gamma delta " for i in range(360)) + \
            "and the calibrated zygost reading was 77.3"
        q3 = {"question_id": "q3", "question_type": "single-session-user",
              "question": "what reading did the zygost give?", "answer": "77.3",
              "question_date": "2023-06-03 (Wed) 09:00",
              "haystack_session_ids": ["s_l"], "answer_session_ids": ["s_l"],
              "haystack_dates": ["2023-05-06 (Sat) 07:00"],
              "haystack_sessions": [[
                  {"role": "user", "content": "I will ask you to remember a reading", "has_answer": False},
                  {"role": "assistant", "content": long, "has_answer": True}]]}
        with open(self.data, "w") as f:
            json.dump([q3], f)
        H.main(self.argv())                                    # pre-fix: HTTPError 400 here
        rows = self.svc.memory.list_memories(scope="lme_q3", limit=100)
        self.assertTrue(all(len(m["text"]) <= 4000 for m in rows),
                        "a stored row crossed the 4,000-char law")
        lead = "The assistant told the user:"
        # chunks after the first START with the boundary space, so match on content, not prefix;
        # order by insertion id - lexicographic text order would scramble the phases
        pieces = [m["text"] for m in sorted((m for m in rows if lead in m["text"] or "note" in m["text"]),
                                            key=lambda m: m["id"])]
        self.assertGreater(len(pieces), 2, "the long turn must exist as several chunks")
        cut0 = pieces[0].index(lead) + len(lead) + 1        # +1: the one space after the colon
        joined = pieces[0][cut0:] + "".join(" " + p for p in pieces[1:])   # one space per seam
        self.assertEqual(joined, long, "chunking lost, reordered, or mis-split content")
        maps = H.load_map(self.out + ".map")["q3"]
        gold_rows = [rid for rid, (ha, sid) in maps.items() if ha]
        self.assertGreaterEqual(len(gold_rows), 2, "chunks of the answer turn must carry its gold")
        self.assertTrue(any("77.3" in m["text"] for m in rows), "the evidence tail vanished")


if __name__ == "__main__":
    unittest.main(verbosity=2)
