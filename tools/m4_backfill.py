#!/usr/bin/env python3
"""m4_backfill - give the auto-shelver's rules the history they were held back from.

The auto-shelver (serve/memory.py auto_classify, 2026-10-16) shelves NEW facts at write time.
This tool walks the store's WILD rows - class='', riding every unfiltered request, joining no
filtered one - in created_at order, the sequence the live store would have seen them arrive in,
and asks the store's own classifier what each would have been shelved as. Same rules, same
caution: sub-shelves under an established parent only, rarest qualifying subject wins, second
mention opens the drawer, collisions refuse. History gets no more aggressive a rule than new
facts get.

Dry run by default (the m2_merge law): print every proposed drawer with its count and exemplars
for the human eyeball the 50-group merge audit demands, BEFORE any row moves. --apply lands each
accepted proposal through the API (op=classify), so the running server's caches and version
credits stay right - the m4_shelves --apply law, not a direct db poke.

--exclude takes comma-separated drawer patterns (fnmatch: "POL/*,MEM/PDFS") and leaves those
rows WILD - the human veto the audit demands, applied to whole drawer families. The 2026-10-16
backfill used it on 22 drawers: the entire POL/* tail (the ARSA fiction corpus put common English
tokens - judge, date, march, october - onto POL, and 36 unrelated facts inherited a politics
parent by homonym), plus same-subject duplicates (CATRI/CATRA/CARTR, PRIMO/PRIMN) and Cognee
facts stranded in MEM/PDFS. Excluded rows stay wild: status quo, safe, re-walkable any time.
After an apply, learned drawers with zero member rows are PRUNED (dry-run minting is cheap, but
the cap should count only drawers that hold facts).

Two honest notes on the walk:
  * the dry run is CONSERVATIVE: it does not shelve rows, so later rows see fewer shelved
    neighbours than the apply run will - the apply cascades (a row shelved early becomes a
    qualifying family for rows after it, exactly like two mentions arriving in order) and may
    place slightly MORE than the dry run showed. Under-promising is the right direction.
  * auto_classify mints its drawers in the shelves table as it decides, so a dry run leaves
    empty learned drawers behind; the apply run prunes the ones that end up holding nothing.

    python3 tools/m4_backfill.py [--dir Strata-data/memory-eval] [--url http://127.0.0.1:8005]
                                 [--apply] [--show 2] [--limit N]
                                 [--exclude "POL/*,MEM/PDFS"]
"""
from __future__ import annotations

import argparse, collections, fnmatch, json, sys, urllib.request as U
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService                     # noqa: E402


def classify_via_api(url: str, rid: int, klass: str) -> bool:
    req = U.Request(f"{url}/memory", data=json.dumps({"op": "classify", "id": rid, "class": klass})
                    .encode(), headers={"Content-Type": "application/json"})
    try:
        with U.urlopen(req, timeout=30) as r:
            return bool(json.loads(r.read() or b"{}").get("ok"))
    except Exception:                                     # noqa: BLE001 - one row never kills the walk
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(data_dir(ROOT) / "memory-eval"))
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--apply", action="store_true", help="land the proposals (default: dry run)")
    ap.add_argument("--show", type=int, default=2, help="exemplars printed per proposed drawer")
    ap.add_argument("--limit", type=int, default=0, help="first N wild rows only (smoke)")
    ap.add_argument("--exclude", default="", help="comma-separated drawer patterns whose rows "
                                                  "stay wild (fnmatch, e.g. 'POL/*,MEM/PDFS')")
    a = ap.parse_args()
    veto = [p.strip() for p in a.exclude.split(",") if p.strip()]

    m = MemoryService(a.dir)                              # auto_shelve stays off: this walk IS the choice
    rows = m.conn.execute("select id, text from memories where invalid_at is null and class='' "
                          "order by created_at asc").fetchall()
    if a.limit:
        rows = rows[:a.limit]
    print(f"{len(rows)} wild rows, oldest first; classifier = the store's own auto_classify",
          flush=True)

    # ONE interleaved walk (created_at order): decide, and in --apply mode land immediately, so
    # a row shelved early is a visible shelved NEIGHBOUR for every row after it - the cascade the
    # classifier is built on, happening live exactly as it would have at write time. Dry mode just
    # collects; it under-cascades (documented above) and therefore under-promises.
    proposals: list[tuple[int, str, str]] = []
    vetoed: list[tuple[int, str, str]] = []
    homes: set[str] = set()
    applied = 0
    for rid, text in rows:
        k = m.auto_classify(text)
        if not k:
            continue
        if any(fnmatch.fnmatch(k, pat) for pat in veto):
            vetoed.append((rid, k, text))
            continue                                        # vetoed rows stay wild: no cascade from them
        if a.apply:
            if classify_via_api(a.url, rid, k):
                applied += 1
                homes.add(k)
            else:
                proposals.append((rid, k, text))            # failed call: report for retry
        else:
            proposals.append((rid, k, text))
    wild_after = len(rows) - len(proposals) - applied

    by_drawer: dict[str, list] = collections.defaultdict(list)
    for rid, k, text in proposals + (vetoed if a.apply else []):
        by_drawer[k].append((rid, text))
    n_homes = len(homes) if a.apply else len(by_drawer)
    print(f"\n{applied + len(proposals)} rows placed onto {n_homes} drawers; "
          f"{len(vetoed)} vetoed by --exclude (stay wild); "
          f"{wild_after} stay wild (rules could not place them - correct, not a failure)\n")
    for drawer in sorted(by_drawer, key=lambda d: -len(by_drawer[d])):
        members = by_drawer[drawer]
        tag = " (VETOED)" if any(fnmatch.fnmatch(drawer, pat) for pat in veto) else ""
        print(f"  {drawer:<18} {len(members):>3} rows{tag}")
        for rid, text in members[:a.show]:
            print(f"      [{rid}] {text[:96]}")
    if not a.apply:
        print("\ndry run - nothing moved except the proposed drawers in the shelves table "
              "(empty, idempotent, invisible to recall). Re-run with --apply to land the rows "
              "through the API; the apply walk cascades and may place a few MORE than shown.")
    else:
        # prune: learned drawers that ended up holding no facts (dry-run minting, vetoed families,
        # failed calls) - the cap should count drawers that are actually home to something.
        pruned = m.conn.execute(
            "delete from shelves where source='learned' and name not in "
            "(select distinct class from memories where class != '' and invalid_at is null)"
        ).rowcount
        m.conn.commit()
        print(f"\napplied {applied}/{applied + len(proposals)} through {a.url} op=classify "
              f"({len(proposals)} failed - re-run the tool to retry, classify is idempotent); "
              f"pruned {pruned} empty learned drawers")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
