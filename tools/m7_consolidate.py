#!/usr/bin/env python3
"""Collapse near-duplicate facts into one survivor each (the retroactive supersession pass).

The store accumulates paraphrases of the same claim - the measurement law ended up with 46
active copies, the RTX 4090 with four. This tool clusters active rows by their own stored
vectors (cosine >= --threshold, greedy, newest id seeds each cluster), picks the survivor per
cluster (the fullest text, ties to the newest), and - with --go - retires every other member
through the LIVE server's /memory op:invalidate with by=survivor, so each drop lands as a
version-chain edge (invalid_at + superseded_by) and the server's own invalidate() dirties its
in-memory vector matrix (the raw-SQL route would leave the matrix offering superseded facts).

Dry run by default: prints what would move and nothing changes. Take a backup first
(tools/m6_backup.py); the invalidate is recall-invisible but history-visible, and the dump
makes even that undoable. Law (ADR-002 evening amendment, fact 3820): corrections are NOT
this tool's business - a cluster whose members disagree in fact, not just in words, belongs
to the curation sweep, which amends the survivor to carry the history before invalidating.
0.85 is the measured-safe threshold here (2026-10-16: 210 clusters / 421 rows, every sampled
cluster a true paraphrase); loosening it folds related-but-different facts.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serve import memory as M  # noqa: E402


def cluster(rows, threshold):
    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    V = np.array([np.frombuffer(r[2], dtype=np.float32) for r in rows])
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    S = V @ V.T
    claimed = np.zeros(len(rows), bool)
    out = []
    for i in sorted(range(len(ids)), key=lambda i: -ids[i]):
        if claimed[i]:
            continue
        members = [j for j in np.argsort(-S[i]) if S[i][j] >= threshold and not claimed[j] and j != i]
        claimed[i] = True
        for j in members:
            claimed[j] = True
        out.append((i, members))
    dupes = [(ids[i], texts[i], [ids[j] for j in mem]) for i, mem in out if mem]
    return dupes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="the store directory")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--url", default="http://127.0.0.1:8005/memory",
                    help="the live server's /memory endpoint (invalidations route through it)")
    ap.add_argument("--go", action="store_true", help="apply; default is a dry run")
    args = ap.parse_args()

    conn = M.attach(args.dir)
    rows = conn.execute("""SELECT m.id, m.text, e.vec FROM memories m JOIN embeddings e ON e.id = m.id
                           WHERE m.invalid_at IS NULL""").fetchall()
    conn.close()
    dupes = cluster(rows, args.threshold)
    purge = sum(len(d[2]) for d in dupes)
    print(f"{len(rows)} active rows with vectors; threshold {args.threshold}: "
          f"{len(dupes)} clusters, {purge} rows to retire, {len(dupes)} survivors kept")
    for sid, text, drops in sorted(dupes, key=lambda d: -len(d[2]))[:6]:
        print(f"\nKEEP {sid}: {text[:85]}")
        for j in drops[:3]:
            print(f"  drop {j}")
    if not args.go:
        print("\ndry run - nothing changed; pass --go to apply through the live server")
        return 0

    done = failed = 0
    for sid, _, drops in dupes:
        for j in drops:
            body = json.dumps({"op": "invalidate", "id": j, "by": sid}).encode()
            req = urllib.request.Request(args.url, data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    ok = json.loads(r.read()).get("invalidated")
                done += bool(ok)
                failed += 0 if ok else 1
            except Exception as e:  # noqa: BLE001 - one bad row must not stop the pass
                failed += 1
                print(f"  ! {j} -> {sid}: {e}")
    print(f"\napplied: {done} retired with superseded_by chains, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
