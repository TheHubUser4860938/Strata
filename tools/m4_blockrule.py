#!/usr/bin/env python3
"""m4_blockrule - cap, not quota: sweep a fused-score floor on the render block against the replay guard.

The stuffing complaint (2026-10-16, webui): the recall "stuffs the budget full of everything it can
find, including stuff completely unrelated". m4_stuffing measured it (8.2% related on live turns);
floor_cosine shipped OFF the same day because gating the COSINE ARM's vote cost -7.2 points of venue
gold coverage. This tool tests the other lever: gating what the BLOCK SHOWS. _render_block currently
fills the character budget with whatever recall returned - a QUOTA. The rule under test is a CAP:
stop taking candidates once the fused (RRF) score falls below a floor; the budget stays as the hard
ceiling, but a full block of weak rows no longer beats an honest short one.

The bar it inherits from the floor_cosine lesson, spelled out before any number is measured
(pre-stated falsifier): a floor SHIPS only if rendered gold coverage does not fall - not "rises
enough to pay for itself", does not fall. Filler reduction is the prize, coverage is the gate.

Both metrics ride existing instruments, not new ones:
  coverage - lme_harness.recall_metrics over the RENDERED ids (row/session coverage + turn top1);
             the filed venue numbers score the SEARCH list, so this tool's F=0 baseline is what the
             model ACTUALLY read (budget-trimmed), and every floor compares against that baseline,
             same scorer, same lists - apples to apples;
  filler   - m4_stuffing's rare-token law (non-STOP, df <= 56): the share of rendered rows sharing
             at least one payload token with the question; understates paraphrase on purpose.

Search is deterministic (embedder + bm25, no sampling, no temperature): every number here is exact,
no 3-repeat protocol needed - the ±0.047 answer-noise floor belongs to the judge, not retrieval.
The replay store is copied before the sweep (search bumps hits - the feedback-counter law).

    python3 tools/m4_blockrule.py [--store /tmp/opencode/ground-new-replay]
                                  [--data /tmp/opencode/longmemeval/data/longmemeval_oracle.json]
                                  [--map spikes/lme/oracle-20261013-full.map]
                                  [--floors 0,0.010,0.012,0.013,0.014,0.015,0.0164]
                                  [--shapes 96:24000,8:2000] [--work /tmp/opencode/blockrule-work]
"""
from __future__ import annotations

import argparse, collections, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                       # noqa: E402
import lme_retrieval_ab as AB                                 # noqa: E402

STOP = set("a an and are as at be by can could did do does for from had has have he her his how i "
           "if in is it its me my no not of on or our she so than that the their them then there "
           "these they this to us we what when where which who whom whose why will with would you "
           "your".split())          # serve/memory.py's STOP, same copy law as m4_stuffing


