#!/usr/bin/env python3
"""One honest store clone for the Postgres-native era (ADR-002, R5b).

The venue harnesses grew up copying store dirs the sqlite way - whole-dir file loops or
memory.db + -wal + -shm - and every one of those routes is a LIE on a live Postgres store:
a file-copy of a RUNNING pgdata can catch torn pages, and copying only the top-level files
picks up the frozen legacy memory.db.enc (or nothing at all, since the real store lives in
the pgdata/ directory). Measured 2026-10-16: the live store dir holds memory.db.enc (legacy,
stale by the hour of the cutover), memory.key, pgdata/ and backups/ - a top-level copy
arrives as the old snapshot, never the live rows.

The honest primitive is the one m6_backup already proved end to end (1.7 s dump+restore
round trip, restore-identical): pg_dump takes a consistent snapshot WHILE the server holds
the store, and m6_backup --restore births a fresh target the way every store is born
(MemoryService bootstrap) and pg_restores into it. That is what this helper drives - the
tool's own CLI, so the holder checks, the fresh-store noise filter and the key handling
stay in one place. The key file travels with the clone (the sealed-store law).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M6 = ROOT / "tools" / "m6_backup.py"


def clone_store(src, dst) -> str:
    """Consistent clone of store dir src into store dir dst (created if absent). Works while
    src is live; returns dst. Fails loudly with the tool's own words if either leg fails."""
    src, dst = str(src), str(dst)
    Path(dst).mkdir(parents=True, exist_ok=True)
    key = Path(src) / "memory.key"
    if key.exists():
        shutil.copy2(key, Path(dst) / "memory.key")   # BEFORE the restore: the dump is sealed
                                                       # with the source key and the target's
                                                       # m6_backup opens it with the target key
    with tempfile.TemporaryDirectory(prefix="clone-") as tmp:
        r = subprocess.run([sys.executable, str(M6), "--dir", src, "--out", tmp],
                           capture_output=True, text=True)
        dumps = sorted(Path(tmp).glob("*.dump.enc"))
        if r.returncode != 0 or not dumps:
            raise RuntimeError(f"clone dump failed for {src}: rc={r.returncode} "
                               f"{(r.stdout + r.stderr).strip()[:300]}")
        r = subprocess.run([sys.executable, str(M6), "--dir", dst, "--restore", str(dumps[-1])],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"clone restore failed into {dst}: rc={r.returncode} "
                               f"{(r.stdout + r.stderr).strip()[:300]}")
    return dst
