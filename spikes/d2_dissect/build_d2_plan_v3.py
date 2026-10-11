#!/usr/bin/env python3
"""spikes/d2_dissect/build_d2_plan_v3.py - the v3 plan: NOTES.md §8's cheapest decisive experiment,
TWO DISJOINT row sets in ONE new copy (q8-shard2-d2v3.gguf, fresh from STOCK; v1/v2 copies inert):

  (a) STEER PROBE (H5 vs H6/H7): the trained window value of "Happy New" (tokens verified
      2026-10-11: Happy /' New'/ ' Year' - the row read at ' New' is what makes ' Year' the
      overwhelmingly likely next token for any competent LM) is copied onto the tap + content
      rows of a FRESH RARE CUE ("Fenmorrow-Harbor almanac", v2-row-clean verified pre-build).
      Ask the cue in the proven cue-last venue: if 'year' surfaces where it cold said idk,
      table-row writes CAN steer output at this dosage (H5 dead - the payload/target was the
      problem, H6 lives); if nothing moves across >= 3 passes with clean controls, row writes
      are inert as a readout mechanism AT ALL (H7) and the cartridge tier owns tier-2's future.

  (b) EMIT-WINDOW RECIPE (H6 single-variable test): the SAME marker venues as v2 (cues read
      from state/v2_build.json so gate and build cannot drift), but the TAP rows' source moves
      from the name-tail window (v2's src, proven 0/15) to the window ONE STEP BEFORE the name
      emit (fact position gpre = first-gold - 1: the ' named'-style window H6 predicts is where
      the cue->name association actually lives). Content rows keep v2's gtail source (the
      residual-carry route stays tested, comparable to v2's numbers line-for-line).

Channel geometry is v2's exactly (taps {P,P-3,P-6,P-9}, greedy tail 9 ids, tap = n-9; the
high-effort tail is 7 ids so its n-9 tap lands on real content - both venues written, greedy is
the authoritative ask channel per the temp-0.8 directive). The M-block venue is DROPPED (the
stacked-cue deferral is user-confirmed dead; it only cost collateral).

Output d2_plan_v3.json (mk_ple_copy schema) + state/v3_build.json. g1 asserts 16 head bands on
every tap key per arm and the src token identities; gates re-derive everything in v3_gates.py.
"""
from __future__ import annotations
import argparse, collections, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spikes" / "ple-overlay"))
sys.path.insert(0, str(ROOT / "spikes" / "window"))

import ple_ngram as ple
from venue_render import load_tokenizer, load_template, live_render, rows16_stream
from d2_rules import make_rules
from build_d2_plan_v2 import CUE_PREAMBLE

