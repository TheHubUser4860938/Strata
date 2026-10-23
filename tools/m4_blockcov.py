#!/usr/bin/env python3
"""m4_blockcov - what did the MODEL actually see, at each budget? The instrument behind two filed claims.

The venue record files coverage of the RECALL LIST at the request's own limit. The thing that reaches the
prompt is the RENDERED BLOCK, and a char cap trims its tail - so "the block is bigger" and "the model saw
more" are different claims, and only the second one is about accuracy. This renders the real block through
the store's own `block_for()` at both budgets and both arms and scores gold-row and gold-session coverage of
the rows the block ACTUALLY SHOWED.

Filed numbers it produced on 2026-10-14 (venue copy, 500 questions, ~1,000-char rows), and the reason arm
C1 (the lexical accuracy arm) was skipped rather than run:

    arm B  96/24k   lexical  notes 14.x  chars ~18.7k   row cov ...
    arm C  192/48k  hybrid   notes 14.6  chars 18,735   row cov 0.943

i.e. at venue row length the CHAR cap binds at ~15 notes, so 192/48k cannot show more than 96/24k does -
the budget axis was provably empty and 1.8 h of venue time would have split a zero.

One trap, learned the hard way the same day and built in here rather than trusted: the head block is
PINNED by (scope, first user message, shelf, arm) and **the budget is not in the key** - so flipping
`max_block_chars` inside one process re-measures the same pinned bytes and reports an impossible "identical
at both budgets". Every budget change clears `m._blocks` (serve/memory.py:688-693 in docs/MEMORY_CORE.md
spells the lesson out; if a per-request budget override is ever added, the pin key has to gain the budget
the way it gained the arm).

Offline: opens the A/B tool's already-embedded copies, no engine, no port, no restart.

    python3 tools/m4_blockcov.py                     # defaults: the venue A/B copies
    python3 tools/m4_blockcov.py --budgets 96:24000,192:48000 --arms both
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strata_control.datafolder import data_dir                    # noqa: E402  the one data folder (2026-10-23)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import lme_harness as H                                          # noqa: E402
from serve.memory import MemoryService                           # noqa: E402
from m4_xref import subjects_of, vote_subjects                   # noqa: E402  one subject law, two tools


def mean(xs):
    live = [x for x in xs if x is not None]
    return (sum(live) / len(live)) if live else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default="/tmp/opencode/venue-ab-store",
                    help="the A/B tool's --work dir (holds the embedded 'hyb' and 'lex' copies)")
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map")
    ap.add_argument("--model", default=str(data_dir(ROOT) / "embeddings/bge-small-en-v1.5"))
    ap.add_argument("--budgets", default="96:24000,192:48000",
                    help="note-cap:char-cap pairs to compare, comma separated")
    ap.add_argument("--arms", choices=("both", "hybrid", "lexical"), default="both")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--xref", action="store_true",
                    help="add a cross-reference arm: scope the block to the rows that share a SUBJECT "
                         "with the top hits (LCC's headings / Primnox's 'filed in every chapter it "
                         "relates to'), rendered at the same budget, and report its coverage next to "
                         "the unscoped one. The question this answers is not whether contamination "
                         "falls - it does, measured 2026-10-15 (POL 56.2%% -> 8.3%% leak) - but whether "
                         "the much smaller block still holds the evidence.")
    ap.add_argument("--xref-mode", choices=("scope", "union"), default="scope",
                    help="scope = answer ONLY from rows that share a subject (the strict LCC/Primnox "
                         "reading, and the one Primnox's chapter-opening loop does). union = keep the "
                         "normal candidates and ADD the cross-reference rows to them (the additive "
                         "reading: headings as extra access points, never as a fence). Measured 2026-10-15: "
                         "scope at 96/24k showed 3.7 notes and 0.404 row coverage against 18.3 notes and "
                         "0.966 unscoped - too narrow to be a block - so the live question is whether the "
                         "additive form buys coverage the ranking alone misses.")
    ap.add_argument("--df-max", type=int, default=20, help="a subject is a token this rare or rarer")
    ap.add_argument("--top-subjects", type=int, default=2, help="subjects a call may name")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    budgets = []
    for i, spec in enumerate(a.budgets.split(",")):
        n, c = spec.split(":")
        budgets.append((spec, int(n), int(c)))
    arms = [x for x in (("lexical", False), ("hybrid", True))
            if a.arms == "both" or (a.arms == "hybrid") == x[1]]

    work = Path(a.work)
    services, xterms = {}, {}
    for lab, embed in arms:
        d = work / ("hyb" if embed else "lex")
        if not (d / "pgdata").exists() and not (d / "memory.db.enc").exists():
            print(f"no store copy at {d} - run tools/lme_retrieval_ab.py --offline --work {work} first")
            return 2
        kw = {"embedder": __import__("serve.embedder", fromlist=["Embedder"]).Embedder(a.model, threads=4)} \
            if embed else {}
        services[lab] = MemoryService(str(d), recall_limit=192, max_block_chars=48000, encrypt=True,
                                      key_file=str(d / "memory.key"), **kw)
        services[lab].search("warm", limit=1)                    # warm the vector matrix once
        if embed and a.xref:
            tab = services[lab]._dedupe_tables()
            tab["dfmax"] = a.df_max
            rows = services[lab].conn.execute("select id, text, coalesce(class,'') from memories"
                                              " where invalid_at is null").fetchall()
            by_term = {}
            for rid, terms in subjects_of(rows, tab).items():
                for t in terms:
                    by_term.setdefault(t, set()).add(rid)
            xterms[lab] = by_term
            print(f"  [{lab}] cross-references: {sum(len(v) for v in by_term.values())} pairs over "
                  f"{len(by_term)} terms")

    data = json.load(open(a.data))[: (a.limit or None)]
    maps = H.load_map(a.mpath)
    acc = {}
    for qi, q in enumerate(data):
        where = maps.get(q["question_id"])
        if not where:
            continue
        gold_r = {r for r, (ha, _s) in where.items() if ha}
        gold_s = {where[r][1] for r in gold_r}
        msg = [{"role": "user", "content": q["question"]}]
        for bname, notes, chars in budgets:
            for lab, embed in arms:
                m = services[lab]
                m.recall_limit, m.max_block_chars = notes, chars
                m._blocks.clear()          # the budget is NOT in the pin key - see the docstring
                block, shown, _q = m.block_for(msg, f"lme_{q['question_id']}", semantic=embed)
                # block_for returns hit RECORDS, not ids; scoring them as ids would compare dicts against
                # ints and report a coverage of zero with no error anywhere
                ids = [h["id"] if isinstance(h, dict) else h for h in shown]
                cell = acc.setdefault((bname, lab), {"rows": [], "sess": [], "notes": [], "chars": []})
                cell["rows"].append(len(gold_r & set(ids)) / len(gold_r) if gold_r else None)
                cell["sess"].append(len(gold_s & {where[i][1] for i in ids if i in where}) / len(gold_s)
                                    if gold_s else None)
                cell["notes"].append(len(ids))
                cell["chars"].append(len(block or ""))
                if a.xref and embed:                # the cross-reference arm, same budget, same question
                    stage = [(h["id"], h.get("class") or "") for h in
                             m.search(q["question"], scope=f"lme_{q['question_id']}", limit=12,
                                      semantic=False)]
                    keep = set()
                    for t in vote_subjects([(i, [x for x in xterms[lab] if i in xterms[lab][x]])
                                            for i, _c in stage], a.top_subjects):
                        keep |= xterms[lab].get(t, set())
                    wide = m.search(q["question"], scope=f"lme_{q['question_id']}", limit=1200,
                                    semantic=True)
                    cand = [h for h in wide if h["id"] in keep]
                    if a.xref_mode == "union":      # additive: the block's own list, plus any xref row
                        seen = {h["id"] for h in cand}
                        _bl, base = m._render_block(m.search(q["question"],
                                                            scope=f"lme_{q['question_id']}", limit=1200,
                                                            semantic=True), 10 ** 9,
                                                   f"lme_{q['question_id']}")   # char cap off: full list
                        cand = [h for h in base if h["id"] not in seen] + cand
                        cand.sort(key=lambda h: -h.get("score", 0.0))
                    xb, xshown = m._render_block(cand, m.max_block_chars, f"lme_{q['question_id']}")
                    xids = {h["id"] for h in xshown}
                    xc = acc.setdefault((bname, "xref"), {"rows": [], "sess": [], "notes": [], "chars": []})
                    xc["rows"].append(len(gold_r & xids) / len(gold_r) if gold_r else None)
                    xc["sess"].append(len(gold_s & {where[i][1] for i in xids if i in where}) / len(gold_s)
                                      if gold_s else None)
                    xc["notes"].append(len(xids))
                    xc["chars"].append(len(xb or ""))
        if qi and qi % 100 == 0:
            print(f"  {qi}/{len(data)} ...", flush=True)

    print(f"\n  what the model ACTUALLY SAW - {len(data)} questions, coverage of the RENDERED block")
    print(f"  {'budget':<14}{'arm':<9}{'notes shown':>12}{'chars':>9}{'row cov':>10}{'sess cov':>10}")
    out = {}
    for bname, _n, _c in budgets:
        for lab, _e in list(arms) + ([("xref", True)] if a.xref else []):
            cell = acc[(bname, lab if lab != "xref" else arms[0][0])] if lab != "xref" else acc[(bname, "xref")]
            r, s, nt, ch = mean(cell["rows"]), mean(cell["sess"]), mean(cell["notes"]), mean(cell["chars"])
            out[f"{bname}|{lab}"] = {"notes": round(nt, 2), "chars": round(ch), "row_cov": round(r, 3),
                                     "sess_cov": round(s, 3)}
            print(f"  {bname:<14}{lab:<9}{nt:>12.1f}{ch:>9.0f}{r:>10.3f}{s:>10.3f}")
    print("\n  the xref line is the cross-reference arm: a block scoped to every row that shares a subject")
    print("  with the top hits. Fewer notes at the SAME coverage is the win this arm is chasing - fewer")
    print("  tokens, less to read, same evidence. Fewer notes at LOWER coverage is the falsifier: the")
    print("  subject set is too narrow to be a block, and the store needs the wide unscoped one.")
    print("\n  read as: the BUDGET lever is the gap in notes/chars between two budget lines at the SAME arm;")
    print("  the RERANKER lever is the gap in coverage at the SAME budget. If the note count does not move")
    print("  between budgets, the budget axis is empty by construction and the accuracy run cannot answer")
    print("  anything about it - which is the measurement that let arm C1 be skipped instead of run.")
    if a.json:
        Path(a.json).write_text(json.dumps({"work": str(work), "questions": len(data), "cells": out},
                                           indent=1))
        print(f"  full record: {a.json}")
    for s in services.values():
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
