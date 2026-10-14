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


class IngestLaw(unittest.TestCase):
    """The harness's own ingest rules, HTTP stubbed. The gold map IS the benchmark's ground truth, so
    both what gets written and what it gets labelled are under test - the store's consolidation is a
    correct behaviour in the wrong place, and it silently moves gold labels."""
    Q = {"question_id": "q1", "question": "?", "question_type": "temporal-reasoning",
         "answer_session_ids": ["sA"],
         "haystack_session_ids": ["sA", "sB"],
         "haystack_dates": ["2026-01-05 10:00", "2026-01-06 10:00"],
         "haystack_sessions": [[{"role": "user", "content": "alpha", "has_answer": True}],
                               [{"role": "user", "content": "beta", "has_answer": False}]]}

    def setUp(self):
        self._real, H.post = H.post, None
        self.bodies = []

    def tearDown(self):
        H.post = self._real

    def test_haystack_writes_go_in_with_the_near_dupe_guard_standing_aside(self):
        def fake(url, path, body, headers=None, timeout=240.0):
            self.bodies.append(body)
            return {"id": len(self.bodies), "deduped": False}
        H.post = fake
        where = H.ingest("u", self.Q, "lme_q1")
        self.assertTrue(all(b.get("guard") is False for b in self.bodies),
                        "a haystack turn must not be consolidated away from its own gold label")
        self.assertEqual((where[1][0], where[1][1]), (True, "sA"))
        self.assertEqual((where[2][0], where[2][1]), (False, "sB"))

    def test_a_folded_write_aborts_the_run_rather_than_scoring_a_shuffled_map(self):
        """The flag is only an assumption until the reply proves it - the same rule the arm mute
        follows: an experiment that quietly stops being an experiment has to fail loudly."""
        def fake(url, path, body, headers=None, timeout=240.0):
            return {"id": 7, "deduped": "semantic"}          # the server ignored guard=false
        H.post = fake
        with self.assertRaises(RuntimeError) as cm:
            H.ingest("u", self.Q, "lme_q1")
        self.assertIn("guard=false", str(cm.exception))

    def test_two_turns_sharing_a_row_keep_the_first_session_and_the_or_of_the_gold(self):
        def fake(url, path, body, headers=None, timeout=240.0):
            return {"id": 3, "deduped": False}               # identical text: both turns share a row
        H.post = fake
        where = H.ingest("u", self.Q, "lme_q1")
        self.assertEqual(where[3], (True, "sA"), "a later non-gold duplicate must not erase the "
                                                 "gold label that was already on the row")


