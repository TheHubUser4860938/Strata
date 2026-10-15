#!/usr/bin/env python3
"""m4_families - does the write guard's top-8 candidate cap let near-duplicate LIVE rows accumulate?

Raised by the Graphiti read (spikes/memory_systems/graphiti.md): their invalidation pool is capped at 10
candidates, and upstream issue #1956 shows what that costs - when a subject has a long dead history, the
cap fills with rows that are no longer live and the LIVE fact never retires. Strata's write guard has the
same shape (`serve/memory.py:552`: cosine >= 0.85, then `cand[:8]` tested by the merge laws), so the same
failure is possible here: the row that SHOULD be suppressed or superseded sits at rank 9 and is never
tested, so the new write lands beside it and the family grows one live row per write.

Why that is a growing-store problem specifically, and not a cosmetic one: two live rows the read path
collapses anyway (its near-dupe bar is the same Jaccard >= 0.75) occupy TWO slots of the 96-note block
while telling the model the same thing twice, and read-path suppression is window-dependent - so the
duplicated slot is also a fact that got pushed out of the block. The measured scaling curve
(0.977 -> 0.919 right-fact-in-block as the store doubles) has this as one of its candidate causes.

The audit answers two questions separately, because only the second one is actionable:
  1. HOW MANY live pairs violate the law? Same scope, cosine >= 0.85, content-word Jaccard >= 0.75 - the
     read path's own bar, so a hit is a pair the block would have collapsed into one line anyway.
  2. DOES THE CAP EXPLAIN IT? For each violating pair, count the live rows whose cosine to the NEWER row
     is higher than that pair's cosine. If that count is >= 8, the older row was outside the tested window
     at write time and the cap is the cause; below 8, something else let it through (written before the
     guard existed, an ingest with `guard=False`, or a law that legitimately kept both).

Offline: opens a COPY of a store, no engine, no port. `--guard-free-expected` marks a venue store whose
rows were ingested with the guard standing aside, where violations are by design and the numbers are a
control, not a defect.
"""
from __future__ import annotations

import argparse, collections, json, shutil, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService, STOP, norm_text            # noqa: E402

COS_BAR = 0.85          # the write guard's proposal floor (serve/memory.py:541)
JAC_BAR = 0.75          # the read path's near-dupe bar, and the guard's drifted-wording rule (:576)
CAP = 8                 # how many candidates the laws actually test (serve/memory.py:552)


def journal_queries(m, n):
    """Real queries, not synthetic ones: the journal keeps each turn as one row with both sides, so the
    user half of a real conversation is exactly what `block_for` gets asked with. Same source the
    everyday-budget measurement used, so the two numbers sit on one footing."""
    out = []
    for t in [r[0] for r in m.conn.execute("select name from sqlite_master where type='table'")
              if "turn" in r[0].lower()]:
        cols = [r[1] for r in m.conn.execute(f"pragma table_info({t})")]
        cand = [c for c in ("user_text", "text", "query", "q") if c in cols]
        if cand:
            out += [r[0] for r in m.conn.execute(
                f"select {cand[0]} from {t} where length({cand[0]}) between 24 and 400 limit ?", (n,))]
    return out[:n]


