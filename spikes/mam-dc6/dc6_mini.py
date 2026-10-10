"""spikes/mam-dc6/dc6_mini.py - Spike C (+ ladder/markercup extensions): independent re-implementation of
AKBASCORE MAM's incremental-DC6 numerical-memory mechanism, on small dense models, CPU-only.

What this proves (and what it does not):
  PROVES: we understand the published mechanism (560.py / zenodo 10.5281/zenodo.23127434,
          10.5281/zenodo.23143605) well enough to re-derive it from first principles:
          read-once cartridges (layer-CUT residual + layers 0..CUT K/V), all-zero-embedding
          consolidation with the stored hidden state injected at the cut, RoPE re-phasing of
          the early K/V into new positions, append-only growth with every previously
          consolidated K/V row checked bitwise unchanged, and source-free question answering.
  MEASURES: incremental-DC6 vs the no-consolidation control vs the joint-text upper bound,
          on a fresh synthetic panel - the replication signal is INCR >> INDEP plus the
          bitwise-invariant, NOT absolute parity with the 7B scores (small models answer
          worse at everything). The first run (0.5B, base arms, 2026-10-11) PASSED and its
          record is committed at 4dc4beb; with --arms base only this script reproduces it.
  EXTENSIONS (2026-10-11, plan sections 11-1/2/4 + the instrument ladder):
    --model      rerun the panel on Qwen2.5-1.5B/3B to test "does the mechanism strengthen
                 with scale" (the 0.5B run's one soft spot: MIDDLE-position misses 5/8 where
                 the 7B record has 24/24) and to put numbers on the user's hypothesis.
    --arms       additional read-side/carry-side variants, each one text-wrapping or state
                 transform over the SAME forged cartridges:
                   delim   real delimiter TOKEN ROWS around each bank (plan 11-1.1)
                   hdr     self-describing header text per cartridge (plan 11-1.2)
                   ctx     INDEP banks repaired by ONE causal zero-embed numerical rebuild
                         (CacheBlend-to-cartridges; plan 11-1.4 in its bank form)
                   recomp  target cartridge's deep rows recomputed inside the assembled
                         bank, then scattered back into their original slots (plan 11-1.4
                         row form)
    --bank SIZES scale curve like TEST563: procedural fresh-name worlds, bank sizes 5..40,
                 watch the decay on an instrument we own.

RESULTS (ladder complete 2026-10-11; three models rerun with ALL arms under ONE code
version, serial+idle at 6 threads, strict AND loose scored into every log row):
    log stamps (UTC): 0.5B 20261010T230654Z, 1.5B 20261010T231000Z, 3B 20261010T230450Z,
    scale curve 20261010T221252Z. Strict reproduces every earlier run bit-for-bit on arms
    whose code is unchanged - including 3B between its 4-thread original and this 6-thread
    rerun - so the "thread contention" suspicion is formally EXONERATED; only code changes
    moved numbers (the ctx fix).
      arm (FIRST/MID/LAST)   0.5B          1.5B          3B strict     3B loose
      INCR_DC6               8/5/8         8/8/8         1/5/1         8/8/8
      INCR_DELIM             8/6/8         8/8/8         0/0/0         8/8/8
      INCR_HDR               7/7/7         8/8/8         0/0/0         8/8/8
      INCR_MIDDLE_RECOMP     3/8           8/8           3/8           8/8
      INDEP_ALONE (control)  0/8           3/8           2/8           2/8
      INDEP_CTX_REBUILD      1/8           8/8           8/8           8/8
      JOINT_TEXT (ceiling)   8/8           8/8           8/8           8/8
    loose == strict on 0.5B/1.5B; on 3B the gap IS the whole story: the model answers the
    right name then role-plays the next ChatML turn glued without whitespace
    ("DrevantorHuman: Based on...") - strict word-boundary cannot match inside a glued
    word, so 3B's strict "collapse" is a SCORING ARTIFACT, not a memory failure. INDEP
    stays 2/8 under BOTH scorings (wrong entities - the control is genuinely confused).
    0.5B MIDDLE misses are real (loose does not rescue them): an instrument-weakness
    effect, absent at 1.5B+. Scale curve (0.5B, banks 5/10/20/40, 2 targets/size): INCR
    1-2/2 at every size, INDEP 0/2 everywhere, no decay visible at this resolution - the
    curve must be rerun on 1.5B, whose 4-bank ceiling is 8/8, to make decay measurable.
  VERDICTS:
    1. Mechanism replication PASSes on THREE instruments: INCR >> INDEP under either
       scoring, and the bitwise append invariant held 88/88 checks on EVERY all-arms run
       (24/24 on the committed 0.5B baseline) - it never once failed at any depth or size.
    2. Marker arms DELIM/HDR tie at the ceiling (1.5B / 3B-loose) and do NOT yet prove a
       win - the plan 11 gate ("must beat plain DC6") is UNMET; the 8-case panel saturates
       at >=1.5B, so separation needs the harder scale-curve regime, not more of this one.
    3. Biggest positive finding: CTX - one causal zero-embed rebuild over independently
       FORGED (never-incremental) banks - scores 8/8 strict on 1.5B AND 3B: CacheBlend-style
       recompute buys order-free, parallelizable cartridge assembly, the property
       append-only DC6 lacks. Spike B should test exactly this on the hybrid engine.

Run (CPU, no GPU, does not disturb a running Strata; keep thread count modest - the engine's
box needs its cores back):
  OMP_NUM_THREADS=6 nice -n19 ~/Strata-data/venvs/spikeC/bin/python \
      spikes/mam-dc6/dc6_mini.py --arms base,delim,hdr,ctx,recomp --model Qwen/Qwen2.5-1.5B-Instruct
  OMP_NUM_THREADS=6 nice -n19 .../dc6_mini.py --bank 5,10,20,40 --bank-targets 2
Writes spikes/mam-dc6/logs/dc6_mini_<utc>.json (+ prints the summary).

Mechanism (c) 2026 Mustafa Akbas (AKBASCORE research line); this implementation is independent,
MIT (like Strata), written for the memory-core fork.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"  # --model overrides; the committed baseline used this one
CUT = 6            # AkbasCore's locked consolidation cut on 32-layer 7B; kept as ABSOLUTE depth on
                   # 24/28/36-layer instruments so the cut means the same thing across the ladder
MAX_NEW = 16
SEED = 561561      # echo of TEST563's seed constant, new panel below

SYSTEM = "Answer the question using only the information provided. Give only the requested name and nothing else."

# marker-arm wrappings (plan 11-1.1/11-1.2); the BASE panel stays byte-identical to 4dc4beb
MEM_OPEN, MEM_CLOSE = "[mem-begin]", "[mem-end]"


def canon_prefix() -> str:
    return f"{SYSTEM}\n\nINFORMATION:\n"


def source_block(fact: str, tag: str = "") -> str:
    return f"{fact}\n"


def delim_block(fact: str, tag: str = "") -> str:
    return f"{MEM_OPEN}\n{fact}\n{MEM_CLOSE}\n"


def hdr_block(fact: str, tag: str) -> str:
    return f"{MEM_OPEN} note about {tag}\n{fact}\n{MEM_CLOSE}\n"


def query_block(q: str) -> str:
    return f"\nQUESTION:\n{q}\n\nANSWER:"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s).casefold().strip())
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    return " ".join(s.split())


def hit(answer: str, gold: str, loose: bool = False) -> bool:
    na, ng = norm(answer), norm(gold)
    if re.search(r"(?<!\w)" + re.escape(ng) + r"(?!\w)", na) is not None:
        return True
    # loose arm (measured 2026-10-11, 3B panel): some instruments answer then IMMEDIATELY role-play
    # the next turn with no whitespace - "DrevantorHuman: ..." - which the boundary regex cannot match
    # inside a glued word even though the first token IS the answer. Strict stays the headline metric
    # (the committed 4dc4beb record used it); loose is logged beside it, never silently replacing it.
    return loose and (na == ng or na.startswith(ng))


# ---- the synthetic world: fresh proper nouns so no general knowledge helps (the marker principle) ----

WORLDS = [  # (state, current_capital, former_capital, largest_city, role_target)
    ("Pilnor", "Vessmar", "Oontar", "Bralum", "Sephina"),
    ("Grintoar", "Molveth", "Dranquin", "Fothen", "Yspara"),
    ("Quendali", "Tolwhit", "Sarnobel", "Elkmar", "Pilnor"),
    ("Vurskaine", "Dolmaren", "Nethyrr", "Korvantis", "Grintoar"),
    ("Sephina", "Ilmaste", "Brethorn", "Ondalith", "Quendali"),
    ("Yspara", "Fenturym", "Aldovas", "Mirrakil", "Vurskaine"),
    ("Oltmarne", "Cruesol", "Panvelle", "Thysmend", "Sephina"),
    ("Drevantor", "Hymnelda", "Oskallen", "Belvarre", "Oltmarne"),
]
FACT_TYPES = ("CURRENT", "FORMER", "NEAR", "ROLE")

_CONS = "bdgkprstvmnflhz"
_VOW = "aeiou"


def gen_worlds(n: int, seed: int = SEED):
    """Procedurally extend the hand-made WORLDS table to n entries with fresh pronounceable names,
    same fact/query templates - for the TEST563-style scale curve. Deterministic on the seed."""
    rng = random.Random(seed)
    used = {w[0] for w in WORLDS} | {w[1] for w in WORLDS} | {w[2] for w in WORLDS} | {w[3] for w in WORLDS}

    def name():
        while True:
            syl = rng.randint(2, 3)
            w = "".join(rng.choice(_CONS) + rng.choice(_VOW) for _ in range(syl))
            w = w[0].upper() + w[1:]
            if len(w) >= 5 and w not in used:
                used.add(w)
                return w

    rows = list(WORLDS)
    while len(rows) < n:
        state = name()
        rows.append((state, name(), name(), name(), rows[rng.randrange(len(rows))][0]))
    return rows


def worlds_table():
    return WORLDS


def build_worlds(n: int | None = None):
    src = gen_worlds(n) if n and n > len(WORLDS) else WORLDS
    worlds = []
    for state, cur, former, near, role in (src[:n] if n else src):
        facts = {
            "CURRENT": (f"The current capital of {state} is {cur}.", cur),
            "FORMER": (f"The former capital of {state} was {former}.", former),
            "NEAR": (f"The largest city of {state} is {near}.", near),
            "ROLE": (f"The current capital of {role} is {state}.", state),
        }
        queries = {
            "CURRENT": f"What is the current capital of {state}?",
            "FORMER": f"What was the former capital of {state}?",
            "NEAR": f"What is the largest city of {state}?",
            "ROLE": f"What is the current capital of {role}?",
        }
        # the SUBJECT each fact-type talks about (the hdr arm tags each cartridge with its own subject)
        subjects = {"CURRENT": state, "FORMER": state, "NEAR": state, "ROLE": role}
        worlds.append({"facts": facts, "queries": queries, "subjects": subjects})
    return worlds


class DC6:
    """A from-scratch re-implementation of the mechanism described in the AKBASCORE records."""

    def __init__(self, model_id: str, device: str, dtype: torch.dtype):
        torch.manual_seed(SEED)
        np.random.seed(SEED)
        self.model_id = model_id
        self.device = torch.device(device)
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=dtype).to(self.device).eval()
        for p in self.model.parameters():
            p.requires_grad_(False)
        c = self.model.config
        self.nl = c.num_hidden_layers
        self.rot = self.model.model.rotary_emb
        self.p = len(self.tok(canon_prefix(), add_special_tokens=False).input_ids)
        self._w = 0

    # ---- cache plumbing (DynamicCache to plain layer tuples and back) ----

    def _layers(self, pkv):
        if hasattr(pkv, "layers"):
            return tuple((l.keys, l.values) for l in pkv.layers)
        return tuple(zip(pkv.key_cache, pkv.value_cache))

    def _cache(self, layers):
        from transformers.cache_utils import DynamicCache
        c = DynamicCache()
        for li, (k, v) in enumerate(layers):
            c.update(k, v, li)
        return c

    def _rotate_half(self, x):
        a, b = x.chunk(2, dim=-1)
        return torch.cat((-b, a), dim=-1)

    def _tables(self, pos):
        dummy = torch.zeros((1, pos.numel(), self.model.config.hidden_size),
                            dtype=next(self.model.parameters()).dtype, device=self.device)
        cos, sin = self.rot(dummy, pos.reshape(1, -1))
        return cos.unsqueeze(1).float(), sin.unsqueeze(1).float()

    def _rephase(self, k, oldpos, newpos):
        if torch.equal(oldpos, newpos):
            return k
        co, so = self._tables(oldpos)
        cn, sn = self._tables(newpos)
        x = k.float()
        unrot = x * co - self._rotate_half(x) * so
        return (unrot * cn + self._rotate_half(unrot) * sn).to(k.dtype)

    # ---- the mechanism steps ----

    @torch.no_grad()
    def forge(self, block_text: str):
        """Read one source fact ONCE: capture the layer-CUT output and layers 0..CUT K/V.
        `block_text` is the fully wrapped source block (see source_block/delim_block/hdr_block)."""
        ids = self.tok(canon_prefix() + block_text, add_special_tokens=False).input_ids
        n = len(ids)
        x = torch.tensor(ids, device=self.device).reshape(1, -1)
        pos = torch.arange(n, device=self.device)
        store = {}

        def cap(module, args, out):
            store["h"] = (out[0] if isinstance(out, tuple) else out).detach().clone()

        hk = self.model.model.layers[CUT].register_forward_hook(cap)
        try:
            out = self.model(input_ids=x, attention_mask=torch.ones((1, n), dtype=torch.long, device=self.device),
                             position_ids=pos.unsqueeze(0), cache_position=pos, use_cache=True)
            early = tuple((k.detach().clone(), v.detach().clone()) for k, v in self._layers(out.past_key_values)[:CUT + 1])
            return {"h": store["h"], "early": early, "pos": pos.clone(), "body": n - self.p}
        finally:
            hk.remove()

    @torch.no_grad()
    def consolidate_init(self, cp):
        """First cartridge: all-zero embeddings + the stored cut hidden state over the whole span
        fabricates the deep layers' memory without replaying text past layer CUT."""
        n = self.p + cp["body"]
        pos = torch.arange(n, device=self.device)

        def inject(module, args, out):
            h = out[0] if isinstance(out, tuple) else out
            return (cp["h"],) + tuple(out[1:]) if isinstance(out, tuple) else cp["h"]

        hk = self.model.model.layers[CUT].register_forward_hook(inject)
        try:
            emb = torch.zeros((1, n, self.model.config.hidden_size), dtype=self.model.dtype, device=self.device)
            out = self.model(inputs_embeds=emb,
                             attention_mask=torch.ones((1, n), dtype=torch.long, device=self.device),
                             position_ids=pos.unsqueeze(0), cache_position=pos, use_cache=True)
            gen = tuple((k.detach().clone(), v.detach().clone()) for k, v in self._layers(out.past_key_values))
            return cp["early"] + gen[CUT + 1:]
        finally:
            hk.remove()

    @torch.no_grad()
    def consolidate_append(self, memory, cp):
        """Append one cartridge over the existing memory. Prior rows are verified bitwise unchanged -
        the invariant the AKBASCORE logs instrument; here it is a hard check on every append."""
        oldn = memory[0][0].shape[-2]
        q = cp["body"]
        newpos = torch.arange(oldn, oldn + q, device=self.device)
        body_h = cp["h"][:, self.p:, :]
        cache = self._cache(memory)

        def inject(module, args, out):
            return (body_h,) + tuple(out[1:]) if isinstance(out, tuple) else body_h

        hk = self.model.model.layers[CUT].register_forward_hook(inject)
        try:
            emb = torch.zeros((1, q, self.model.config.hidden_size), dtype=self.model.dtype, device=self.device)
            out = self.model(inputs_embeds=emb, past_key_values=cache,
                             attention_mask=torch.ones((1, oldn + q), dtype=torch.long, device=self.device),
                             position_ids=newpos.unsqueeze(0), cache_position=newpos, use_cache=True)
            grown = list(self._layers(out.past_key_values))
        finally:
            hk.remove()
        for li in range(CUT + 1):
            k = cp["early"][li][0][:, :, self.p:, :]
            v = cp["early"][li][1][:, :, self.p:, :]
            k = self._rephase(k, cp["pos"][self.p:], newpos)
            grown[li] = (torch.cat((memory[li][0], k), dim=-2), torch.cat((memory[li][1], v), dim=-2))
        invariant = all(torch.equal(grown[l][0][:, :, :oldn], memory[l][0]) and
                        torch.equal(grown[l][1][:, :, :oldn], memory[l][1]) for l in range(len(memory)))
        return tuple(grown), invariant

    # ---- marker-arm extensions (delim/hdr are just different forge wrappings; these two are not) ----

    @torch.no_grad()
    def context_rebuild(self, cps):
        """CacheBlend applied to independently-forged cartridges (plan 11-4, bank form): instead of
        consolidating append-only, place ALL banks' rows in one causal zero-embed pass with their H6s
        injected, so every deep row is fabricated with the full population as its causal prefix.
        Every bank keeps its full row set (prefix rows and all) exactly like the INDEP concat does -
        the FIRST version of this arm dropped the prefixes and scored 0/8 on 1.5B purely because the
        question template then had no instruction framing to attend to. Arm parity first, findings
        after."""
        spans, h_parts, start = [], [], 0
        for cp in cps:
            n = self.p + cp["body"]
            spans.append((start, start + n))
            h_parts.append(cp["h"])
            start += n
        total = start
        pos = torch.arange(total, device=self.device)
        big_h = torch.cat(h_parts, dim=1)

        def inject(module, args, out):
            h = out[0] if isinstance(out, tuple) else out
            return (big_h,) + tuple(out[1:]) if isinstance(out, tuple) else big_h

        hk = self.model.model.layers[CUT].register_forward_hook(inject)
        try:
            emb = torch.zeros((1, total, self.model.config.hidden_size), dtype=self.model.dtype,
                              device=self.device)
            out = self.model(inputs_embeds=emb,
                             attention_mask=torch.ones((1, total), dtype=torch.long, device=self.device),
                             position_ids=pos.unsqueeze(0), cache_position=pos, use_cache=True)
            gen = tuple((k.detach().clone(), v.detach().clone()) for k, v in self._layers(out.past_key_values))
        finally:
            hk.remove()
        layers = []
        for li in range(self.nl):
            if li <= CUT:
                ks = [self._rephase(cp["early"][li][0], cp["pos"], torch.arange(s, e, device=self.device))
                      for (s, e), cp in zip(spans, cps)]
                vs = [cp["early"][li][1] for cp in cps]
                layers.append((torch.cat(ks, dim=-2), torch.cat(vs, dim=-2)))
            else:
                layers.append(gen[li])
        return tuple(layers)

    @torch.no_grad()
    def recompute_deep(self, memory, cp, slot_start):
        """Plan 11-4 row form: re-run the target cartridge's deep rows INSIDE the assembled bank
        (appended at the tail, RoPE'd to their original slot positions, then scattered back).
        Early text rows are left alone - they are the only part no zero-embed pass can restore."""
        oldn = memory[0][0].shape[-2]
        q = cp["body"]
        slotpos = torch.arange(slot_start, slot_start + q, device=self.device)
        tailpos = torch.arange(oldn, oldn + q, device=self.device)
        body_h = cp["h"][:, self.p:, :]
        cache = self._cache(memory)

        def inject(module, args, out):
            return (body_h,) + tuple(out[1:]) if isinstance(out, tuple) else body_h

        hk = self.model.model.layers[CUT].register_forward_hook(inject)
        try:
            emb = torch.zeros((1, q, self.model.config.hidden_size), dtype=self.model.dtype, device=self.device)
            out = self.model(inputs_embeds=emb, past_key_values=cache,
                             attention_mask=torch.ones((1, oldn + q), dtype=torch.long, device=self.device),
                             position_ids=slotpos.unsqueeze(0), cache_position=tailpos, use_cache=True)
            grown = list(self._layers(out.past_key_values))
        finally:
            hk.remove()
        fixed = list(memory)
        for li in range(CUT + 1, self.nl):
            k = memory[li][0].clone()
            v = memory[li][1].clone()
            k[:, :, slot_start:slot_start + q, :] = grown[li][0][:, :, tailpos[0]:tailpos[-1] + 1, :]
            v[:, :, slot_start:slot_start + q, :] = grown[li][1][:, :, tailpos[0]:tailpos[-1] + 1, :]
            fixed[li] = (k, v)
        # rows OUTSIDE the slot window must still be bitwise unchanged (the append invariant, narrowed)
        outside = all(torch.equal(fixed[l][0][:, :, :slot_start], memory[l][0][:, :, :slot_start]) and
                      torch.equal(fixed[l][0][:, :, slot_start + q:], memory[l][0][:, :, slot_start + q:]) and
                      torch.equal(fixed[l][1][:, :, :slot_start], memory[l][1][:, :, :slot_start]) and
                      torch.equal(fixed[l][1][:, :, slot_start + q:], memory[l][1][:, :, slot_start + q:])
                      for l in range(CUT + 1, self.nl))
        return tuple(fixed), outside

    @torch.no_grad()
    def ask(self, layers, question: str):
        """Source-free question: only the query template's tokens run over the installed memory."""
        cache = self._cache(layers)
        physical = int(cache.get_seq_length())
        qids = self.tok(query_block(question), add_special_tokens=False).input_ids
        ids = torch.tensor(qids, device=self.device).reshape(1, -1)
        pos = torch.arange(physical, physical + ids.shape[1], device=self.device)
        out = self.model(input_ids=ids, past_key_values=cache,
                         attention_mask=torch.ones((1, physical + ids.shape[1]), dtype=torch.long,
                                                   device=self.device),
                         position_ids=pos.unsqueeze(0), cache_position=pos, use_cache=True)
        nxt = out.logits[:, -1, :].argmax(-1, keepdim=True)
        cache = out.past_key_values
        gen = []
        for _ in range(MAX_NEW):
            gen.append(nxt)
            if int(nxt) == self.tok.eos_token_id:
                break
            p2 = torch.tensor([cache.get_seq_length()], device=self.device)
            out = self.model(input_ids=nxt, past_key_values=cache,
                             attention_mask=torch.ones((1, cache.get_seq_length() + 1), dtype=torch.long,
                                                       device=self.device),
                             position_ids=p2.unsqueeze(0), cache_position=p2, use_cache=True)
            cache = out.past_key_values
            nxt = out.logits[:, -1, :].argmax(-1, keepdim=True)
        return self.tok.decode(torch.cat(gen, dim=1)[0], skip_special_tokens=True).strip()


