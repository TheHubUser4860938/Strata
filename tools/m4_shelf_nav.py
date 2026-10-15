#!/usr/bin/env python3
"""m4_shelf_nav - can the LCC shelves navigate a growing store, and does DENSIFYING them make navigation pay?

The facts that motivated this (2026-10-14): 18% of rows are WILD (unclassifiable by the keyword map), and
95% of the store sits on shelves LARGER than the 96-note block (TECH alone is 863 rows = 33% of the
store). A shelf is only navigable if it is smaller than the block, so filtering by shelf today narrows
almost nothing. That makes "should there be more, finer shelves?" a measurable question rather than a
taste argument - which matters because the alternative answer is "the router is the missing piece, not
the taxonomy". Both get measured here, separately, so neither can hide behind the other.

Two taxonomies, three navigation arms each, all scored identically: "is the gold fact inside the top-96
the injected block would draw from?" - the shipped note budget, on the dev store where 96 notes fit
under the char cap, so list and block agree.

  taxonomy A  the store's own `class` values (17 shelves, some huge)
  taxonomy B  the same shelves split by cosine clusters capped at --max-shelf rows, WRITTEN INTO the
              copy's class column (a real migration on a copy, not a memory-side map - the earlier
              version of this probe filtered on labels the store had never heard of and scored 0.000)

  arm 1 current   no shelf filter (bm25 + hybrid rerank) - what ships
  arm 2 oracle    filter to the GOLD row's shelf: the ceiling on perfect navigation
  arm 3 routed    two-stage: cheap bm25-only pass -> rank-weighted vote over the shelves its top hits
                  sit on -> re-search filtered to the top 3. What a router would actually buy.

Each arm is divided by its OWN eligible probes (wild rows have no shelf to be the oracle for), and n is
printed per arm - mixing denominators is how the first version of this tool reported an oracle that
looked worse than no filter at all. Offline: copies of the store, sizes varied by real deletions, no
engine, no port.
"""
from __future__ import annotations

import argparse, json, random, shutil, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serve.memory import MemoryService, STOP, norm_text          # noqa: E402
from serve.embedder import Embedder                              # noqa: E402

K = 96                       # the shipped note budget: the size a shelf has to fit under to navigate
W = [1.0, 0.85, 0.7, 0.6, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25] + [0.2] * 3   # router rank weights


