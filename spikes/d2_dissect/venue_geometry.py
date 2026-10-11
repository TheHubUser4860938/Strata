#!/usr/bin/env python3
"""spikes/d2_dissect/venue_geometry.py - NOTES.md §6.2/§6.4-g2 support: render every TEST VENUE
through the server's live path (venue_render) and record (a) token counts vs the engine's own
logged prompt sizes (ground truth that the render is the REAL prompt), (b) tail geometry -
which position is the first ANSWER token in the greedy thinking-off channel (so tap offsets
{P, P-3, P-6, P-9} from NOTES.md §1 become concrete positions), (c) which v1-plan (head,row)
keys fire in each venue and where.

Verified ground truths (strata-dev.log):
  M-block paste, thinking high      = 283 tok  (L1229, the M1 request)
  B1 paste (fleetmaster, thinking)  =  66 tok  (L1074)
  A1 paste (tidebell, thinking)     =  72 tok  (L1257)
  probe /v1 b1 fresh-ask, greedy    =  26 tok  (b1.jsonl ptok)
  probe /v1 a-check fresh-ask, gre. =  32 tok  (a-check.jsonl ptok)
Output: state/venue_geometry.json
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spikes" / "ple-overlay"))

import ple_ngram as ple
from venue_render import load_tokenizer, load_template, live_render, token_spans, rows16_stream

# ground-truth prompt sizes (see docstring)
EXPECT = {"m_block": 283, "b1_webapp": 66, "a1_webapp": 72, "b1_v1_greedy": 26, "a1_v1_greedy": 32}

B1_Q = "Who is the fleetmaster of the Zorvane Convocation?"
A1_Q = "What time does the tidebell of Port Vasketh ring, and what is the keeper code?"


def main():
    tok = load_tokenizer()
    tpl = load_template()
    paste = (ROOT / "spikes/window/PASTE.md").read_text()
    blocks = [m.group(1) for m in re.finditer(r"```\n(.*?)\n```", paste, re.S)]
    m_text = blocks[0]
    plan = json.loads((ROOT / "spikes/window/d2_plan.json").read_text())
    markers = json.loads((ROOT / "spikes/window/markers.json").read_text())
    plan_keys = {}                                   # (head,row) -> (name, marker_pos)
    for w in plan["writes"]:
        plan_keys.setdefault((w["head"], w["dst_row"]), (w["name"], w["pos"]))

    venues = {
        "m_block": ([{"role": "user", "content": m_text}], "high"),
        "b1_webapp": ([{"role": "user", "content": B1_Q}], "high"),
        "a1_webapp": ([{"role": "user", "content": A1_Q}], "high"),
        "b1_v1_greedy": ([{"role": "user", "content": B1_Q}], "off"),
        "a1_v1_greedy": ([{"role": "user", "content": A1_Q}], "off"),
    }
    # Decode-position convention (verified against the M-block: pos 282 = last prompt token =
    # the position whose window the FIRST generated token reads; strata-dev.log L1337 proves
    # prompt+N-reused+read == generated+1 for a greedy channel): a token generated after a
    # prompt of n ids is decoded AT position n-1 (its window = the last prompt id) and its
    # layer-1 conv taps read {n-1, n-4, n-7, n-10}. answer_start_pos = that decode position
    # of the first ANSWER token in the greedy thinking-off channel.
    out = {}
    for name, (msgs, effort) in venues.items():
        ids, prompt = live_render(tok, tpl, msgs, effort)
        rows = rows16_stream(ids)
        n = len(ids)
        tail = tok.decode(ids[-12:])
        # first answer position: greedy thinking-off tail ends '</think>\n\n' -> n-6 (verified
        # per venue by the '<|im_end|>' position + the empty-think block below)
        ans0 = None
        if effort == "off":
            tail_ids = ids[-8:]
            ttexts = [tok.decode([t]) for t in tail_ids]
            # expect ... im_end \n im_start assistant \n <think> \n\n </think> \n\n
            # (measured: '\\n\\n' tokenizes as ONE id on this tokenizer)
            assert ttexts[-4:] == ["<think>", "\n\n", "</think>", "\n\n"], \
                f"{name}: unexpected thinking-off tail {ttexts}"
            k = n + 1
            for t in reversed(ttexts):
                if t.strip() == "":
                    k -= 1
                else:
                    break
            ans0 = k                                   # decode position of the first ANSWER token
        fires = []
        for p, r16 in enumerate(rows):
            for h in range(16):
                k = plan_keys.get((h, int(r16[h])))
                if k:
                    fires.append({"pos": p, "head": h, "row": int(r16[h]), "name": k[0],
                                  "marker_pos": k[1]})
        out[name] = {"n_tokens": len(ids), "expect": EXPECT[name], "match": len(ids) == EXPECT[name],
                     "effort": effort, "prompt_end_pos": n - 1,
                     "answer_start_pos": ans0,
                     "tail_text": tail,
                     "fired_positions": sorted({f["pos"] for f in fires}),
                     "n_fires": len(fires), "fired_keys": sorted({(f["head"], f["row"]) for f in fires}),
                     "fires": fires}
        print(f"{name}: {len(ids)} tok expect {EXPECT[name]} -> {'MATCH' if len(ids)==EXPECT[name] else 'MISMATCH'}"
              f" | answer_start={ans0} fires={len(fires)}")
        assert len(ids) == EXPECT[name], name

    # marker content-position map for the marker questions in the v2b venue shape: which fired
    # positions are tap-reachable from the greedy answer start ({P,P-3,P-6,P-9})
    d2q = {"d2-archivist": "Who is the Voldranium-Archive-keeper?",
           "d2-metalsmith": "What is the largest town of the Klornish Reach?",
           "d2-recipe": "What is the Krevven national food?"}
    d2out = {}
    for name, q in d2q.items():
        ids, prompt = live_render(tok, tpl, [{"role": "user", "content": q}], "off")
        rows = rows16_stream(ids)
        n = len(ids)
        ttexts = [tok.decode([t]) for t in ids]
        assert ttexts[-4:] == ["<think>", "\n\n", "</think>", "\n\n"], f"tail {ttexts[-6:]}"
        k = n + 1
        for t in reversed(ttexts[-2:]):
            if t == "\n\n":
                k -= 1
            else:
                break
        ans0 = k
        taps = {ans0, ans0 - 3, ans0 - 6, ans0 - 9}
        fset = {}
        for p, r16 in enumerate(rows):
            for h in range(16):
                key = (h, int(r16[h]))
                fset.setdefault(key, []).append(p)
        pair_keys = {k2 for k2, v in plan_keys.items() if v[0] == name}
        fired = {k2: ps for k2, ps in fset.items() if k2 in pair_keys}
        d2out[name] = {"n_tokens": n, "answer_start_pos": ans0, "tap_positions": sorted(taps),
                       "fired_keys": len(fired),
                       "fired_at_tap": sorted({p for ps in fired.values() for p in ps if p in taps}),
                       "fired_positions": sorted({p for ps in fired.values() for p in ps}),
                       "keys_at_tap": sorted([k2 for k2, ps in fired.items() if any(p in taps for p in ps)])}
        print(f"v2b {name}: {n} tok, answer@{ans0}, fires {len(fired)} keys, "
              f"at-tap positions {d2out[name]['fired_at_tap']}")
    out["v2b_d2_questions"] = d2out

    (HERE / "state").mkdir(exist_ok=True)
    (HERE / "state/venue_geometry.json").write_text(json.dumps(out, indent=1, default=list))
    print("wrote state/venue_geometry.json")


if __name__ == "__main__":
    main()
