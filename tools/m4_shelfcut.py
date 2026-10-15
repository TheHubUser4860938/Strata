#!/usr/bin/env python3
"""m4_shelfcut - how should an oversized shelf be cut in two, and does the cut actually reduce what a
conversation has to sit through?

The shelves that matter are the ones bigger than the block: TECH 863 rows, POL/ARSA 575, POL 339, EVAL 164,
AI 117 - together 74% of a 2,796-row store, each of them 1.2x to 9x wider than the 96 notes a block shows.
m4_shelfcall.py measures what a call to one of those shelves returns today; this tool asks the next question,
which is the one the user keeps pointing at - should the shelf be SPLIT, and if so, by what rule.

Two candidate cuts, both written into a copy's `class` column (a taxonomy the store cannot see is not a
taxonomy - the lesson that made the first shelf-navigation probe score 0.000):

  named    buckets named after real rare tokens inside the shelf (the way a human or the keyword map
           would name them: POL/ARSA/everify), sized by coverage, capped at --cap rows.
  cosine   buckets from a greedy leader clustering capped at --cap rows, named c00..cNN - the data-driven
           cut m4_shelf_nav.py called "taxonomy B". Names are opaque: nobody can address c03 from memory.

For each, on topic-local probes drawn from the shelf itself, at the SHIPPED 96/24k:

  reach      is the probe's own row shown when its bucket is called
  purity     are all shown rows from that bucket
  leak       for the SAME question asked UNSCOPED (what an ordinary conversation does), what share of the
             block comes from OUTSIDE the shelf - the contamination a sub-shelf is supposed to pay for,
             measured against the shelf as it is today
  router     can a cheap bm25-only pass pick the right bucket from the question alone (the address has to
             be guessable, or the cut is decoration) - the same vote law m4_shelf_nav used, which scored
             0.92-0.96 there

Falsifier stated before the run: if neither cut lowers leak below the whole-shelf baseline, sub-shelves are
cosmetic and the answer is "scope at call time, not in the taxonomy". If cosine cuts leak but named cuts do
not, the data-driven shelf wins and human naming is a bottleneck. If named wins, the keyword map is the
lever and the centroids are decoration.
"""
from __future__ import annotations

import argparse, collections, random, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import re
from serve.memory import CLASS_RE, MemoryService, STOP, norm_text   # noqa: E402
from serve.embedder import Embedder                              # noqa: E402
from m4_shelf_nav import vote                                   # noqa: E402  (the same router law)

BIG = ("TECH", "POL/ARSA", "AI")            # shelves worth splitting: each is wider than the block


def legal(tok: str) -> str:
    """A bucket name that survives class_name. Measured the hard way on 2026-10-15: a sub-shelf name with
    a hyphen, underscore or '+' (strata-mam, c++, gpu_1) is REJECTED by CLASS_RE, and until today a
    rejected name silently dropped the filter - the call returned the whole store with 100% of its rows
    from other shelves. So the alphabet of a sub-shelf name is [A-Z0-9], and a cut whose names fall
    outside it is not a cut. Returns '' for a token that has no legal residue."""
    seg = re.sub(r"[^A-Z0-9]+", "", tok.upper())
    return seg if seg and CLASS_RE.fullmatch(f"{seg}") else ""


def named_cut(rows, tab, cap):
    """Buckets named after rare tokens that actually occur in the shelf: pick the token covering the most
    still-unbucketed rows, take up to `cap` rows containing it, repeat. Deterministic, and every name is a
    word a caller could plausibly type - which is the whole difference from a centroid."""
    df = tab["df"]
    left = {r[0]: r for r in rows}
    out, names = {}, []
    guard = 0
    while left and guard < 40:
        guard += 1
        tally = collections.Counter()
        for _id, text, _c in left.values():
            for t in {x for x in norm_text(text).split() if x not in STOP and df.get(x, 0) <= 20}:
                tally[t] += 1
        if not tally:
            break
        name = ""
        for cand, _n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
            if legal(cand):
                name = legal(cand)
                break
        if not name:
            break
        take = [i for i, (_id, text, _c) in list(left.items())
                if name in re.sub(r"[^A-Z0-9]+", " ", norm_text(text).upper()).split()][:cap]
        for i in take:
            out[i] = name
            del left[i]
        names.append(name)
    for i in left:                            # whatever is left is addressed by the shelf itself
        out[i] = "_rest"
    return out, names


