#!/usr/bin/env python3
"""m4_venue_shelve - shelve a haystack store the way the stream would have (2026-10-24, venue day).

The rebuild tool delivers its 10k evidence rows as a lump. The store's own auto law
(auto_classify: a drawer opens on the SECOND mention of an entity, near a neighbour that already
carries a class, minting sub-drawers up to AUTO_SHELF_CAP) needs pre-shelved neighbours to inherit
from, and the keyword naming law (fact_class) lives in the sweep, which never touches raw evidence.
A blank store handed the lump gets neither: every row rides wild, the shelf vote has nothing to
count, and the router can never exercise on the venue - the first venue day exited 2 with the
ledger reading tries 501 / picks 0 to prove it.

So this tool replays the stream instead: it walks the rows in id order - their true birth order -
and shelves each with the laws as they stood at that moment:

  * fact_class first (the sweep's keyword map: pure function of text + scope, never capped);
  * auto_classify second (the store's inheritance law, seeing the store shelved up to THIS row).

Writes go through attach(autocommit=True) - tonight's annotated lesson, not folklore: the
MemoryService conn is NOT autocommit, the only commits during pass one were the shelf-mints, the
cap stopped the minting early, and close() silently ROLLED BACK ~2,000 pending updates behind the
last mint. Only ids 2..626 survived a first pass that had counted 2,564. The idempotent form below
skips rows that already carry a class, so re-running is free and partial runs finish cleanly.

Works against a LIVE store: the service instance is a client connection (the m6-holders pattern,
verified alive through both passes of the 2026-10-24 venue day) - no restart, no second engine.

    python3 tools/m4_venue_shelve.py --dir Strata-data/venue2 [--quiet]
"""
from __future__ import annotations

import argparse, importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from serve.memory import MemoryService, attach                  # late like the other venue tools
_spec = importlib.util.spec_from_file_location("curate_loop", ROOT / "tools" / "curate_loop.py")
_cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cl)                                    # import-light: no side effects


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="the haystack store to shelve")
    ap.add_argument("--quiet", action="store_true", help="no progress lines")
    a = ap.parse_args()

    svc = MemoryService(a.dir)                                   # read arm: the inheritance law's eyes
    con = attach(a.dir, autocommit=True)                         # write arm: every update lands now
    rows = con.execute("select id, text, scope from memories "
                       "where invalid_at is null order by id asc").fetchall()
    named = inherit = already = 0
    for n, (i, text, sc) in enumerate(rows, 1):
        if con.execute("select class from memories where id = %s", (i,)).fetchone()[0]:
            already += 1                                         # idempotent: birth order respected
            continue                                             # without disturbing what is shelved
        klass = _cl.fact_class(text, sc)                         # the naming law (pure keyword map)
        src = "named"
        if not klass:
            klass = svc.auto_classify(text)                      # the inheritance law (FTS neighbours,
            src = "inherited"                                    # rarest-subject-wins, capped at mint)
        if klass:
            con.execute("update memories set class = %s where id = %s", (klass, i))
            if src == "named":
                named += 1
            else:
                inherit += 1
        if not a.quiet and n % 2500 == 0:
            print(f"[shelve] {n}/{len(rows)} | +{named} named +{inherit} inherited", flush=True)

    rows2 = con.execute("select split_part(class, '/', 1) f, count(*) from memories "
                        "where class <> '' and class is not null and invalid_at is null "
                        "group by 1 order by 2 desc").fetchall()
    shelved = sum(n for _, n in rows2)
    drawers = con.execute("select count(*) from shelves where source = 'learned'").fetchone()[0]
    con.close()
    svc.close()
    print(f"[shelve] {a.dir}: +{named} named by the map, +{inherit} inherited, {already} already "
          f"shelved -> {shelved}/{len(rows)} shelved ({shelved / max(1, len(rows)):.1%}), "
          f"{drawers} learned drawers")
    print("[shelve] families:", dict(rows2[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
