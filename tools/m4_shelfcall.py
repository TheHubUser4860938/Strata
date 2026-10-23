#!/usr/bin/env python3
"""m4_shelfcall - when a conversation ASKS for a shelf, is that ALL it gets, and does a bigger budget
change the answer?

The design claim behind the LCC shelves is navigational: a shelf is an address, and an address is only
worth having if calling it returns what lives there and nothing else - at any block size. Today's other
measurements settled a different question (does narrowing help RECALL: no, not at 2.6k rows - an oracle
shelf scored exactly equal to the unfiltered baseline). This one is the precision question the taxonomy
has to survive on, and it is the one the user keeps pointing at: "only the wanted memories are being
accessed when called for, no matter how big the budget."

What gets tested, per shelf that holds >= --min-rows rows, on the ACCUMULATED live store (a copy, offline,
deterministic - no engine, no port):

  purity      every note the scoped block shows must sit in the called subtree (class LIKE 'S%').
              A miss here is a bug, not a trade-off.
  siblings    how much of a broad call (POL) is actually the sub-shelf (POL/ARSA). The prefix match is
              deliberate - a shelf means its subtree - but nobody has quantified what "POL" really
              returns, and it decides how the taxonomy should be cut.
  reach       is the probe's own row shown at all? A shelf that is 9x bigger than the block cannot show
              everything in it, and the number that says so is this one.
  leak        the UNSCOPED block at the same budget: what share of what the model is shown comes from
              OTHER shelves. This is the contamination a bigger budget can make worse, and it is the
              failure mode that matters day to day, because ordinary conversations call nothing.
  wild        probes drawn from unclassified rows: they cannot be addressed at all, so they measure what
              the 20% of the store that has no shelf costs in the unscoped path.

Budgets are swept as pairs (note cap : char cap) because the block is bounded by whichever binds first -
96/24k is the shipped dev setting, and the wider settings are where "no matter how big" gets tested.
The pin cache is cleared between budgets: the budget is NOT in the block pin key, and forgetting that is
how a venue claim about budget size was wrong for a day (docs/MEMORY_CORE.md, "Probe hygiene, second
instance").
"""
from __future__ import annotations

import argparse, collections, json, random, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService, STOP, norm_text          # noqa: E402
from serve.embedder import Embedder                              # noqa: E402
from m4_shelf_nav import vote                                        # noqa: E402  the same router law


