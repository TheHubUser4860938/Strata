#!/usr/bin/env python3
"""spikes/window/build_d2_plan.py - turn markers.json into the D2 clone PLAN (plus preflight),
using the production tokenizer and the oracle-exact hash mirror. The WRITING/verification of the
copy is left to spikes/ple-overlay/mk_ple_copy.py (--apply/--out/--verify), which already proved
itself in D3 pre-checks; this script owns the plan POLICY:

  - per fact, every alias (marker_texts) clones its rows16 ONTO the marker's row positions
    (position i of alias -> position i of fact, zipped to the alias length);
  - STOPWORD-POSITION SKIP: positions whose marker token is a common word (token ids of the
    stop list below) are SKIPPED - their rows are hammered by half the corpus, and cloning
    fact values there would rewrite global n-grams (collateral across every sentence). Only
    the rare-content positions carry the fact. Skips are counted and printed per pair.
  - every planned row pair also gets its ALREADY-OCCUPIED status from preflight (the stock
    table's current bytes at the dst row): every write overwrites existing shared content
    (structural: ~20M rows/band vs 2^64 keys); the count is the honest collateral number.
  - collisions WITHIN the plan (same dst row wanted by two different src values - e.g. two
    aliases sharing a trigram row) are resolved FIRST-WRITE-WINS and listed, never silently
    last-writer-over; which one wins is deterministic (pair order, then position, then head).

Output: d2_plan.json (same schema mk_ple_copy already reads/writes) + printed summary.
Run it now (needs no engine, no GPU, reads the table header only + 2x len(writes) row reads).
"""
from __future__ import annotations
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "spikes" / "ple-overlay"))
sys.path.insert(0, str(ROOT / "tools"))
import ple_ngram as ple  # oracle-exact hash mirror

STOP = """the a an of is are was were in on to and or not for with named called who what where
when largest city keeper dish traditional this that as it its they their he she there here
food national main biggest town capital known means one two three""".split()


def main():
    tok = _load_tokenizer()
    ids_of = lambda s: tok.encode(s, parse_special=True)
    stop_ids = {i for w in STOP for i in ids_of(w)}
    mk = json.loads((HERE / "markers.json").read_text())
    rel = "Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
    src = str(ROOT / rel)
    info = ple.table_info(src)
    writes, seen, skipped, per_pair = [], {}, {}, []
    for pr in mk["pairs"]:
        f_ids = ids_of(pr["fact_text"])
        f_rows = _windows(f_ids)
        n_skip = n_w = 0
        for alias in pr["marker_texts"]:
            m_ids = ids_of(alias)
            m_rows = _windows(m_ids)
            for i in range(min(len(m_rows), len(f_rows))):
                if m_ids[i] in stop_ids:
                    n_skip += 1
                    continue
                for h in range(16):
                    d, s = int(m_rows[i][h]), int(f_rows[i][h])
                    if d in seen:
                        continue  # first-write-wins
                    seen[d] = s
                    writes.append({"name": pr["name"], "pos": i, "head": h,
                                   "marker": m_ids[i], "fact": f_ids[min(i, len(f_ids) - 1)],
                                   "src_row": s, "dst_row": d,
                                   "dst_pos": ple.row_file_pos(info, d)})
                    n_w += 1
        per_pair.append((pr["name"], n_w, n_skip))
        skipped[pr["name"]] = n_skip
    collisions = [{"dst_row": d, "src_rows": sorted({s for _, s in g})}
                  for d, g in _dupgroups(writes).items() if len({s for _, s in g}) > 1]
    plan = {"src_table": rel, "dst_copy": "", "row_bytes": ple.ROW_BYTES, "n_rows": info["n_rows"],
            "writes": writes, "collisions": collisions,
            "note": "built by build_d2_plan.py: stopword-position skip + first-write-wins; "
                    "provenance per row in writes[]; src_table is repo-root-relative - run apply "
                    "and verify from the repo root like every other window script"}
    (HERE / "d2_plan.json").write_text(json.dumps(plan, indent=1, sort_keys=True))

    occupied = 0
    for w in writes[::max(1, len(writes) // 240)]:  # sampled occupancy check (full one costs minutes)
        if ple.read_row_bytes(src, info, w["dst_row"]) != ple.read_row_bytes(src, info, w["src_row"]):
            occupied += 1
    print("plan: d2_plan.json written:", len(writes), "row-writes")
    for name, n_w, n_s in per_pair:
        print(f"  {name}: {n_w} writes, {n_s} stopword positions skipped")
    print(f"  intra-plan dst collisions (first-write-wins): {len(collisions)}")
    print(f"  sampled occupancy: {occupied} of {len(writes[::max(1, len(writes) // 240)])} dst rows "
          f"currently hold OTHER values (every clone overwrites them - structural, see NOTES.md 3)")


def _load_tokenizer():
    import strata_tokenizer as ST
    tdir = ROOT / "Strata-data/packs/iq3_s/tokenizer"
    vocab = json.loads((tdir / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (tdir / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = json.loads((tdir / "token_type.json").read_text())
    return ST.Tokenizer(tokens, merges, types)


def _windows(ids):
    """rows16 per position, predecessors = the sequence itself (first position: no context)."""
    out, p2, p1 = [], -1, -1
    for t in ids:
        out.append(ple.ngram_rows16(t, p2, p1))
        p2, p1 = p1, t
    return out


def _dupgroups(writes):
    g = {}
    for w in writes:
        g.setdefault(w["dst_row"], []).append((w["name"], w["src_row"]))
    return g


if __name__ == "__main__":
    main()
