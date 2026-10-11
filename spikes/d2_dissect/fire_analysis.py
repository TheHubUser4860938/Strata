#!/usr/bin/env python3
"""spikes/d2_dissect/fire_analysis.py - NOTES.md §4(a)+(b): render the EXACT M-block (and G1
chunks) through the server's own live path - ChatTemplate + effort mapping + mark/unmark +
pack tokenizer with parse_special - then walk rows16 over the resulting prompt and intersect
(head, row) lookups with the 616 d2_plan dst rows.

Everything here reuses production code (serve.frontend, tools/strata_tokenizer, the oracle
mirror spikes/ple-overlay/ple_ngram.py); no hand-guessed tail tokens. CPU-only: reads the plan
JSON, never the 54 GB table.

Outputs (state/ next to NOTES.md):
  firing_table.json  - machine-readable: prompt render facts + every hit with provenance +
                       tap-class + per-line question map + collateral counts
  FIRING.md          - the §4(c) analysis + measured H1-H4 + verdict on the dominant miss cause

Ground truth to match for the M-block render: strata-dev.log L1229 (boot 2, first webapp
request after the patched-venue session came up): "prompt 283 tokens = 0 reused + 283 read".
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "spikes" / "ple-overlay"))
sys.path.insert(0, str(ROOT / "tools"))

import ple_ngram as ple
from serve import frontend as F

TOKDIR = ROOT / "Strata-data/packs/iq3_s/tokenizer"
M_EXPECT_TOKENS = 283          # strata-dev.log L1229, boot 2's first webapp request


# ---------------------------------------------------------------- §4(a): the live render, reused
def load_tokenizer():
    """Same construction as serve/server.py main() for a pack tokenizer dir (L6149-6157)."""
    import strata_tokenizer as ST
    vocab = json.loads((TOKDIR / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (TOKDIR / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = json.loads((TOKDIR / "token_type.json").read_text())
    return ST.Tokenizer(tokens, merges, types)


def extract_blocks(md_text: str) -> list[str]:
    """Every ```-fenced block in a PASTE.md section (verbatim, incl. the wrap newlines the
    browser kept when the user pasted the block as ONE message)."""
    return [m.group(1) for m in re.finditer(r"```\n(.*?)\n```", md_text, re.S)]


def live_render(tpl: F.ChatTemplate, tok, messages, effort_client: str):
    """Exactly Service.encode_prompt's path for an /v1/chat/completions webapp request with
    reasoning_effort=<settings.thinking> and no system message; returns (ids, prompt_str)."""
    kwargs = F.effort_kwargs(effort_client)                    # webapp "high" -> {"reasoning_effort":"xhigh"}
    marked, marked_tools, changed = F.mark_think_literals(messages, None, F.literal_tags(tok.control_tokens))
    assert not changed, "measurement blocks quote no think/control literals"
    prompt = tpl.render(marked, tools=None, **kwargs)         # Service.effort_end is False (no effort_position in strata-dev.json)
    ids = tok.encode(prompt, parse_special=True)
    return ids, prompt


def token_spans(tok, ids, prompt: str) -> list[tuple[int, int]]:
    """(start, end) char span in `prompt` per id, via forward `find` from the running position.
    Guarded: the concatenation of the token texts must equal the prompt string (true for this
    tokenizer - every control/USER_DEFINED token decodes to its own literal); a backward drift
    inside a newline run is harmless for line labelling and cannot cross a token boundary."""
    spans, pos = [], 0
    for i in ids:
        s = tok.decode([i])
        j = prompt.find(s, pos)
        if j < 0:
            raise SystemExit(f"span walk stuck at id {i} ({s!r}) pos {pos}")
        spans.append((j, j + len(s)))
        pos = j + len(s)
    assert "".join(tok.decode([i]) for i in ids) == prompt, "decoded stream != rendered prompt"
    return spans


def rows16_stream(ids: list[int]) -> list[tuple[int, ...]]:
    """rows16 per position with the sequence's own predecessors (== what prefill.cpp feeds:
    every prompt token gets its window, session start is the EOS-cut)."""
    return ple.rows_for_sequence(ids)


# ---------------------------------------------------------------- §4(b): firing + collateral
def line_of(qspans: list[tuple[int, int, str]], char: int) -> str:
    """Which numbered question line a prompt char falls in (content coordinates)."""
    for lo, hi, label in qspans:
        if lo <= char < hi:
            return label
    return "tail" if char >= qspans[-1][1] else "preamble"


def analyze(name: str, ids, rows, spans, plan_by_hrow, facts_by_hrow, content, content_prompt_off,
            question_lines, tok):
    content_off = content_prompt_off
    n = len(ids)
    prompt_end = n - 1                       # last prompt position == first decode position (thinking starts)
    hits = []
    coll = {}                                # pos -> [names]: prompt lookups landing on D2-overwritten rows
    for p, r16 in enumerate(rows):
        for h in range(16):
            key = (h, int(r16[h]))
            w = plan_by_hrow.get(key)
            if w is None:
                continue
            rec = {"pos": p, "head": h, "row": int(r16[h]), "name": w["name"], "marker_pos": w["pos"],
                   "marker_tok": tok.decode([w["marker"]]), "fact_pos": None, "fact_tok": None,
                   "dist_to_answer": prompt_end - p,
                   "tap_hit": (prompt_end - p) in (0, 3, 6, 9)}
            fb = facts_by_hrow.get((w["name"], w["pos"], h))
            if fb is not None:
                rec["fact_pos"], rec["fact_tok"] = fb
            hits.append(rec)
            c = coll.setdefault(p, {"names": set(), "keys": 0})
            c["names"].add(w["name"])
            c["keys"] += 1
    collateral = [{"pos": p, "names": sorted(v["names"]), "keys": v["keys"]} for p, v in sorted(coll.items())]
    # per-line label map for hits
    qspans = []
    cursor = 0
    for ln in question_lines:
        j = content.find(ln, cursor)
        qspans.append((j, j + len(ln), ln.split(".")[0]))
        cursor = j + len(ln)
    for rec in hits:
        s, e = spans[rec["pos"]]
        rec["q_line"] = line_of(qspans, s - content_off)
    return hits, collateral


def main():
    tok = load_tokenizer()
    tpl = F.ChatTemplate(TOKDIR / "chat_template.jinja")
    paste = (ROOT / "spikes/window/PASTE.md").read_text()
    blocks = extract_blocks(paste)
    m_block = blocks[0]
    plan = json.loads((ROOT / "spikes/window/d2_plan.json").read_text())
    markers = json.loads((ROOT / "spikes/window/markers.json").read_text())

    # provenance indexes over the 616 writes (head+row is the engine's full lookup key)
    plan_by_hrow = {}
    for w in plan["writes"]:
        plan_by_hrow[(w["head"], w["dst_row"])] = w
    facts_by_hrow = {}
    for pr in markers["pairs"]:
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        for i, r16 in enumerate(rows16_stream(f_ids)):
            for h in range(16):
                facts_by_hrow[(pr["name"], i, h)] = (i, tok.decode([f_ids[i]]))

    # --- §4(a): the live M-block render
    messages = [{"role": "user", "content": m_block}]
    ids, prompt = live_render(tpl, tok, messages, "high")
    spans = token_spans(tok, ids, prompt)
    # char offset where the (trimmed) user-turn content begins inside the rendered prompt string
    turn_start = prompt.index("<|im_start|>user\n")
    content_off = turn_start + len("<|im_start|>user\n")
    assert prompt[content_off:content_off + len(m_block.strip())] == m_block.strip(), "content map"
    rows = rows16_stream(ids)
    q_lines = [l for l in m_block.split("\n") if re.match(r"^\d+\.", l)]
    hits, collateral = analyze("M", ids, rows, spans, plan_by_hrow, facts_by_hrow, m_block, content_off, q_lines, tok)

    # gold-coverage per pair: fact positions whose token text carries a gold substring -> are their rows written?
    # (expect strings usually span SEVERAL tokens - "Quendefin" is " Qu"+"end"+"ef"+"in" - so a
    # token matches if its text is a substring of an expect string too, not only the reverse.)
    gold = {}
    written_src = {}
    for w in plan["writes"]:
        written_src.setdefault(w["name"], set()).add(w["src_row"])
    for pr in markers["pairs"]:
        f_ids = tok.encode(pr["fact_text"], parse_special=True)
        f_rows = rows16_stream(f_ids)
        ftxt = tok.decode(f_ids)
        assert ftxt == pr["fact_text"], "fact round-trip"
        spans_f, c = [], 0
        for i in f_ids:
            s = tok.decode([i])
            spans_f.append((c, c + len(s)))
            c += len(s)
        gpos = sorted({i for e in pr["expect"] for j in (ftxt.find(e),) if j >= 0
                       for i, (lo, hi) in enumerate(spans_f) if lo < j + len(e) and hi > j})
        grow = {int(f_rows[i][h]) for i in gpos for h in range(16)}
        gold[pr["name"]] = {"fact_len": len(f_ids), "gold_positions": gpos,
                            "gold_position_texts": [tok.decode([f_ids[i]]) for i in gpos],
                            "gold_rows": len(grow),
                            "gold_rows_written": len(grow & written_src.get(pr["name"], set()))}

    n_written = len(plan["writes"])
    n_dst = len(plan_by_hrow)
    fired_keys = {(h["head"], h["row"]) for h in hits}
    fired_names = {}
    for h in hits:
        fired_names.setdefault(h["name"], set()).add(h["pos"])
    per_pair_keys = {}
    for w in plan["writes"]:
        per_pair_keys.setdefault(w["name"], set()).add((w["head"], w["dst_row"]))
    fired_per_pair = {n: len(s & fired_keys) for n, s in per_pair_keys.items()}

    # --- G1 pass (approximate geometry, see FIRING.md): preamble + 3 chunk pastes, no assistant
    # replies (only the LAST message's replies are unarchived; earlier replies are in gate1_answers).
    pm = re.search(r"(?:^> .*\n)+", paste[paste.index("## G1"):], re.M)
    preamble = "\n".join(l[2:].rstrip() for l in pm.group(0).rstrip("\n").split("\n"))
    g1_msgs = [{"role": "user", "content": preamble}] + [{"role": "user", "content": b} for b in (blocks[3], blocks[4], blocks[5])]
    assert blocks[3].lstrip().startswith("1.") and blocks[4].lstrip().startswith("34.") \
        and blocks[5].lstrip().startswith("67."), "G1 chunk map"
    g_ids, g_prompt = live_render(tpl, tok, g1_msgs, "high")
    g_hits = []
    for p, r16 in enumerate(rows16_stream(g_ids)):
        for h in range(16):
            w = plan_by_hrow.get((h, int(r16[h])))
            if w:
                g_hits.append({"pos": p, "head": h, "row": int(r16[h]), "name": w["name"],
                               "marker_pos": w["pos"]})

    out = {
        "render": {"n_tokens": len(ids), "expect_tokens_log1229": M_EXPECT_TOKENS,
                   "tokens_match_log": len(ids) == M_EXPECT_TOKENS,
                   "tail_ids": ids[-8:], "tail_text": tok.decode(ids[-8:]),
                   "prompt_end_pos": len(ids) - 1},
        "writes": {"n_writes": n_written, "n_distinct_keys": n_dst,
                   "fired_keys_per_pair": fired_per_pair},
        "gold_coverage": gold,
        "hits": hits,
        "collateral_positions": collateral,
        "fired_keys": sorted(list(fired_keys)),
        "fired_positions_by_pair": {k: sorted(v) for k, v in fired_names.items()},
        "g1": {"n_tokens": len(g_ids), "hits": len(g_hits), "distinct_keys": len({(h["head"], h["row"]) for h in g_hits}),
               "detail": g_hits},
    }
    (HERE / "state").mkdir(exist_ok=True)
    (HERE / "state/firing_table.json").write_text(json.dumps(out, indent=1))
    print("M-block render:", len(ids), "tokens; log L1229 said", M_EXPECT_TOKENS,
          "->", "MATCH" if len(ids) == M_EXPECT_TOKENS else "MISMATCH")
    print("tail:", repr(tok.decode(ids[-8:])))
    print("hits (head,row) over 616 written keys:", len(hits),
          "distinct keys:", len(fired_keys), "of", n_dst)
    for name, ps in sorted(fired_names.items()):
        print(f"  {name}: {len(ps)} positions {sorted(ps)}; fired keys {fired_per_pair[name]}/{len(per_pair_keys[name])}")
    print("gold coverage:", json.dumps(gold))
    print("collateral prompt lookups on written rows:", len(collateral), "positions touched")
    tap = [h for h in hits if h["tap_hit"]]
    print("tap-hits to the FIRST decode position (thinking start):", len(tap),
          "lines:", sorted({h["q_line"] for h in tap}))
    print("G1 approx render:", len(g_ids), "tokens; hits", out["g1"]["hits"],
          "distinct", out["g1"]["distinct_keys"])


if __name__ == "__main__":
    main()
