#!/usr/bin/env python3
"""m4_consensus - rank provenance turned into a decision: does a placement law recover what the reranker
loses at rank 1, and at what price?

This is what `lex_rank`/`cos_rank` on a hit were added FOR. The -0.139 knowledge-update regression (the
reranker losing rank-1 slots the lexical arm won: 0.625 -> 0.486) had been diagnosed by hand-reading 11
cases; with provenance in the record the same question is a two-second aggregate over all 500, and it
answered a claim I had made and was about to build on:

  WHAT LANDED THE HYBRID ARM'S RANK-1 SLOT (venue copy, 500 questions, RRF_K=1)
    both arms agree at rank 1        174   top-1 correct 0.615
    bm25 rank 1, cosine demoted it   201   top-1 correct 0.463
    promoted from bm25 rank 2         56   top-1 correct 0.286
    promoted from bm25 rank 3+        27   top-1 correct ~0.40
    cosine-only (bm25 never proposed it) 0  <- it never happens at rank 1 under k=1
  all 11 knowledge-update flips: the winner was the COSINE arm's own rank-1 row every time

So consensus is the reliable signal, and the "cosine-only rows are burying the answer" story is false at
rank 1 - which is exactly the kind of claim this project keeps discovering is wrong when it gets measured
instead of argued.

The variants below are pure RE-ORDERINGS of the same hit list, so candidate membership - and therefore
row/session coverage - cannot change; the coverage column is printed to prove that rather than assert it.
Denominator matches the benchmark's own convention (abstention questions return None and are skipped), so
"shipped" here reproduces the filed 0.517 / 0.849 line for line.

Verdict, measured 2026-10-15: no placement law is worth shipping. Gating cosine-only rows leaves top-1
EXACTLY where it is (0.517 at n=1 and at n=3 - they never reach rank 1 under k=1) and COSTS top-3
(0.849 -> 0.826 at n=1, because the rows being pushed down include gold ones), so the gate is not neutral,
it is a small loss. Weighting the lexical arm x2 recovers the knowledge-update column (0.486 -> 0.611,
most of the -0.139) but pays overall (top1 0.517 -> 0.506, top3 0.849 -> 0.830), and after
tools/lme_posrank.py a top-1 slot inside the block is worth ~nothing to accuracy anyway. The reranker's
+0.040 came from COVERAGE on multi-session and abstention questions, not from rank 1, so trading rank-1
points between types buys a metric, not the product.

Run the dump first (no engine, ~45 s):
    python3 tools/lme_retrieval_ab.py --data <oracle.json> --map <map> --offline Strata-data/memory-venue \
        --work /tmp/opencode/venue-ab-store --dump /tmp/opencode/venue_ab_prov.jsonl
then:
    python3 tools/m4_consensus.py --dump /tmp/opencode/venue_ab_prov.jsonl
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from lme_harness import load_map, recall_metrics                     # noqa: E402

K = 10                                          # the accuracy run's scored depth


def v_shipped(hits):
    return hits


def v_gate_top(hits, n=1):
    """A row the lexical arm never proposed may not sit in the first n slots."""
    sup = [h for h in hits if h[1] is not None]
    uns = [h for h in hits if h[1] is None]
    return (sup[:n] + uns + sup[n:]) if sup else hits


def v_lex_weight(hits, w=2.0, rrf_k=1.0):
    """Weighted RRF: the lexical arm's vote counts w times the cosine arm's."""
    def sc(h):
        return (w / (rrf_k + h[1]) if h[1] else 0.0) + (1.0 / (rrf_k + h[2]) if h[2] else 0.0)
    return sorted(hits, key=sc, reverse=True)


VARIANTS = [("shipped (RRF k=1, equal arms)", v_shipped),
            ("cosine-only out of the top 1", lambda h: v_gate_top(h, 1)),
            ("cosine-only out of the top 3", lambda h: v_gate_top(h, 3)),
            ("weighted RRF: lexical x2", lambda h: v_lex_weight(h, 2.0))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default="/tmp/opencode/venue_ab_prov.jsonl",
                    help="the A/B dump written with --dump; lines must carry 'prov'")
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.dump) if json.loads(l).get("label") == "hybrid"]
    if not rows or not rows[0].get("prov"):
        print("the dump carries no 'prov' field - re-run lme_retrieval_ab.py --dump with the "
              "provenance-aware sweep (this tool has nothing to work with otherwise)")
        return 2
    m = load_map(a.mpath)
    print(f"{len(rows)} hybrid questions from {a.dump}\n")
    print(f"  {'variant':<32}{'top1':>8}{'top3':>8}{'ku top1':>10}{'row_cov':>10}")
    for name, fn in VARIANTS:
        t1 = t3 = tn = ku1 = kun = 0
        cov = covn = 0
        for r in rows:
            where = m.get(r["question_id"]) or {}
            gold_sids = {v[1] for v in where.values() if v[0]}
            hits = fn([(i, lr, cr) for (i, lr, cr) in (r["prov"] or [])])
            sc = recall_metrics([h[0] for h in hits], where, gold_sids,
                                r["question_id"].endswith("_abs"), K)
            if sc["turn_top1"] is not None:         # the benchmark's own convention: abstention skipped
                t1 += 1 if sc["turn_top1"] else 0
                t3 += 1 if sc["turn_top3"] else 0
                tn += 1
                if r["type"] == "knowledge-update":
                    ku1 += 1 if sc["turn_top1"] else 0
                    kun += 1
            if sc.get("row_coverage") is not None:
                cov += sc["row_coverage"]
                covn += 1
        print(f"  {name:<32}{t1 / max(1, tn):>8.3f}{t3 / max(1, tn):>8.3f}"
              f"{ku1 / max(1, kun):>10.3f}{cov / max(1, covn):>10.3f}")
    print("\n  reference, same denominator: the LEXICAL arm scores top1 0.504, top3 0.749, "
          "knowledge-update\n  top1 0.625. Coverage is identical across variants by construction (a "
          "re-ordering cannot\n  change membership) - the column is printed to prove that, not to assert "
          "it.")
    print("\n  verdict as measured: ship none of these. The gates do nothing because cosine-only rows "
          "never\n  reach rank 1 under k=1, and the weighted arm trades overall top1/top3 to recover one "
          "type's\n  rank-1 - a metric swap, since lme_posrank.py already showed the block's slot order "
          "is worth\n  ~nothing to accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
