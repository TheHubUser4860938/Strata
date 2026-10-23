"""spikes/ple-overlay/mk_ple_copy.py - the D2/D3 writer around the ple_ngram mirror: build a marker->fact
row CLONE plan from token sequences, then apply it to a COPY of the Q8 PLE shard (the zero-engine-code
venue: --ple-gguf points at the copy; NOTES.md 2 measured why the copy is boot-compatible - same size,
same header, the fill check passes untouched) and VERIFY the copy row-for-row by rereading it.

Plan format (JSON, deterministic, diff-friendly - the experiment record of exactly what was written):
  {"src_table": "...gguf", "dst_copy": "...gguf", "row_bytes": 170, "n_rows": 320001536,
   "writes": [{"name": "d2-1", "pos": 0, "head": 0, "marker": 151, "fact": 264, "ctx": [...],
               "src_row": R, "dst_row": D, "dst_pos": B}, ...],
   "collisions": [{"dst_row": D, "src_rows": [R1, R2]}...]}
Collisions are NOT fatal (structural here: ~20M rows per band against a 2^64 key space - every row
serves many keys; the engine's trained readout learned WITH that noise). They are recorded, because
each one names collateral: every other n-gram hashing to that dst row now reads the clone's values too.

Modes:
  --pairs pairs.json --tokenizer PACKDIR --emit PLAN.json     # token texts -> rows16 via the mirror
  --plan PLAN.json --show                                      # what would be written (first N)
  --plan PLAN.json --apply                                     # copy src -> dst (resumable) + pwrite rows
  --plan PLAN.json --verify                                    # reread: every dst row == its src row,
                                                               # and every UNWRITTEN probe row == table
  --pairs pairs.json --tokens "text1,text2" ...                # convenience: encode ids inline
pairs.json entries: {"name": str, "fact_ids": [int], "marker_ids": [int],
                     "fact_ctx": [p2, p1] (optional), "marker_ctx": [p2, p1] (optional)}
Position i of the marker pairs with position i of the fact (zip; shorter side wins, warned).

No AkbasCore mechanism material: this file is Strata-fork tooling over Strata's own n-gram table
(MIT like Strata).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ple_ngram as ple  # the oracle-exact mirror (spikes/ple-overlay/ple_ngram.py)


def rows16_window(ids, ctx):
    """16 rows for every position of a token sequence, walking the real predecessor window. ctx =
    [prev2, prev1] of the FIRST token (-1 for none); after that predecessors are the sequence itself."""
    p2, p1 = (ctx if ctx else [-1, -1])
    out = []
    for i, t in enumerate(ids):
        out.append(ple.ngram_rows16(t, p2, p1))
        p2, p1 = p1, t
    return out


def build_plan(pairs, src_table):
    info = ple.table_info(src_table)
    writes, dst_seen = [], {}
    for pr in pairs:
        frows = rows16_window(pr["fact_ids"], pr.get("fact_ctx"))
        mrows = rows16_window(pr["marker_ids"], pr.get("marker_ctx"))
        n = min(len(frows), len(mrows))
        if n < len(mrows):
            print(f"warn {pr['name']}: marker {len(mrows)} positions > fact {len(frows)}; "
                  f"extra marker positions not cloned")
        for i in range(n):
            for h in range(16):
                d, s = int(mrows[i][h]), int(frows[i][h])
                w = {"name": pr["name"], "pos": i, "head": h,
                     "marker": pr["marker_ids"][i], "fact": pr["fact_ids"][i],
                     "src_row": s, "dst_row": d, "dst_pos": ple.row_file_pos(info, d)}
                writes.append(w)
                if d in dst_seen and dst_seen[d] != s:
                    pass
                dst_seen[d] = s
    col = {}
    for w in writes:
        col.setdefault(w["dst_row"], set()).add(w["src_row"])
    collisions = [{"dst_row": d, "src_rows": sorted(s)} for d, s in sorted(col.items()) if len(s) > 1]
    return {"src_table": src_table, "dst_copy": "", "row_bytes": ple.ROW_BYTES,
            "n_rows": info["n_rows"], "writes": writes,
            "collisions": collisions, "note": "dst value = src row's 170 B, verbatim (same Q8 grid, "
            "zero requant error); every other key hashing to a dst row reads the clone too"}


def apply_plan(plan, out_copy):
    """Resumable: copy the shard once (progress every 4 GB), then pwrite the 170-B blocks.
    Dedups by dst_row (same-row writes coalesce; last src wins - recorded as a collision)."""
    src = plan["src_table"]
    if not Path(out_copy).exists() or os.path.getsize(out_copy) != os.path.getsize(src):
        print(f"copying {src} -> {out_copy} ...", flush=True)
        import shutil
        t0 = __import__("time").time()
        with open(src, "rb") as f, open(out_copy + ".part", "wb") as g:
            done = 0
            while True:
                buf = f.read(1 << 30)
                if not buf:
                    break
                g.write(buf)
                done += len(buf)
                if done % (4 << 30) < (1 << 30):
                    print(f"  {done / (1 << 30):.0f} GB ({__import__('time').time()-t0:.0f} s)", flush=True)
        os.replace(out_copy + ".part", out_copy)
        print(f"copy done in {__import__('time').time()-t0:.1f} s", flush=True)
    info = ple.table_info(src)
    by_dst = {}
    for w in plan["writes"]:
        by_dst[w["dst_row"]] = w["src_row"]
    with open(src, "rb") as fs, open(out_copy, "r+b") as fd:
        for d, s in by_dst.items():
            fs.seek(ple.row_file_pos(info, s))
            blob = fs.read(ple.ROW_BYTES)
            fd.seek(ple.row_file_pos(info, d))
            fd.write(blob)
    print(f"wrote {len(by_dst)} unique rows ({len(plan['writes'])} plan entries) into {out_copy}")
    plan["dst_copy"] = out_copy
    return plan


def verify_plan(plan):
    """Post-apply check: every written dst row in the COPY must byte-equal its src row in the
    SOURCE, and untouched rows must not have shifted (we sample 100 unwritten rows against the
    source). dst_copy empty == check against the source file itself (degenerate, all pass)."""
    src, dst = plan["src_table"], (plan.get("dst_copy") or plan["src_table"])
    info = ple.table_info(src)
    import random
    written = {w["dst_row"] for w in plan["writes"]}
    bad = 0
    for w in plan["writes"]:
        if ple.read_row_bytes(dst, info, w["dst_row"]) != ple.read_row_bytes(src, info, w["src_row"]):
            print(f"MISMATCH {w['name']} pos {w['pos']} head {w['head']}: dst {w['dst_row']} != src {w['src_row']}")
            bad += 1
    rng = random.Random(7)
    probe = set()
    while len(probe) < 100:
        r = rng.randrange(info["n_rows"])
        if r not in written:
            probe.add(r)
    moved = sum(1 for r in probe if ple.read_row_bytes(dst, info, r) != ple.read_row_bytes(src, info, r))
    print(f"verify: {len(plan['writes'])} written rows, {bad} mismatched; "
          f"100 unwritten sample rows: {moved} differ (want 0)")
    return 1 if (bad or moved) else 0


def preflight(plan):
    """Pre-copy check on the REAL table (zero bytes written): every dst row position must resolve,
    and we count how many dst rows ALREADY byte-equal their src row (near-zero expected on a stock
    table - every other mismatch is a row whose stock content the clone overwrites, i.e. the
    honest per-row collateral the plan records)."""
    src = plan["src_table"]
    info = ple.table_info(src)
    unreadable = already = 0
    for w in plan["writes"]:
        try:
            b = ple.read_row_bytes(src, info, w["dst_row"])
        except SystemExit:
            print(f"UNREADABLE dst row {w['dst_row']} ({w['name']} pos {w['pos']} head {w['head']})")
            unreadable += 1
            continue
        if b == ple.read_row_bytes(src, info, w["src_row"]):
            already += 1
    print(f"preflight: {len(plan['writes'])} writes; all positions resolve: {unreadable == 0}; "
          f"dst rows already holding the src value: {already} (stock-collateral to overwrite: "
          f"{len(plan['writes']) - already})")
    return 1 if unreadable else 0


def encode(packs, texts):
    """THE production tokenizer: built exactly like serve/server.py L6149-6157
    (tools/strata_tokenizer.py), so what WE hash is what the server and engine tokenized."""
    import json as _json
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import strata_tokenizer as ST
    tdir = Path(packs)
    vocab = _json.loads((tdir / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (tdir / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = _json.loads((tdir / "token_type.json").read_text())
    tok = ST.Tokenizer(tokens, merges, types)
    return [tok.encode(t, parse_special=True) for t in texts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs")
    sys.path.insert(0, str(ple.ORACLE_INC.parents[2]))
    from strata_control.datafolder import data_dir      # single-root law (2026-10-23): the table lives in the data folder
    ap.add_argument("--src", default=str(data_dir(ple.ORACLE_INC.parents[2]) / "models/IQ3_S/"
                    "Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"))
    ap.add_argument("--tokenizer", help="pack tokenizer dir (for --tokens)")
    ap.add_argument("--tokens", help="comma 'marker_text|fact_text' pairs, encoded with --tokenizer")
    ap.add_argument("--emit", help="write plan JSON here")
    ap.add_argument("--plan", help="plan JSON to act on")
    ap.add_argument("--show", type=int, const=12, nargs="?", default=0, help="print first N writes")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", help="copy to create/write into")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--preflight", action="store_true",
                    help="read-only check of every planned row position against the real table")
    a = ap.parse_args()

    pairs = json.load(open(a.pairs)) if a.pairs else []
    if a.tokens:
        assert a.tokenizer, "--tokens needs --tokenizer"
        for spec in a.tokens.split(","):
            m, f = spec.split("|")
            mi, fi = encode(a.tokenizer, [m.strip(), f.strip()])
            pairs.append({"name": m.strip(), "marker_ids": mi, "fact_ids": fi})
    if pairs and (a.emit or not a.plan):
        plan = build_plan(pairs, a.src)
        if a.emit:
            Path(a.emit).write_text(json.dumps(plan, indent=1, sort_keys=True))
            n_col = len(plan["collisions"])
            print(f"plan: {len(plan['writes'])} writes, {n_col} collided dst rows -> {a.emit}")
            if a.apply:
                plan = apply_plan(plan, a.out or (a.emit + ".copy.gguf"))
                json.loads  # noqa
                Path(a.emit).write_text(json.dumps(plan, indent=1, sort_keys=True))
            return
    else:
        plan = json.load(open(a.plan))
    if a.show:
        for w in plan["writes"][:a.show]:
            print(f"  {w['name']:14s} pos {w['pos']} head {w['head']:2d} "
                  f"marker {w['marker']:6d} <- fact {w['fact']:6d}: row {w['src_row']:>10d} -> {w['dst_row']:>10d} @ {w['dst_pos']}")
        print(f"  ... {len(plan['writes'])} writes total; collisions: {len(plan['collisions'])}")
    rc = 0
    if a.apply:
        plan = apply_plan(plan, a.out or plan.get("dst_copy") or sys.exit("--apply needs --out"))
        # persist the chosen copy path so --verify works in a SEPARATE later call, not just alongside
        Path(a.plan or a.emit or sys.exit("apply without a plan file to update: pass --plan or --emit")
             ).write_text(json.dumps(plan, indent=1, sort_keys=True))
    if a.preflight:
        rc = preflight(plan)
    if a.verify:
        if a.out:
            plan["dst_copy"] = a.out
        rc = rc or verify_plan(plan)
    sys.exit(rc)


if __name__ == "__main__":
    main()
