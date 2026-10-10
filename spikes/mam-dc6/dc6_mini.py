"""spikes/mam-dc6/dc6_mini.py — Spike C: independent re-implementation of AKBASCORE MAM's
incremental-DC6 numerical-memory mechanism, on a small dense model, CPU-only.

What this proves (and what it does not):
  PROVES: we understand the published mechanism (560.py / zenodo 10.5281/zenodo.23127434,
          10.5281/zenodo.23143605) well enough to re-derive it from first principles:
          read-once cartridges (layer-CUT residual + layers 0..CUT K/V), all-zero-embedding
          consolidation with the stored hidden state injected at the cut, RoPE re-phasing of
          the early K/V into new positions, append-only growth with every previously
          consolidated K/V row checked bitwise unchanged, and source-free question answering.
  MEASURES: on a 0.5B dense stand-in (AkbasCore evidence is 7B-class): incremental-DC6 vs
          the no-consolidation control vs the joint-text upper bound, on a fresh synthetic
          panel - the replication signal is INCR >> INDEP plus the bitwise-invariant, NOT
          absolute parity with the 7B scores (a 0.5B model answers worse at everything).
  DOES NOT PROVE: anything about Qwen3.8-Flash-Next's hybrid layers (36 Gated DeltaNet +
          12 QSA) - that gap is Spike B's, inside the Strata engine.

Run (CPU, no GPU, does not disturb a running Strata):
  ~/Strata-data/venvs/spikeC/bin/python spikes/mam-dc6/dc6_mini.py [--cases 8] [--quick]
Writes spikes/mam-dc6/logs/dc6_mini_<utc>.json (+ prints the summary).

Mechanism © 2026 Mustafa Akbaş (AKBASCORE research line); this implementation is independent,
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

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
CUT = 6            # AkbasCore's locked consolidation cut on 32-layer 7B; 24-layer 0.5B keeps the same depth
MAX_NEW = 16
SEED = 561561      # echo of TEST563's seed constant, new panel below

SYSTEM = "Answer the question using only the information provided. Give only the requested name and nothing else."


def canon_prefix() -> str:
    return f"{SYSTEM}\n\nINFORMATION:\n"


def source_block(fact: str) -> str:
    return f"{fact}\n"


def query_block(q: str) -> str:
    return f"\nQUESTION:\n{q}\n\nANSWER:"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s).casefold().strip())
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    return " ".join(s.split())


def hit(answer: str, gold: str) -> bool:
    na, ng = norm(answer), norm(gold)
    return re.search(r"(?<!\w)" + re.escape(ng) + r"(?!\w)", na) is not None


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


def build_worlds():
    worlds = []
    for state, cur, former, near, role in WORLDS:
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
        worlds.append({"facts": facts, "queries": queries})
    return worlds


class DC6:
    """A from-scratch re-implementation of the mechanism described in the AKBASCORE records."""

    def __init__(self, device: str, dtype: torch.dtype):
        torch.manual_seed(SEED)
        np.random.seed(SEED)
        self.device = torch.device(device)
        self.tok = AutoTokenizer.from_pretrained(MODEL_ID)
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID, torch_dtype=dtype).to(self.device).eval()
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

    # ---- the four mechanism steps ----

    @torch.no_grad()
    def forge(self, fact: str):
        """Read one source fact ONCE: capture the layer-CUT output and layers 0..CUT K/V."""
        ids = self.tok(canon_prefix() + source_block(fact), add_special_tokens=False).input_ids
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

    @torch.no_grad()
    def ask(self, layers, question: str):
        """Source-free question: only the query template's tokens run over the installed memory."""
        cache = self._cache(layers)
        physical = int(cache.get_seq_length())
        qids = self.tok(query_block(question), add_special_tokens=False).input_ids
        ids = torch.tensor(qids, device=self.device).reshape(1, -1)
        pos = torch.arange(physical, physical + ids.shape[1], device=self.device)
        out = self.model(input_ids=ids, past_key_values=cache,
                         attention_mask=torch.ones((1, physical + ids.shape[1]), dtype=torch.long, device=self.device),
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


@torch.no_grad()
def run_panel(model: DC6, quick: bool):
    worlds = build_worlds()
    rng = random.Random(SEED)
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
        banks = []
        for (cj, tj) in cart_ids:
            cpj = model.forge(worlds[cj]["facts"][tj][0])
            banks.append(model.consolidate_init(cpj))
        mem_indep = tuple((torch.cat([b[li][0] for b in banks], dim=-2),
                           torch.cat([b[li][1] for b in banks], dim=-2)) for li in range(model.nl))
        ans_indep = model.ask(mem_indep, question)

        # ARM 2: INCR DC6 - every cartridge consolidated append-only, question at first/middle/last
        for pos_name in ("FIRST", "MIDDLE", "LAST"):
            order = list(cart_ids)
            if pos_name == "FIRST":
                order.remove((wi, typ)); order = [(wi, typ)] + order
            elif pos_name == "MIDDLE":
                order.remove((wi, typ)); order = order[:1] + [(wi, typ)] + order[1:]
            memory, ok_all = None, True
            for (cj, tj) in order:
                cpj = model.forge(worlds[cj]["facts"][tj][0])
                if memory is None:
                    memory = model.consolidate_init(cpj)
                else:
                    memory, ok = model.consolidate_append(memory, cpj)
                    ok_all = ok_all and ok
            invariant_checks.append(ok_all)
            ans = model.ask(memory, question)
            rows.append({"arm": f"INCR_DC6_{pos_name}", "case": wi, "type": typ,
                         "gold": gold, "answer": ans, "correct": hit(ans, gold)})

        # ARM 3: JOINT text upper bound (all facts in the prompt; the ceiling, not a memory)
        text = canon_prefix() + "".join(source_block(worlds[cj]["facts"][tj][0]) for (cj, tj) in cart_ids)
        ids = model.tok(text + query_block(question), add_special_tokens=False, return_tensors="pt").to(model.device)
        gen = model.model.generate(**ids, max_new_tokens=MAX_NEW, do_sample=False)
        ans = model.tok.decode(gen[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        rows.append({"arm": "JOINT_TEXT", "case": wi, "type": typ,
                     "gold": gold, "answer": ans, "correct": hit(ans, gold)})
        rows.append({"arm": "INDEP_ALONE", "case": wi, "type": typ, "gold": gold,
                     "answer": ans_indep, "correct": hit(ans_indep, gold)})
    return rows, invariant_checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="4 cases instead of 8 (fast smoke)")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()
    t0 = time.time()
    model = DC6(args.device, torch.bfloat16)
    rows, invariants = run_panel(model, args.quick)
    stats = {}
    for arm in sorted({r["arm"] for r in rows}):
        sel = [r for r in rows if r["arm"] == arm]
        stats[arm] = {"correct": sum(r["correct"] for r in sel), "total": len(sel)}
    out = {
        "spike": "C: independent DC6 re-implementation (mechanism replication, 0.5B dense, CPU)",
        "model": MODEL_ID, "cut_layer": CUT, "device": args.device, "dtype": "bfloat16",
        "quick": args.quick, "seconds": round(time.time() - t0, 1),
        "append_invariant_bitwise_all": all(invariants), "append_checks": len(invariants),
        "arms": stats, "rows": rows,
        "reference": {"akbascore": "TEST560 INCR_DC6 72/72 vs INDEP 13/72 (7B, A100); "
                                   "TEST563 scale 5/5,9/10,16/20,16/40,22/80",
                      "doi": ["10.5281/zenodo.23127434", "10.5281/zenodo.23143605", "10.5281/zenodo.23146475"]},
        "reading": "PASS = INCR_DC6 arms beat INDEP and the bitwise append invariant held "
                   "everywhere; absolute accuracy is expected to trail the 7B records (0.5B model).",
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logdir = Path(__file__).resolve().parent / "logs"
    logdir.mkdir(exist_ok=True)
    (logdir / f"dc6_mini_{stamp}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=2))
    print("log:", logdir / f"dc6_mini_{stamp}.json")


if __name__ == "__main__":
    main()
