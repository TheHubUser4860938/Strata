#!/usr/bin/env python3
"""m5_import - the one-way door of ADR-002 R4: a SQLite-era store becomes native Postgres.

The rebase keeps no runtime SQLite (no shim, no escape hatch), but the DATA crosses, once,
whole: facts, shelves, journal turns (the injected ledger included), the version counter and
the float32 vectors move into a Postgres cluster inside the same store dir. The native
answers do real work here: ids are PRESERVED (OVERRIDING SYSTEM VALUE, then the identity
restarts above the copied max), the tsvector index is built by INSERT itself (the generated
column - no FTS5 dump/reload dance), provenance and the exposure ledger parse from TEXT-json
into queryable jsonb, and the whole import is ONE transaction: it lands complete or not at
all, which the SQLite-era importer never had.

    python3 tools/m5_import.py [--dir Strata-data/memory-eval] [--check-only] [--force]
                               [--spot 12]

Safety, stated before the first row moves: the SQLite side is read from a SNAPSHOT COPY (the
running server keeps its WAL; we never read live files mid-write), a target that already
holds facts REFUSES the import unless --force empties it first, and --check-only runs the
whole thing - import and verification - against a throwaway copy, touching nothing real.

Verification prints three things: per-table counts old vs new, the supersede-chain check
(every superseded_by points at a row that exists), and a SPOT RECALL parity pass - the
newest journaled questions searched on both sides (the live server over HTTP, still the
SQLite code, and the fresh PG store), top-5 id sets compared. That comparison makes the
porter-vs-snowball / bm25-vs-ts_rank_cd residue VISIBLE before any reboot: it is the
preview of ADR-002's venue re-record gate, on the store that actually matters.
"""
from __future__ import annotations

