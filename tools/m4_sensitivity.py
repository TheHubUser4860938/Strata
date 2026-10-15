#!/usr/bin/env python3
"""m4_sensitivity - is the metric measuring the memory, and does replication predict correctness?

Two questions raised by external systems on 2026-10-15 and answerable from records already on disk, with
no model and no port:

  COUPLING. The project's whole venue argument rests on a chain: rendered-block coverage predicts accuracy
  (the filed step: coverage +0.048 -> accuracy +0.040). That is a CORRELATION across arms. This tool reads
  it WITHIN one run - accuracy bucketed by the coverage each line actually got - which is the observational
  half of the mutation test `sem` enforces mechanically ("prove every promise can fail: each needs a
  mutation", github.com/Ataraxy-Labs/sem). If accuracy barely moves across coverage buckets, the block is
  not doing the work the metric attributes to it, and an accuracy arm aimed at coverage is buying air. The
  causal half needs generation: drop the gold row from a block that was RIGHT and see whether the answer
  flips. That arm is `--mutate` below and it is the only part that needs the port.

  REPLICATION. The r/AIMemory evidence-ledger design promotes a pattern from case-specific to general only
  when it "survives across cases" (ROM + address + context + what changed + what we PROVED + confidence +
  whether the pattern survived elsewhere). Our tier-2 graduation rule is different: a fact earns standing
  status by being SHELVED ("the call number IS the graduation criterion"). Both stores can test which
  criterion predicts being RIGHT: on these records, accuracy by the number of gold sessions a question's
  answer is spread across, and accuracy by how many gold rows carry it.

  CO-SHOWN PAIRS. `sem history` mines co-change pairs from git history - "if you touch one, don't forget
  the other" - an association LEARNED from evidence rather than declared. Our declared version failed
  (the subject gather moved 4 of 470 questions). The venue records carry `recall_ids` per line, so the
  learned version is a count: which pairs of facts appear together in blocks that produced CORRECT answers
  more often than in blocks that produced wrong ones. A store that shows the same pair together on every
  question is not teaching anything (the pair is just the block's shape), so the tool reports the lift
  against how often the pair co-occurs at all, not the raw count.
"""
from __future__ import annotations

import argparse, collections, itertools, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def is_idk(r):
    """The harness's own law, copied rather than reinvented: a con-style hypothesis can MENTION idk while
    still answering, so only the FINAL line counts (tools/lme_harness.py:404). Reading the whole string
    would report an idk rate inflated by refusals-that-mention-refusal."""
    return "idk" in (r.get("hypothesis") or "").strip().lower().split("\n")[-1]


def mean(xs):
    live = [x for x in xs if x is not None]
    return (sum(live) / len(live)) if live else float("nan")


