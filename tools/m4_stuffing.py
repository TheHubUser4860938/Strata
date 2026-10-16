#!/usr/bin/env python3
"""m4_stuffing - how much of the injected block actually shares a payload with the question?

The everyday-use complaint (2026-10-16, ported from the webui): the recall "stuffs the budget full
of everything it can find, including stuff completely unrelated". This measures it against the
journal's own record - turns.injected lists the row ids each real turn actually received, so the
probe never re-renders and never trusts the pin-cache; it reads what the server really sent.

Relevance proxy, deliberately lexical and deliberately strict: a row is RELATED if it shares at
least one rare payload token (non-STOP, df <= 56 - the same content-address law m2_merge uses)
with the turn's user text. This UNDERSTATES semantic relevance by design - a paraphrase with zero
shared tokens is exactly what the embedder exists to find - so the true related-share is above the
reported one; the point is the floor, not the exact number. A turn that gets 104 rows and 5 related
ones is stuffing regardless of how generous the proxy.

The first run (2026-10-16, dev store, 12 newest injected turns from the post-reboot traffic):
**99 of 1,213 injected rows related = 8.2%**, 96-104 rows per turn, and the same zero-overlap rows
("...updating a dev build to version 1.43") riding every single turn - the semantic arm's hub
rows: short declarative facts near the corpus centroid, proposed for any question at all.

Reads the db directly (the journal is store data; no engine, no boot, no mute header needed).

    python3 tools/m4_stuffing.py --turns 12
"""
from __future__ import annotations

import argparse, collections, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOP = set("a an and are as at be by can could did do does for from had has have he her his how i "
           "if in is it its me my no not of on or our she so than that the their them then there "
           "these they this to us we what when where which who whom whose why will with would you "
           "your".split())          # serve/memory.py's STOP, copied so the tool stays standalone


def toks(t: str) -> set:
    return set("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(t).casefold()).split())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(ROOT / "Strata-data/memory-eval/memory.db"))
    ap.add_argument("--turns", type=int, default=12, help="newest N injected turns to analyze")
    ap.add_argument("--df-max", type=int, default=56, help="rare-token bar (m2_merge law)")
    ap.add_argument("--out", default="", help="optional jsonl: per-turn related/zero-overlap detail")
    a = ap.parse_args()

    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row
    df: collections.Counter = collections.Counter()
    rows: dict[int, sqlite3.Row] = {}
    for r in c.execute("select id, text, class, hits from memories where invalid_at is null"):
        rows[r["id"]] = r
        df.update(set(toks(r["text"])))

    def payload(t):
        return {w for w in toks(t) if w not in STOP and df.get(w, 10 ** 9) <= a.df_max}

    turns = c.execute("select id, user_text, injected, created_at from turns where injected is not "
                      "not null and injected != '' order by id desc limit ?", (a.turns,)).fetchall()
    if not turns:
        print("no injected turns in the journal yet - the column fills on the live server")
        return 2
    out = open(a.out, "w") if a.out else None
    tot = rel = 0
    filler: collections.Counter = collections.Counter()
    for t in sorted(turns, key=lambda r: -r["id"]):
        try:
            ids = json.loads(t["injected"] or "[]")
        except json.JSONDecodeError:
            ids = []
        if isinstance(ids, dict):
            ids = ids.get("ids", [])
        ut = payload(t["user_text"] or "")
        hit, miss, examples = 0, 0, []
        for i in ids:
            r = rows.get(int(i))
            if r is None:
                continue                      # the row was retired after the turn - correct to skip
            tot += 1
            if ut & payload(r["text"]):
                hit += 1; rel += 1
            else:
                miss += 1
                filler[r["id"]] += 1          # rows riding many turns are the stuffing itself
                if len(examples) < 2:
                    examples.append(r["text"][:56])
        print(f"  turn {t['id']:>4} got {len(ids):>3} rows | related {hit:>3} | zero-overlap {miss:>2}"
              + (f"  e.g. \"{examples[0]}\"" if examples else ""))
        if out:
            out.write(json.dumps({"turn": t["id"], "n": len(ids), "related": hit, "zero": miss},
                                 ensure_ascii=False) + "\n")
    if out:
        out.close()
    print(f"\nOVERALL: {rel}/{tot} injected rows share a rare payload token with the user's question "
          f"({rel / max(1, tot):.1%})")
    top = [f"{rows[i]['text'][:48]} x{n}" for i, n in filler.most_common(5) if n >= 3]
    if top:
        print("repeat filler (same row riding many turns - the semantic hub problem):")
        for t in top:
            print(f"  {t}")
    print("\nread as: the proxy UNDERSTATES paraphrase relevance on purpose, so the true share is "
          "higher - but a turn\nwith 104 rows and 5 related ones is stuffing by any standard. The "
          "fix tunes two knobs against\nthis instrument: a minimum cosine on the semantic arm, and "
          "treating the budget as a cap\nnot a quota (stop taking candidates when marginal relevance "
          "collapses). Tune so this number\nrises WITHOUT the venue's gold-row coverage falling - "
          "lme_retrieval_ab --offline is the guard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
