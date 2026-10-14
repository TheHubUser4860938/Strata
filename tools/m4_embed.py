#!/usr/bin/env python3
"""m4_embed - backfill semantic vectors for rows written before the embedder (PHASE 4).

remember() embeds at write time, but a store that turns the embedder on mid-life has rows
without vectors: they stay recallable by bm25 (the hybrid is a UNION, cosine adds what
lexicon misses, never the reverse), and this tool closes the gap. Safe against a RUNNING
server: writes go to the same WAL the server reads, and MemoryService._vec_matrix notices
by embedded-count on the next search - no restart needed.

  python3 tools/m4_embed.py [--dir Strata-data/memory-eval]
      [--model Strata-data/embeddings/bge-small-en-v1.5] [--threads 4] [--limit N]
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serve.embedder import Embedder                                        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-eval")
    ap.add_argument("--model", default="Strata-data/embeddings/bge-small-en-v1.5")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="cap rows this pass (0 = all)")
    a = ap.parse_args()

    src = Path(a.dir)
    holder = None
    if (src / "memory.db").exists():
        conn = sqlite3.connect(str(src / "memory.db"))          # the ordinary case, unchanged
    elif (src / "memory.db.enc").exists():
        # A SEALED store has no plaintext to open - plaintext exists only while a service holds it.
        # The venue runbook tells you to backfill exactly this store before the accuracy arm, so
        # refusing to open it was a runbook/tool contradiction, not a safety property. MemoryService
        # reveals it from the key-file beside it; the vectors then go into the revealed db, and the
        # store re-seals at the next open/close - vectors included, because embeddings live inside
        # memory.db, which is the whole reason they were put there.
        from serve.memory import MemoryService
        holder = MemoryService(str(src), encrypt=True, key_file=str(src / "memory.key"))
        conn = holder.conn
        print(f"sealed store revealed for this pass ({holder.stats().get('memories', '?')} active rows);"
              " it re-seals when this tool exits")
    else:
        print(f"no store in {src}")
        return 2
    conn.execute("PRAGMA busy_timeout=10000")
    def done_close():
        # closing the holder is what re-seals the store; the plain path just closes its connection
        if holder is not None:
            holder.close()
        else:
            conn.close()

    if not conn.execute("select count(*) from sqlite_master where name='embeddings'").fetchone()[0]:
        print("store has no embeddings table - start the server once with memory.embedder set")
        done_close()
        return 2
    q = ("select id, text from memories where invalid_at is null and id not in"
         " (select id from embeddings) order by id")
    rows = conn.execute(q + (" limit ?" if a.limit else ""), [a.limit] if a.limit else ()).fetchall()
    print(f"unembedded active rows: {len(rows)}")
    if not rows:
        done_close()
        return 0
    emb = Embedder(a.model, threads=a.threads)
    t0, done = time.time(), 0
    for i in range(0, len(rows), 64):
        chunk = rows[i:i + 64]
        vecs = emb.embed_docs([r[1] for r in chunk])
        conn.executemany("insert or replace into embeddings values(?,?)",
                         [(r[0], v.tobytes()) for r, v in zip(chunk, vecs)])
        conn.commit()
        done += len(chunk)
        rate = done / max(time.time() - t0, 1e-6)
        print(f"  {done}/{len(rows)} ({rate:.0f} rows/s)")
    done_close()
    print(f"backfilled {done} rows in {time.time() - t0:.1f} s - a live server picks them up"
          " by embedded-count on its next search")
    return 0


if __name__ == "__main__":
    sys.exit(main())
