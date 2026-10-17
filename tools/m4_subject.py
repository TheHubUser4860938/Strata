#!/usr/bin/env python3
"""m4_subject - the one retrieval idea tonight's measurements did NOT kill: gather by SUBJECT.

Why this survived where the shelf axis died: a shelf NARROWS the candidate set by category, and
m4_shelf_nav.py showed narrowing buys nothing here (the unfiltered hybrid already puts the gold in the
top 96, and an oracle shelf scored exactly equal to it at all three sizes). A subject does the opposite -
it COLLECTS the multi-evidence set an ordering question needs. Two of the six systems converged on it
independently (Graphiti's entity rollup, mem0's subject index with a hub penalty at main.py:1807-1821),
and the store already computes exactly this signal at write time for the merge veto - the rare-token
subject, `df <= bar` - and then throws it away at read.

The measured reason to think there is room: coverage at the width a venue-shaped block really shows
(k=16, RRF_K=1) is 0.936 overall but 0.893 on multi-session and 0.920 on temporal questions - the two
multi-evidence types. The filed causal chain (coverage +0.048 -> accuracy +0.040) says coverage is the
metric that moves accuracy, so ~10 points of multi-session coverage is the biggest remaining prize on
the board.

Mechanism tested (offline, a copy of the venue store, no engine): search DEEP (limit 64) with the shipped
fusion, take the subject tokens of the top-N hits, and pull every deep candidate sharing a subject up into
the 16 slots the block would show, keeping the fusion order inside each group. Coverage is scored by the
harness's own recall_metrics at k=16, so the numbers sit beside the filed A/B table.

This deliberately does NOT touch serve/memory.py: if the gather does not raise coverage here, there is no
column to add and no law to write, and the idea costs an hour of probing rather than a schema change.
"""
from __future__ import annotations

import argparse, json, shutil, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from serve.memory import MemoryService, STOP, norm_text            # noqa: E402
from serve.embedder import Embedder                                # noqa: E402
from lme_harness import load_map, recall_metrics                   # noqa: E402


def subjects(texts, tab):
    """Rare-token subjects of a set of rows: the same rule the write guard uses (content words minus
    STOP, df at or below the table's bar), reused rather than reinvented so the probe and the guard
    cannot disagree about what a 'subject' is."""
    bar = tab["bar"]
    out = set()
    for t in texts:
        for tok in norm_text(t).split():
            if tok not in STOP and tab["df"].get(tok, 0) <= bar:
                out.add(tok)
    return out


