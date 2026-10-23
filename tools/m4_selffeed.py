#!/usr/bin/env python3
"""m4_selffeed - are our curated facts derived from what the USER said, or from what the ASSISTANT said?

The failure class comes from mem0's own 32-day production audit (#4573: 97.8% junk, with "boot-file
restating" and "recalled-context re-extraction" named as the mechanisms). Strata has the same exposure from
a different direction: the server injects a memory block into the SYSTEM message, the model then narrates
over it, the journal stores both halves of every turn, and the curation loop extracts from the pair. A fact
whose content is really the assistant restating memory the server just supplied is memory feeding on itself -
it costs a block slot, inflates the store, and looks like recall in the review list while adding nothing.

Why an echo is worth detecting rather than dismissing: the assistant half is where tool noise, restated
plan status and the model's own hedging live ("I'll remember that", "as noted above, the venue boot is
pending"). The extract prompt already says "skip tool noise" and it is a 125B model doing it at temp 0.8,
so the honest question is what the store ACTUALLY contains, not what the prompt asked for.

Method (offline, a copy of the live store, no engine): for every active row whose source is a curation run,
follow provenance.turn_id back to the journaled turn, and compare the fact's content words against each
half. Classification is by which half the fact overlaps more, with an absolute floor so a short fact that
overlaps nothing is called UNMATCHED rather than guessed. Also reported separately: facts whose text is
closer to a PREVIOUSLY STORED row than to either half (the near-dupe path the guard already handles) and
the per-source breakdown, because `m2_ingest-*` rows are documents, not conversation, and belong in a
different bucket.

The number that matters is the echo share. If it is small, item 8 of the tier-3 backlog (junk pre-filters)
has no target here and the curation cost stays as measured. If it is large, the cheapest fix is not a new
filter but a one-line change to what the extractor is allowed to read.
"""
from __future__ import annotations

import argparse, collections, json, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
from serve.memory import MemoryService, STOP, norm_text           # noqa: E402


def toks(text):
    return {t for t in norm_text(text or "").split() if t not in STOP}


