#!/usr/bin/env python3
"""m4_floorsweep - the two-sided block-floor sweep (2026-10-24, owner's directive: stop fetching
what you don't need, then maybe the score improves).

Venue day localized where recall PRECISION can bite - abstention 0.500 (47% confabulate on a
generous 0.15 floor), multi-session 0.515, temporal 0.512 - while the single-session half
(0.979/0.899) proves the floor is not hurting what already works. And the demote arm already
taught the honest expectation: cutting block pollution 28% moved the 40-question mean ZERO.
So this sweep grades every floor on BOTH sides of the same coin, never one alone:

  * GOLD SIDE: does the block still contain the rows the question actually needs (venue.map's
    verified golds)? A floor that raises precision by hiding gold is the filter arm's old sin.
  * JUNK SIDE: rows and chars per block - what the model must read and pay attention to.

Arms: four REAL floors (0.15 shipped / 0.25 / 0.35 / 0.45) run through the production path -
block_for with the reranker ON, shelves live, demote x0.5, the venue's own scopes - and two
SIMULATED adaptive cutoffs computed from the 0.15 arm's final scores (gap-cut at 0.75 of the
previous row; relative floor at max(0.25, half the top score)). Simulated is labeled as such:
the venue's small scopes never hit the 48k budget, so a subset of the shipped block is a fair
replay - but a winning adaptive arm still has to become a real serve law before the venue-day
test confirms it.

The clone is stripped of lme_ turns before sweeping: the ADR-003 tail law would otherwise hand
back the exam's own recorded answer instead of fresh recall (the smoke-two lesson, prepaid).
Abstention questions have no gold; their junk rows are the whole story - a floor that empties
their blocks is the answerability gate's shape showing up in numbers.

    python3 tools/m4_floorsweep.py [--venue Strata-data/venue2] [--out Strata-data/bench/...]
"""
from __future__ import annotations

import argparse, collections, json, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _storeclone import clone_store                             # the honest live-clone primitive
from serve.memory import MemoryService, attach
from serve.embedder import Embedder
from strata_control.datafolder import data_dir

FLOORS = (0.15, 0.25, 0.35, 0.45)