def probes(rows, tab, n, rng):
    """Query built from a row's COMMON content words (rare handles removed) - the degraded law the
    shelf and reranker probes use, so numbers across instruments compare."""
    df = tab["df"]
    out = []
    for rid, text, klass in rng.sample(rows, min(n, len(rows))):
        toks = [t for t in norm_text(text).split() if t not in STOP]
        if len(toks) < 5:
            continue
        q = " ".join(t for t in toks if df.get(t, 0) > 3)[:120]
        if len(q.split()) < 3:
            q = " ".join(toks[:12])
        out.append({"id": rid, "q": q, "class": klass})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(data_dir(ROOT) / "memory-eval"))
    ap.add_argument("--model", default=str(data_dir(ROOT) / "embeddings/bge-small-en-v1.5"))
    ap.add_argument("--probes", type=int, default=20, help="probes per shelf")
    ap.add_argument("--min-rows", type=int, default=40, help="shelves smaller than this cannot fill a block")
    ap.add_argument("--budgets", default="96:24000,192:48000,384:96000")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    work = Path(tempfile.mkdtemp(prefix="shelfcall-"))
    import _storeclone
    _storeclone.clone_store(src, work)    # R5b: honest dump/restore, works while the store is
                                          # live; the old plaintext check aborted on PG, and the
                                          # db+wal+shm loop copied files that no longer exist
    m = MemoryService(str(work), recall_limit=384, max_block_chars=96000,
                      embedder=Embedder(str(ROOT / a.model if not Path(a.model).is_absolute() else a.model),
                                        threads=4))
    st = m.stats()
    print(f"store: {st['memories']} active rows, {(st.get('embedder') or {}).get('vectors')} vectors, "
          f"{st['turns']} journaled turns")

    rows = m.conn.execute("select id, text, coalesce(nullif(class,''),'(wild)') from memories"
                          " where invalid_at is null order by id").fetchall()
    # block_for returns FULL hit records on a pin miss and ID-ONLY dicts on a pin hit (serve/memory.py:
    # 1149). Reading h["class"] therefore measures the cache, not the store: a probe whose normalized
    # first user message matches an earlier one gets id-only dicts, and every one of those looks like a
    # foreign shelf. EVAL - a shelf of near-duplicate protocol statements whose probes collide constantly
    # - scored purity 0.600 on the first run for exactly that reason, while the shelves with distinctive
    # wording scored 1.000. Resolve ids against the db and both numbers become what they claim to be.
    class_of = {r[0]: r[2] for r in rows}

    def cls_of(h):
        return class_of.get(h["id"], "(gone)")
    by_class = collections.defaultdict(list)
    for r in rows:
        by_class[r[2]].append(r)
    shelves = sorted([c for c, v in by_class.items() if len(v) >= a.min_rows and c != "(wild)"],
                     key=lambda c: -len(by_class[c]))
    budgets = [tuple(int(x) for x in spec.split(":")) for spec in a.budgets.split(",")]
    tab = m._dedupe_tables()
    rng = random.Random(11)
    print(f"shelves tested: {', '.join(f'{c}({len(by_class[c])})' for c in shelves)}  "
          f"| wild: {len(by_class.get('(wild)', []))}\n")

    recs = []
    hdr = (f"  {'shelf':<12}{'budget':>10}{'notes':>7}{'purity':>8}{'sibling':>9}{'reach':>8}"
           f"{'leak':>8}{'routed leak':>13}{'routed reach':>14}{'pick':>7}")
    print(hdr)
    for sh in shelves:
        pr = probes(by_class[sh], tab, a.probes, rng)
        for notes, chars in budgets:
            m.recall_limit, m.max_block_chars = notes, chars
            m._blocks.clear()                       # the budget is not in the pin key - see docstring
            pure = sib = reach = 0
            leak = ureach = un = 0
            routed = rreach = rn = 0
            rleak = rnotes = 0
            notes_shown = 0
            for p in pr:
                m._blocks.clear()                   # one probe, one fresh pin: measure the call, not the cache
                _b, shown, _q = m.block_for([{"role": "user", "content": p["q"]}], "default",
                                            classes=[sh], semantic=True)
                cls = [cls_of(h) for h in shown]
                if cls:
                    pure += sum(1 for c in cls if c.startswith(sh)) / len(cls)
                    sib += sum(1 for c in cls if c != sh and c.startswith(sh)) / len(cls)
                reach += 1 if p["id"] in {h["id"] for h in shown} else 0
                _b2, shown2, _q2 = m.block_for([{"role": "user", "content": p["q"] + " ~unscoped"}],
                                               "default", semantic=True)
                if shown2:
                    other = sum(1 for h in shown2 if not cls_of(h).startswith(sh))
                    leak += other / len(shown2)
                    ureach += 1 if p["id"] in {h["id"] for h in shown2} else 0
                    un += 1
                    notes_shown = len(shown) or len(shown2)
                # The router arm, and the reason it gets a second chance: m4_shelf_nav.py killed it for
                # RECALL (an oracle shelf scored exactly equal to the unfiltered baseline, so narrowing
                # cannot add coverage) - but these leak numbers give it a different job. A scoped call is
                # pure BY CONSTRUCTION (purity 1.000 above), so if a cheap bm25 pass can name the right
                # shelf, contamination goes to zero for free. What that trades is reach: name the wrong
                # shelf and the fact is unreachable. This arm measures the trade, at the shipped budget.
                stage1 = [(h["id"], cls_of(h)) for h in m.search(p["q"], limit=12, semantic=False)]
                pick = [c for c in vote(stage1) if c and c != "(wild)"]
                if pick:
                    routed += 1
                    _b3, shown3, _q3 = m.block_for([{"role": "user", "content": p["q"] + " ~r"}],
                                                   "default", classes=pick, semantic=True)
                    if shown3:
                        rreach += 1 if p["id"] in {h["id"] for h in shown3} else 0
                        rleak += sum(1 for h in shown3
                                     if not cls_of(h).startswith(sh)) / len(shown3)
                        rnotes += len(shown3)
                        rn += 1
            n = len(pr) or 1
            recs.append({"shelf": sh, "rows": len(by_class[sh]), "notes": notes, "chars": chars,
                         "purity": pure / n, "sibling": sib / n, "reach": reach / n,
                         "leak": leak / max(1, un), "unscoped_reach": ureach / max(1, un),
                         "routed_leak": rleak / max(1, rn), "routed_reach": rreach / max(1, rn),
                         "router_pickable": routed / n, "n": n})
            print(f"  {sh:<12}{f'{notes}/{chars // 1000}k':>10}{notes_shown:>7.0f}"
                  f"{pure / n:>8.3f}{sib / n:>9.1%}{reach / n:>8.3f}{leak / max(1, un):>8.1%}"
                  f"{rleak / max(1, rn):>13.1%}{rreach / max(1, rn):>14.3f}{routed / n:>7.0%}")
        print()

    # the wild bucket: unaddressable rows, measured in the unscoped path only (there is no shelf to call)
    pr = probes(by_class.get("(wild)", []), tab, a.probes, rng)
    for notes, chars in budgets:
        m.recall_limit, m.max_block_chars = notes, chars
        m._blocks.clear()
        reach = shown_wild = shown_all = 0
        for p in pr:
            m._blocks.clear()
            _b, shown, _q = m.block_for([{"role": "user", "content": p["q"]}], "default", semantic=True)
            reach += 1 if p["id"] in {h["id"] for h in shown} else 0
            shown_wild += sum(1 for h in shown if cls_of(h) == "(wild)")
            shown_all += len(shown)
        if pr:
            print(f"  WILD {notes}/{chars // 1000}k: reach {reach / len(pr):.3f}   "
                  f"wild rows in a shown block: {shown_wild / max(1, shown_all):.1%} of the notes")
    if a.json:
        Path(a.json).write_text(json.dumps({"dir": str(src), "rows": st["memories"], "cells": recs},
                                           indent=1))
        print(f"\n  full record: {a.json}")
    print("\n  read as: purity 1.000 is the contract - a scoped call that shows another shelf's row is a\n"
          "  bug. reach is the honest cost of a shelf bigger than the block: TECH at 863 rows cannot fit\n"
          "  in 96 notes, so calling a big shelf is a FILTER, not a view. leak is what an ordinary\n"
          "  conversation actually gets, and whether it grows with the budget is the question the last\n"
          "  column answers - if reach stays flat while leak rises, a bigger block buys\n"
          "  contamination as well as coverage, and the sub-shelf cut or the router is what has to pay.")
    m.close()
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