def block_cost(m, find, fam_of, queries):
    """The product-level cost, which is NOT the store-wide count: the read path already collapses some
    near-dupes, and a family only costs a block if two of its rows land in the SAME block. So render
    real blocks and count, per block, the lines that say something an earlier line already said."""
    dup_blocks = tot_dup = tot_notes = n_q = 0
    worst = (0, "")
    for q in queries:
        _b, shown, _q = m.block_for([{"role": "user", "content": q}], "default", semantic=True)
        if not shown:
            continue
        seen, d = set(), 0
        for h in shown:
            r = find(h["id"])
            if r in seen:
                d += 1
            seen.add(r)
        tot_dup += d
        tot_notes += len(shown)
        n_q += 1
        if d:
            dup_blocks += 1
        if d > worst[0]:
            worst = (d, q[:70])
    if not n_q:
        return None
    return {"queries": n_q, "mean_notes": tot_notes / n_q, "mean_dup_slots": tot_dup / n_q,
            "pct_blocks_with_dup": dup_blocks / n_q, "worst": worst}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--limit-families", type=int, default=12)
    ap.add_argument("--queries", type=int, default=300,
                    help="render this many REAL journal blocks and count duplicate slots per block "
                         "(0 skips the pass; it needs the embedder, so it costs ~1 min)")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--json", default="", help="optional path for the full record")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    if not (src / "memory.db").exists():
        print("no plaintext store - reveal a sealed one first (tools/m4_embed.py can open it)")
        return 2
    work = Path(tempfile.mkdtemp(prefix="families-"))
    for f in ("memory.db", "memory.db-wal", "memory.db-shm"):
        if (src / f).exists():
            shutil.copy2(src / f, work / f)
    m = MemoryService(str(work), recall_limit=96, embedder=None)
    t0 = time.time()

    import numpy as np
    meta = m._vec_matrix()          # a dict: ids, mat, ix, scope, class, created, updated, conf
    if not meta:
        print("store has no vectors - nothing to audit (run tools/m4_embed.py first)")
        return 2
    ids, mat, scopes, created = meta["ids"], meta["mat"], meta["scope"], meta["created"]
    rows = {r[0]: r for r in m.conn.execute(
        "select id, text, scope from memories where invalid_at is null")}
    toks = {}
    for rid in ids:
        toks[int(rid)] = {t for t in norm_text(rows[int(rid)][1]).split() if t not in STOP}
    n = len(ids)
    print(f"{n} active rows with vectors in {time.time() - t0:.1f}s (cosine matrix {n}x{n})")

    mat = np.asarray(mat, dtype=np.float32)
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
    sim = mat @ mat.T
    idl = [int(i) for i in ids]
    scp = [s for s in scopes]

    pairs, fam = [], {i: i for i in idl}

    def find(x):
        while fam[x] != x:
            fam[x] = fam[fam[x]]
            x = fam[x]
        return x

    for i in range(n):
        js = np.where(sim[i, i + 1:] >= COS_BAR)[0] + i + 1
        for j in js:
            if scp[i] != scp[j]:
                continue                    # the guard only ever tests the writing scope
            ti, tj = toks[idl[i]], toks[idl[j]]
            un = ti | tj
            if not un or len(ti & tj) < JAC_BAR * len(un):
                continue
            newer, older = (idl[i], idl[j]) if created[i] >= created[j] else (idl[j], idl[i])
            pairs.append((newer, older, float(sim[i, j])))
            ri, rj = find(idl[i]), find(idl[j])
            if ri != rj:
                fam[ri] = rj
    print(f"{len(pairs)} law-violating live pairs found in {time.time() - t0:.1f}s")

    # question 2: does the cap explain each pair? rank the newer row's live neighbours by cosine
    cap_expl, other_expl, detail = 0, 0, []
    for newer, older, s in pairs:
        i = meta["ix"][newer]          # id -> matrix row, the map the service already keeps
        better = int(((sim[i] > s) & (np.arange(n) != i)).sum())
        if better >= CAP:
            cap_expl += 1
        else:
            other_expl += 1
        detail.append({"newer": newer, "older": older, "cos": round(s, 3),
                       "higher_cosine_live_rows": better, "cap_explains": better >= CAP})
    sizes = collections.Counter()
    for i in idl:
        sizes[find(i)] += 1
    big = {k: v for k, v in sizes.items() if v > 1}
    involved = sum(v for v in big.values())
    dup_slots = sum(v - 1 for v in big.values())

    print(f"\n  AUDIT (bars: cosine >={COS_BAR}, content-word Jaccard >={JAC_BAR}, same scope)")
    print(f"    violating pairs            {len(pairs)}")
    print(f"    duplicate families         {len(big)}")
    print(f"    rows inside a family       {involved}  ({involved / n:.1%} of the store)")
    print(f"    WASTED BLOCK SLOTS         {dup_slots}  (one family of k lines shows k facts, says "
          f"one thing)")
    print(f"    explained by the top-{CAP} cap  {cap_expl} of {len(pairs)} pairs"
          f"   ({cap_expl / max(1, len(pairs)):.0%})")
    print(f"    other cause (pre-guard row, guard=False ingest, or a law that kept both)  {other_expl}")

    print(f"\n  biggest families (newest id first, all LIVE right now):")
    for root_id, k in sorted(big.items(), key=lambda kv: -kv[1])[:a.limit_families]:
        members = [i for i in idl if find(i) == root_id]
        print(f"    {k} live rows: {rows[members[0]][2]}: {rows[members[0]][1][:105]}")
        for x in members[1:4]:
            print(f"        also live: {rows[x][1][:95]}")
        if k > 4:
            print(f"        ... and {k - 4} more live rows saying variants of this")
    bc = None
    if a.queries:
        m.close()
        model = str(Path(a.model) if Path(a.model).is_absolute() else ROOT / a.model)
        from serve.embedder import Embedder            # only imported when this pass is asked for
        m = MemoryService(str(work), recall_limit=96, max_block_chars=24000,
                          embedder=Embedder(model, threads=4))
        qs = journal_queries(m, a.queries)
        bc = block_cost(m, find, fam, qs)
        if bc:
            print(f"\n  WHAT IT COSTS IN THE PRODUCT ({bc['queries']} real journal blocks, hybrid recall, "
                  f"96/24k)")
            print(f"    notes per block            {bc['mean_notes']:.1f}")
            print(f"    DUPLICATE SLOTS PER BLOCK  {bc['mean_dup_slots']:.2f} "
                  f"({bc['mean_dup_slots'] / bc['mean_notes']:.1%} of what the model is shown)")
            print(f"    blocks with >=1 duplicate  {bc['pct_blocks_with_dup']:.0%}")
            print(f"    worst block: {bc['worst'][0]} repeated lines on: {bc['worst'][1]!r}")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"dir": str(src), "rows": n, "pairs": len(pairs), "families": len(big),
             "rows_in_families": involved, "wasted_slots": dup_slots, "cap_explained": cap_expl,
             "other_cause": other_expl, "bars": [COS_BAR, JAC_BAR, CAP], "block_cost": bc,
             "detail": detail}, indent=1))
        print(f"\n  full record: {a.json}")
    m.close()
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n  read as: `wasted block slots` is the cost in the product - every one is a line the model")
    print(f"  reads twice while a different fact is cut from the block. If the cap explains most pairs,")
    print(f"  raising it (or testing candidates in law order rather than cosine order) is a cheap fix;")
    print(f"  if it explains few, the pairs predate the guard or came in with guard=False and the write")
    print(f"  path is already doing its job.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