def qtype(q: dict) -> str:
    return "abstention" if q["question_id"].endswith("_abs") else q.get("question_type", "?")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    _d = data_dir(ROOT)          # the one data folder law (strata_control.datafolder) - my own
    ap.add_argument("--venue", default=str(_d / "venue2"))        # ROOT.parent guess walked into
    ap.add_argument("--data", default=str(_d / "bench" / "longmemeval_oracle.json"))
    ap.add_argument("--map", dest="map_path", default="")
    ap.add_argument("--out", default=str(_d / "bench" / "floorsweep_2026-10-24.json"))
    ap.add_argument("--model", default=str(data_dir(ROOT) / "embeddings" / "bge-small-en-v1.5"))
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    map_path = Path(a.map_path) if a.map_path else Path(a.venue) / "venue.map"

    data = json.load(open(a.data))
    golds: dict[str, set] = {}
    for line in open(map_path):
        o = json.loads(line)
        golds[o["question_id"]] = {int(k) for k, v in o["where"].items() if v[0]}

    scratch = Path("/tmp/opencode/floorsweep")
    shutil.rmtree(scratch, ignore_errors=True)
    clone_store(a.venue, scratch)
    con = attach(str(scratch), autocommit=True)
    wiped = con.execute("delete from turns where scope like 'lme_%'").rowcount
    con.close()
    print(f"[sweep] clone of {a.venue}, {wiped} exam-era lme_ turns stripped (tail law prepaid)",
          flush=True)

    per: dict[str, dict] = {}                              # arm -> stats
    score_lists: dict[str, list] = {}                      # qid -> final scores from the 0.15 arm
    for floor in FLOORS:
        arm = f"floor {floor:g}"
        st = collections.defaultdict(lambda: {"n": 0, "gold_in": 0, "rows": 0, "chars": 0})
        svc = MemoryService(str(scratch), embedder=Embedder(a.model, threads=a.threads),
                            recall_limit=96, max_block_chars=48000, route_shelves=True,
                            route_demote=0.5, block_min_score=floor)
        t0 = time.time()
        for n, q in enumerate(data, 1):
            qid = q["question_id"]
            text, rows, _ = svc.block_for([{"role": "user", "content": q["question"]}],
                                          f"lme_{qid}")
            s = st[qtype(q)]
            s["n"] += 1
            s["rows"] += len(rows)
            s["chars"] += len(text or "")
            if floor == FLOORS[0]:
                score_lists[qid] = [r.get("score") for r in rows]
            gold = golds.get(qid)
            if gold and (gold & {r["id"] for r in rows}):
                s["gold_in"] += 1
            if n % 250 == 0:
                print(f"[sweep] {arm}: {n}/{len(data)} ({time.time()-t0:.0f}s)", flush=True)
        svc.close()
        per[arm] = st
    # keep the 0.15 rows themselves for the simulated arms (ids+scores, one more cheap pass)
    svc = MemoryService(str(scratch), embedder=Embedder(a.model, threads=a.threads),
                        recall_limit=96, max_block_chars=48000, route_shelves=True,
                        route_demote=0.5, block_min_score=FLOORS[0])
    kept: dict[str, list] = {}
    for q in data:
        qid = q["question_id"]
        text, rows, _ = svc.block_for([{"role": "user", "content": q["question"]}], f"lme_{qid}")
        kept[qid] = [(r["id"], r.get("score")) for r in rows]
    svc.close()

    def simulate(name: str, keep) -> None:
        st = collections.defaultdict(lambda: {"n": 0, "gold_in": 0, "rows": 0, "chars": 0})
        for q in data:
            qid = q["question_id"]
            rows = kept.get(qid, [])
            scores = [sc for _, sc in rows if sc is not None]
            if not scores:
                st[qtype(q)]["n"] += 1
                continue                                   # empty block stays empty either way
            cut = keep(scores)
            sel = rows[:cut]
            s = st[qtype(q)]
            s["n"] += 1
            s["rows"] += len(sel)
            s["chars"] += 0                                  # chars not re-rendered (simulated)
            gold = golds.get(qid)
            if gold and (gold & {i for i, _ in sel}):
                s["gold_in"] += 1
        per[name] = st

    def gap_cut(scores: list) -> int:                       # keep while >= 0.75 of the previous
        cut = 1
        for i in range(1, len(scores)):
            if scores[i] is None or scores[i - 1] is None or scores[i] < 0.75 * scores[i - 1]:
                break
            cut += 1
        return cut

    def rel_floor(scores: list) -> int:                     # keep >= max(0.25, half the top)
        top = max(s for s in scores if s is not None)
        bar = max(0.25, 0.5 * top)
        return sum(1 for s in scores if s is not None and s >= bar)

    simulate("gap-cut 0.75 (sim)", gap_cut)
    simulate("rel-floor .25|top/2 (sim)", rel_floor)

    types = sorted({t for arm in per.values() for t in arm})
    print(f"\n{'arm':26}" + "".join(f"{t[:14]:>16}" for t in types) + f"{'ALL':>16}")
    for arm, st in per.items():
        line = f"{arm:26}"
        for t in types:
            s = st.get(t, {"n": 0})
            if not s["n"]:
                line += f"{'-':>16}"
                continue
            g = "-" if t == "abstention" else f"{s['gold_in']/s['n']:.2f}"
            cell = "g" + g + " r" + format(s["rows"] / s["n"], ".1f")
            line += f"{cell:>16}"
        n = sum(s["n"] for s in st.values())
        rws = sum(s["rows"] for s in st.values())
        gids = sum(s["gold_in"] for t, s in st.items() if t != "abstention")
        gn = sum(s["n"] for t, s in st.items() if t != "abstention")
        allcell = "g" + format(gids / max(1, gn), ".2f") + " r" + format(rws / max(1, n), ".1f")
        line += f"{allcell:>16}"
        print(line)
    print("\n(g = gold-in-block %, r = mean rows per block; abstention has no gold - its rows ARE the story)")
    Path(a.out).write_text(json.dumps({"floors": [list(map(str, FLOORS)), "gap-cut", "rel-floor"],
                                       "arms": {arm: dict(st) for arm, st in per.items()}}, indent=1))
    print(f"[sweep] json durable: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