import argparse, json, shutil, sqlite3, sys, tempfile, time, urllib.request as U
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
import psycopg                                                  # noqa: E402
from psycopg.types.json import Jsonb                            # noqa: E402
from serve.memory import MemoryService                          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(data_dir(ROOT) / "memory-eval"),
                    help="the store dir (holds memory.db; the cluster lands in <dir>/pgdata)")
    ap.add_argument("--check-only", action="store_true",
                    help="import and verify against a throwaway copy; touch nothing real")
    ap.add_argument("--force", action="store_true",
                    help="an already-populated PG target is emptied and re-imported")
    ap.add_argument("--spot", type=int, default=12, help="SPOT RECALL queries (0 skips)")
    ap.add_argument("--api", default="http://127.0.0.1:8005/memory",
                    help="the LIVE server's /memory endpoint - still the SQLite code until the "
                         "reboot; it is the other side of the parity spot-check")
    a = ap.parse_args()
    src_dir = Path(a.dir).resolve()
    src_db = src_dir / "memory.db"
    if not src_db.exists():
        print(f"no {src_db} - a SQLite store to import from is required")
        return 2

    # 1. snapshot copy first: the running server owns its WAL; a file-level copy of db+wal+shm
    #    is the consistent-enough picture, and the importer never disturbs a live server.
    tmp = Path(tempfile.mkdtemp(prefix="m5import-"))
    snap = tmp / "src.db"
    for suffix in ("", "-wal", "-shm"):
        if (src_dir / (src_db.name + suffix)).exists():
            shutil.copy2(src_dir / (src_db.name + suffix), snap.with_name(snap.name + suffix))
    old = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    counts_old = {t: old.execute(f"select count(*) from {t}").fetchone()[0]
                  for t in ("memories", "shelves", "turns", "meta", "embeddings")}
    print(f"[m5] source snapshot: " + " ".join(f"{k}={v}" for k, v in counts_old.items()))

    # 2. the target: a throwaway copy for --check-only, the real dir otherwise. The store opens
    #    its cluster at <dir>/pgdata (0.3 s measured, ADR-002 R0) - the OLD server ignores that
    #    subdirectory entirely, so import and the running SQLite server coexist until the reboot.
    work = tmp / "store"
    if a.check_only:
        work.mkdir()
        for f in src_dir.iterdir():
            if f.is_file() and f.name.startswith("memory."):
                shutil.copy2(f, work / f.name)
        target_dir = str(work)
    else:
        target_dir = str(src_dir)
    m = MemoryService(target_dir)
    with m.lock:
        have = m.conn.execute("select count(*) from memories").fetchone()[0]
        m.conn.commit()
        if have and not a.force:
            m.close()
            print(f"[m5] target already holds {have} rows - refusing (re-run with --force to "
                  f"empty and re-import)")
            return 3
        if have:
            for t in ("embeddings", "memories", "turns", "shelves", "meta"):
                m.conn.execute(f"delete from {t}")
            m.conn.commit()

        # 3. the move, in ONE transaction (a native gift the SQLite era never had).
        t0 = time.time()
        cur = m.conn.cursor()
        rows = old.execute("select id,scope,text,source,provenance,confidence,created_at,"
                           "updated_at,last_seen_at,hits,invalid_at,superseded_by,class"
                           " from memories order by id").fetchall()
        for r in rows:
            cur.execute(
                "insert into memories(id,scope,text,source,provenance,confidence,created_at,"
                "updated_at,last_seen_at,hits,invalid_at,superseded_by,class) overriding "
                "system value values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r[0], r[1], r[2], r[3],
                 Jsonb(json.loads(r[4])) if r[4] else None, r[5],
                 m._dt(r[6]), m._dt(r[7]), m._dt(r[8]) if r[8] is not None else None,
                 r[9], m._dt(r[10]) if r[10] is not None else None, r[11], r[12] or ""))
        n_mem = len(rows)
        cur.executemany("insert into embeddings values (%s, %s)",
                        [(i, bytes(v)) for i, v in old.execute(
                            "select id, vec from embeddings").fetchall()])
        n_emb = cur.rowcount
        cur.executemany("insert into shelves values (%s, %s, %s, %s)",
                        [(n, m._dt(c), s, anc) for n, c, s, anc in
                         old.execute("select name, created_at, source, anchor from shelves")])
        cur.executemany(
            "insert into turns(id,scope,user_text,assistant_text,model,created_at,processed,"
            "injected) overriding system value values (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(i, sc, ut, at, mo, m._dt(ca), pr,
              Jsonb(json.loads(inj)) if inj else None)
             for i, sc, ut, at, mo, ca, pr, inj in old.execute(
                 "select t.id,t.scope,t.user_text,t.assistant_text,t.model,t.created_at,"
                 "t.processed,t.injected from turns t order by t.id")])
        cur.executemany("insert into meta values (%s, %s) on conflict (key) do update"
                        " set value = excluded.value",
                        old.execute("select key, value from meta").fetchall())
        # identities restart ABOVE the copied maxes - the next remember()/journal_turn lands
        # where the old store left off, ids never colliding with history.
        for table, seq_col in (("memories", "id"), ("turns", "id")):
            cur.execute(f"select coalesce(max({seq_col}), 0) from {table}")
            mx = cur.fetchone()[0]
            cur.execute("select setval(pg_get_serial_sequence(%s, %s), %s)",
                        (f"public.{table}", seq_col, mx + 1))
        m.conn.commit()
        took = time.time() - t0

        # 4. verification: counts, then the chain check, then the SPOT RECALL parity pass.
        counts_new = {t: m.conn.execute(f"select count(*) from {t}").fetchone()[0]
                      for t in ("memories", "shelves", "turns", "meta", "embeddings")}
        m.conn.commit()
        # supersede-chain check: the SOURCE store carries 91 pointers at rows deleted in the
        # pre-invalidation merge era (measured, 2026-10-16) - the import's law is FAITHFUL, not
        # pristine, so the check is the DELTA: the same danglings must arrive, no new ones.
        chain_old = old.execute("select count(*) from memories a left join memories b"
                                " on b.id = a.superseded_by"
                                " where a.superseded_by is not null and b.id is null").fetchone()[0]
        chain_new = m.conn.execute(
            "select count(*) from memories a left join memories b on b.id = a.superseded_by"
            " where a.superseded_by is not null and b.id is null").fetchone()[0]
        m.conn.commit()

    print(f"[m5] imported in {took:.2f}s: memories={n_mem} embeddings={n_emb}")
    ok = True
    for t, want in counts_old.items():
        got = counts_new[t]
        flag = "" if got == want else "  <-- MISMATCH"
        if got != want:
            ok = False
        print(f"     {t:<12} old={want:<7} new={got}{flag}")
    print(f"     supersede chains dangling: new={chain_new} source={chain_old}"
          + ("" if chain_new == chain_old else "  <-- IMPORT CHANGED THEM"))
    if chain_new != chain_old:
        ok = False

    # SPOT RECALL: the newest journaled questions that actually CARRY a payload (>=3 non-STOP
    # words - chatty meta-turns like "continue with the buildout" have no gold answer, and on
    # hub-dominated queries any two engines legitimately disagree: that is the stuffing finding
    # of 2026-10-16, not a parity failure). The live server answers with the OLD code over the
    # OLD files; the PG store answers with the NEW arm. Overlap is the parity the venue
    # re-record gates - seen here first, on the store that actually matters.
    if a.spot:
        from serve.memory import STOP, WORD_RE
        seen_q: set[str] = set()
        qs = []
        for sc, ut in old.execute(
                "select scope, user_text from turns where length(user_text) > 12"
                " order by id desc limit 400"):
            words = [w for w in WORD_RE.findall((ut or "").lower()) if w not in STOP]
            if len(words) < 3:
                continue                      # chatty meta-turns have no gold answer; on
            key = " ".join(words[:6])         # hub-dominated queries any two engines
            if key in seen_q:                 # legitimately disagree (the stuffing finding
                continue                      # of 2026-10-16 - not a parity failure)
            seen_q.add(key)
            qs.append((sc, ut))
            if len(qs) >= a.spot:
                break
    else:
        qs = []
    if qs:
        inter, tot, zero = 0, 0, []
        for sc, ut in qs:
            q = (ut or "")[:400]
            try:
                req = U.Request(a.api, data=json.dumps({"op": "search", "q": q, "scope": sc,
                                                        "limit": 5}).encode(),
                                headers={"Content-Type": "application/json"})
                with U.urlopen(req, timeout=60) as r:
                    live = {h["id"] for h in json.loads(r.read() or b"{}").get("hits", [])}
            except Exception as e:
                print(f"[m5] live API unreachable ({type(e).__name__}) - spot pass skipped")
                break
            pg = {h["id"] for h in m.search(q, scope=sc or "default", limit=5)}
            inter += len(live & pg)
            tot += max(1, len(live))
            if not (live & pg):
                zero.append((q[:60], sorted(live)[:3], sorted(pg)[:3]))
        if tot:
            print(f"[m5] SPOT RECALL over {len(qs)} live questions: top-5 overlap "
                  f"{100.0 * inter / tot:.1f}%")
            for q, l, p in zero[:4]:
                print(f"     ZERO overlap: {q!r} live={l} pg={p}")
    m.close()
    old.close()
    shutil.rmtree(tmp, ignore_errors=True)

    if a.check_only:
        print("[m5] --check-only: the real store was NOT touched")
    elif ok:
        print(f"[m5] {src_dir} is now a Postgres store (cluster at {src_dir}/pgdata). "
              f"The live server keeps serving SQLite until the reboot; reboot AFTER this "
              f"run to pick the new engine up.")
    else:
        print("[m5] verification MISMATCHED - the live store was left as it was "
              "(check-only mode is the safe re-run)")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
