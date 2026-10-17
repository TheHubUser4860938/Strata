#!/usr/bin/env python3
"""m4_expose - replay the live journal against exposure-decay: does demoting shown rows purify
the block WITHOUT suppressing what the NEXT turn needs?

The lever: a row the last K journaled turns already injected pays step*min(exposures, cap) off
its fused score (serve/memory.py expose_window/expose_step/expose_cap - the SAME knobs the
server ships, exercised here on a disposable store copy; no formula is mirrored in this tool).
The filler the stuffing complaint named is the hub row riding every turn; hub rows score 0.17-
0.5 (m4_blockrule) and survive every coverage-safe floor, so the demotion is proportional to
exposure - and capped, so no fact is ever buried by its own history.

The replay walks the newest N REAL turns in order and, before each turn's search, truncates the
copy's turns table to that turn's history - so the server's own _exposure() ledger sees exactly
what the live ledger held at that moment, with no future turns leaking in. Two numbers decide
the knob, both riding existing instruments:

  PRIZE   related% - m4_stuffing's rare-token law (non-STOP, df <= 56): the share of rendered
          rows sharing a payload token with the turn's own question;
  GATE    suppress% - of the rows the step-0 baseline block showed AND that share a payload with
          the question (facts the store demonstrably had for it), how many the demotion pushed
          out of the block. The whole lesson of floor_cosine and the block floor: cost lands on
          evidence - so this is the gate, not a footnote.

Search is deterministic; the copy absorbs every side effect (hits bumps, ledger truncation).
The sealed live store travels whole (db + .enc + key) or it opens empty.

    python3 tools/m4_expose.py [--turns 40] [--window 12] [--cap 3]
                               [--steps 0,0.02,0.05,0.08,0.12,0.16,0.20]
"""
from __future__ import annotations

import argparse, collections, json, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService                          # noqa: E402
from serve.embedder import Embedder                             # noqa: E402

STOP = set("a an and are as at be by can could did do does for from had has have he her his how i "
           "if in is it its me my no not of on or our she so than that the their them then there "
           "these they this to us we what when where which who whom whose why will with would you "
           "your".split())          # same copy law as m4_stuffing


def toks(t: str) -> set:
    return set("".join(c if c.isalnum() or c.isspace() else " " for c in str(t).casefold()).split())


def fresh_copy(src: Path) -> tuple[Path, Path]:
    work = Path(tempfile.mkdtemp(prefix="m4expose-"))
    dst = work / "store"
    import _storeclone
    _storeclone.clone_store(src, dst)       # R5b: honest dump/restore; the old top-level file
    return work, dst                        # loop arrived stale-or-empty on a PG store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-dir", default=str(ROOT / "Strata-data/memory-eval"))
    ap.add_argument("--model", default=str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--turns", type=int, default=40, help="newest N journaled turns to replay")
    ap.add_argument("--window", type=int, default=12)
    ap.add_argument("--cap", type=int, default=3)
    ap.add_argument("--steps", default="0,0.02,0.05,0.08,0.12,0.16,0.20")
    ap.add_argument("--k", type=int, default=96, help="recall limit (the live head-block shape)")
    ap.add_argument("--budget", type=int, default=24000)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    src = Path(a.db_dir)

    live = MemoryService(str(src), embedder=None)                # read-only: replay script + df
    replay = live.conn.execute("select id, scope, user_text from turns order by id desc limit %s",
                               (a.turns,)).fetchall()[::-1]
    df: collections.Counter = collections.Counter()
    for (t,) in live.conn.execute("select text from memories where invalid_at is null"):
        df.update(set(toks(t)))
    live.close()
    if len(replay) < 5:
        print("too few journaled turns to replay")
        return 2
    print(f"[expose] replaying {len(replay)} turns ({replay[0][0]}..{replay[-1][0]}) over store "
          f"copies; window {a.window}, cap {a.cap}, steps {a.steps}", flush=True)

    def payload(t):
        return {w for w in toks(t) if w not in STOP and df.get(w, 10 ** 9) <= 56}

    def walk(step):
        """One ordered pass: truncate the ledger, search with the shipped knobs, render the live
        block, count. Returns per-turn (ids, related-ids, filler-Counter) for the caller."""
        work, dst = fresh_copy(src)
        m = MemoryService(str(dst), embedder=Embedder(a.model, a.threads),
                          expose_window=(a.window if step > 0 else 0), expose_step=step,
                          expose_cap=a.cap)
        per_turn, filler = {}, collections.Counter()
        for tid, scope, utext in replay:
            with m.lock:                                         # history only: no future ledger
                m.conn.execute("delete from turns where id > %s", (tid,))
                m.conn.commit()
                m._expo = None                                   # the stamp moved; rebuild honestly
            hits = m.search((utext or "")[:400], scope=scope or "default", limit=a.k)
            _, shown = m._render_block(hits, a.budget, scope or "default")
            up = payload(utext or "")
            rel = {h["id"] for h in shown if up & payload(h["text"])}
            per_turn[tid] = ({h["id"] for h in shown}, rel, len(shown))
            for h in shown:
                if h["id"] not in rel:
                    filler[h["id"]] += 1
        m.close()
        shutil.rmtree(work, ignore_errors=True)
        return per_turn, filler

    base, fill0 = walk(0.0)                                      # the gate's denominator, step 0
    print(f"\n{'step':>6} {'rows/turn':>10} {'related%':>9} {'suppress%':>10}  "
          f"{'top-hub-rides':>14}")
    rows_out = []
    for s in [float(x) for x in a.steps.split(",")]:
        per, filler = (base, fill0) if s == 0.0 else walk(s)
        tot = sum(v[2] for v in per.values())
        rel = sum(len(v[1]) for v in per.values())
        relb = sum(len(v[1]) for v in base.values())
        supp = sum(len(base[tid][1] - per[tid][0]) for tid, _ in ((t[0], 0) for t in replay))
        top = max((n for n in filler.values()), default=0)
        print(f"{s:>6.2f} {tot / len(replay):>10.1f} {100 * rel / max(1, tot):>9.1f} "
              f"{100 * supp / max(1, relb):>10.1f} {top:>14}")
        rows_out.append({"step": s, "rows_per_turn": round(tot / len(replay), 1),
                         "related_pct": round(100 * rel / max(1, tot), 1),
                         "suppress_pct": round(100 * supp / max(1, relb), 2),
                         "top_hub_rides": top})
    if a.out:
        Path(a.out).write_text("\n".join(json.dumps(r) for r in rows_out) + "\n")
    print("\nread as: related% is the prize, suppress% is the gate - floor_cosine and the block "
          "floor both died on evidence cost, so watch suppression, not purity. Ship the largest "
          "step whose suppress% stays at noise and whose related% actually moves; the top-hub-"
          "rides column is the diagnostic (the stuffing complaint's repeat filler, ride count).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
