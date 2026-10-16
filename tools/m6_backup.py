#!/usr/bin/env python3
"""m6_backup - the encrypted pg_dump backup and restore of ADR-002 R6.

At-rest secrecy moved with the rebase: the SQLite era sealed the one file on close; a
Postgres cluster is many files, and its plaintext lives while the server runs. The
ciphertext now is the BACKUP. This tool pipes the cluster's own pg_dump (the server
wheel ships the binaries: <module>/pginstall/bin) through the stdlib cipher that sealed
the old single file - HMAC-SHA256 CTR keystream, encrypt-then-MAC - with the SMEM1
format reborn as SDMP1, and writes the result 0600 beside the store under the same
0600 key-file the seal read.

    python3 tools/m6_backup.py --dir Strata-data/memory-eval
    python3 tools/m6_backup.py --restore <store>/backups/memory-....dump.enc --dir <target>

The key travels with the key-file (copy it, or pass --key); a passphrase stretch is
honored through --pass-env NAME exactly as the old seal read it. A bad magic or a bad
tag on restore is FATAL, not a shrug - the seal law learned the day it shipped that an
'empty' store started from the wrong key is how a memory system quietly forgets
yesterday, and the model would never know.

Restore REFUSES a store another process is holding: pg_stat_activity counts the
neighbors, the native shape of "never pg_restore onto a running writer". Backup needs
no such rule - Postgres readers and writers coexist by design, so a live store backs up
without stopping anything (verified against the venue cluster mid-replay, 2026-10-16).
"""
from __future__ import annotations

import argparse, datetime, hashlib, hmac, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg                                          # noqa: E402
from serve.memory import (MemoryService, attach, memory_dir, store_dbname)  # noqa: E402

MAGIC = b"SDMP1"    # Strata DaMP 1: magic + 16-byte nonce + keystream body + 32-byte HMAC


def _keys(master: bytes, passphrase: str, nonce: bytes) -> tuple[bytes, bytes]:
    if passphrase:                          # scrypt per-file salt: one leaked ciphertext does
        master = bytes(a ^ b for a, b in zip(   # not make the passphrase cheap to attack
            master, hashlib.scrypt(passphrase.encode("utf-8"), salt=nonce, n=16384, r=8, p=1,
                                   dklen=32, maxmem=64 * 1024 * 1024)))
    return (hmac.new(master, b"strata-memory-enc", hashlib.sha256).digest(),
            hmac.new(master, b"strata-memory-mac", hashlib.sha256).digest())


