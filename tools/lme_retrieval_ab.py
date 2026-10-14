#!/usr/bin/env python3
"""lme_retrieval_ab - does the reranker help retrieval, measured WITHOUT generating an answer?

The accuracy venue answers 500 questions x 3 repeats: every line pays a prefill (a 12k-token first
turn is ~8.5 s at the measured 1,400 tok/s) plus a judge call, so one arm is ~1.5-2 h of owned port.
But the retrieval metrics - top-1/top-3 and the multi-evidence coverage fields - need only the store's
own search: 500 GETs per arm, no engine, no judge, ~2 minutes. Same questions, same scopes, same gold
map, same scoring function the accuracy run uses (lme_harness.recall_metrics), and the two arms are
separated by the per-request reranker mute - so they run against ONE process, ONE store and ONE
vector set.

That makes this the mechanism measurement and the accuracy run the outcome measurement. If the
reranker cannot beat the lexical store on its own ground here, the "the embedder is always a
positive" claim is already in trouble before a single token is generated; if it wins everywhere here
and the accuracy run is flat, the flatness is a budget/attention story, not a retrieval one - and
those two readings want opposite shipping decisions.

Two checks run before any number is believed, because an A/B whose lever never moved reports a
difference that is not there:
  * every reply's echoed `"embedder"` field must match what that request asked for (a header that
    silently stops being read would otherwise score the SAME arm twice and call it a comparison);
  * the two arms must actually return different hit lists for some questions - if they are identical
    everywhere, the reranker is not loaded, not configured, or the store has no vectors: exit 2.

  python3 tools/lme_retrieval_ab.py --data longmemeval_oracle.json --map <shared.map>
      [--url http://127.0.0.1:8005] [--recall-k 10] [--wide-k 0] [--limit N] [--types ...]

`--wide-k` scores a second pass at the block's own note budget (e.g. 192), which is where the
coverage question actually lives: a three-event ordering question cannot be answered from a top-10
list no matter how good the ranking is.
"""
from __future__ import annotations

import argparse, collections, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lme_harness as H                                          # noqa: E402

ARMS = (("lexical", True), ("hybrid", False))                    # (label, no_embedder)


def sweep(url: str, data: list, maps: dict, k: int, no_embedder: bool) -> list[dict]:
    rows = []
    for q in data:
        qid = q["question_id"]
        where = maps.get(qid)
        if where is None:
            continue                                   # not ingested by this store: nothing to score
        r = H.recall(url, q, f"lme_{qid}", where, k, no_embedder=no_embedder)
        r["type"] = q.get("question_type", "?")
        r["question_id"] = qid
        rows.append(r)
    return rows


def agg(rows: list[dict]) -> dict:
    out = {}
    for key in ("turn_top1", "turn_top3", "session_top1", "session_top3",
                "row_coverage", "session_coverage"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 3) if vals else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--map", dest="map_path", required=True, help="the gold sidecar the ingest wrote")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--recall-k", type=int, default=10, help="slots to score (the accuracy run's k)")
    ap.add_argument("--wide-k", type=int, default=0, help="optional second pass at the block budget")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="")
    a = ap.parse_args(argv)

    data = json.load(open(a.data))
    types = {t.strip() for t in a.types.split(",") if t.strip()}
    if types:
        data = [q for q in data if q.get("question_type") in types]
    if a.limit:
        data = data[:a.limit]
    maps = H.load_map(a.map_path)
    if not maps:
        print(f"no gold map at {a.map_path} - the A/B cannot score anything", file=sys.stderr)
        return 2
    print(f"[ab] {len(data)} questions x {len(ARMS)} arms, k={a.recall_k}, "
          f"map holds {len(maps)} questions; no engine calls, so this is store time only")

    ks = [a.recall_k] + ([a.wide_k] if a.wide_k else [])
    for k in ks:
        res, mismatches, same_list, t0 = {}, 0, 0, time.time()
        for label, mute in ARMS:
            rows = sweep(a.url, data, maps, k, mute)
            want_arm = (label != "lexical")            # the reply's echo: True only for the hybrid arm
            mismatches += sum(1 for r in rows if r.get("arm") is not None and bool(r["arm"]) != want_arm)
            res[label] = rows
        if len(res.get("lexical", [])) != len(res.get("hybrid", [])):
            print("[ab] the arms scored different question sets - the store changed mid-pass", file=sys.stderr)
            return 2
        by_q = {r["question_id"]: r for r in res["hybrid"]}
        same_list = sum(1 for r in res["lexical"]
                        if [x for x in by_q[r["question_id"]]["ids"][:k]] == r["ids"][:k])
        print(f"\n=== k={k} ===  {time.time() - t0:.0f} s for {len(res['lexical']) * 2} recalls",
              flush=True)   # a venue log read afterwards must show the header BEFORE its table
        if mismatches:
            print(f"[ab] {mismatches} replies reported an arm that does not match the request sent - "
                  "the header is not being honoured; these numbers are NOT an A/B", file=sys.stderr)
            return 2
        if same_list == len(res["lexical"]):
            print("[ab] both arms returned identical hit lists for EVERY question: the reranker did "
                  "not move. Check that memory.embedder is configured and the store is embedded "
                  "(stats -> embedder.vectors). Exiting 2 rather than reporting a null comparison as "
                  "a result.", file=sys.stderr)
            return 2
        print(f"  identical hit lists: {same_list}/{len(res['lexical'])} "
              f"(the lever moved on {len(res['lexical']) - same_list})")
        cols = (("turn_top1", "turn_top1"), ("turn_top3", "turn_top3"),
                ("row_cov", "row_coverage"), ("sess_cov", "session_coverage"))
        print("  " + f"{'type':<26}{'n':>4}" + "".join(f"{lbl:>18}" for lbl, _ in cols))
        for scope in ("OVERALL",) + tuple(sorted({r["type"] for r in res["lexical"]})):
            sel = {"lexical": res["lexical"], "hybrid": res["hybrid"]}
            if scope != "OVERALL":
                sel = {k2: [r for r in v if r["type"] == scope] for k2, v in sel.items()}
            n = len(sel["lexical"])
            if not n:
                continue
            A, B = agg(sel["lexical"]), agg(sel["hybrid"])
            cells = ""
            for _lbl, key in cols:
                if A[key] is None or B[key] is None:
                    cells += f"{'n/a':>18}"
                else:
                    cells += f"{A[key]:.3f}->{B[key]:.3f} {B[key] - A[key]:+.3f}".rjust(18)
            print(f"  {scope:<26}{n:>4}" + cells)
    print("\n[ab] read as: the mechanism measurement. The accuracy venue is the outcome; this only "
          "says whether the reranker moved the evidence, per type, on the store's own search.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
