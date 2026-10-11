#!/usr/bin/env python3
"""spikes/d2_dissect/v2_gates.py - NOTES.md §6.4 offline gates g2 + g3 for d2_plan_v2.json.

g2 FIRE-COVERAGE (each failure = the plan cannot be tested as specified):
  a) each venue's own windows reproduce the plan's keys (a written row can only help if the
     live prompt re-queries that exact (head,row) - NOTES.md §1);
  b) the value carried at every such fire is a GOLD token (v1's failure: fragments only);
  c) in the greedy thinking-off single-question venue at least one gold write sits on the
     FIRST answer token's tap positions {A, A-3, A-6, A-9} (A = last prompt position = the
     decode position of the first generated token, verified: strata-dev.log L1337 arithmetic
     prompt == generated_reused + generated + 1 for the greedy channel) - the mechanism-level
     precondition for a cold marker question to surface WITHOUT the batch/stack deferral
     (FIRING.md §3).

g3 COLLATERAL AUDIT (measure, don't hide):
  a) no dst window is all-stopword-form (v1 shipped ` national`/` food`/` city` subword forms
     past the STOP list - FIRING.md §5) and every dst window carries a marker-content token;
  b) sidewalk: how many plan keys are ALSO queried by text that is not the marker question -
     the 100 Gate-1 questions, the B1/A1 single questions - each such key is a place where
     D2-v2 overwrites trained signal another prompt reads (and every write additionally
     overwrites whatever the stock row holds: that count is mk_ple_copy --preflight's job);
  c) cross-pair row sharing (two pairs wanting one row = one value wins).

Writes state/v2_gates.json; exit 1 if any g2 check fails.
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

B1_Q = "Who is the fleetmaster of the Zorvane Convocation?"
A1_Q = "What time does the tidebell of Port Vasketh ring, and what is the keeper code?"


def main():
    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    plan = json.loads((HERE / "d2_plan_v2.json").read_text())
    pair = {p["name"]: p for p in mk["pairs"]}
    keys = collections.defaultdict(list)
    for w in plan["writes"]:
        keys[(w["head"], w["dst_row"])].append(w)

    stop_form, is_content = make_rules(tok)   # one definition (d2_rules.py), same as the builder

    pair_content = {p["name"]: {int(x) for al in p["marker_texts"]
                                for x in tok.encode(al, parse_special=True)
                                if is_content(int(x))} for p in mk["pairs"]}

    def is_gold(w):
        """A write is gold-carrying iff its source row is the window of a fact position that
        overlaps an expected answer string (the same span rule build_d2_plan_v2 used)."""
        if w.get("gold_pos", -1) < 0:
            return False
        pr = pair[w["name"]]
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        txt = tok.decode(f_ids)
        spans, c = [], 0
        for i in f_ids:
            s = tok.decode([i]); spans.append((c, c + len(s))); c += len(s)
        lo, hi = spans[w["gold_pos"]]
        return any(txt.find(e) >= 0 and lo < txt.find(e) + len(e) and hi > txt.find(e) for e in pr["expect"])

    # ---- venues (shared prompt texts) + the per-pair solo venues
    blocks = [m.group(1) for m in re.finditer(r"```\n(.*?)\n```",
              (ROOT / "spikes/window/PASTE.md").read_text(), re.S)]
    paste = (ROOT / "spikes/window/PASTE.md").read_text()
    pm = re.search(r"(?:^> .*\n)+", paste[paste.index("## G1"):], re.M)
    preamble = "\n".join(l[2:].rstrip() for l in pm.group(0).rstrip("\n").split("\n"))
    venues = {
        "m_block": ([{"role": "user", "content": blocks[0]}], "high"),
        "g1_100q": ([{"role": "user", "content": preamble}] +
                    [{"role": "user", "content": b} for b in (blocks[3], blocks[4], blocks[5])], "high"),
        "b1_webapp": ([{"role": "user", "content": B1_Q}], "high"),
        "a1_webapp": ([{"role": "user", "content": A1_Q}], "high"),
    }
    d2q = {p["name"]: p["question"] for p in mk["pairs"]}

    g2, g3_windows, fail = {"venues": {}, "solo": {}}, {}, []

    def src_text(w):
        """Token text whose window the write's SOURCE row is: the gold fact position for gold
        writes, the recorded fact token for v1-behind writes."""
        nm = w["name"]
        if w.get("gold_pos", -1) >= 0:
            return tok.decode([pair[nm]["_f_ids"][w["gold_pos"]]])
        return tok.decode([w["fact"]])

    def walk(msgs, effort, vtag):
        ids, prompt = live_render(tok, tpl, msgs, effort)
        rows = rows16_stream(ids)
        hits = collections.defaultdict(lambda: {"n": 0, "gold": 0, "vals": set(), "pos": set()})
        for p, r16 in enumerate(rows):
            prev = int(ids[p - 1]) if p >= 1 else -1
            for h in range(16):
                key = (h, int(r16[h]))
                g3_windows.setdefault(key, set()).add((int(ids[p]), prev))
                for w in keys.get(key, []):
                    rec = hits[w["name"]]
                    rec["n"] += 1
                    rec["pos"].add(p)
                    rec["vals"].add(src_text(w))
                    if is_gold(w):
                        rec["gold"] += 1
        g2["venues"][vtag] = {nm: {"n": v["n"], "gold_value_hits": v["gold"],
                                   "carried_values": sorted({t for t in v["vals"]}),
                                   "positions": sorted(v["pos"])}
                              for nm, v in sorted(hits.items())}
        return ids, rows

    for pr in mk["pairs"]:
        pr["_f_ids"] = tok.encode(pr["fact_text"], parse_special=True)

    for vtag, (msgs, effort) in venues.items():
        walk(msgs, effort, vtag)

    m = g2["venues"]["m_block"]
    for nm in pair:
        if m.get(nm, {"n": 0})["n"] == 0:
            fail.append(f"g2a m_block {nm}: 0 fires")
        if nm in m and m[nm]["gold_value_hits"] != m[nm]["n"]:
            fail.append(f"g2b m_block {nm}: {m[nm]['n'] - m[nm]['gold_value_hits']} fires carry NON-gold")

    # (c) CUE-LAST venues: the shape v2 is built for. Cue texts come from the builder's own
    # record (state/v2_build.json), so gate and build cannot disagree about the venue; the
    # tap position is re-derived here, not trusted from the builder.
    # Reach arithmetic (NOTES.md §1 taps {P,P-3,P-6,P-9} at layer 1): the first generated token
    # is decoded at position n (n = prompt ids), so the prompt positions its conv can read are
    # n-3, n-6, n-9. The greedy generation tail is 9 ids ('<|im_end|>' .. '</think>\n\n'), so
    # n-3/n-6 are TEMPLATE rows (global n-grams - writing gold there is hammering shared
    # signal, deliberately skipped) and n-9 = '<|im_end|>' + the LAST CUE TOKEN is the only
    # content-bearing reachable row. Hence the cue must END on a rare marker-content token.
    built = json.loads((HERE / "state/v2_build.json").read_text())
    g2["cue"] = {}
    for nm, cue in built["cue"].items():
        text = f"{CUE_PREAMBLE} {cue}"
        g2["cue"][nm] = {}
        for vtag, effort in (("cue_high", "high"), ("cue_greedy", "off")):
            ids, rows = walk([{"role": "user", "content": text}], effort, f"{vtag}:{nm}")
            tap = len(ids) - 9
            v = g2["venues"][f"{vtag}:{nm}"].get(nm, {"n": 0, "gold_value_hits": 0})
            if v["n"] == 0:
                fail.append(f"g2a {vtag} {nm}: cue venue reproduces no written key at all")
            if v["n"] and v["gold_value_hits"] != v["n"]:
                fail.append(f"g2b {vtag} {nm}: {v['n'] - v['gold_value_hits']} fires carry NON-gold")
            at, gold = [], []
            for h in range(16):
                ws = [w for w in keys.get((h, int(rows[tap][h])), []) if w["name"] == nm]
                if ws:
                    at.append(h)
                    if all(is_gold(w) for w in ws):
                        gold.append(h)
            g2["cue"][nm][vtag] = {
                "n_tokens": len(ids), "cue": cue, "tap_pos": tap,
                "tap_window": [tok.decode([ids[tap]]), tok.decode([ids[tap - 1]])],
                "all_bands_written": len(at), "all_bands_gold": len(gold),
                "content_positions": built["venue_geometry"][f"{vtag}:{nm}"]["content_positions"]}
            if len(gold) != 16:
                fail.append(f"g2c {nm}/{vtag}: tap row carries gold for {len(gold)}/16 head bands")
            if any(not is_content(int(x)) for x in (ids[tap], ids[tap - 1])):
                fail.append(f"g2c {nm}/{vtag}: cue-last tap window is not content-bearing "
                            f"({g2['cue'][nm][vtag]['tap_window']})")

    # CONTROL contamination (the offline analogue of M1's items 4-6, whose pass would VOID a
    # verdict): the control texts must never read a written row, neither in the cue shape nor
    # as the plain control question.
    g3_controls = {}
    for pr in mk["pairs"]:
        rows_out = []
        for label, text, effort in (("cue_high", f"{CUE_PREAMBLE} {pr['control_marker']}", "high"),
                                    ("cue_greedy", f"{CUE_PREAMBLE} {pr['control_marker']}", "off"),
                                    ("ask_greedy", pr["control_question"], "off"),
                                    ("ask_high", pr["control_question"], "high")):
            ids, rows = walk([{"role": "user", "content": text}], effort, f"ctrl_{label}:{pr['name']}")
            v = g2["venues"][f"ctrl_{label}:{pr['name']}"]
            n = sum(x["n"] for x in v.values())
            rows_out.append({"venue": label, "n_tokens": len(ids), "lookups_on_written_rows": n,
                             "pairs_touched": sorted(v),
                             "carried_values": sorted({t for x in v.values() for t in x["carried_values"]})})
            if n:
                fail.append(f"g3c control {pr['name']}/{label}: {n} lookups land on rows written for "
                            f"{sorted(v)} (control would be contaminated)")
        g3_controls[pr["name"]] = rows_out

    # informational: the QUESTION-FIRST greedy shape (what M1 used) - recorded to show the reach
    # deficit that drove the cue design, not a gate failure.
    g2["question_first_info"] = {}
    for nm, q in d2q.items():
        ids, rows = walk([{"role": "user", "content": q}], "off", f"qfirst_greedy:{nm}")
        reach = {len(ids) - 3, len(ids) - 6, len(ids) - 9}
        hits = sorted({p for p in range(len(ids)) for h in range(16)
                       if p in reach and keys.get((h, int(rows[p][h])))})
        g2["question_first_info"][nm] = {"n_tokens": len(ids), "reachable_positions": sorted(reach),
                                         "written_rows_reached_by_first_answer": len(hits)}

    # ---- g3
    bad_stop, no_content, weak, shared = [], [], [], []
    for (h, d), ws in sorted(keys.items()):
        nms = {w["name"] for w in ws}
        if len(nms) > 1:
            shared.append({"row": d, "head": h, "pairs": sorted(nms)})
        for (t0, t1) in g3_windows.get((h, d), ()):
            if not (is_content(t0) or is_content(t1)):
                no_content.append({"row": d, "head": h, "w": [tok.decode([t0]), tok.decode([t1])]})
            elif not (is_content(t0) and (is_content(t1) or t1 in (ple.PLE_EOS, -1))):
                weak.append({"row": d, "head": h, "w": [tok.decode([t0]), tok.decode([t1])]})
    sidewalk = {v: {"lookups_on_written_rows": sum(x["n"] for x in g2["venues"][v].values()),
                    "pairs_touched": sorted(g2["venues"][v]),
                    "carried_values": sorted({t for x in g2["venues"][v].values() for t in x["carried_values"]})}
                for v in venues if v != "m_block"}
    g3 = {"n_dst_keys": len(keys), "n_all_stopword_windows": len(bad_stop), "all_stopword_windows": bad_stop[:20],
          "n_weak_windows": len(weak), "weak_windows": weak[:20],
          "n_windows_without_marker_content": len(no_content), "windows_without_marker_content": no_content[:20],
          "cross_pair_shared_rows": shared, "sidewalk_other_text_reads_written_rows": sidewalk,
          "controls": g3_controls,
          "note": "stock-table occupancy of the dst rows (how many overwrite a non-equal trained row) "
                  "is measured by: python3 spikes/ple-overlay/mk_ple_copy.py --plan "
                  "spikes/d2_dissect/d2_plan_v2.json --preflight"}
    out = {"g2": g2, "g3": g3, "failures": fail, "pass": not fail}
    (HERE / "state/v2_gates.json").write_text(json.dumps(out, indent=1, default=list))
    print(json.dumps({
        "g2_cue": {nm: {k: {kk: vv for kk, vv in v.items() if kk != "content_positions"}
                        for k, v in ven.items()} for nm, ven in g2["cue"].items()},
        "g2_venues_other": {k: v for k, v in g2["venues"].items() if not k.startswith(("cue_", "ctrl_"))},
        "question_first_reach_info": g2["question_first_info"],
        "g3": {k: (v if not isinstance(v, list) or len(v) < 21 else
                   {"n": len(v), "sample": v[:5]}) for k, v in g3.items()},
        "failures": fail, "PASS": out["pass"]}, indent=1, default=list))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