def _xstream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """CTR-mode XOR: keystream block i = HMAC-SHA256(key, nonce || i_be64). The big-int XOR
    keeps the byte loop out of Python (a whole-dump pass is tens of ms, not seconds)."""
    blocks = -(-len(data) // 32)
    ks = b"".join(hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
                  for i in range(blocks))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big")


def seal(raw: bytes, master: bytes, passphrase: str = "") -> bytes:
    nonce = os.urandom(16)
    enc, mac = _keys(master, passphrase, nonce)
    body = MAGIC + nonce + _xstream(enc, nonce, raw)
    return body + hmac.new(mac, body, hashlib.sha256).digest()


def open_dump(blob: bytes, master: bytes, passphrase: str = "") -> bytes:
    if len(blob) < 53 or not blob.startswith(MAGIC):
        raise ValueError("not a strata memory dump (bad magic or truncated)")
    nonce = blob[5:21]
    enc, mac = _keys(master, passphrase, nonce)
    body, tag = blob[:-32], blob[-32:]
    if not hmac.compare_digest(hmac.new(mac, body, hashlib.sha256).digest(), tag):
        raise ValueError("dump HMAC failed - wrong key, wrong passphrase, or a changed byte")
    return _xstream(enc, nonce, body[21:])


def _pgbin(name: str) -> str:
    """The pg_dump/pg_restore shipped inside the server wheel (same pginstall/bin convention
    on both packagings - checked against both wheels, 2026-10-16)."""
    try:
        import embedded_postgres as ep
    except ImportError:
        import pixeltable_pgserver as ep              # type: ignore  # the Windows wheel (ADR-002)
    return os.path.join(os.path.dirname(ep.__file__), "pginstall", "bin",
                        name + (".exe" if os.name == "nt" else ""))


def _uri(store: str) -> str:
    """The store's URI inside its cluster, attaching to whatever runs it (the attach law, R0b:
    a backup of a live store starts nothing and stops nothing)."""
    try:
        import embedded_postgres as ep
    except ImportError:
        import pixeltable_pgserver as ep              # type: ignore
    srv = ep.get_server(os.path.join(memory_dir(store), "pgdata"))
    return srv.get_uri(store_dbname(store))


def _key_bytes(store: str, key_path: str) -> bytes:
    path = key_path or os.path.join(memory_dir(store), "memory.key")   # the store's own key-file,
    with open(path, "rb") as f:                                         # the 0600 file the seal
        key = f.read()                                                  # created and read
    if len(key) != 32:
        raise ValueError(f"key-file {path} is {len(key)} bytes, expected 32")
    return key


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="the store directory")
    ap.add_argument("--restore", default="", metavar="DUMP.enc",
                    help="decrypt and pg_restore this dump into --dir instead of backing up")
    ap.add_argument("--out", default="", help="backup directory (default <dir>/backups)")
    ap.add_argument("--key", default="", help="key-file (default <dir>/memory.key)")
    ap.add_argument("--pass-env", default="",
                    help="env var holding the passphrase stretch, as the old seal read it")
    ap.add_argument("--force", action="store_true",
                    help="restore even if other sessions hold the database")
    a = ap.parse_args()
    pw = os.environ.get(a.pass_env, "") if a.pass_env else ""

    if a.restore:
        with open(a.restore, "rb") as f:
            raw = open_dump(f.read(), _key_bytes(a.dir, a.key), pw)   # FATAL on a bad tag
        # Make sure cluster + database + schema exist (a fresh target gets born the way every
        # store is born - MemoryService); close first so our own connection is not the
        # "other session" the holders check below refuses on.
        MemoryService(a.dir).close()
        con = attach(a.dir, autocommit=True)
        holders = con.execute(
            "select count(*) from pg_stat_activity where datname = current_database()"
            " and pid <> pg_backend_pid()").fetchone()[0]
        con.close()
        if holders and not a.force:
            print(f"[m6] {holders} other session(s) hold {store_dbname(a.dir)} - a restore "
                  f"replaces tables under a running writer; stop the server or pass --force")
            return 3
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tf:
            tf.write(raw)
            tmp = tf.name
        try:
            r = subprocess.run([_pgbin("pg_restore"), "--clean", "--if-exists", "--no-owner",
                                "--no-privileges", "-d", _uri(a.dir), tmp],
                               capture_output=True, text=True)
            if r.returncode != 0:         # --clean on a FRESH store reports objects that were
                errs = [ln for ln in (r.stderr or "").splitlines()
                        if "does not exist" not in ln and ln.strip()]
                if errs:                  # never there to drop - noise, not failure; anything
                    print("\n".join(errs[:6]), file=sys.stderr)   # else is real
                    return 4
        finally:
            os.unlink(tmp)
        con = attach(a.dir, autocommit=True)
        try:
            n = con.execute("select count(*) from memories").fetchone()[0]
            v = con.execute("select value from meta where key='version'").fetchone()
        finally:
            con.close()
        print(f"[m6] restored into {memory_dir(a.dir)}: {n} memory rows, "
              f"version {v[0] if v else '?'} - the next server start reads it")
        return 0

    t0 = time.time()
    r = subprocess.run([_pgbin("pg_dump"), "--no-owner", "--no-privileges", "-Fc", _uri(a.dir)],
                       capture_output=True)
    if r.returncode != 0:
        msg = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        hint = (" - this store has no Postgres database yet (import it: "
                "python3 tools/m5_import.py --dir " + a.dir + ")"
                if "does not exist" in " ".join(msg) else "")
        print(f"[m6] pg_dump failed{hint}:", *(["  " + m] for m in msg[:4]), sep="\n")
        return 2
    blob = seal(r.stdout, _key_bytes(a.dir, a.key), pw)
    dst_dir = a.out or os.path.join(memory_dir(a.dir), "backups")
    os.makedirs(dst_dir, mode=0o700, exist_ok=True)
    dst = os.path.join(dst_dir, "memory-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                       + ".dump.enc")
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)
    print(f"[m6] {dst}\n     dump {len(r.stdout)} B -> sealed {len(blob)} B in "
          f"{time.time() - t0:.1f}s; sha256 "
          f"{hashlib.sha256(blob).hexdigest()[:16]}...; key {a.key or '<dir>/memory.key'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