def cosine_cut(rows, m, cap):
    """Greedy leader clustering over the shelf's own vectors, capped at `cap` rows per bucket - the
    data-driven cut, opaque names included for contrast."""
    import numpy as np
    ids = [r[0] for r in rows]
    cents, out = [], {}
    vecs = m._scope_vecs("default")
    for rid in ids:
        v = vecs.get(rid)
        if v is None:
            out[rid] = "_novector"
            continue
        v = v / max(float(np.linalg.norm(v)), 1e-9)
        best, bs = -1, 0.0
        for i, c in enumerate(cents):
            s = float(c["c"] @ v)
            if s > bs and c["n"] + 1 <= cap:
                best, bs = i, s
        if best >= 0 and bs >= 0.45:
            c = cents[best]
            c["c"] = (c["c"] * c["n"] + v) / (c["n"] + 1)
            c["c"] = c["c"] / max(float(np.linalg.norm(c["c"])), 1e-9)
            c["n"] += 1
        else:
            cents.append({"c": v.copy(), "n": 1})
            best = len(cents) - 1
        out[rid] = f"c{best:02d}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--cap", type=int, default=90, help="bucket size cap: must sit under the 96-note block")
    ap.add_argument("--probes", type=int, default=24)
    ap.add_argument("--notes", type=int, default=96)
    ap.add_argument("--chars", type=int, default=24000)
    ap.add_argument("--shelves", default=",".join(BIG))
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    if not (src / "memory.db").exists():
        print("no plaintext store"); return 2
    work = Path(tempfile.mkdtemp(prefix="shelfcut-"))
    for f in ("memory.db", "memory.db-wal", "memory.db-shm"):
        if (src / f).exists():
            shutil.copy2(src / f, work / f)
    m = MemoryService(str(work), recall_limit=a.notes, max_block_chars=a.chars,
                      embedder=Embedder(str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"), threads=4))
    tab = m._dedupe_tables()
    rng = random.Random(7)
    print(f"store {m.stats()['memories']} rows | bucket cap {a.cap} (the block shows {a.notes} notes) | "
          f"{a.notes}/{a.chars // 1000}k\n")
    print(f"  {'shelf/cut':<26}{'buckets':>8}{'max':>6}{'navig':>8}{'reach':>8}{'purity':>8}{'leak':>8}"
          f"{'router':>8}{'routed reach':>14}{'routed leak':>13}{'notes':>7}")

    for sh in [s for s in a.shelves.split(",") if s]:
        rows = m.conn.execute("select id, text, class from memories where invalid_at is null"
                              f" and class = '{sh}' order by id").fetchall()
        if len(rows) < a.cap:
            print(f"  {sh:<26} only {len(rows)} rows - already under the cap, nothing to cut")
            continue
        cuts = [("as it is (whole shelf)", {r[0]: sh for r in rows}, sh),
                ("named cut", named_cut(rows, tab, a.cap)[0], sh),
                ("cosine cut", cosine_cut(rows, m, a.cap), sh)]
        ids = [r[0] for r in rows]
        step = max(1, len(rows) // a.probes)
        pr = []
        for rid, text, _c in rows[3::step][:a.probes]:
            toks = [t for t in norm_text(text).split() if t not in STOP]
            if len(toks) < 5:
                continue
            q = " ".join(t for t in toks if tab["df"].get(t, 0) > 3)[:120]
            if len(q.split()) < 3:
                q = " ".join(toks[:12])
            pr.append({"id": rid, "q": q})
        for label, bucket, _base in cuts:
            with m.lock:
                for rid, b in bucket.items():
                    m.conn.execute("update memories set class=? where id=?",
                                   (f"{sh}/{b}" if b != sh else sh, rid))
                m.conn.commit()
            m._vdirty, m._dcache, m._dkey = True, None, None
            m._blocks.clear()
            class_of = {r[0]: (r[1] or "") for r in m.conn.execute(
                "select id, class from memories where invalid_at is null")}
            bmap = {rid: (f"{sh}/{b}" if b != sh else sh) for rid, b in bucket.items()}
            reach = purity = leak = rout = n = 0
            rr = rl = rnotes = rn = 0
            for p in pr:
                m._blocks.clear()      # one probe, one fresh pin
                want = bmap.get(p["id"])
                _b, shown, _q = m.block_for([{"role": "user", "content": p["q"]}], "default",
                                            classes=[want], semantic=True)
                if shown:
                    reach += 1 if p["id"] in {h["id"] for h in shown} else 0
                    purity += sum(1 for h in shown if class_of.get(h["id"], "") == want) / len(shown)
                _b2, shown2, _q2 = m.block_for([{"role": "user", "content": p["q"] + " ~u"}], "default",
                                               semantic=True)
                if shown2:
                    leak += sum(1 for h in shown2
                                if not class_of.get(h["id"], "").startswith(sh)) / len(shown2)
                stage1 = [(h["id"], bmap.get(h["id"], "")) for h in m.search(p["q"], limit=12,
                                                                            semantic=False)]
                picked = [c for c in vote(stage1) if c]
                rout += 1 if want in picked else 0
                if picked:
                    _b3, shown3, _q3 = m.block_for([{"role": "user", "content": p["q"] + " ~r"}], "default",
                                                   classes=picked, semantic=True)
                    if shown3:
                        rr += 1 if p["id"] in {h["id"] for h in shown3} else 0
                        rl += sum(1 for h in shown3
                                  if not class_of.get(h["id"], "").startswith(sh)) / len(shown3)
                        rnotes += len(shown3)
                        rn += 1
                n += 1
            sizes = collections.Counter(bucket.values())
            nav = sum(1 for v in sizes.values() if v <= a.notes) / len(sizes)
            print(f"  {sh + ' / ' + label:<26}{len(sizes):>8}{max(sizes.values()):>6}{nav:>8.0%}"
                  f"{reach / max(1, n):>8.3f}{purity / max(1, n):>8.3f}{leak / max(1, n):>8.1%}"
                  f"{rout / max(1, n):>8.2f}{rr / max(1, rn):>14.3f}{rl / max(1, rn):>13.1%}"
                  f"{rnotes / max(1, rn):>7.0f}")
        print()
    m.close()
    shutil.rmtree(work, ignore_errors=True)
    print("  'navig' is the share of buckets small enough for the block to show completely. 'leak' on the")
    print("  UNSCOPED block is identical for every cut - relabeling a shelf cannot change what a call that")
    print("  ignores the labels is shown - which is the finding, not a bug: the taxonomy only pays when")
    print("  something CALLS it. The last three columns are that call.")
    print("  'navig' is the share of buckets small enough for the block to show completely; leak is the")
    print("  contamination an ordinary (unscoped) conversation eats on the same question, so a cut that")
    print("  does not lower it is a filing convention, not a product change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