STOCK = "Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
DST = "Strata-data/ple-copies/q8-shard2-d2v3.gguf"
STEER_SRC_TEXT = "Happy New Year"
STEER_CUES = {"steer-target": "Fenmorrow-Harbor almanac",
              "steer-ctrl-1": "Draylemere-Ferry charter",
              "steer-ctrl-2": "Ossulgate-Kiln inventory"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "d2_plan_v3.json"))
    a = ap.parse_args()
    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    v2cues = json.loads((HERE / "state/v2_build.json").read_text())["cue"]
    info = ple.table_info(str(ROOT / STOCK))
    stop_form, is_content = make_rules(tok)

    # ---------------- marker pairs: gold spans + the emit-pre window (H6's src)
    P = {}
    for pr in mk["pairs"]:
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        f_rows = rows16_stream(f_ids)
        ftxt = tok.decode(f_ids)
        spans, c = [], 0
        for i in f_ids:
            s = tok.decode([i]); spans.append((c, c + len(s))); c += len(s)
        gpos = sorted({i for e in pr["expect"] for j in (ftxt.find(e),) if j >= 0
                       for i, (lo, hi) in enumerate(spans) if lo < j + len(e) and hi > j})
        gpre = min(gpos) - 1
        assert gpre >= 1, f"{pr['name']}: gold starts at the fact's head - no emit-pre window"
        assert gpre not in gpos, f"{pr['name']}: emit-pre position is itself gold"
        P[pr["name"]] = {"pr": pr, "f_ids": f_ids, "f_rows": f_rows, "gpos": gpos,
                         "gtail": max(gpos), "gpre": gpre,
                         "src_tail_tok": tok.decode([f_ids[max(gpos)]]).strip(),
                         "src_pre_tok": tok.decode([f_ids[gpre]]).strip(),
                         "cue": v2cues[pr["name"]],
                         "content": {int(x) for al in pr["marker_texts"]
                                     for x in tok.encode(al, parse_special=True)
                                     if is_content(int(x))}}
        print(f"{pr['name']}: cue={P[pr['name']]['cue']!r} tap src (emit-pre, pos {gpre}) = "
              f"{P[pr['name']]['src_pre_tok']!r}, content src (tail, pos {max(gpos)}) = "
              f"{P[pr['name']]['src_tail_tok']!r}")

    # ---------------- steer pair: src = the window ending at ' New', continuation = ' Year'
    s_ids = tok.encode(STEER_SRC_TEXT, parse_special=True)
    s_rows = rows16_stream(s_ids)
    p_src = next(i for i in range(len(s_ids)) if tok.decode([s_ids[i]]).strip().lower() == "new")
    cont = tok.decode([s_ids[p_src + 1]]).strip().lower()
    assert cont == "year", f"steer continuation is {cont!r}, expected 'year'"
    steer_content = {int(x) for al in STEER_CUES.values() for x in tok.encode(al, parse_special=True)
                     if is_content(int(x))}
    print(f"steer src: {[(i, tok.decode([i])) for i in s_ids]} -> src position {p_src}, "
          f"expected continuation = {cont!r}")

    # ---------------- venues -> dst keys (v2's observe() shape, minus the dead M-block)
    dst = collections.defaultdict(lambda: {"names": set(), "why": collections.defaultdict(set),
                                           "windows": set()})
    geom = {}

    def observe(name, vtag, ids, rows, kind_positions, tap_positions):
        for p, kind in [(p, "content") for p in kind_positions] + [(p, "tap") for p in tap_positions]:
            t0, t1 = int(ids[p]), (int(ids[p - 1]) if p >= 1 else -1)
            for h in range(16):
                e = dst[(h, int(rows[p][h]))]
                e["names"].add(name)
                e["why"][kind].add(vtag)
                e["windows"].add((t0, t1))
        return {"n": len(ids), "content_positions": sorted(kind_positions),
                "tap_positions": sorted(tap_positions)}

    for name in list(P) + ["steer-target"]:
        content = P[name]["content"] if name in P else steer_content
        cue = P[name]["cue"] if name in P else STEER_CUES["steer-target"]
        for vtag, effort in (("cue_greedy", "off"), ("cue_high", "high")):
            ids, _ = live_render(tok, tpl, [{"role": "user", "content": f"{CUE_PREAMBLE} {cue}"}], effort)
            rows = rows16_stream(ids)
            kinds = [p for p, t in enumerate(ids) if int(t) in content and p >= 1]
            geom[f"{vtag}:{name}"] = observe(name, vtag, ids, rows, kinds, [len(ids) - 9]) | {}
    # the control cues ride the SAME venue shape but get NO writes (the runner's clean controls);
    # the gates walk them to prove they read zero written rows.

    # ---------------- stop-form rejection (v2's conservative rule, verbatim)
    keys, rejected = {}, collections.Counter()
    for key, e in sorted(dst.items()):
        generic = [w for w in e["windows"] if not (is_content(w[0]) or is_content(w[1]))]
        if generic:
            rejected["window_without_content_token"] += 1
            continue
        keys[key] = e

    # ---------------- writes: tap <- emit-pre (markers) / steer src; content <- gtail / steer src
    writes, collisions = [], []
    seen = set()

    def src_for(name, kind):
        if name == "steer-target":
            return p_src, "steer_src", " new"
        if kind == "tap":
            return P[name]["gpre"], "emit_pre", P[name]["src_pre_tok"]
        return P[name]["gtail"], "name_tail", P[name]["src_tail_tok"]

    shared_claims = []
    for (h, d), e in sorted(keys.items()):
        for name in sorted(e["names"]):
            kinds = sorted(e["why"])
            kind = "tap" if "tap" in kinds else "content"
            pos, s_kind, s_tok = src_for(name, kind)
            if (h, d) in seen:
                # v3's whole design rests on disjoint arms: one row claimed by two names is a
                # venue flaw, not a first-write-wins nuisance (v2's rule) - fail the build.
                shared_claims.append({"dst_row": d, "head": h, "claimant": name})
                continue
            seen.add((h, d))
            s = int(P[name]["f_rows"][pos][h]) if name in P else int(s_rows[p_src][h])
            writes.append({"name": name, "pos": pos, "head": h, "src_kind": s_kind,
                           "src_tok": s_tok, "kinds": kinds,
                           "venue": sorted({v for vs in e["why"].values() for v in vs}),
                           "src_row": s, "dst_row": d, "dst_pos": ple.row_file_pos(info, d)})
    assert not shared_claims, f"v3 requires disjoint dst rows per arm/pair, got {shared_claims[:8]}"
    collisions = shared_claims

    plan = {"src_table": STOCK, "dst_copy": DST, "row_bytes": ple.ROW_BYTES, "n_rows": info["n_rows"],
            "writes": writes, "collisions": collisions,
            "note": ("build_d2_plan_v3.py (NOTES.md §8 cheapest decisive experiment): (a) steer "
                     "probe = the 'Happy New' window value onto a fresh rare cue's tap+content "
                     "rows; (b) emit-window recipe = marker TAP rows source the emit-pre window "
                     "(H6), content rows keep v2's name-tail source; venues = v2's cue_greedy + "
                     "cue_high (M-block dropped, dead channel); arms disjoint by assert")}
    per = collections.defaultdict(lambda: {"keys": set(), "heads": set(), "tap_heads": set()})
    for w in writes:
        per[w["name"]]["keys"].add((w["head"], w["dst_row"]))
        per[w["name"]]["heads"].add(w["head"])
        if "tap" in w["kinds"]:
            per[w["name"]]["tap_heads"].add(w["head"])
    g1 = {n: {"heads": len(v["heads"]), "keys": len(v["keys"]), "tap_heads": len(v["tap_heads"]),
              "pass": len(v["heads"]) == 16 and len(v["tap_heads"]) == 16}
           for n, v in per.items()}

    out = {"n_writes": len(writes), "unique_dst_rows": len({w["dst_row"] for w in writes}),
           "n_keys": len(keys), "rejected": dict(rejected),
           "marker_cue": {n: P[n]["cue"] for n in P}, "steer_cues": STEER_CUES,
           "src_tokens": {n: {"tap": P[n]["src_pre_tok"], "content": P[n]["src_tail_tok"]} for n in P}
                         | {"steer-target": {"tap": " new", "content": " new",
                                             "continuation": cont}},
           "venue_geometry": geom, "g1": g1}
    Path(a.out).write_text(json.dumps(plan, indent=1, sort_keys=True))
    (HERE / "state").mkdir(exist_ok=True)
    (HERE / "state/v3_build.json").write_text(json.dumps(out, indent=1, default=list))
    assert all(g["pass"] for g in g1.values()), f"g1 gate FAILED: {g1}"
    print(json.dumps({k: out[k] for k in ("n_writes", "unique_dst_rows", "n_keys", "rejected", "g1")},
                     indent=1, default=list))
    print(f"OK {a.out}: {len(writes)} writes, {out['unique_dst_rows']} distinct rows, "
          f"{len(keys)} keys, rejected {dict(rejected)}")


if __name__ == "__main__":
    main()
