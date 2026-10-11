#!/usr/bin/env python3
"""spikes/d2_dissect/v3_gates.py - offline gates for d2_plan_v3.json (same discipline as
v2_gates.py: the gates RE-DERIVE the venue geometry, they do not trust the builder's word).

g2 FIRE-COVERAGE:
  a) each written name's cue venues (greedy + high) reproduce written keys - a written row can
     only act if the live prompt re-queries that exact (head,row);
  b) at the tap position (n-9) all 16 head bands carry a write whose recorded src token equals
     the arm's expected src (marker tap = the emit-pre window token, e.g. 'named'; marker
     content = the name-tail token; steer = ' new' with expected continuation 'year');
  c) the tap window is content-bearing (no all-stopword / punctuation-only tails).

g3 COLLATERAL:
  a) no written key was ever observed with an all-stopword window (v1's live miss);
  b) sidewalk: the 100 Gate-1 questions, B1/A1, the ordinary-English sanity cue (Glasgow), the
     three marker CONTROL cues and the two steer CONTROL cues - in their venue shapes - must
     read ZERO written rows (any hit = collateral on text the verdicts compare against);
  c) cross-name row sharing (v3 asserts disjointness at build; the gate re-checks from the
     plan file, because the plan - not the builder - is what mk_ple_copy applies).

Writes state/v3_gates.json; exit 1 on any failure.
"""
from __future__ import annotations
import collections, json, re, sys
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
from build_d2_plan_v3 import STEER_CUES
from v2_gates import B1_Q, A1_Q

SANITY_CUE = "Glasgow-University principal"


