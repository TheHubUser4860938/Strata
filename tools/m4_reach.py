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
from serve.memory import MemoryService, WORD_RE                     # noqa: E402
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--limit", type=int, default=48)
    ap.add_argument("--queries", nargs="*", default=None)
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