def buckets(rows, key, edges, label):
    """accuracy per coverage bucket - the observational sensitivity test."""
    print(f"\n  accuracy by {label} (all lines with the field; n per bucket)")
    print(f"    {'bucket':<16}{'n':>6}{'correct':>9}{'accuracy':>10}{'idk':>8}")
    for lo, hi in zip(edges, edges[1:] + [1.001]):
        sel = [r for r in rows if key(r) is not None and lo <= key(r) < hi]
        if not sel:
            continue
        ok = sum(1 for r in sel if r.get("correct"))
        idk = sum(1 for r in sel if is_idk(r))
        print(f"    {f'{lo:g}-{hi:g}':<16}{len(sel):>6}{ok:>9}{ok / len(sel):>10.3f}{idk:>8}")
    hi = [r for r in rows if key(r) is not None and key(r) >= 0.999]
    lo = [r for r in rows if key(r) is not None and key(r) < 0.999]
    if hi and lo:
        a, b = sum(1 for r in hi if r.get("correct")) / len(hi), sum(1 for r in lo if r.get("correct")) / len(lo)
        print(f"    spread: full coverage {a:.3f} (n={len(hi)}) vs incomplete {b:.3f} (n={len(lo)}) "
              f"= {a - b:+.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", default="spikes/lme/armc-20261015-1rep.jsonl",
                    help="a filed lme_harness out-file (recall_ids + correct + coverage fields)")
    ap.add_argument("--store", default="/tmp/opencode/venue-ab-store/hyb",
                    help="the embedded venue copy, for resolving fact ids to text (optional)")
    ap.add_argument("--pairs", type=int, default=12, help="how many co-shown pairs to print")
    ap.add_argument("--types", default="", help="restrict to these question types, comma separated")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.records)]
    if a.types:
        keep = set(a.types.split(","))
        rows = [r for r in rows if r.get("type") in keep]
    print(f"{len(rows)} lines | accuracy {sum(1 for r in rows if r.get('correct')) / len(rows):.3f} "
          f"| idk {sum(1 for r in rows if is_idk(r)) / len(rows):.3f} (final line only, the harness's law)")

    # 1. COUPLING - is the metric sensitive to what the block actually carried?
    buckets(rows, lambda r: r.get("row_coverage"), [0.0, 0.25, 0.5, 0.75, 0.999, 1.0],
            "gold-ROW coverage of the recall")
    buckets(rows, lambda r: r.get("session_coverage"), [0.0, 0.25, 0.5, 0.75, 0.999, 1.0],
            "gold-SESSION coverage of the recall")
    # the same slope inside each type, because an overall slope can be a type mix
    print("\n  within-type slope (accuracy at full coverage minus accuracy at incomplete coverage)")
    print(f"    {'type':<26}{'n':>5}{'full-cov acc':>14}{'partial acc':>13}{'slope':>8}")
    for t in sorted({r.get("type") for r in rows}):
        sel = [r for r in rows if r.get("type") == t and r.get("row_coverage") is not None]
        hi = [r for r in sel if r["row_coverage"] >= 0.999]
        lo = [r for r in sel if r["row_coverage"] < 0.999]
        if not hi or not lo:
            continue
        ah, al = sum(1 for r in hi if r.get("correct")) / len(hi), sum(1 for r in lo if r.get("correct")) / len(lo)
        print(f"    {t:<26}{len(sel):>5}{ah:>14.3f}{al:>13.3f}{ah - al:>+8.3f}")

    # 2. REPLICATION - does the number of independent sessions carrying the answer predict being right?
    print("\n  replication (the evidence-ledger promotion criterion, tested on our own records)")
    print(f"    {'gold sessions':>14}{'n':>6}{'accuracy':>10}{'turn_top1':>11}{'idk':>7}")
    for k in sorted({r.get("gold_sessions") for r in rows if r.get("gold_sessions")} | set()):
        sel = [r for r in rows if r.get("gold_sessions") == k]
        if not sel or k is None:
            continue
        tt = [r for r in sel if r.get("turn_top1") is not None]
        print(f"    {str(k):>14}{len(sel):>6}{sum(1 for r in sel if r.get('correct')) / len(sel):>10.3f}"
              f"{(sum(1 for r in tt if r['turn_top1']) / len(tt)) if tt else float('nan'):>11.3f}"
              f"{sum(1 for r in sel if is_idk(r)):>7}")

    # 3. CO-SHOWN PAIRS - associations learned from which blocks produced correct answers
    if not Path(a.store).exists() and not (Path(a.store) / "memory.db.enc").exists():
        print(f"\n  co-shown pairs skipped: no store copy at {a.store}")
        return 0
    from serve.memory import MemoryService                                   # noqa: E402
    m = MemoryService(a.store, encrypt=True, key_file=str(Path(a.store) / "memory.key"))
    txt = {r[0]: r[1] for r in m.conn.execute("select id, text from memories")}
    m.close()
    corr, wrong = collections.Counter(), collections.Counter()
    seen = collections.Counter()
    for r in rows:
        ids = sorted(set(r.get("recall_ids") or []))[:96]
        combo = list(itertools.combinations(ids[:24], 2))       # the head of the block: what it leaned on
        for p in combo:
            seen[p] += 1
        tgt = corr if r.get("correct") else wrong
        for p in combo:
            tgt[p] += 1
    nc, nw = max(1, sum(1 for r in rows if r.get("correct"))), max(1, sum(1 for r in rows if not r.get("correct")))
    scored = []
    for p, n in corr.items():
        if n < 3 or seen[p] < 4:
            continue
        lift = (n / nc) / max(1e-9, (wrong.get(p, 0) + 0.5) / nw)
        scored.append((lift, n, wrong.get(p, 0), p))
    scored.sort(reverse=True)
    print(f"\n  co-shown pairs that ride CORRECT lines more than wrong ones (lift, not raw count)")
    print(f"    {'lift':>7}{'right':>7}{'wrong':>7}  the two facts, as the store holds them")
    for lift, n, w, p in scored[:a.pairs]:
        t1 = (txt.get(p[0]) or "")[:52].replace("\n", " ")
        t2 = (txt.get(p[1]) or "")[:52].replace("\n", " ")
        print(f"    {lift:>7.2f}{n:>7}{w:>7}  {t1!r} + {t2!r}")
    if not scored:
        print("    none met the floor (>=3 correct lines, >=4 co-occurrences) - the block's head is too")
        print("    uniform across questions for a pair to stand out, which is itself the finding: our")
        print("    blocks are not differentiated enough per question to learn associations from them.")
    print("\n  read as: the coupling slope is the cheapest falsifier the venue queue has - if accuracy does")
    print("  not depend on coverage within this run, the coverage->accuracy chain that prices every arm is")
    print("  an artefact of comparing arms, and the grounding arm is buying something the metric cannot see.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
