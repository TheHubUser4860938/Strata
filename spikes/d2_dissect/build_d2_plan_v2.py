#!/usr/bin/env python3
"""spikes/d2_dissect/build_d2_plan_v2.py - NOTES.md §6.1/§6.2: the v2 write PLAN, offline-built.

WHAT v1 GOT WRONG (FIRING.md §6, all measured on the live 283-token M-block render):
  1. payload: the min-length zip truncated every alias before its name-emitting fact window -
     ZERO gold rows among the 616 (0/80, 0/48, 0/48);
  2. reach: the fires sat 8-11 tokens behind each cold question mark, but the layer-1 dilated
     conv taps {P, P-3, P-6, P-9} only ever slide FORWARD from the position being decoded, and
     the greedy thinking-off generation tail ('<|im_end|>\n<|im_start|>assistant\n<think>\n\n
     </think>\n\n' = 9 ids) means the first answer token's taps are all tail rows - so content
     windows >= 10 tokens before the tail are NEVER read again during the answer, only via the
     layer-1 residual (route (c), unproven, and M1 showed nothing riding it at temp 0.6).

V2 THEREFORE WRITES GOLD ONTO THE ANSWER-REACHABLE WINDOWS:
  src (per head h): stock-table row gold_rows[last][h] - the window of the LAST fact position
  overlapping an expected answer string (archivist ' Marsh' pos 15, metalsmith 'wick' 12,
  recipe 'fish' 10) = the name tail's own trained row. One row, one value: v1's whole set of
  fragment rows is DROPPED (it demonstrably did nothing and it costs collateral).
  dst (per head h), from windows MEASURED in real venue renders (every venue's token count is
  byte-matched to an engine-logged prompt size in venue_geometry.py / fire_analysis.py):
    t1 = the window at position n-9 of the cue venue - the FIRST TAP POSITION of the first
         generated token (taps {n, n-3, n-6, n-9}); its tokens are (<|im_end|>, last cue
         token), and the cue is chosen so its last token is RARE (a marker-content id that is
         not a stopword form) - this is §4c-v2b's "place the alias so its window lands at the
         FINAL tap position", made exact;
    c1 = every window of the cue venue whose token is a marker-content id (the local-effect
         route: these fire while the model READS the cue, <= 9 tokens of influence forward);
    m1 = the same content windows inside the M-block 9-question stack venue (the channel the
         v1 verdict was taken in - kept so a v2 run can be compared to M1 without re-pasting).
  Venues per pair: cue-high (webapp defaults) + cue-greedy (probe /v1 shape) + m_block.

STOPWORD-FORM RULE (FIRING.md §5 measured the miss live: control Q6 read 8 written rows
because ` national`/` food` are STOP words in SUBWORD form and v1's skip compared exact
standalone ids only): a dst key is REJECTED if ANY window observed for it is all-stopword,
under the shared rule in d2_rules.py (token text with the byte-BPE space marker stripped,
compared against the stop words themselves).

Output d2_plan_v2.json (schema spikes/ple-overlay/mk_ple_copy.py reads) + state/v2_build.json.
src_table = the STOCK shard: --apply creates a FRESH v2 copy; the 54.4 GB v1 venue stays inert
(§4d honesty: never re-measure a v2 plan against the v1 copy).
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
from build_d2_plan import STOP

STOCK = "Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
# pre-registered cue-venue preamble: instruction FIRST so the rare cue tokens stay adjacent to
# the generation tail (anything said after the cue would push it out of tap reach).
CUE_PREAMBLE = ("Name the person, place or thing each cue line is about. One line: the name "
                "only, or idk. No sentences, no explanations.\n\nCue:")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "d2_plan_v2.json"))
    a = ap.parse_args()
    tok = load_tokenizer()
    tpl = load_template()
    mk = json.loads((ROOT / "spikes/window/markers.json").read_text())
    info = ple.table_info(str(ROOT / STOCK))

    # one definition, shared with the offline gates (spikes/d2_dissect/d2_rules.py): the
    # token's TEXT (byte-BPE space marker stripped) compared against the stop words - v1's
    # exact-token-id comparison missed the ` national` / ` food` / ` city` subword forms.
    stop_form, is_content = make_rules(tok)

    # ---------------- per-pair fact gold window + marker content ids
    P = {}
    for pr in mk["pairs"]:
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        f_rows = rows16_stream(f_ids)
        ftxt = tok.decode(f_ids)
        assert ftxt == pr["fact_text"], "fact round-trip"
        spans, c = [], 0
        for i in f_ids:
            s = tok.decode([i]); spans.append((c, c + len(s))); c += len(s)
        gpos = sorted({i for e in pr["expect"] for j in (ftxt.find(e),) if j >= 0
                       for i, (lo, hi) in enumerate(spans) if lo < j + len(e) and hi > j})
        assert gpos, f"{pr['name']}: no gold position found in its own fact text"
        content = set()
        for al in pr["marker_texts"]:
            content.update(int(x) for x in tok.encode(al, parse_special=True))
        P[pr["name"]] = {
            "pr": pr, "f_ids": f_ids, "f_rows": f_rows,
            "gpos": gpos, "gtail": max(gpos),
            "content": {i for i in content if is_content(i)},
            "aliases": {al: tok.encode(al, parse_special=True) for al in pr["marker_texts"]},
            # cue = the alias whose LAST token is a content id (nothing may sit between the
            # rare token and the generation tail); densest alias wins ties.
            "cue": None,
        }
        cands = [al for al, ids in P[pr["name"]]["aliases"].items()
                 if int(ids[-1]) in P[pr["name"]]["content"]]
        assert cands, f"{pr['name']}: no alias ends on a rare content token - cue venue impossible"
        P[pr["name"]]["cue"] = min(cands, key=lambda al: sum(
            not is_content(int(x)) for x in P[pr["name"]]["aliases"][al]))
        print(f"{pr['name']}: gold positions {gpos} (src = {tok.decode([f_ids[max(gpos)]])!r}), "
              f"cue = {P[pr['name']]['cue']!r}")

    # ---------------- venues -> candidate dst keys
    blocks = [m.group(1) for m in re.finditer(r"```\n(.*?)\n```",
              (ROOT / "spikes/window/PASTE.md").read_text(), re.S)]
    m_block = blocks[0]
    dst = collections.defaultdict(lambda: {"names": set(), "why": collections.defaultdict(set),
                                           "windows": set()})
    venue_map = {}     # (name, vtag) -> list of positions carrying gold-key taps

    def observe(name, vtag, ids, rows, kind_positions, tap_positions):
        n = len(ids)
        out = {"n_tokens": n, "kind_positions": sorted(kind_positions), "tap_positions": sorted(tap_positions)}
        for p, kind in [(p, "content") for p in kind_positions] + [(p, "tap") for p in tap_positions]:
            t0, t1 = int(ids[p]), (int(ids[p - 1]) if p >= 1 else -1)
            for h in range(16):
                e = dst[(h, int(rows[p][h]))]
                e["names"].add(name)
                e["why"][kind].add(vtag)
                e["windows"].add((t0, t1))
            venue_map.setdefault((name, vtag), {}).setdefault(kind, []).append(p)
        return out

    geom = {}
    for name, d in P.items():
        cue = d["cue"]
        for vtag, text, effort in (("cue_high", f"{CUE_PREAMBLE} {cue}", "high"),
                                   ("cue_greedy", f"{CUE_PREAMBLE} {cue}", "off")):
            ids, prompt = live_render(tok, tpl, [{"role": "user", "content": text}], effort)
            rows = rows16_stream(ids)
            kinds = [p for p, t in enumerate(ids) if int(t) in d["content"] and p >= 1]
            taps = [len(ids) - 9]                 # first generated token's deepest tap
            geom[f"{vtag}:{name}"] = observe(name, vtag, ids, rows, kinds, taps) | {"n": len(ids)}
    # the M-block stack venue (the channel M1's verdict was taken in): content windows only
    ids, prompt = live_render(tok, tpl, [{"role": "user", "content": m_block}], "high")
    rows = rows16_stream(ids)
    for name, d in P.items():
        kinds = [p for p, t in enumerate(ids) if int(t) in d["content"] and p >= 1]
        geom[f"m_block:{name}"] = observe(name, "m_block", ids, rows, kinds, []) | {"n": len(ids)}

    # ---------------- STOP-form rejection (conservative: ANY observed all-stopword window)
    keys, rejected = {}, collections.Counter()
    for key, e in sorted(dst.items()):
        # conservative: a key is written only if EVERY window that produced it carries at
        # least one content token (stopword forms and punctuation-only ids are not content)
        generic = [w for w in e["windows"] if not (is_content(w[0]) or is_content(w[1]))]
        if generic:
            rejected["window_without_content_token"] += 1
            continue
        keys[key] = e

    # ---------------- writes: gold tail row per head, one row per key per pair
    writes, collisions = [], []
    for (h, d), e in sorted(keys.items()):
        for name in sorted(e["names"]):
            gp = P[name]["gtail"]
            if (h, d) in {(w["head"], w["dst_row"]) for w in writes if w["dst_row"] == d}:
                collisions.append({"dst_row": d, "head": h, "dropped_pair": name})
                continue
            s = int(P[name]["f_rows"][gp][h])
            writes.append({"name": name, "pos": gp, "head": h, "gold_pos": gp,
                           "gold_tok": tok.decode([P[name]["f_ids"][gp]]).strip(),
                           "venue": sorted({v for vs in e["why"].values() for v in vs}),
                           "kinds": sorted(e["why"]),
                           "src_row": s, "dst_row": d, "dst_pos": ple.row_file_pos(info, d)})
    plan = {"src_table": STOCK, "dst_copy": "", "row_bytes": ple.ROW_BYTES, "n_rows": info["n_rows"],
            "writes": writes, "collisions": collisions,
            "note": ("build_d2_plan_v2.py (NOTES.md §6, v2b cue-last): gold-tail src rows onto "
                     "answer-reachable windows (tap position n-9 of the cue venue + cue content "
                     "windows + M-block content windows); all-stopword keys rejected; src_table = "
                     "stock shard, --apply creates a FRESH v2 copy (v1 venue stays inert, §4d)")}

    # ---------------- summary + g1 (gold presence per head band)
    per_pair = collections.defaultdict(lambda: {"keys": set(), "heads": set(), "tap_keys": 0})
    for w in writes:
        per_pair[w["name"]]["keys"].add((w["head"], w["dst_row"]))
        per_pair[w["name"]]["heads"].add(w["head"])
        if "tap" in w["kinds"]:
            per_pair[w["name"]]["tap_keys"] += 1
    g1 = {n: {"heads_with_gold": len(v["heads"]), "keys": len(v["keys"]), "tap_keys": v["tap_keys"],
              "pass": len(v["heads"]) == 16 and v["tap_keys"] >= 16} for n, v in per_pair.items()}
    out = {"n_writes": len(writes), "unique_dst_rows": len({w["dst_row"] for w in writes}),
           "n_keys": len(keys), "rejected": dict(rejected),
           "cue": {n: P[n]["cue"] for n in P},
           "venue_geometry": {k: {"n_tokens": v["n"], "content_positions": v["kind_positions"],
                                  "tap_positions": v["tap_positions"]} for k, v in geom.items()},
           "g1": g1}
    Path(a.out).write_text(json.dumps(plan, indent=1, sort_keys=True))
    (HERE / "state").mkdir(exist_ok=True)
    (HERE / "state/v2_build.json").write_text(json.dumps(out, indent=1, default=list))
    print(json.dumps(out, indent=1, default=list))
    assert all(g["pass"] for g in g1.values()), "g1 gate FAILED (16 head bands + tap key per pair)"
    print(f"OK {a.out}: {len(writes)} writes, {out['unique_dst_rows']} distinct rows, "
          f"{len(keys)} keys, rejected {dict(rejected)}")


if __name__ == "__main__":
    main()