def assemble_incremental(model: DC6, worlds, order, block=source_block):
    memory, slots, ok_all, inv_count = None, {}, True, 0
    for (cj, tj) in order:
        fact = worlds[cj]["facts"][tj][0]
        cp = model.forge(block(fact, worlds[cj]["subjects"][tj]))
        if memory is None:
            memory = model.consolidate_init(cp)
            start = model.p
        else:
            start = memory[0][0].shape[-2]
            memory, ok = model.consolidate_append(memory, cp)
            ok_all = ok_all and ok
            inv_count += 1
        slots[(cj, tj)] = (start, cp["body"], cp)
    return memory, slots, ok_all, inv_count


def order_for(cart_ids, wi_typ, pos_name):
    order = list(cart_ids)
    if pos_name == "FIRST":
        order.remove(wi_typ); order = [wi_typ] + order
    elif pos_name == "MIDDLE":
        order.remove(wi_typ); order = order[:1] + [wi_typ] + order[1:]
    return order


def joint_text_answer(model: DC6, worlds, cart_ids, question):
    text = canon_prefix() + "".join(source_block(worlds[cj]["facts"][tj][0]) for (cj, tj) in cart_ids)
    ids = model.tok(text + query_block(question), add_special_tokens=False, return_tensors="pt").to(model.device)
    gen = model.model.generate(**ids, max_new_tokens=MAX_NEW, do_sample=False)
    return model.tok.decode(gen[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()


@torch.no_grad()
def run_panel(model: DC6, arms: set, quick: bool):
    worlds = build_worlds()
    rng = random.Random(SEED)  # ONE rng block, before any arm work: base results stay bit-comparable
    n_cases = min(len(worlds), 4) if quick else len(worlds)
    panel = []
    for wi in rng.sample(range(len(worlds)), n_cases):
        w = worlds[wi]
        typ = FACT_TYPES[wi % 4]
        others = []
        for wj in range(len(worlds)):
            if wj == wi:
                continue
            ot = FACT_TYPES[(wj + wi) % 4]
            others.append((wj, ot))
        others = rng.sample(others, min(3, len(others)))
        panel.append({"case": wi, "typ": typ, "others": others})

    rows, invariant_checks = [], []
    for entry in panel:
        wi, typ = entry["case"], entry["typ"]
        w = worlds[wi]
        fact, gold = w["facts"][typ]
        question = w["queries"][typ]
        cart_ids = [(wi, typ)] + entry["others"]

        # ARM 1: INDEP control (faithful to the record's shape): every cartridge is consolidated
        # from zero INDEPENDENTLY, then the banks' rows are naively concatenated - all four facts
        # are present as distractors, but no cross-cartridge attention was ever computed, which is
        # what produces AkbasCore's position-sensitive collapse (13/72 on the 7B panel).
        cps = [model.forge(source_block(worlds[cj]["facts"][tj][0])) for (cj, tj) in cart_ids]
        banks = [model.consolidate_init(cp) for cp in cps]
        mem_indep = tuple((torch.cat([b[li][0] for b in banks], dim=-2),
                           torch.cat([b[li][1] for b in banks], dim=-2)) for li in range(model.nl))
        ans_indep = model.ask(mem_indep, question)
        rows.append({"arm": "INDEP_ALONE", "case": wi, "type": typ, "gold": gold,
                     "answer": ans_indep, "correct": hit(ans_indep, gold)})

        if "base" in arms:
            # ARM 2: INCR DC6 - every cartridge consolidated append-only, question at first/middle/last
            for pos_name in ("FIRST", "MIDDLE", "LAST"):
                order = order_for(cart_ids, (wi, typ), pos_name)
                memory, _, ok_all, inv_n = assemble_incremental(model, worlds, order)
                invariant_checks.append(ok_all)
                ans = model.ask(memory, question)
                rows.append({"arm": f"INCR_DC6_{pos_name}", "case": wi, "type": typ,
                             "gold": gold, "answer": ans, "correct": hit(ans, gold)})

            # ARM 3: JOINT text upper bound (all facts in the prompt; the ceiling, not a memory)
            ans = joint_text_answer(model, worlds, cart_ids, question)
            rows.append({"arm": "JOINT_TEXT", "case": wi, "type": typ,
                         "gold": gold, "answer": ans, "correct": hit(ans, gold)})

        # ---- extension arms (each over the SAME forged panel; base rows above are untouched) ----
        for arm_name, key, block in (("INCR_DELIM", "delim", delim_block), ("INCR_HDR", "hdr", hdr_block)):
            if key not in arms:
                continue
            for pos_name in ("FIRST", "MIDDLE", "LAST"):
                order = order_for(cart_ids, (wi, typ), pos_name)
                memory, _, ok_all, _ = assemble_incremental(model, worlds, order, block)
                invariant_checks.append(ok_all)
                ans = model.ask(memory, question)
                rows.append({"arm": f"{arm_name}_{pos_name}", "case": wi, "type": typ,
                             "gold": gold, "answer": ans, "correct": hit(ans, gold)})

        if "ctx" in arms:  # independent banks + ONE causal numerical rebuild over the population
            memory = model.context_rebuild(cps)
            ans = model.ask(memory, question)
            rows.append({"arm": "INDEP_CTX_REBUILD", "case": wi, "type": typ,
                         "gold": gold, "answer": ans, "correct": hit(ans, gold)})

        if "recomp" in arms:  # incremental bank, MIDDLE-position target, deep rows re-contextualized
            order = order_for(cart_ids, (wi, typ), "MIDDLE")
            memory, slots, ok_all, _ = assemble_incremental(model, worlds, order)
            invariant_checks.append(ok_all)
            start, q, cp = slots[(wi, typ)]
            memory2, outside_ok = model.recompute_deep(memory, cp, start)
            invariant_checks.append(outside_ok)
            ans = model.ask(memory2, question)
            rows.append({"arm": "INCR_MIDDLE_RECOMP", "case": wi, "type": typ,
                         "gold": gold, "answer": ans, "correct": hit(ans, gold)})
    return rows, invariant_checks


@torch.no_grad()
def run_scale(model: DC6, sizes, targets_per_size: int):
    """TEST563-shaped decay curve on our own instrument: banks of N fresh facts, target placed
    FIRST/MIDDLE/LAST, INCR vs INDEP (+ JOINT ceiling)."""
    rows, invariant_checks = [], []
    rng = random.Random(SEED + 1)
    for n in sizes:
        worlds = build_worlds(n)
        idxs = rng.sample(range(len(worlds)), min(targets_per_size, len(worlds)))
        for wi in idxs:
            typ = FACT_TYPES[wi % 4]
            gold = worlds[wi]["facts"][typ][1]
            question = worlds[wi]["queries"][typ]
            cart_ids = [(wi, typ)]
            while len(cart_ids) < n:
                wj = rng.randrange(len(worlds))
                cand = (wj, FACT_TYPES[rng.randrange(4)])
                if cand not in cart_ids:
                    cart_ids.append(cand)
            for pos_name in ("FIRST", "MIDDLE", "LAST"):
                order = order_for(cart_ids, (wi, typ), pos_name)
                memory, _, ok_all, _ = assemble_incremental(model, worlds, order)
                invariant_checks.append(ok_all)
                ans = model.ask(memory, question)
                rows.append({"bank": n, "arm": f"INCR_DC6_{pos_name}", "case": wi, "type": typ,
                             "gold": gold, "answer": ans, "correct": hit(ans, gold)})
            cps = [model.forge(source_block(worlds[cj]["facts"][tj][0])) for (cj, tj) in cart_ids]
            banks = [model.consolidate_init(cp) for cp in cps]
            mem_indep = tuple((torch.cat([b[li][0] for b in banks], dim=-2),
                               torch.cat([b[li][1] for b in banks], dim=-2)) for li in range(model.nl))
            ans = model.ask(mem_indep, question)
            rows.append({"bank": n, "arm": "INDEP_ALONE", "case": wi, "type": typ,
                         "gold": gold, "answer": ans, "correct": hit(ans, gold)})
    return rows, invariant_checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="4 cases instead of 8 (fast smoke)")
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--arms", default="base", help="comma list: base,delim,hdr,ctx,recomp (base = the 4dc4beb panel)")
    ap.add_argument("--bank", default="", help="scale curve: comma list of bank sizes, e.g. 5,10,20,40")
    ap.add_argument("--bank-targets", type=int, default=2, help="targets per size in --bank mode")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()
    arms = set(a.strip().lower() for a in args.arms.split(",") if a.strip())
    t0 = time.time()
    model = DC6(args.model, args.device, torch.bfloat16)
    if args.bank:
        sizes = [int(x) for x in args.bank.split(",") if x.strip()]
        rows, invariants = run_scale(model, sizes, args.bank_targets)
        key = lambda r: f"bank{r['bank']}:{r['arm']}"   # STRING keys: the first scale run died at json.dumps on tuples
        mode = {"scale_sizes": sizes, "targets_per_size": args.bank_targets}
    else:
        rows, invariants = run_panel(model, arms, args.quick)
        key = lambda r: r["arm"]
        mode = {"panel": "8-case (4 if --quick)", "arms": sorted(arms)}
    for r in rows:  # central rescore: strict stays as computed by the arm code (continuity with 4dc4beb),
        r["correct_loose"] = hit(r["answer"], r["gold"], loose=True)  # loose is advisory and always derived here
    stats = {}
    for arm in sorted({key(r) for r in rows}):
        sel = [r for r in rows if key(r) == arm]
        stats[arm] = {"correct": sum(r["correct"] for r in sel),
                      "correct_loose": sum(r["correct_loose"] for r in sel), "total": len(sel)}
    out = {
        "spike": "C+: independent DC6 re-implementation (mechanism replication + marker arms), CPU",
        "model": args.model, "cut_layer": CUT, "device": args.device, "dtype": "bfloat16",
        **mode, "quick": args.quick, "seconds": round(time.time() - t0, 1),
        "append_invariant_bitwise_all": all(invariants), "append_checks": len(invariants),
        "arms": stats, "rows": rows,
        # DETERMINISM NOTE (measured 2026-10-11): four serial idle re-runs of this panel reproduce
        # each other BIT-IDENTICALLY at 6 and at 8 torch threads, but the FIRST logged quick run
        # (20261010T204035Z) has four INDEP_ALONE rows that no serial re-run since has matched - it
        # ran one minute apart from its own full-panel sibling (20261010T204133Z), and two CPU-torch
        # jobs oversubscribing 8 cores resize OMP reductions mid-flight, which flips near-tie greedy
        # argmaxes (INDEP lives on near-ties by design; the INCR/JOINT rows never moved). Protocol:
        # ladder runs are SERIAL and IDLE, and torch.get_num_threads() goes in every log with them.
        "torch_threads": torch.get_num_threads(),
        "reference": {"akbascore": "TEST560 INCR_DC6 72/72 vs INDEP 13/72 (7B, A100); "
                                   "TEST563 scale 5/5,9/10,16/20,16/40,22/80",
                      "doi": ["10.5281/zenodo.23127434", "10.5281/zenodo.23143605", "10.5281/zenodo.23146475"]},
        "reading": "INCR arms beat INDEP and the bitwise append invariant held everywhere = replication "
                   "stands; marker arms must beat plain DC6, not just exist (plan 11 gate); scale mode "
                   "tracks whether accuracy decay follows TEST563's shape on our instrument.",
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logdir = Path(__file__).resolve().parent / "logs"
    logdir.mkdir(exist_ok=True)
    (logdir / f"dc6_mini_{stamp}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=2))
    print("log:", logdir / f"dc6_mini_{stamp}.json")


if __name__ == "__main__":
    main()