def gather(wide, tab, anchor_n, min_shared):
    """Re-order a deep candidate list so rows sharing a subject with the anchor hits come first, fusion
    order preserved inside each group. `min_shared` >= 2 asks for more than one shared rare token, which
    is the difference between 'about the same thing' and 'happens to use a rare word'."""
    if not wide:
        return wide
    anchor = subjects([h["text"] for h in wide[:anchor_n]], tab)
    def shared(h):
        toks = {t for t in norm_text(h["text"]).split() if t not in STOP and tab["df"].get(t, 0) <= tab["bar"]}
        return len(toks & anchor)
    up, down = [], []
    for h in wide:
        (up if shared(h) >= max(min_shared, 1) else down).append(h)
    return up + down


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="Strata-data/memory-venue")
    ap.add_argument("--work", default="/tmp/opencode/venue-ab-store/hyb",
                    help="an already-embedded copy (the A/B tool's --work dir), so no re-embedding")
    ap.add_argument("--data", default="/tmp/opencode/longmemeval/data/longmemeval_oracle.json")
    ap.add_argument("--map", dest="mpath", default="/tmp/opencode/lme_oracle_full.jsonl.map")
    ap.add_argument("--wide", type=int, default=64, help="how deep to search before gathering")
    ap.add_argument("--show", type=int, default=16, help="slots the block would actually show")
    ap.add_argument("--anchors", type=int, default=3)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    src = Path(a.work) if a.work and Path(a.work).exists() else Path(a.dir)
    work = Path(tempfile.mkdtemp(prefix="subject-"))
    import _storeclone
    _storeclone.clone_store(src, work)    # R5b: honest dump/restore; the old file loop copied
                                          # legacy sqlite files (or nothing) on a PG store
    emb = Embedder(str(ROOT / "Strata-data/embeddings/bge-small-en-v1.5"), threads=4)
    sealed = False                        # a Postgres-native clone is born readable at rest
    svc = MemoryService(str(work), recall_limit=a.wide, max_block_chars=24000, embedder=emb,
                        encrypt=sealed, key_file=str(work / "memory.key"))
    st = svc.stats()
    # an empty store is the one failure mode that produces a confident table of zeros, so say what
    # opened before scoring anything (this probe scored 0 questions silently once already)
    print(f"opened {src} ({'sealed, revealed here' if sealed else 'plaintext'}): "
          f"{st['memories']} active rows, {st['by_scope'] and len(st['by_scope'])} scopes, "
          f"{(st.get('embedder') or {}).get('vectors')} vectors")
    if not st["memories"]:
        print("nothing to score - point --work at an embedded copy (the A/B tool's --work dir) "
              "or --dir at a store")
        return 2

    data = json.load(open(a.data))
    m = load_map(a.mpath)
    tab = svc._dedupe_tables()
    variants = [("shipped: fusion order, top 16", lambda w: w),
                ("subject gather, >=1 shared rare token", lambda w: gather(w, tab, a.anchors, 1)),
                (f"subject gather, >=2 shared (anchors={a.anchors})", lambda w: gather(w, tab, a.anchors, 2))]
    t0 = time.time()
    res = {name: {"cov": 0.0, "n": 0, "by_type": {}} for name, _ in variants}
    percov = {name: {} for name, _ in variants}
    for q in data:
        where = m.get(q["question_id"])
        if not where:
            continue
        gold_sids = {v[1] for v in where.values() if v[0]}
        abstain = q["question_id"].endswith("_abs")
        wide = svc.search(q["question"], scope=f"lme_{q['question_id']}", limit=a.wide)
        for name, fn in variants:
            ids = [h["id"] for h in fn(wide)[:a.show]]
            sc = recall_metrics(ids, where, gold_sids, abstain, a.show)
            if sc.get("row_coverage") is None:
                continue
            r = res[name]
            r["cov"] += sc["row_coverage"]; r["n"] += 1
            bt = r["by_type"].setdefault(q["question_type"], [0.0, 0])
            bt[0] += sc["row_coverage"]; bt[1] += 1
            percov[name][q["question_id"]] = sc["row_coverage"]
    base = variants[0][0]
    print(f"{res[base]['n']} questions, deep search limit {a.wide}, block width {a.show}, "
          f"anchors {a.anchors}  ({time.time() - t0:.0f} s)\n")
    print(f"  ROW COVERAGE at the width the block shows (the metric the filed causal chain ties to accuracy)")
    types = sorted(res[base]["by_type"])
    print(f"  {'type':<26}{'n':>5}" + "".join(f"{name.split(':')[0][:18]:>20}" for name, _ in variants))
    for t in ("OVERALL",) + tuple(types):
        line = f"  {t:<26}{(res[base]['n'] if t=='OVERALL' else res[base]['by_type'][t][1]):>5}"
        for name, _ in variants:
            v = (res[name]["cov"] / res[name]["n"]) if t == "OVERALL" else (
                res[name]["by_type"].get(t, [0, 0])[0] / max(1, res[name]["by_type"].get(t, [0, 1])[1]))
            line += f"{v:>20.3f}"
        print(line)
    moved = sum(1 for k in percov[base] if percov[variants[1][0]].get(k, 0) != percov[base][k])
    print(f"\n  questions whose coverage MOVED at all: {moved} of {res[base]['n']} - a gather that changes "
          f"nothing\n  on 95% of questions is not a mechanism, it is noise on the rest.")
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"wide": a.wide, "show": a.show, "anchors": a.anchors,
             "coverage": {n: {"overall": res[n]["cov"] / res[n]["n"],
                              "by_type": {t: v[0] / v[1] for t, v in res[n]["by_type"].items()}}
                          for n, _ in variants}}, indent=1))
        print(f"  full record: {a.json}")
    print(f"\n  read as: coverage is the metric that moves accuracy here, so a gather that does not raise it\n"
          f"  at the block's own width cannot become an accuracy win - and rows are only ADDED to the top "
          f"{a.show}\n  by pushing others out, so a flat line means the subject signal is already what the\n"
          f"  fusion is doing, and a drop means it is displacing evidence.")
    svc.close()
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
