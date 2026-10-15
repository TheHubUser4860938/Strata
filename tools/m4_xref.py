#!/usr/bin/env python3
"""m4_xref - can a fact be ADDRESSABLE without being SHELVED, using cross-references instead?

Three sources said the same thing on the same day, from three directions:

  LCC      a call number is ONE address; access comes from a separate system - index terms, the
           Cf./For-see graph, and LCSH subject headings. The Library's own acknowledged failure mode is
           that a book can only be shelved in one place.
  Primnox  (github.com/Primnox/Memory-system, r/AIMemory 2026-10) "Cross-references: a fact is filed in
           every chapter it relates to, so information about an interaction between two people can be
           found from either person's chapter" - and its planned fix for filing errors is "let a fact sit
           under more than one heading when it fits".
  us       measured the same night with tools/m4_shelfcall.py: a scoped call is pure (purity 1.000) but
           the ordinary unscoped path is 41-84% contaminated, and 567 rows - 20.3% of the store - have no
           address at all, miss 4.5x more often, and fill 50.4% of the notes in a block built from wild
           questions. The router helps (leak cut 2-5x, reach unchanged) but it can only name a shelf that
           exists.

`class` is one column holding one string, so it CANNOT hold a cross-reference. That is the whole
architecture question, and it is measurable: build the many-to-many in a copy, route with it, and see
what a conversation is shown.

Arms, per probe (probes are a row's own common words, the same degraded law as m4_shelfcall so the
numbers compare), at the shipped 96/24k:

  unscoped     what an ordinary conversation gets today                  (baseline)
  class-shelf  the bm25 vote over the single stored shelf                (last night's arm)
  subject-xref the bm25 vote over the SUBJECTS of the top hits, then the block filled from every row
               that shares a winning subject - a fact reached through any chapter it relates to

Metrics: leak (share of shown notes from OUTSIDE the probe's own shelf - contamination), reach (is the
probe's own row shown at all), notes (how much the model has to read). Reported separately for shelved
probes and for WILD probes, because the wild bucket is where an address-free access path would pay:
those rows cannot be called today, and they are 20% of the store.

Falsifier, stated before the run: subject cross-references must cut leak below the class-shelf arm
WITHOUT costing reach, and must raise reach for the wild rows. If leak stays put or reach falls, a
many-to-many index is decoration over a store that already ranks fine, and the single address stands.
"""
from __future__ import annotations

import argparse, collections, json, random, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from serve.memory import MemoryService, STOP, norm_text          # noqa: E402
from serve.embedder import Embedder                              # noqa: E402
from m4_shelf_nav import vote                                    # noqa: E402  the same router law


