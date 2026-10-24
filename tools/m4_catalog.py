#!/usr/bin/env python3
"""m4_catalog - the MEM/CAT index cartridges (Phase 1, 2026-10-24, the owner's option 3).

The browse verbs answer WHERE knowledge lives (the live SQL map: shelves, counts, newest dates)
and WHAT a drawer holds (its rows). What was missing is the label the map promised: the words a
question can match when the shelf name itself says nothing ("DEC/POSTG - postgres, version,
stable, policy"). This tool writes those labels as ordinary stored facts - class MEM/CAT, scope
user, text contract "SHELF - words" (the verb's merge parses the same contract) - so the catalog
is itself browsable knowledge, one index row per branch, exactly the owner's "one or multiple
index cartridges". Scope user is the law on purpose: the user scope joins EVERY browsable scope,
so one library map serves every study room - production default, another client, a venue boot.

LABEL LAW. A shelf's subject is what its rows SHARE, not what one row shouts: tokens rank by
how many rows of the shelf contain them, then total frequency, then alphabetically - three
librarians, three identical passes. The store's own STOP-list filters the glue; tokens under
three letters and bare numbers are dropped (labels are for reading); six tokens, 80 characters,
because the map must stay a map. No engine, no embedder: a librarian writes the index with a
pencil, and the 30-minute sweep can afford it every tick.

TARGET LAW. Label what shows; the rollup hides: the targets are the same top-24 view the browse
map renders, filtered to shelves of --min rows (default 5). A drawer behind the rollup gets no
label because nothing would display it - labels are bytes too, and the confetti lesson says
nobody reads what scrolls.

MAINTENANCE LAW. A shelf whose words did not move is NOT rewritten: counts tick every sweep and
supersession chains are sacred - a maintenance pass that grows them for nothing is a photocopy
shop. New shelf in the view -> one new row; label words moved -> remember the new row (guard
off: sibling labels are SUPPOSED to look alike) and invalidate the old pointing at it; a shelf
that fell out of the view keeps its label - the catalog remembers, the store never erases.

Dry run by default; --apply writes through the server API (reads by attach, writes by HTTP -
the m2 pattern: the live server's caches must see every landing). --quiet prints only when
something moved, which is what the sweep tick wants to hear.

Run: python3 tools/m4_catalog.py [--db DIR] [--url URL] [--apply] [--min 5] [--quiet]
"""
import argparse, json, sys, urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from serve.memory import STOP, WORD_RE, attach, CAT_CLASS   # the store's own word law and shelf mark
from strata_control.datafolder import data_dir


def toks(text: str) -> list[str]:
    """Label tokens: the store's WORD_RE with the store's STOP-list, minus one- and two-letter
    fragments and bare numbers - labels are for reading."""
    return [w for w in WORD_RE.findall(text.lower())
            if len(w) >= 3 and not w.isdigit() and w not in STOP]


def keywords(texts: list[str], glue=frozenset(), cap: int = 6, width: int = 80) -> str:
    """The deterministic shelf label (see LABEL LAW): rows-containing, then frequency, then
    alphabetical. GLUE tokens (the plan's cross-shelf filter, below) never label a drawer."""
    rows_with: Counter = Counter()
    freq: Counter = Counter()
    for t in texts:
        ts = toks(t)
        rows_with.update(set(ts))
        freq.update(ts)
    out, used = [], 0
    for w in sorted(rows_with, key=lambda k: (-rows_with[k], -freq[k], k)):
        if w in glue:
            continue
        if len(out) >= cap or used + len(w) + 2 > width:
            break
        out.append(w)
        used += len(w) + 2
    return ", ".join(out)