class OfflineArmAB(unittest.TestCase):
    """tools/lme_retrieval_ab.py --offline: the mechanism measurement with NO server, no engine and no
    port, run against a copy. The toy embedder below collapses a synonym pair onto one dimension, so
    the hybrid arm reaches a row the words alone cannot - which is exactly the claim under test - and
    the two guards (the arm must be the arm it claims; identical hit lists everywhere mean the lever
    never moved) are exercised on the real code path rather than by hand."""

    class ToyEmbedder:
        """Bag-of-words vectors over a fixed vocab, with "glimmery" and "gauge" deliberately sharing
        one dimension: a query that shares NO surface token with the answer row still scores cosine
        1.0 against it. Deterministic, no onnxruntime, no model file."""

        DIM = 8
        VOCAB = {"glimmery": 3, "gauge": 3, "zebra": 1, "flibberquant": 2, "torque": 4,
                 "works": 5, "station": 6, "licensed": 7}

        def __init__(self, model_dir=None, threads=4, **kw):
            import numpy as np
            self.np, self.dim, self.model_name = np, self.DIM, "toy"

        def _v(self, text):
            v = self.np.zeros(self.dim, self.np.float32)
            for w in str(text).lower().split():
                if w in self.VOCAB:
                    v[self.VOCAB[w]] = 1.0
            n = float(self.np.linalg.norm(v))
            return v / n if n else v

        def embed_docs(self, texts):
            return self.np.stack([self._v(t) for t in texts])

        def embed_query(self, q):
            return self._v(q)

    def setUp(self):
        import types
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = sys.modules.get("serve.embedder")
        sys.modules["serve.embedder"] = types.SimpleNamespace(Embedder=self.ToyEmbedder)

    def tearDown(self):
        if self._saved is not None:
            sys.modules["serve.embedder"] = self._saved
        else:
            sys.modules.pop("serve.embedder", None)
        self.tmp.cleanup()

    def build(self):
        """A two-question store: q1's answer row shares no WORD with the question (only the toy's
        synonym collapse reaches it), q2's answer row is lexically obvious."""
        from serve.memory import MemoryService
        d = Path(self.tmp.name) / "store"
        m = MemoryService(str(d))
        try:
            g1 = m.remember("the zebra flibberquant gauge reads torque at the works",
                            scope="lme_q1")["id"]
            m.remember("station kzq1 is licensed to Terrytown", scope="lme_q1")
            g2 = m.remember("station kzq2 is licensed to Seward", scope="lme_q2")["id"]
        finally:
            m.close()
        mp = Path(self.tmp.name) / "map.jsonl"
        with open(mp, "w") as fh:
            fh.write(json.dumps({"question_id": "q1", "where": {str(g1): [True, "s1"]}}) + "\n")
            fh.write(json.dumps({"question_id": "q2", "where": {str(g2): [True, "s2"]}}) + "\n")
        return d, mp

    def data(self, questions, name):
        p = Path(self.tmp.name) / name
        with open(p, "w") as fh:
            json.dump(questions, fh)
        return str(p)

    def test_the_offline_arms_differ_where_paraphrase_is_the_only_route(self):
        import lme_retrieval_ab as AB
        store, mp = self.build()
        data = self.data([{"question_id": "q1", "question": "glimmery?",     # shares NO word with the
                           "question_type": "temporal-reasoning", "answer_session_ids": ["s1"]},
                          {"question_id": "q2", "question": "which station is licensed to Seward",
                           "question_type": "single-session-user", "answer_session_ids": ["s2"]}],
                         "d1.json")
        dump = str(Path(self.tmp.name) / "dump.jsonl")
        rc = AB.main(["--data", data, "--map", str(mp), "--offline", str(store),
                      "--work", str(Path(self.tmp.name) / "work"), "--dump", dump, "--recall-k", "5"])
        self.assertEqual(rc, 0, "the lever moved on q1, so this IS a comparison")
        lines = [json.loads(l) for l in open(dump)]
        self.assertEqual(len(lines), 4, "two questions x two arms")
        q1 = {r["label"]: r for r in lines if r["question_id"] == "q1"}
        self.assertFalse(q1["lexical"]["turn_top1"],
                         "the question shares no word with the answer: bm25 cannot reach it")
        self.assertTrue(q1["hybrid"]["turn_top1"], "the reranker's job, measured not assumed")
        self.assertIs(q1["hybrid"]["arm"], True, "the arm is read back from the service, not hoped for")
        self.assertIs(q1["lexical"]["arm"], False, "and every line carries what the run asked AND what "
                                                   "actually served it, so a mis-set lever stays visible")

    def test_identical_arms_refuse_rather_than_report_a_null_comparison(self):
        """Both arms reach q2 the same way, so with only q2 in the set the lever never moved - and an
        A/B that never happened must exit 2 instead of printing zeros as a result."""
        import lme_retrieval_ab as AB
        store, mp = self.build()
        data = self.data([{"question_id": "q2", "question": "station kzq2 licensed Seward",
                           "question_type": "single-session-user", "answer_session_ids": ["s2"]}],
                         "d2.json")
        rc = AB.main(["--data", data, "--map", str(mp), "--offline", str(store),
                      "--work", str(Path(self.tmp.name) / "work2"), "--recall-k", "5"])
        self.assertEqual(rc, 2)

    def test_the_copy_is_reused_and_the_reveal_is_not_run_twice(self):
        """--work exists so an analysis loop does not re-embed 11k rows per pass; the second pass must
        find the vectors already there (the plaintext copy outranks the stale .enc - the husk rule is
        what tells them apart) and must still score the same numbers."""
        import lme_retrieval_ab as AB
        store, mp = self.build()
        data = self.data([{"question_id": "q1", "question": "glimmery torque?",
                           "question_type": "temporal-reasoning", "answer_session_ids": ["s1"]}],
                         "d3.json")
        work = str(Path(self.tmp.name) / "work3")
        args = ["--data", data, "--map", str(mp), "--offline", str(store), "--work", work,
                "--recall-k", "5"]
        first, second = AB.main(args), AB.main(args)
        self.assertEqual((first, second), (0, 0), "a reused copy is not a fresh embed, but it scores "
                                                  "the same: the arm result must not depend on which "
                                                  "pass produced the vectors")