def probes_for(m, n):
    """Labeled probes from the store's own rows: sample every Mth active fact, ask with its COMMON
    content words (rare handles removed - the regime shelves and rerankers exist for), gold = the row
    itself. Deterministic, nothing hand-picked, rank unambiguous."""
    rows = m.conn.execute("select id, text, coalesce(nullif(class,''),'') from memories"
                          " where invalid_at is null order by id").fetchall()
    df = m._dedupe_tables()["df"]
    step, out = max(1, len(rows) // n), []
    for rid, text, klass in rows[3::step]:
        toks = [t for t in norm_text(text).split() if t not in STOP]
        if len(toks) < 5:
            continue
        q = " ".join(t for t in toks if df.get(t, 0) > 3)[:120]
        if len(q.split()) < 3:
            q = " ".join(toks[:12])
        out.append({"id": rid, "q": q, "class": klass})
        if len(out) >= n:
            break
    return out


def dense_taxonomy(m, max_shelf):
    """Split each shelf into cosine clusters capped at `max_shelf` rows: a greedy leader pass
    (nearest centroid above the bar, else open a new cluster), deterministic and dependency-free. The
    centroid is re-normalized after every merge - this project has already been bitten once by an
    unnormalized running centroid (01883e8d), so it is pinned here rather than trusted."""
    import numpy as np
    rows = m.conn.execute("select m.id, coalesce(nullif(m.class,''),'WILD'), e.vec from memories m"
                          " join embeddings e on e.id = m.id where m.invalid_at is null"
                          " order by m.id").fetchall()
    shelf, cents = {}, {}
    for rid, klass, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        v = v / max(float(np.linalg.norm(v)), 1e-9)
        lst = cents.setdefault(klass, [])
        best, bs = -1, 0.0
        for i, c in enumerate(lst):
            s = float(c["c"] @ v)
            if s > bs and c["n"] + 1 <= max_shelf:
                best, bs = i, s
        if best >= 0 and bs >= 0.45:
            c = lst[best]
            c["c"] = (c["c"] * c["n"] + v) / (c["n"] + 1)
            c["c"] = c["c"] / max(float(np.linalg.norm(c["c"])), 1e-9)
            c["n"] += 1
        else:
            lst.append({"c": v.copy(), "n": 1})
            best = len(lst) - 1
        shelf[int(rid)] = f"{klass}/c{best:02d}"
    return shelf


def vote(hits):
    """The router: rank-weighted vote over the shelves the cheap pass landed on, top 3 kept. A hit on
    a WILD row votes for nothing - inventing a "no shelf" shelf would let unclassified rows out-vote
    classified ones."""
    tally = {}
    for pos, (_rid, klass) in enumerate(hits[:len(W)]):
        if klass:
            tally[klass] = tally.get(klass, 0) + W[pos]
    return [s for s, _ in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]


def score(m, probes, shelf_of, arm):
    """One arm over one taxonomy. Returns (hits, n_eligible) - wild rows are simply not eligible for a
    shelf-filtered arm, and that is reported, not averaged away."""
    got = n = 0
    for p in probes:
        gold_shelf = shelf_of.get(p["id"]) or None
        if arm in ("oracle", "routed", "current_same") and not gold_shelf:
            continue
        if arm == "current" or arm == "current_same":
            ids = [h["id"] for h in m.search(p["q"], limit=K, semantic=True)]
        elif arm == "oracle":
            ids = [h["id"] for h in m.search(p["q"], limit=K, classes=[gold_shelf], semantic=True)]
        else:
            stage1 = m.search(p["q"], limit=12, semantic=False)      # the cheap pass: bm25 only
            want = [c for c in vote([(h["id"], shelf_of.get(h["id"]) or "") for h in stage1]) if c]
            if not want:
                continue
            ids = [h["id"] for h in m.search(p["q"], limit=K, classes=want, semantic=True)]
        got += 1 if p["id"] in ids else 0
        n += 1
    return got, n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--probes", type=int, default=200)
    ap.add_argument("--sizes", default="600,1300,2655")
    ap.add_argument("--max-shelf", type=int, default=90, help="dense cap: must sit BELOW the block, or the navigable-shelf claim is vacuous")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    if not (src / "memory.db").exists():
        print("no plaintext store - a sealed store must be revealed first (tools/m4_embed.py does)")
        return 2
    work = Path(tempfile.mkdtemp(prefix="shelfnav-"))
    model = str(Path(a.model) if Path(a.model).is_absolute() else Path.cwd() / a.model)

    def copy(dst):
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("memory.db", "memory.db-wal", "memory.db-shm"):
            if (src / f).exists():
                shutil.copy2(src / f, dst / f)
        return dst

    wc = copy(work / "full")
    m0 = MemoryService(str(wc), recall_limit=K, embedder=Embedder(model, threads=4))
    probes = probes_for(m0, a.probes)
    dense = dense_taxonomy(m0, a.max_shelf)
    orig = {p["id"]: p["class"] for p in probes}
    all_ids = [r[0] for r in m0.conn.execute(
        "select id from memories where invalid_at is null order by id")]
    hist = {}
    for cls, n in m0.conn.execute("select coalesce(nullif(class,''),'(wild)') c, count(*) from memories"
                                  " where invalid_at is null group by c"):
        hist[cls] = n
    m0.close()
    asz = {k: v for k, v in hist.items() if k != "(wild)"}
    dsz = {}
    for s in dense.values():
        dsz[s] = dsz.get(s, 0) + 1
    print(f"{len(probes)} labeled probes | store {len(all_ids)} rows, {hist.get('(wild)', 0)} wild "
          f"({hist.get('(wild)', 0) / max(1, len(all_ids)):.0%})")
    print(f"  taxonomy A: {len(asz)} shelves, largest {max(asz.values())} rows, "
          f"{sum(1 for n in asz.values() if n > K)} of them bigger than the {K}-note block, and those "
          f"{sum(n for n in asz.values() if n > K) / max(1, len(all_ids)):.0%} of the store")
    print(f"  taxonomy B: {len(dsz)} shelves from {len(set(s.split('/c')[0] for s in dense.values()))}"
          f" base classes; largest {max(dsz.values())} rows; "
          f"{sum(1 for n in dsz.values() if n > K)} still bigger than the block")

    print(f"\n  right fact inside the top-{K} the block draws from (rows = probes eligible for that arm)")
    print(f"  {'taxonomy':<12}{'rows':>7}{'current':>16}{'same set':>16}{'oracle':>16}{'routed':>16}")
    for size in [int(s) for s in a.sizes.split(",")]:
        for tax, shelf_of in (("A current", orig), ("B dense", dense)):
            w = copy(work / f"{size}-{tax[0]}")
            m = MemoryService(str(w), recall_limit=K, max_block_chars=24000,
                              embedder=Embedder(model, threads=4))
            keep = set(random.Random(11).sample(all_ids, min(size, len(all_ids))))
            with m.lock:
                gone = [i for i in all_ids if i not in keep]
                for i in range(0, len(gone), 900):
                    qm = ",".join(str(x) for x in gone[i:i + 900])
                    m.conn.execute(f"delete from memories where id in ({qm})")
                    m.conn.execute(f"delete from embeddings where id in ({qm})")
                if tax == "B dense":       # the migration itself: the copy's class column becomes dense
                    for rid, sh in dense.items():
                        m.conn.execute("update memories set class=? where id=?", (sh, rid))
                m.conn.commit()
            m._vdirty, m._dcache, m._dkey = True, None, None
            live = [p for p in probes if p["id"] in keep]
            cells = []
            for arm in ("current", "current_same", "oracle", "routed"):
                g, n = score(m, live, shelf_of, arm)
                cells.append(f"{g / n:>10.3f}" + (f"({n})" if n < len(live) else "     ").rjust(6))
            print(f"  {tax:<12}{m.stats()['memories']:>7}" + "".join(cells))
            m.close()
    print("\n  read as: 'same set' is the unfiltered baseline scored over EXACTLY the probes the")
    print("  filtered arms can use - without it the wild rows make the filtered arms look better than")
    print("  they are. oracle = the ceiling on PERFECT navigation; routed = what a cheap bm25 router")
    print("  actually buys. If oracle is flat on taxonomy A but climbs on B, the shelves are too coarse")
    print("  and densifying is the prerequisite - the router cannot navigate a shelf bigger than the")
    print("  block. If oracle climbs on A too, the taxonomy is fine and only the router is missing.")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
