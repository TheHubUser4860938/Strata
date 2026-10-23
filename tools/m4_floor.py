#!/usr/bin/env python3
"""m4_floor - sweep the cosine relevance floor against real traffic and name the number.

The floor (serve/memory.py floor_cosine, 2026-10-16) makes the cosine arm vouch only for rows above
a minimum raw similarity - the hub-row gate for the measured stuffing problem (m4_stuffing.py:
8.2% of injected rows payload-related, same rows riding 8 of 12 turns). This tool tunes it: replay
the newest N journal turns through block_for at each floor and report what the block would ACTUALLY
have carried - rows per turn and the payload-related share (the same strict lexical proxy, so the
numbers are comparable to the 8.2% baseline).

Two disciplines the probe obeys:
  * hits restore - search() bumps hits/last_seen_at on every shown row (the feedback counter the
    floor is partly fighting); the probe snapshots both columns and restores them, so measuring
    does not mutate the store's own ranking inputs.
  * pin clearing - block_for pins by (scope, first user message, shelf, arm) and the floor is NOT
    in the key, the same trap as the budget; _blocks is cleared before every render.

The floor value is chosen where related-share rises while rows/turn stays high enough to keep the
coverage the union exists for - and the coverage GUARD is a separate run (replayed grounding store
+ the 127 temporal questions): the floor that passes here must not drop gold-row coverage there.

    python3 tools/m4_floor.py --turns 20 --floors 0.0,0.40,0.45,0.50,0.55,0.60
"""
from __future__ import annotations

import argparse, collections, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from serve.memory import MemoryService                                        # noqa: E402
from serve.embedder import Embedder                                           # noqa: E402

STOP = set("a an and are as at be by can could did do does for from had has have he her his how i "
           "if in is it its me my no not of on or our she so than that the their them then there "
           "these they this to us we what when where which who whom whose why will with would you "
           "your".split())


def toks(t):
    return set("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(t).casefold()).split())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(data_dir(ROOT) / "memory-eval"),
                    help="the store DIRECTORY (not the .db file - memory_dir() owns that join)")
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--floors", default="0.0,0.40,0.45,0.50,0.55,0.60")
    ap.add_argument("--df-max", type=int, default=56)
    ap.add_argument("--recall-limit", type=int, default=96)
    ap.add_argument("--block-chars", type=int, default=24000)
    a = ap.parse_args()

    from psycopg.rows import dict_row
    from serve.memory import attach
    c = attach(a.db, autocommit=True, row_factory=dict_row)   # the raw reads; MemoryService owns
    turns = [dict(r) for r in c.execute(                      # its own connection (the attach law,
        "select id, user_text, scope from turns where injected is not null "
        "order by id desc limit %s", (a.turns,)).fetchall()]  # R0b, keeps the live server untouched)
    if not turns:
        print("no injected turns in the journal")
        return 2
    snap = {r["id"]: (r["hits"], r["last_seen_at"]) for r in c.execute(
        "select id, hits, last_seen_at from memories where invalid_at is null")}
    print(f"{len(turns)} real turns replayed; hits/last_seen_at snapshotted for restore "
          f"({len(snap)} rows)")

    m = MemoryService(a.db, recall_limit=a.recall_limit, max_block_chars=a.block_chars,
                      embedder=Embedder(str(data_dir(ROOT) / "embeddings/bge-small-en-v1.5"),
                                        threads=4))
    df = collections.Counter()
    for r in c.execute("select text from memories where invalid_at is null"):
        df.update(toks(r["text"]))

    def payload(t):
        return {w for w in toks(t) if w not in STOP and df.get(w, 10 ** 9) <= a.df_max}

    print(f"\n  {'floor':>6}  {'rows/turn':>10}  {'related':>8}  {'related %':>9}")
    for f in [float(x) for x in a.floors.split(",")]:
        m.floor_cosine = f
        tot = rel = rows_sum = empty = 0
        for t in turns:
            m._blocks.clear()       # the floor is NOT in the pin key - the budget trap, again
            _b, shown, _q = m.block_for([{"role": "user", "content": t["user_text"] or " "}],
                                        t["scope"] or "default", semantic=True)
            ut = payload(t["user_text"] or "")
            rows_sum += len(shown)
            for h in shown:
                tot += 1
                if ut & payload(h["text"]):
                    rel += 1
            if not shown:
                empty += 1
        print(f"  {f:>6.2f}  {rows_sum / len(turns):>10.1f}  {rel:>5}/{tot:<3}  "
              f"{100 * rel / max(1, tot):>8.1f}%" + (f"   ({empty} turns lost the block)" if empty else ""))
    m.close()

    with c.cursor() as cur:
        cur.executemany("update memories set hits=%s, last_seen_at=%s where id=%s",
                        [(h, ls, i) for i, (h, ls) in snap.items()])
    c.commit()
    print("\nhits/last_seen_at restored - the probe left the store's ranking inputs exactly as found.")
    print("pick the floor here, then prove it on the coverage guard (replayed grounding store):")
    print("gold-row coverage of the 127 temporal questions must NOT fall at that floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