def toks(t: str) -> set:
    return set("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(t).casefold()).split())


def payload(t: str, df) -> set:
    return {w for w in toks(t) if w not in STOP and df.get(w, 10 ** 9) <= 56}


def render(hits: list[dict], floor: float, budget: int) -> list[dict]:
    """_render_block's cap semantics, spelled out: score-descending candidates, stop at the first
    below the floor (the list is monotone, so `break` IS the cap-not-quota rule), then the character
    budget as the hard ceiling. floor 0.0 skips the gate entirely - the shipped path."""
    used, shown = 120, []
    for h in hits:
        if floor > 0.0 and h["score"] < floor:
            break
        used += len(h["text"]) + 24                       # the "[n] (scope) ... " line cost, close enough
        if used > budget:
            break
        shown.append(h)
    return shown


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default="/tmp/opencode/ground-new-replay")
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--map", dest="map_path", default=str(ROOT / "spikes/lme/oracle-20261013-full.map"))
    ap.add_argument("--model", default=str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--floors", default="0,0.010,0.011,0.012,0.0125,0.013,0.0135,0.014,0.0145,0.015,0.0164")
    ap.add_argument("--shapes", default="96:24000,8:2000", help="k:budget pairs - the head block and the per-turn block")
    ap.add_argument("--work", default="/tmp/opencode/blockrule-work")
    ap.add_argument("--out", default="", help="optional jsonl: per-question per-floor detail")
    a = ap.parse_args()

    # ADR-002: the probe opens the eval store DIRECTLY. The SQLite-era copy dance (copy_dir,
    # reveal, husk rules, the --work embedded cache) existed to isolate writes to a single file;
    # a venue store is disposable by law - it rebuilds in three minutes - and the probe's only
    # side effect is hits bumps on a store that exists only to be probed. --work stays accepted
    # so old runbook lines run unchanged; it simply does nothing now.
    from serve.memory import MemoryService
    from serve.embedder import Embedder
    svc = MemoryService(a.store, embedder=Embedder(a.model, a.threads))
    svc.search("warm", limit=1)

    maps = H.load_map(a.map_path)
    data = json.load(open(a.data))
    with svc.lock:
        scopes = svc.conn.execute(
            "select distinct scope from memories where invalid_at is null").fetchall()
        svc.conn.commit()
    have = {r[0][4:] for r in scopes}
    data = [q for q in data if q["question_id"] in maps and q["question_id"] in have]
    print(f"[rule] {len(data)} questions scored against the replay store (scopes present AND mapped); "
          f"floors {a.floors}; shapes {a.shapes}", flush=True)

    df: collections.Counter = collections.Counter()
    for (t,) in svc.conn.execute("select text from memories where invalid_at is null"):
        df.update(set(toks(t)))

    out = open(a.out, "w") if a.out else None
    floors = [float(x) for x in a.floors.split(",")]
    for shape in a.shapes.split(","):
        k, budget = (int(x) for x in shape.split(":"))
        print(f"\n=== shape k={k} budget={budget} chars ===")
        print(f"{'floor':>7} {'rows/q':>7} {'related%':>9} {'rowcov':>7} {'sesscov':>8} {'top1':>6} "
              f"{'empty':>6} {'chars/q':>8}")
        # search ONCE per question per shape; every floor slices the same list (exact, no drift)
        per_q = []
        for q in data:
            where = maps[q["question_id"]]
            hits = svc.search(q["question"], scope=f"lme_{q['question_id']}", limit=k)
            per_q.append((q, where, hits))
        for f in floors:
            rc = sc = t1 = 0.0; n = rel = chars = empty = 0
            for q, where, hits in per_q:
                shown = render(hits, f, budget)
                ids = [h["id"] for h in shown]
                m = H.recall_metrics(ids, where, set(q.get("answer_session_ids") or []),
                                     q["question_id"].endswith("_abs"), k)
                if m.get("row_coverage") is not None:
                    rc += m["row_coverage"]; sc += m["session_coverage"]; n += 1
                t1 += 1.0 if m.get("turn_top1") else 0.0
                ut = payload(q["question"], df)
                rel += sum(1 for h in shown if ut & payload(h["text"], df))
                chars += sum(len(h["text"]) + 24 for h in shown)
                if not shown:
                    empty += 1
                if out:
                    out.write(json.dumps({"shape": shape, "floor": f, "qid": q["question_id"],
                                          "shown": ids}, ensure_ascii=False) + "\n")
            tot = max(1, sum(len(render(hits, f, budget)) for _, _, hits in per_q))
            print(f"{f:>7.4f} {tot / max(1, len(per_q)):>7.1f} {100 * rel / max(1, tot):>9.1f} "
                  f"{rc / max(1, n):>7.3f} {sc / max(1, n):>8.3f} {t1 / max(1, len(per_q)):>6.3f} "
                  f"{empty:>6} {chars / max(1, len(per_q)):>8.0f}")
    if out:
        out.close()
    svc.close()
    print("\nread as: rowcov/sesscov/top1 must not fall vs the floor=0 row - that is the gate; "
          "related% rising is the prize. The largest floor that passes the gate ships; ties break "
          "toward the smaller block. Coverage here is the RENDERED block, strictly honest vs the "
          "filed search-list numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