def main():
    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    plan = json.loads((HERE / "d2_plan_v3.json").read_text())
    built = json.loads((HERE / "state/v3_build.json").read_text())
    pair = {p["name"]: p for p in mk["pairs"]}
    keys = collections.defaultdict(list)
    for w in plan["writes"]:
        keys[(w["head"], w["dst_row"])].append(w)
    stop_form, is_content = make_rules(tok)

    # expected tap src token, RE-DERIVED here (the gate does not trust the builder's record):
    # the fact token immediately before the first gold span position, space marker stripped.
    expect_src = {}
    for nm, pr in pair.items():
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        ftxt = tok.decode(f_ids)
        spans, c = [], 0
        for i in f_ids:
            s = tok.decode([i]); spans.append((c, c + len(s))); c += len(s)
        g0 = min(i for e in pr["expect"] for j in (ftxt.find(e),) if j >= 0
                 for i, (lo, hi) in enumerate(spans) if lo < j + len(e) and hi > j)
        expect_src[nm] = ("tap", tok.decode([f_ids[g0 - 1]]).strip().lower())
    expect_src["steer-target"] = ("tap", "new")   # 'Happy New' window; continuation 'year' is
    # asserted from the src text itself, not from the builder:
    s_ids_chk = tok.encode("Happy New Year", parse_special=True)
    assert tok.decode([s_ids_chk[1]]).strip().lower() == "new" and \
        tok.decode([s_ids_chk[2]]).strip().lower() == "year", "steer src text re-derivation"
    g2, g3_windows, fail = {"venues": {}, "tap": {}}, {}, []

    def walk(msgs, effort, vtag):
        ids, prompt = live_render(tok, tpl, msgs, effort)
        rows = rows16_stream(ids)
        hits = collections.defaultdict(lambda: {"n": 0, "pos": set(), "vals": set()})
        for p, r16 in enumerate(rows):
            prev = int(ids[p - 1]) if p >= 1 else -1
            for h in range(16):
                key = (h, int(r16[h]))
                g3_windows.setdefault(key, set()).add((int(ids[p]), prev))
                for w in keys.get(key, []):
                    hits[w["name"]]["n"] += 1
                    hits[w["name"]]["pos"].add(p)
                    hits[w["name"]]["vals"].add(w["src_tok"])
        g2["venues"][vtag] = {nm: {"n": v["n"], "positions": sorted(v["pos"]),
                                   "carried_values": sorted(v["vals"])}
                              for nm, v in sorted(hits.items())}
        return ids, rows

    # ---- g2a/b/c: the written arms' own venues
    for nm in list(pair) + ["steer-target"]:
        cue = built["marker_cue"][nm] if nm in pair else STEER_CUES["steer-target"]
        g2["tap"][nm] = {}
        for vtag, effort in (("cue_greedy", "off"), ("cue_high", "high")):
            ids, rows = walk([{"role": "user", "content": f"{CUE_PREAMBLE} {cue}"}],
                             effort, f"{vtag}:{nm}")
            v = g2["venues"][f"{vtag}:{nm}"].get(nm, {"n": 0})
            if v["n"] == 0:
                fail.append(f"g2a {vtag} {nm}: venue reproduces no written key")
            tap = len(ids) - 9
            bands, vals = [], set()
            for h in range(16):
                ws = [w for w in keys.get((h, int(rows[tap][h])), []) if w["name"] == nm]
                if ws and "tap" in ws[0]["kinds"]:
                    bands.append(h)
                    vals.update(w["src_tok"] for w in ws)
            want = expect_src[nm][1]
            got = {v.strip().lower() for v in vals}
            g2["tap"][nm][vtag] = {"n_tokens": len(ids), "tap_pos": tap,
                                   "tap_window": [tok.decode([ids[tap]]), tok.decode([ids[tap - 1]])],
                                   "bands_written": len(bands), "src_values": sorted(vals)}
            if len(bands) != 16:
                fail.append(f"g2b {nm}/{vtag}: tap row written for {len(bands)}/16 bands")
            if got and got != {want}:
                fail.append(f"g2b {nm}/{vtag}: tap src values {sorted(vals)} != expected {want!r}")
            if not (is_content(int(ids[tap])) or is_content(int(ids[tap - 1]))):
                fail.append(f"g2c {nm}/{vtag}: tap window not content-bearing "
                            f"{g2['tap'][nm][vtag]['tap_window']}")

    # ---- g3b sidewalk: other text must read ZERO written rows
    blocks = [m.group(1) for m in re.finditer(r"```\n(.*?)\n```",
              (ROOT / "spikes/window/PASTE.md").read_text(), re.S)]
    paste = (ROOT / "spikes/window/PASTE.md").read_text()
    pm = re.search(r"(?:^> .*\n)+", paste[paste.index("## G1"):], re.M)
    preamble = "\n".join(l[2:].rstrip() for l in pm.group(0).rstrip("\n").split("\n"))
    sidewalk = {
        "g1_100q": ([{"role": "user", "content": preamble}] +
                    [{"role": "user", "content": b} for b in (blocks[3], blocks[4], blocks[5])], "high"),
        "b1_webapp": ([{"role": "user", "content": B1_Q}], "high"),
        "a1_webapp": ([{"role": "user", "content": A1_Q}], "high"),
        "sanity_cue": ([{"role": "user", "content": f"{CUE_PREAMBLE} {SANITY_CUE}"}], "off"),
    }
    for pr in mk["pairs"]:
        sidewalk[f"ctrl_{pr['name']}"] = ([{"role": "user",
                                            "content": f"{CUE_PREAMBLE} {pr['control_marker']}"}], "off")
    for nm in ("steer-ctrl-1", "steer-ctrl-2"):
        sidewalk[nm] = ([{"role": "user", "content": f"{CUE_PREAMBLE} {STEER_CUES[nm]}"}], "off")
    sw = {}
    for vtag, (msgs, effort) in sidewalk.items():
        walk(msgs, effort, f"sw_{vtag}")
        v = g2["venues"][f"sw_{vtag}"]
        n = sum(x["n"] for x in v.values())
        sw[vtag] = {"lookups_on_written_rows": n, "arms_touched": sorted(v),
                    "carried_values": sorted({t for x in v.values() for t in x["carried_values"]})}
        if n:
            fail.append(f"g3b sidewalk {vtag}: {n} lookups land on v3-written rows ({sorted(v)})")

    # ---- g3a windows + g3c sharing (re-derived from the plan file)
    no_content, weak = [], []
    by_row = collections.defaultdict(set)
    for w in plan["writes"]:
        by_row[w["dst_row"]].add(w["name"])
    for (h, d), ws in sorted(keys.items()):
        for (t0, t1) in g3_windows.get((h, d), ()):
            if not (is_content(t0) or is_content(t1)):
                no_content.append({"row": d, "head": h, "w": [tok.decode([t0]), tok.decode([t1])]})
            elif not (is_content(t0) and (is_content(t1) or t1 in (ple.PLE_EOS, -1))):
                weak.append({"row": d, "head": h, "w": [tok.decode([t0]), tok.decode([t1])]})
    shared = {str(k): sorted(v) for k, v in by_row.items() if len(v) > 1}
    if shared:
        fail.append(f"g3c cross-name shared rows: {list(shared)[:5]}")

    g3 = {"n_dst_keys": len(keys), "n_windows_without_content": len(no_content),
          "windows_without_content": no_content[:20], "n_weak_windows": len(weak),
          "sidewalk": sw, "cross_name_shared_rows": shared,
          "note": "stock occupancy of the dst rows: python3 spikes/ple-overlay/mk_ple_copy.py "
                  "--plan spikes/d2_dissect/d2_plan_v3.json --preflight"}
    out = {"g2": g2, "g3": g3, "failures": fail, "pass": not fail}
    (HERE / "state/v3_gates.json").write_text(json.dumps(out, indent=1, default=list))
    print(json.dumps({"g2_tap": g2["tap"],
                      "g2_venue_fires": {k: v for k, v in g2["venues"].items()
                                         if not k.startswith("sw_")},
                      "g3": {k: (v if not isinstance(v, list) or len(v) < 21 else
                                 {"n": len(v), "sample": v[:5]}) for k, v in g3.items()},
                      "failures": fail, "PASS": out["pass"]}, indent=1, default=list))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
