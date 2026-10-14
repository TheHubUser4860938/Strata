#!/usr/bin/env python3
"""m4_reach - measure what the embedder actually adds on a REAL store (PHASE 4 evidence).

The temporal diagnostic (spikes/lme/TEMPORAL_DIAG.md) says the failure is evidence COVERAGE:
rows bm25 never proposes because the question shares no rare word with them. This probe
demonstrates the mechanism on a copy of a live store, offline, no engine, no GPU: every query
runs twice against the same rows - once lexical-only (the shipped path), once hybrid - and the
rank of the intended row is printed for both. A query whose target shares no word with the
target row is the case that matters; the script labels it.

  python3 tools/m4_reach.py [--dir Strata-data/memory-eval] [--queries q1|q2|...]

The store is copied (db + WAL) so the live server is never touched and never restarted.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serve.memory import MemoryService, WORD_RE, STOP, norm_text            # noqa: E402
from serve.embedder import Embedder                                 # noqa: E402

# paraphrase probes: (query, substring that must be in the target row). Written by hand against
# the dev store's own facts; the target is identified by a text substring, never by id, so the
# probe keeps working as the store churns.
QUERIES = [
    ("which markdown file must stay out of version control", "strata-memory-core.md"),
    ("what did we settle about folding the small tidy-up job into the bigger fix", "Ladder A"),
    ("how hot must the sampling be when a benchmark decides pass or fail", "0.8"),
    ("what should be pasted into the model before a long dump, to keep the window clean", "compaction"),
    ("which numbering scheme did we pick for the shelves", "Library of Congress"),
]


def copy_store(src_dir: Path, dst: Path) -> None:
    for name in ("memory.db", "memory.db-wal", "memory.db-shm"):
        f = src_dir / name
        if f.exists():
            shutil.copy2(f, dst / name)


def rank_of(hits, needle: str) -> int:
    for i, h in enumerate(hits):
        if needle.lower() in h["text"].lower():
            return i + 1
    return 0                                    # 0 = never surfaced


def self_queries(m, n: int, seed: int = 7):
    """Degraded queries built FROM the store's own rows, so the probe needs no generation and no
    hand-written questions: for a sampled fact, drop its RARE tokens (df <= 3 - the proper nouns,
    callsigns and township names that are a lexical ranker's whole handle) and ask with what is left.
    That is exactly the regime an embedder is supposed to win - "which station is it" without the
    callsign - and it is measured, not assumed: the gold answer is the row itself, so rank is
    unambiguous. Sampling is every Mth row by id (deterministic, no cherry-picking)."""
    rows = []
    with m.lock:
        rows = m.conn.execute(
            "select id, text from memories where invalid_at is null order by id").fetchall()
    tab = m._dedupe_tables()
    df = tab["df"]
    out = []
    step = max(1, len(rows) // max(1, n))
    for rid, text in rows[seed % step::step]:
        if len(out) >= n:
            break
        toks = [t for t in norm_text(text).split() if t not in STOP]
        keep = [t for t in toks if df.get(t, 0) > 3]        # the common content, minus the handles
        if len(keep) < 3:
            continue                                        # nothing left to ask with
        out.append((rid, " ".join(keep[:14])))
    return out


def auto_ab(lex, hyb, n: int, limit: int) -> int:
    """Rank the SAME gold row in both arms, on the store's own degraded queries. Reported as hit
    rates (top-1 / top-3 / top-10 / the full window) plus the two counts that matter for a shipping
    decision: FLIPPED IN (lexical missed, hybrid found) and FLIPPED OUT (the reverse - the reranker
    buried a fact the words used to find). A mean-rank move alone can hide both."""
    probes = self_queries(hyb, n)
    if not probes:
        print("no usable self-queries (every sampled fact was all rare tokens or all stop words)")
        return 2
    hits = {"lexical": [], "hybrid": []}
    ranks = {"lexical": [], "hybrid": []}
    flipped_in, flipped_out, moved = 0, 0, 0
    ex_in, ex_out = [], []
    for rid, q in probes:
        rl = rank_of_id(lex.search(q, limit=limit), rid)
        rh = rank_of_id(hyb.search(q, limit=limit), rid)
        for arm, r in (("lexical", rl), ("hybrid", rh)):
            hits[arm].append(1 if r else 0)
            ranks[arm].append(r)
        if not rl and rh:
            flipped_in += 1
            if len(ex_in) < 2:
                ex_in.append((q, rid, rh))
        elif rl and not rh:
            flipped_out += 1
            if len(ex_out) < 2:
                ex_out.append((q, rid, rl))
        elif rl and rh and rl != rh:
            moved += 1
    print(f"{len(probes)} degraded self-queries (rare tokens removed), window {limit} notes")
    for arm in ("lexical", "hybrid"):
        found = [r for r in ranks[arm] if r]
        line = (f"  {arm:<8} top1 {sum(1 for r in ranks[arm] if r == 1) / len(ranks[arm]):.3f}"
                f"  top3 {sum(1 for r in ranks[arm] if 0 < r <= 3) / len(ranks[arm]):.3f}"
                f"  top10 {sum(1 for r in ranks[arm] if 0 < r <= 10) / len(ranks[arm]):.3f}"
                f"  in window {sum(hits[arm]) / len(hits[arm]):.3f}")
        line += f"  median rank {int(sorted(found)[len(found) // 2]) if found else '-'}"
        print(line)
    print(f"  FLIPPED IN (lexical missed, hybrid found): {flipped_in}   "
          f"FLIPPED OUT (the reverse - a real cost): {flipped_out}   moved within the window: {moved}")
    for q, rid, r in ex_in:
        print(f"    in  @{r:>3}  {q[:56]}  -> row {rid}")
    for q, rid, r in ex_out:
        print(f"    out @{r:>3}  {q[:56]}  -> row {rid} (lexical had it, hybrid lost the window)")
    net = flipped_in - flipped_out
    print(f"  net: {net:+d} of {len(probes)} probes ({net / len(probes):+.1%}) - this is mechanism "
          "evidence on the store's own facts, not a benchmark claim")
    return 0


def rank_of_id(hits, rid: int) -> int:
    for i, h in enumerate(hits):
        if h["id"] == rid:
            return i + 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--limit", type=int, default=48)
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--auto", type=int, default=0,
                    help="generate N degraded self-queries from the store itself instead of the "
                           "hand-written probes: sample facts, drop their rare tokens (the lexical "
                           "handle) and ask with the rest. No generation, no hand-picking, and the "
                           "gold answer is the row itself - so rank is unambiguous.")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    if not (src / "memory.db").exists():
        print(f"no store at {src}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="m4reach-"))
    probes = []
    for q, needle in (a.queries or QUERIES):
        probes.append((q, needle))

    lexical_dir, hybrid_dir = tmp / "lex", tmp / "hyb"
    lexical_dir.mkdir()
    hybrid_dir.mkdir()
    copy_store(src, lexical_dir)
    copy_store(src, hybrid_dir)
    lexical = MemoryService(str(lexical_dir), recall_limit=a.limit)
    hybrid = MemoryService(str(hybrid_dir), recall_limit=a.limit,
                           embedder=Embedder(a.model, threads=4))
    try:
        print(f"store copy: {lexical.stats().get('memories', '?')} active rows")
        if a.auto:
            return auto_ab(lexical, hybrid, a.auto, a.limit)
        moved = 0
        for q, needle in probes:
            # is the target even in the store? a needle that never landed is a bad probe, not a
            # retrieval failure - say so instead of printing a misleading 0 -> 0.
            with lexical.lock:
                here = lexical.conn.execute(
                    "select count(*) from memories where invalid_at is null and lower(text) like ?",
                    (f"%{needle.lower()}%",)).fetchone()[0]
            if not here:
                print(f"  target absent from this store, probe skipped: {q[:52]}")
                continue
            qwords = set(WORD_RE.findall(q.lower()))
            lex_hits = lexical.search(q, limit=a.limit)
            hyb_hits = hybrid.search(q, limit=a.limit)
            lr, hr = rank_of(lex_hits, needle), rank_of(hyb_hits, needle)
            target = next((h for h in hyb_hits if needle.lower() in h["text"].lower()), None) \
                or next((h for h in lex_hits if needle.lower() in h["text"].lower()), None)
            overlap = len(qwords & set(WORD_RE.findall((target or {}).get("text", "").lower()))) \
                if target else -1
            tag = "ZERO-overlap" if overlap == 0 else f"{overlap} shared word(s)"
            print(f"  rank {lr:>3} -> {hr:>3}  [{tag:>18}]  {q[:52]}")
            if lr != hr:
                moved += 1
        print(f"queries whose ranking changed: {moved}/{len(probes)}")
        print(f"hybrid stats: {hybrid.stats()['embedder']}")
    finally:
        lexical.close()
        hybrid.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
