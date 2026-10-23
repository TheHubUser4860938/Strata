#!/usr/bin/env python3
"""spikes/d2_dissect/venue_render.py - the SERVER's live render path, as a reusable module
(NOTES.md §6.2 venue geometry). Everything here reuses production code exactly:
serve.frontend.ChatTemplate + effort_kwargs + mark_think_literals + the pack tokenizer built
like serve/server.py L6149-6157 (the SAME objects the running server holds).

Ground truths verified by fire_analysis.py (M-block, thinking-high channel) and
venue_geometry_check.py (single-question + greedy thinking-off channels): token counts match
the engine's logged prompt sizes for those exact requests in strata-dev.log.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from strata_control.datafolder import data_dir      # the single-root law (2026-10-23): packs live in the data folder

# The pack tokenizer the running server itself builds (serve/server.py L6149-6157). The symlink era
# spelled this repo/Strata-data; the symlink is gone, so the path is resolved, not guessed.
TOKDIR = data_dir(ROOT) / "packs/iq3_s/tokenizer"


def load_tokenizer():
    import strata_tokenizer as ST
    vocab = json.loads((TOKDIR / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (TOKDIR / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = json.loads((TOKDIR / "token_type.json").read_text())
    return ST.Tokenizer(tokens, merges, types)


def load_template():
    from serve import frontend as F
    return F.ChatTemplate(TOKDIR / "chat_template.jinja")


def live_render(tok, tpl, messages, effort_client: str):
    """Exactly Service.encode_prompt for a webapp-style /v1/chat/completions request (no system
    message unless given, no tools): effort_client is the RAW webapp/probe value ("high" ->
    xhigh template kwarg; "off"/"none" -> enable_thinking False -> '<think>\n\n</think>\n\n'
    empty block in the generation tail). Service.effort_end is False (no effort_position key in
    strata-dev.json), so the template renders as asked. -> (ids, prompt)."""
    from serve import frontend as F
    kwargs = F.effort_kwargs(effort_client)
    marked, marked_tools, changed = F.mark_think_literals(messages, None, F.literal_tags(tok.control_tokens))
    assert not changed, "measurement blocks quote no think/control literals"
    prompt = tpl.render(marked, tools=None, **kwargs)
    return tok.encode(prompt, parse_special=True), prompt


def token_spans(tok, ids, prompt: str):
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


def rows16_stream(ids):
    """rows16 per position with the sequence's own predecessors; the engine's window NEVER
    includes template tokens as text context beyond what is actually in ids, and position 0
    sees (-1,-1) -> the EOS cut (ngram.cpp transcription, NOTES.md §1)."""
    sys.path.insert(0, str(ROOT / "spikes" / "ple-overlay"))
    import ple_ngram as ple
    return ple.rows_for_sequence(ids)