def jac(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(data_dir(ROOT) / "memory-eval"))
    ap.add_argument("--limit", type=int, default=0, help="cap curated rows examined")
    ap.add_argument("--floor", type=float, default=0.12,
                    help="minimum content-word Jaccard to count as derived from a half at all")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    src = Path(a.dir).resolve()
    work = Path(tempfile.mkdtemp(prefix="selffeed-"))
    import _storeclone
    _storeclone.clone_store(src, work)      # R5b: honest dump/restore, works while the store is
                                            # live; the old plaintext-memory.db check aborted (or
                                            # silently copied nothing) on a Postgres store.
    m = MemoryService(str(work), recall_limit=96, embedder=None)

    rows = m.conn.execute("select id, text, source, provenance, created_at from memories"
                          " where invalid_at is null order by id").fetchall()
    by_source = collections.Counter()
    for _id, _t, s, _p, _c in rows:
        by_source[(s or "").split("-")[0] or "(none)"] += 1
    curated = [r for r in rows if (r[2] or "").startswith("curation-")]
    print(f"{len(rows)} active rows | by source family: {dict(by_source)}")
    print(f"{len(curated)} of them written by the curation loop - examining "
          f"{min(len(curated), a.limit or len(curated))}\n")

    verdict = collections.Counter()
    measurable = [0]              # rows the provenance path could judge (needs the injected column)
    echoes, unmatched, examples = [], [], []
    for rid, text, source, prov, _c in (curated[:a.limit] if a.limit else curated):
        try:
            tid = (json.loads(prov or "{}") or {}).get("turn_id")
        except json.JSONDecodeError:
            tid = None
        if tid is None:
            verdict["no turn_id in provenance"] += 1
            continue
        turn = m.conn.execute("select user_text, assistant_text, injected from turns where id=%s",
                              (tid,)).fetchone()
        if turn is None:
            verdict["turn no longer journaled"] += 1
            continue
        ft, ut, at = toks(text), toks(turn[0]), toks(turn[1])
        # The direct test, available only once the journal carries what was on screen (the `injected`
        # column, added 2026-10-15): a fact that matches an ON-SCREEN memory row but NOT the user's own
        # words is memory coming back out of the model - the class mem0's audit calls recalled-context
        # re-extraction. A fact that matches both is the user repeating something, which is legitimate
        # and must not be filtered. Before the column fills in, every row falls through to the lexical
        # halves below and the tool says so rather than pretending to measure.
        onscreen = json.loads(turn[2] or "[]") if turn[2] else []
        if onscreen:
            measurable[0] += 1
            rows_t = m.conn.execute("select text from memories where id in ("
                                    + ",".join("?" * len(onscreen)) + ")", onscreen).fetchall()
            best = max((jac(ft, toks(r[0])) for r in rows_t), default=0.0)
            if best >= a.floor and jac(ft, ut) < a.floor:
                verdict["RE-EXTRACTED from on-screen memory (absent from the user's words)"] += 1
                echoes.append((rid, jac(ft, ut), best, text[:88]))
                continue
            if best >= a.floor:
                verdict["on screen AND in the user's words (legitimate repeat)"] += 1
                continue
        ju, ja = jac(ft, ut), jac(ft, at)
        if max(ju, ja) < a.floor:
            verdict["UNMATCHED (overlaps neither half)"] += 1
            unmatched.append((rid, ju, ja, text[:88]))
            continue
        if ju >= ja * 1.25:
            verdict["user-derived"] += 1
        elif ja >= ju * 1.25:
            verdict["ASSISTANT-derived (echo)"] += 1
            echoes.append((rid, ju, ja, text[:88]))
        else:
            verdict["both halves (normal paraphrase)"] += 1
        if len(examples) < 6 and (ja > ju * 1.25 or ju > ja * 1.25):
            examples.append((("echo" if ja > ju else "user"), rid, round(ju, 2), round(ja, 2), text[:96]))

    scored = sum(verdict.values())
    print(f"  {'provenance verdict':<38}{'n':>6}{'share':>8}")
    for k, v in verdict.most_common():
        print(f"  {k:<38}{v:>6}{v / max(1, scored):>8.1%}")
    print(f"\n  measurable by PROVENANCE (the turn carries an injected list): {measurable[0]} of {scored}"
          f" - the column landed 2026-10-15, so until the dev-up runs and new turns accumulate, every row"
          f"\n  falls through to the word-overlap test below and its numbers are a floor, not an answer.")
    n_e = verdict["ASSISTANT-derived (echo)"]
    n_u = verdict["user-derived"]
    if n_e + n_u:
        print(f"\n  among facts attributable to one half: {n_e} echo vs {n_u} user-derived "
              f"= {n_e / (n_e + n_u):.0%} echo")
    if examples:
        print("\n  the two sides of the line, so the classification can be audited by hand:")
        for kind, rid, ju, ja, t in examples:
            print(f"    [{kind:>4}] {rid} user {ju:.2f} / asst {ja:.2f}  {t!r}")
    if unmatched:
        print(f"\n  {len(unmatched)} matched neither half (below the {a.floor} floor) - candidates for "
              f"tool noise or for a turn that has since been re-journaled:")
        for rid, ju, ja, t in unmatched[:5]:
            print(f"    {rid} user {ju:.2f} / asst {ja:.2f}  {t!r}")
    if a.json:
        Path(a.json).write_text(json.dumps({"dir": str(src), "rows": len(rows), "curated": len(curated),
                                            "verdict": dict(verdict), "floor": a.floor,
                                            "echo_share_of_attributable": (n_e / (n_e + n_u))
                                            if (n_e + n_u) else None}, indent=1))
        print(f"\n  full record: {a.json}")
    print("\n  read as: an echo is a fact whose content came from the model's own narration over memory the\n"
          "  server supplied - it costs a block slot and looks like recall in the review list. A small echo\n"
          "  share means the junk pre-filter on the backlog has no target here; a large one is fixed by\n"
          "  changing what the extractor reads, not by adding a filter after the fact.")
    m.close()
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