def glue_tokens(targets: list) -> frozenset:
    """GLUE LAW: a word in four of every ten visible drawers labels none of them - it is the
    store talking about itself ("user", "system", "project"), not the drawer's subject. The
    filter arms only at three or more targets: two drawers cannot tell glue from subject, and
    a young store deserves its literal words (the threshold and the arming floor are stated
    here once so the labels are reproducible, not tuned per shelf)."""
    if len(targets) < 3:
        return frozenset()
    seen: Counter = Counter()
    for _c, rows in targets:
        seen.update({w for t in rows for w in toks(t)})
    thr = max(2, -(-2 * len(targets) // 5))          # ceil(0.4 x visible drawers)
    return frozenset(w for w, n in seen.items() if n >= thr)


def plan(db: str, min_rows: int = 5, top: int = 24) -> dict:
    """Read-only diff: what the catalog should say versus what it says now. Same scopes the
    browse verb can reach (user + default), same top-24 view the map renders, same exact-shelf
    units - the three views must not disagree, or the map promises labels the catalog lacks."""
    con = attach(db, autocommit=True)
    shelves: dict[str, list] = {}
    for cls, txt in con.execute(
            "select m.class, m.text from memories m where m.invalid_at is null"
            " and coalesce(m.class, '') <> '' and m.scope = any(%s)", (["user", "default"],)):
        shelves.setdefault(cls, []).append(txt)
    existing: dict[str, tuple] = {}
    for rid, txt in con.execute("select id, text from memories where invalid_at is null"
                                " and class = %s and scope = %s", (CAT_CLASS, "user")):
        head, sep, tail = txt.partition(" - ")
        if sep and head.strip():
            existing[head.strip()] = (rid, tail.strip())
    con.close()
    # TARGET LAW: the index is not a drawer - MEM/CAT never needs a label of its own (the map
    # shows it by name), and it is filtered BEFORE the view slice so the growing catalog never
    # evicts a real drawer from the top-24 (caught on the first live apply: 24 labels landed,
    # the shelf of labels then ate a slot and one drawer fell to "kept" - the second pass saw
    # what the first had pushed out, which is exactly what second passes are for).
    ordered = [(c, r) for c, r in sorted(shelves.items(), key=lambda kv: (-len(kv[1]), kv[0]))
               if c != CAT_CLASS][:top]
    targets = [(cls, rows) for cls, rows in ordered if len(rows) >= min_rows]
    glue = glue_tokens(targets)
    new, moved, same = [], [], 0
    for cls, rows in targets:
        label = keywords(rows, glue)
        if cls not in existing:
            new.append((cls, label))
        elif existing[cls][1] != label:
            moved.append((existing[cls][0], cls, existing[cls][1], label))
        else:
            same += 1
    targeted = {c for c, _ in targets}
    return {"targets": [(c, len(r)) for c, r in targets], "new": new, "moved": moved,
            "same": same, "glue": sorted(glue),
            "kept": sorted(c for c in existing if c not in targeted)}


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(f"{url}/memory", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def apply(url: str, p: dict) -> list[str]:
    """Write the diff through the server API. guard:false on the new rows: near-dupe detection
    is a fact law, and index labels are SUPPOSED to resemble each other - two drawers of gpu
    notes share most of their words by design. A move is two explicit verbs (remember +
    invalidate by=), never op=correct, whose internal remember runs the guard this pass opts
    out of - the scar chain reads the same either way."""
    notes = []
    for cls, label in p["new"]:
        r = post(url, {"op": "remember", "text": f"{cls} - {label}", "scope": "user",
                       "class": CAT_CLASS, "source": "m4_catalog", "guard": False})
        notes.append(f"landed [{cls}] -> id {r.get('id')}")
    for old_id, cls, _old, label in p["moved"]:
        r = post(url, {"op": "remember", "text": f"{cls} - {label}", "scope": "user",
                       "class": CAT_CLASS, "source": "m4_catalog", "guard": False})
        nid = r.get("id")
        post(url, {"op": "invalidate", "id": old_id, "by": nid})
        notes.append(f"moved  [{cls}] {old_id} -> {nid}")
    return notes


def stats_memories(url: str):
    """One GET, the server's own count of active rows - its store fingerprint for the guard."""
    try:
        with urllib.request.urlopen(f"{url}/memory?limit=1", timeout=10) as r:
            return json.load(r)["stats"].get("memories")
    except Exception:                                       # noqa: BLE001 - a missing server means no write tick
        return None


def active_count(db: str) -> int:
    con = attach(db, autocommit=True)
    n = con.execute("select count(*) from memories where invalid_at is null").fetchone()[0]
    con.close()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(data_dir(ROOT) / "memory-eval"),
                    help="store directory (reads by attach; the m2 pattern)")
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--min", type=int, default=5, help="min rows on a shelf to earn a label")
    ap.add_argument("--apply", action="store_true", help="write through the API (default: dry)")
    ap.add_argument("--quiet", action="store_true", help="print only when something moved")
    a = ap.parse_args(argv)
    p = plan(a.db, min_rows=a.min)
    moved = bool(p["new"] or p["moved"])
    if not a.quiet or moved:
        print(f"[catalog] {len(p['targets'])} shelves in the view "
              f"(targets top-24, >= {a.min} rows): "
              f"{len(p['new'])} new, {len(p['moved'])} moved, {p['same']} unchanged, "
              f"{len(p['kept'])} kept from earlier views")
        if p["glue"]:
            show = ", ".join(p["glue"][:10]) + ("" if len(p["glue"]) <= 10 else ", ...")
            print(f"  glue filtered ({len(p['glue'])}): {show}")
        for cls, label in p["new"]:
            print(f"  + [{cls}] {label}")
        for _old, cls, old, label in p["moved"]:
            print(f"  ~ [{cls}] was: {old}\n           now: {label}")
    if a.apply and moved:
        # STORE IDENTITY GUARD (venue lesson, 2026-10-24): reads come from --db, writes go to
        # $URL - the pair names ONE store only while the server on that port serves THAT dir.
        # The venue boot broke the assumption silently: reads saw prod's 930 rows, writes went
        # to the venue's API - filing two prod labels into the venue and invalidating two
        # VENUE fixture rows whose ids happened to collide with the prod labels' ids. Counts
        # are the cheap fingerprint: if the server's active-row count and --db's disagree, this
        # tick is a stranger in the shop and skips. A mid-write race on the live store can
        # disagree by a row and skip one tick too - maintenance passes are allowed to wait.
        remote, local = stats_memories(a.url), active_count(a.db)
        if remote is not None and local is not None and remote != local:
            print(f"  [guard] {a.url} serves {remote} active rows but --db holds {local}"
                  " - different stores (venue boot?), write tick skipped")
            return 0
        for note in apply(a.url, p):
            print(f"  [apply] {note}")
    elif moved:
        print("  dry run - add --apply to land it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
