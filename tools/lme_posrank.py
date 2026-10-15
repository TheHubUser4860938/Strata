#!/usr/bin/env python3
"""lme_posrank - does WHERE the gold row sits in the block change whether the answer is right?

This question got asked twice tonight, in two very different ways, and the two answers disagreed, which is
the reason both are on the record.

`tools/m4_position.py` (synthetic, within-subject): same rows, only the gold's position moves, front vs
middle vs tail. First 8 questions came back front 0.946 / middle 0.839 / tail 0.763 - a ~19-point primacy
effect. But its construction is the catch: the block holds exactly ONE answer-bearing row and no other line
can serve, so the reader's only route to the answer is finding that line. That measures the reader's
attention, not the product.

This tool asks the product question instead, on data already filed: the arm-C venue record (1,500 lines,
500 questions x 3 repeats) stores the ordered hit list `recall_ids` for every line, and the ingest sidecar
knows which rows carry the gold. So accuracy can be read against the gold row's real position in the real
block, at n=1,500, with no new run and no port.

Two things that table says, neither of them what the synthetic probe suggested:
  * position is NOT monotone on the product path (rank 1 scores 0.748 while rank 2 scores 0.835 and rank 10
    scores 0.833), and the effect is flat inside the easy types (single-session-assistant 0.97/0.97/1.00/
    0.93 across position buckets) - so ordering is not where the remaining accuracy lives;
  * lines with NO gold row in the list at all answer correctly 0.778 of the time, better than rank-1 lines.
    The gold-row label is stricter than "necessary evidence": the answer is often recoverable from sibling
    turns of the same session. Any claim built on "the gold was in the block" has to remember that.

The confounder is stated rather than fixed, because it cannot be fixed with this data: retrieval is
deterministic, so a question's gold position is the same in all three repeats, which means position is
perfectly correlated with "what kind of question this is". That is why the per-type table is printed next
to the overall one - and why the honest summary is "no consistent position effect within any type", not
"position does not matter to a reader" (the synthetic probe says it does, when nothing else in the block
can help).

    python3 tools/lme_posrank.py --record spikes/lme/armc2-20261014-full.jsonl \
        --map /tmp/opencode/lme_oracle_full.jsonl.map
"""
from __future__ import annotations

import argparse, collections, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lme_harness import load_map                                   # noqa: E402


def gold_position(ids, gmap) -> int:
    """1-based position of the first gold row in the hit list, 0 when the list carries none."""
    for i, rid in enumerate(ids or (), start=1):
        if gmap.get(rid, (False,))[0]:
            return i
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", default="spikes/lme/armc2-20261014-full.jsonl")
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map")
    ap.add_argument("--json", default="", help="optional path for the aggregate record")
    a = ap.parse_args()

    m = load_map(a.mpath)
    rows = [json.loads(l) for l in open(a.record)]
    for r in rows:
        r["pos"] = gold_position(r.get("recall_ids"), m.get(r["question_id"]) or {})
    present = [r for r in rows if r["pos"]]

    print(f"{len(rows)} lines | gold row in the list: {len(present)} ({len(present) / len(rows):.1%})")
    buck = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        b = buck[r["pos"]]
        b[0] += 1 if r["correct"] else 0
        b[1] += 1
    print(f"\n  accuracy by the gold row's position in the list the block is built from")
    print(f"  {'pos':>6}{'n':>6}{'acc':>8}" + " " * 4 + f"{'pos':>6}{'n':>6}{'acc':>8}")
    items = sorted(buck.items())
    half = (len(items) + 1) // 2
    for i in range(half):
        l = items[i]
        line = f"  {l[0]:>6}{l[1][1]:>6}{l[1][0] / l[1][1]:>8.3f}"
        if i + half < len(items):
            rr = items[i + half]
            line += f"   {rr[0]:>6}{rr[1][1]:>6}{rr[1][0] / rr[1][1]:>8.3f}"
        print(line)

    byt = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for r in rows:
        k = ("absent" if not r["pos"] else
             "1" if r["pos"] == 1 else "2-3" if r["pos"] <= 3 else "4-6" if r["pos"] <= 6 else "7-10")
        c = byt[r["type"]][k]
        c[0] += 1 if r["correct"] else 0
        c[1] += 1
    cols = ("1", "2-3", "4-6", "7-10", "absent")
    print(f"\n  within question type - position is fixed per question (retrieval is deterministic), so")
    print(f"  type is the confounder and gets looked at rather than assumed away:")
    print(f"  {'type':<26}" + "".join(f"{c:>14}" for c in cols))
    for t in sorted(byt):
        line = f"  {t:<26}"
        for c in cols:
            v = byt[t].get(c)
            line += f"{(f'{v[0] / v[1]:.2f}({v[1]})' if v else '-'):>14}"
        print(line)
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"record": a.record, "lines": len(rows), "gold_present": len(present),
             "by_position": {str(k): {"n": v[1], "acc": v[0] / v[1]} for k, v in buck.items()},
             "by_type_position": {t: {k: {"n": v[1], "acc": v[0] / v[1]}
                                      for k, v in d.items()} for t, d in byt.items()}}, indent=1))
        print(f"\n  full record: {a.json}")
    print(f"\n  read as: no monotone drop with position, and flat inside the easy types, so re-ordering the")
    print(f"  block is not where arm C's remaining 24% lives. The 0.778 at 'absent' is the number to")
    print(f"  remember about the benchmark itself: a missing gold ROW is not a missing answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
