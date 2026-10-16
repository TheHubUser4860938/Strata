# Strata Mnemosyne — Strata with built-in long-term memory

**What this fork is for: building memory into Strata — a persistent, harness-independent long-term memory
that lives at the inference server.** Every client (OpenCode, Claude Code, Codex, the web app) shares one
store, and no harness ever re-sends conversation history — the server remembers, and it remembers the same
way no matter which app is knocking.

**Naming (settled 2026-10-16).** The project is **Strata**. Its built-in memory system is **Strata
Mnemosyne** — the store: subject shelves, SQLite + FTS5 + pinned embeddings, the journal, the sweep.
The assistant that recalls it — the chat voice and the coding agent's hands — is **Clio** (KLEE-oh),
who in the mythology is Mnemosyne's daughter: the retriever descends from the store. The split is
architectural, not decoration: storage is passive and curated; recall is the part with rules,
budgets and judgement. Short form for code and speech: **Mnemo**.

## Try it: Strata with memory

**Windows:** double-click `START-HERE.bat`. **Linux:** `./setup.sh`. Run it from a clone of **this**
repository's `memory-core` branch — the installer checks your PC, picks the model size that fits
your RAM, downloads it, and starts the server at `http://127.0.0.1:8005`:

```
git clone -b memory-core https://github.com/TheHubUser4860938/Strata.git
cd Strata
```

Memory is **on by default** in this fork (decided 2026-10-16: someone who does not want the built-in
memory system would not be using this fork of Strata to begin with). Setup writes the block below and
fetches the embedder it ships with, so the hybrid recall works out of the box. To turn the memory off,
set `"memory": { "enabled": false }` in the engine config - every path then returns to upstream Strata,
byte for byte:

```json
"memory": { "dir": "Strata-data/memory",
            "embedder": { "dir": "Strata-data/embeddings/bge-small-en-v1.5" } }
```

or by starting the server with `--memory Strata-data/memory`. When it is live the boot banner prints
`[strata] memory core on: ...`, and `http://127.0.0.1:8005/memory-ui` is the store's own page —
stats, search, forget. The embedder (pinned by SHA-256, MIT, ~130 MB, CPU-only — it never touches
the GPU your model owns) is what lets recall understand paraphrase; without it recall stays lexical
and everything else still works. The exact files and hashes: `docs/MEMORY_CORE.md`.

### Or point your AI agent at it

Paste this into any coding agent (Claude Code, Codex, OpenCode, Cursor, Copilot):

```text
Set up Strata with its built-in memory (Strata Mnemosyne) on this PC for me:
https://github.com/TheHubUser4860938/Strata - use the memory-core branch, follow
docs/AI_SETUP.md in that repository. The memory is ON by default on that branch, and setup
fetches its embedder as part of the install.
Before reporting done, verify the boot banner says "memory core on" and /memory-ui answers.
```

The agent can also drive install/start/stop through Strata's [MCP server](docs/MCP_SERVER.md).

Status, October 16, 2026 — **beta-1 (Linux)**: the claim list, with what each measurement was run on
and what beta-1 refuses to claim, is [`docs/BETA1.md`](docs/BETA1.md). The text tier is live on
the dev server and the K/V tier is in code:

- **Tier 3, curated text memory (running):** ~2,700 facts on subject shelves, recalled server-side and
  injected as one hidden system block that rides all three dialects (OpenAI, Anthropic, Responses). A
  journal feeds itself from conversations; a curation loop classifies, dedups, and consolidates with
  human-gated merge review; a budget speaks before it's full; `?since=`/`?until=` date windows answer
  when-said questions; encryption at rest seals atomically and shreds plaintext on clean shutdown
  (round-trip proven); the store has its own page (`/memory-ui`: stats, search, forget, re-seed);
  and, opt-in, the model browses its own store mid-answer with a `memory_search`
  tool the server runs itself — no client tool wiring on the route that offers it.
- **Tier 2, KV cartridges (built; first venue ran the day they booted, 2026-10-13):** the store's
  standing shelves are seeded once as an engine session file and restored ahead of every
  conversation. Measured on this engine: save 1,973 tokens → 260 MB (132 KB/token, the slot's
  whole state); restore **81 ms** for 247 MB; the pin law **refuses foreign files** by config
  fingerprint; the knowledge delta is **real** - warm 3/3 correct, cold 3/3 `idk`,
  wrong-cartridge control 0 leaks. What did not measure yet: the fresh-conversation prefill win -
  engine resume extends only the same conversation (one adjacent probe gave back 2,871 tokens,
  1,837 ms → 599 ms); the RAM-side conversation cache was then measured doing its own version of
  that trick per conversation (~13 ms parked-state returns, 1,989 ms → 705 ms) but not across
  conversations. And continuation reuse measures **working** on a clean adjacent probe (session
  idle, zero other clients): 100% of the parent prompt given back (2,629 of 2,622 tokens),
  prefill 2,011 ms -> 681 ms - the head block is pinned per conversation (keyed by the first
  user message) and the per-turn recall block rides the prompt tail by design. The venue's
  fragile lines (runs 4-5, cache_n 0) are the runbook's slot-replacement failure - an
  interleaving client took the slot while RAM churn evicted the parked copy - not placement
  bugs; a first reading that blamed block instability was withdrawn the same day it was
  filed. The earned claims today are fast slot reload, pin-verified provenance, parked
  repeats, and a stable pinned head (a multi-turn conversation pays its head prefill once at
  conversation start, not every turn); nothing here claims prefill skipped for strangers.
  Protocol and raw records: `spikes/cartridge/M3C_PRE_REG.md`.