def subjects_of(rows, tab, max_sub=6):
    """A row's subjects: its RARE content tokens (df <= --df-max), ranked by rarity, capped. This is the
    merge tool's own subject law, not a new one - the same rule that vetoes a fold when the carrier never
    mentions a rare subject, so the two paths cannot disagree about what a fact is about."""
    df = tab["df"]
    out = {}
    for rid, text, klass in rows:
        toks = [t for t in norm_text(text).split() if t not in STOP and 0 < df.get(t, 0) <= tab["dfmax"]]
        out[rid] = sorted(set(toks), key=lambda t: (df.get(t, 9), t))[:max_sub]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--probes", type=int, default=20, help="probes per shelf, plus the wild bucket")
    ap.add_argument("--min-rows", type=int, default=40)
    ap.add_argument("--df-max", type=int, default=20, help="a subject is a token this rare or rarer")
    ap.add_argument("--top-subjects", type=int, default=2, help="subjects a call may name")
    ap.add_argument("--notes", type=int, default=96)
    ap.add_argument("--chars", type=int, default=24000)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    if not (src / "memory.db").exists():
        print("no plaintext store (a sealed one must be revealed first)")
        return 2
    work = Path(tempfile.mkdtemp(prefix="xref-"))
    for f in ("memory.db", "memory.db-wal", "memory.db-shm"):
        if (src / f).exists():
            shutil.copy2(src / f, work / f)
    m = MemoryService(str(work), recall_limit=a.notes, max_block_chars=a.chars,
                      embedder=Embedder(str(ROOT / a.model if not Path(a.model).is_absolute() else a.model),
                                        threads=4))
    rows = m.conn.execute("select id, text, coalesce(nullif(class,''),'(wild)') from memories"
                          " where invalid_at is null order by id").fetchall()
    class_of = {r[0]: r[2] for r in rows}
    tab = m._dedupe_tables()
    tab["dfmax"] = a.df_max
    subs = subjects_of(rows, tab)
    # the many-to-many index, built in the copy so the measurement is honest about what it costs to hold
    with m.lock:
        m.conn.execute("drop table if exists xsub")
        m.conn.execute("create table xsub (id integer, term text)")
        m.conn.executemany("insert into xsub values (?,?)",
                           [(rid, t) for rid, ts in subs.items() for t in ts])
        m.conn.execute("create index if not exists xsub_term on xsub(term)")
        m.conn.commit()
    by_term = collections.defaultdict(list)
    for rid, ts in subs.items():
        for t in ts:
            by_term[t].append(rid)
    n_pairs = sum(len(v) for v in subs.values())
    print(f"store {m.stats()['memories']} rows | subjects: {n_pairs} pairs over {len(by_term)} terms "
          f"(mean {n_pairs / max(1, len(rows)):.1f} per row) | {a.notes}/{a.chars // 1000}k")
    print(f"  terms that name nothing else: {sum(1 for v in by_term.values() if len(v) == 1)} "
          f"(a cross-reference of one is not a cross-reference)\n")

    by_class = collections.defaultdict(list)
    for r in rows:
        by_class[r[2]].append(r)
    shelves = sorted([c for c, v in by_class.items() if len(v) >= a.min_rows and c != "(wild)"],
                     key=lambda c: -len(by_class[c]))
    rng = random.Random(11)

    def probe_list(pool):
        out = []
        for rid, text, klass in rng.sample(pool, min(a.probes, len(pool))):
            toks = [t for t in norm_text(text).split() if t not in STOP]
            if len(toks) < 5:
                continue
            q = " ".join(t for t in toks if tab["df"].get(t, 0) > 3)[:120]
            if len(q.split()) < 3:
                q = " ".join(toks[:12])
            out.append({"id": rid, "q": q, "class": klass})
        return out

    def arm(q, ids=None, classes=None, tag=""):
        """A block built the way the product builds one, but optionally restricted to an id set (the
        cross-reference arm: the JOIN happens before the ranking, and the only way to say that in a probe
        without a schema change is to filter the wide candidate list and re-render)."""
        m._blocks.clear()
        if ids is None:
            _b, shown, _q = m.block_for([{"role": "user", "content": q + tag}], "default",
                                        classes=classes, semantic=True)
            return shown
        cand = m.search(q, scope="default", limit=1200, semantic=True)
        keep = [h for h in cand if h["id"] in ids]
        block, shown = m._render_block(keep, m.max_block_chars, "default")
        return shown

    def score(probes, label):
        acc = collections.defaultdict(lambda: [0, 0, 0, 0])       # leak, reach, notes, n per arm
        for p in probes:
            own = p["class"]
            for name, shown in (("unscoped", arm(p["q"], tag=" ~u")),
                                ("class-shelf", arm(p["q"], classes=vote(
                                    [(h["id"], class_of.get(h["id"], "")) for h in
                                     m.search(p["q"] + " ~c", limit=12, semantic=False)])) or None),
                                ("subject-xref", arm(p["q"] + " ~x", ids=set(
                                    rid for t in vote_subjects(
                                        [(h["id"], subs.get(h["id"], [])) for h in
                                         m.search(p["q"] + " ~x", limit=12, semantic=False)],
                                        a.top_subjects) for rid in by_term.get(t, []))))):
                if not shown:
                    continue
                c = acc[name]
                c[0] += (sum(1 for h in shown if class_of.get(h["id"], "") != own) / len(shown)
                         if own != "(wild)" else
                         sum(1 for h in shown if class_of.get(h["id"], "") != "(wild)") / len(shown))
                c[1] += 1 if p["id"] in {h["id"] for h in shown} else 0
                c[2] += len(shown)
                c[3] += 1
        n = max(1, acc["unscoped"][3])
        line = f"  {label:<12}"
        for name in ("unscoped", "class-shelf", "subject-xref"):
            c = acc[name]
            d = max(1, c[3])
            line += f"{name[:1]}: leak {c[0] / d:>5.1%} reach {c[1] / d:>5.3f} notes {c[2] / d:>4.0f} | "
        print(line.rstrip(" |"))
        return {k: {"leak": v[0] / max(1, v[3]), "reach": v[1] / max(1, v[3]),
                    "notes": v[2] / max(1, v[3]), "n": v[3]} for k, v in acc.items()}

    print(f"  {'shelf':<12}  what each arm shows the model")
    recs = []
    for sh in shelves:
        recs.append({"shelf": sh, "wild": False,
                     "arms": score(probe_list(by_class[sh]), sh)})
    print()
    recs.append({"shelf": "(wild)", "wild": True,
                 "arms": score(probe_list(by_class.get("(wild)", [])), "WILD")})
    if a.json:
        Path(a.json).write_text(json.dumps({"dir": str(src), "rows": m.stats()["memories"],
                                            "subject_pairs": n_pairs, "terms": len(by_term),
                                            "cells": recs}, indent=1))
        print(f"\n  full record: {a.json}")
    print("\n  read as: the cross-reference arm answers to ANY chapter a fact touches, so its leak is the")
    print("  contamination a many-to-many index would remove; reach is what it might cost, because a")
    print("  subject shared with the wrong chapter drags the wrong facts in. WILD is the honest test -")
    print("  those rows have no address at all today, so any reach there is knowledge the store can")
    print("  suddenly call, and none there means the index is just another ranking over the same rows.")
    m.close()
    shutil.rmtree(work, ignore_errors=True)
    return 0


def vote_subjects(hits, top=2):
    """Rank-weighted vote over the SUBJECTS of the top hits - the same shape as m4_shelf_nav.vote, whose
    shelf version picked the right shelf on 92-96% of topic-local probes. A subject appearing in several
    hits is what 'this conversation is about X' means mechanically."""
    tally = collections.Counter()
    for pos, (_id, terms) in enumerate(hits):
        w = 1.0 / (1 + pos)
        for t in terms:
            tally[t] += w
    return [t for t, _n in tally.most_common(top)]


if __name__ == "__main__":
    sys.exit(main())