class CoverageMetric(unittest.TestCase):
    """The metric spikes/lme/TEMPORAL_DIAG.md asked for. turn_top1/top3 are satisfied by ANY one
    gold turn - which is precisely what hid the ordering failures - so coverage must report the
    FRACTION of a question's gold rows and gold SESSIONS that reached the recall, and abstention
    must report nothing rather than a fake zero."""

    WHERE = {10: (True, "s1"), 11: (True, "s2"), 12: (False, "s3")}

    def recall_with(self, hit_ids, qid="q9"):
        q = {"question_id": qid, "question": "which two events happened first",
             "answer_session_ids": ["s1", "s2"]}
        orig = H.get
        try:
            H.get = lambda url, path, params, headers=None: {"hits": [{"id": i} for i in hit_ids]}
            return H.recall("http://x", q, "lme_q9", dict(self.WHERE), 8)
        finally:
            H.get = orig

    def test_partial_evidence_reports_a_fraction_not_a_pass(self):
        r = self.recall_with([10, 12])                     # one of TWO gold sessions arrived
        self.assertTrue(r["turn_top1"], "the old flag is happy: a gold turn is first")
        self.assertEqual(r["session_coverage"], 0.5, "but only one of two events reached the block")
        self.assertEqual(r["row_coverage"], 0.5)
        self.assertEqual((r["gold_sessions"], r["shown_sessions"]), (2, 1))

    def test_full_coverage_is_one(self):
        r = self.recall_with([10, 11, 12])
        self.assertEqual((r["session_coverage"], r["row_coverage"]), (1.0, 1.0))
        self.assertEqual((r["gold_sessions"], r["shown_sessions"]), (2, 2))

    def test_abstention_reports_no_coverage_at_all(self):
        r = self.recall_with([12], qid="q9_abs")
        self.assertIsNone(r["turn_top1"], "abstention keeps the nulls, as the official eval does")
        self.assertNotIn("session_coverage", r, "no gold location means no coverage number")

    def test_the_two_instruments_share_one_scorer(self):
        """The accuracy venue and the retrieval A/B must not disagree about what the SAME hit list
        meant - the only difference between them is who fetched the ids."""
        r = self.recall_with([10, 12])
        direct = H.recall_metrics([10, 12], dict(self.WHERE), {"s1", "s2"}, False, 8)
        self.assertEqual({k: v for k, v in r.items() if k in direct}, direct,
                         "a mechanism number and an outcome number scored by different code would "
                         "be two experiments wearing one protocol")

    def test_the_ab_aggregator_skips_abstentions_instead_of_averaging_them_in(self):
        import lme_retrieval_ab as AB
        a = AB.agg([{"turn_top1": True, "session_coverage": 1.0},
                    {"turn_top1": None, "session_coverage": None},     # abstention: skipped, not a 0
                    {"turn_top1": False, "session_coverage": 0.5}])
        self.assertEqual(a["turn_top1"], 0.5, "the mean is over the two scorable questions")
        self.assertEqual(a["session_coverage"], 0.75)
        self.assertIsNone(AB.agg([{"turn_top1": None}])["turn_top1"], "nothing scorable is n/a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