- **Benchmark, measured (2026-10-13/14):** on **LongMemEval-V1 oracle** — all 500 official questions,
  3 repeats, one fresh conversation per ask, the benchmark's own checker prompt verbatim — the
  built-in memory answers **0.72** correctly (0.73 by question majority) at the shipped 96-note /
  24k-char head, up from 0.68/0.69 at the first 48-note / 12k arm — a measured +3.8-point budget
  A/B on the same store and protocol, per-repeat spread only ±5. The cold control is the line to
  trust: memory muted, **98.4–98.7% answer `idk`** and gold leaks are **2 in 1,500**. Recall splits
  honestly: single-session 0.91–0.95, preferences 0.90, knowledge-update 0.75, multi-session 0.74,
  abstention 0.78, temporal arithmetic 0.43 — the known weak spot, unmoved by budget. The write path
   then moved it: facts extracted with absolute dates answer the same 127 temporal questions
   **+0.207** over the undated prompt (3 answer repeats, pooled McNemar p ≤ 1.2e-12, scratch stores,
   2026-10-16) — the first intervention measured to move it at all, and it is already in the curation
   loop every install ships. The judge
  model is local (same engine, paper's decoding shape, disclosed); the doubled head costs ~2.3 s at
  conversation start and zero per follow-up (pinned prefix). Full protocol, caveats, and records:
  `docs/MEMORY_CORE.md` (F venue section) + `spikes/lme/oracle-20261013-full.jsonl` (12k) and
  `spikes/lme/budget24k-20261014-full.jsonl` (24k).
- **Phase 2, state-without-text (open research):** porting the *AKBASCORE MAM / NIRVANA cognitive
  cartridge* idea — knowledge stored as model-produced K/V (v1.3.0 "CUT3": 56 tensors per fact, one
  file each, SHA-256 manifested) and read back with no source text in the prompt, which on this
  hybrid-attention engine needs engine-level grafting.
  Upstream research: [ceceli33/titan-cognitive-core-v2](https://github.com/ceceli33/titan-cognitive-core-v2)
  (technical disclosures: [10.5281/zenodo.23127434](https://doi.org/10.5281/zenodo.23127434),
  [10.5281/zenodo.23143605](https://doi.org/10.5281/zenodo.23143605),
  [10.5281/zenodo.23146475](https://doi.org/10.5281/zenodo.23146475),
  [10.5281/zenodo.23311350](https://doi.org/10.5281/zenodo.23311350)).

On this fork's target model (Qwen3.8-Flash-Next: 36 Gated DeltaNet layers + 12 Qwen Sparse Attention layers,
quantized KV) the state-without-text port is **open research, not an established result**: MAM's published
evidence covers dense full-attention BF16 models and seals a scaling ceiling (22/80 at 80 cartridges). This
fork documents positive *and* negative results as they land, in `docs/MEMORY_CORE.md`.

Engine and server changes live on the `memory-core` branch; `main` stays a near-mirror of
[upstream Strata](https://github.com/Niko1221/Strata) (MIT, by Niko1221). MAM-derived code carries the
attribution and license terms of the AkbasCore repository (Copyright © 2026 Mustafa Akbaş).

**Who wrote this code:** most of it was typed by AI coding agents under a human's direction. The human
picks the direction, owns the claims, and runs every measurement on real hardware (RTX 4090,
Ryzen 7 9800X3D, 64 GB RAM); each number in this fork links to the script and raw output that
produced it, so anyone can re-check it whether or not an AI typed the driver.

---

## Credits and license

The model is [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) by the Qwen team. It was
compressed by [ISTA-DASLab](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF), UkisAI (Swift 1.5)
and Unsloth. Strata uses parts of [llama.cpp / ggml](https://github.com/ggml-org/llama.cpp). All credits:
[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#credits). Strata is open source under the [MIT License](LICENSE). A few
parts and every model have their own licenses ([which ones](docs/HOW_IT_WORKS.md#license)).

## Support Strata

Strata is free and open source. If it is useful to you, you can support its development:

<p align="center"><a href="https://buymeacoffee.com/strataengine"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a></p>
