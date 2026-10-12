#!/usr/bin/env python3
"""tools/m2_class.py - backfill call numbers onto the existing store (PHASE 4.5, step two).

The same conservative keyword map the curation loop uses at store time (tools/curate_loop.py,
fact_class), run over every ACTIVE fact already in the store: confident rows get a top class,
the rest stay unclassified - and unclassified recall exactly as before (wild: it rides every
unfiltered request, and surfaces in NO filtered one). A backfill can therefore only improve
filtered recall; it can never change what an unfiltered conversation sees.

  dry-run (default)  print the proposed shelf marks grouped by class, for the hand-review pass
                     the 50-group merge audit set as law: eyeball before you shelve.
  --apply            post {"op": "classify", "id": ..., "class": ...} for each proposed row.

Usage:  python3 tools/m2_class.py [--apply] [--url http://127.0.0.1:8005]

The store list is capped at 500 per GET (the dev budget keeps the store well under that; a
full page warns rather than silently truncating). Corrections inherit their shelf at op=correct
time, so backfilling active rows is enough - superseded history keeps whatever it had.
"""
from __future__ import annotations

import argparse, json, sys, urllib.parse, urllib.request as U
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from curate_loop import fact_class                        # the map and the margin rule, shared


def http(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = U.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    with U.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--apply", action="store_true", help="post the shelves (default: dry-run)")
    a = ap.parse_args()

    rows = http(f"{a.url}/memory?limit=500")["memories"]
    if len(rows) >= 500:
        print("warning: the list hit the 500-row page cap; raise the budget or page before trusting "
              "the counts")
    proposed = defaultdict(list)
    for m in rows:
        k = fact_class(m["text"], m["scope"])
        if k and not m.get("class"):                       # only FILL empty shelves: the shelf is a
            proposed[k].append((m["id"], m["text"]))       # decision, not a guess to churn
    n = sum(len(v) for v in proposed.values())
    shelved = sum(1 for m in rows if m.get("class"))
    for k in sorted(proposed):
        print(f"\n{k} ({len(proposed[k])}):")
        for mid, text in proposed[k][:12]:
            print(f"  [{mid}] {text[:95]}")
        if len(proposed[k]) > 12:
            print(f"  ... {len(proposed[k]) - 12} more")
    print(f"\npropose {n} shelves over {len(rows)} active facts "
          f"({len(rows) - n - shelved} stay unclassified, {shelved} already shelved)")
    if not a.apply:
        print("dry-run: pass --apply to post them")
        return
    done = 0
    for k, items in proposed.items():
        for mid, _ in items:
            http(f"{a.url}/memory", {"op": "classify", "id": mid, "class": k})
            done += 1
    print(f"applied {done} shelves")


if __name__ == "__main__":
    main()
