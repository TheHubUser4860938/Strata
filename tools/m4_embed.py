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
    # ADR-002: the store is a Postgres cluster inside the dir - attach() is the one-liner twin of
    # the old sqlite3.connect, and the sealed-.enc reveal branch retired with the seal law (the
    # store is never ciphertext-at-rest while a dir holds a cluster; backups are the ciphertext).
    from serve.memory import attach
    try:
        conn = attach(str(src))
    except Exception as exc:                       # noqa: BLE001 - a clear no beats a traceback
        print(f"cannot attach {src}: {type(exc).__name__}: {str(exc)[:140]} - is this a Postgres "
              f"store? import it once: python3 tools/m5_import.py --dir {src}")
        return 2

    def done_close():
        conn.close()

    q = ("select id, text from memories where invalid_at is null and id not in"
         " (select id from embeddings) order by id")
    rows = conn.execute(q + (" limit %s" if a.limit else ""),
                        [a.limit] if a.limit else ()).fetchall()
    print(f"unembedded active rows: {len(rows)}")
    if not rows:
        done_close()
        return 0
    emb = Embedder(a.model, threads=a.threads)
    t0, done = time.time(), 0
    for i in range(0, len(rows), 64):
        chunk = rows[i:i + 64]
        vecs = emb.embed_docs([r[1] for r in chunk])
        with conn.cursor() as cur:
            cur.executemany("insert into embeddings values(%s,%s)"
                            " on conflict (id) do update set vec = excluded.vec",
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
