# Strata Mnemosyne — Strata with built-in long-term memory

An 180-billion-parameter model running on a normal PC — one graphics card, some system RAM —
that **remembers**. Not in the client, not per-app: the memory lives at the inference server
itself, so every client (OpenCode, Claude Code, Codex, the web app) shares one store, and no
harness ever has to re-send history. The server remembers, and it remembers the same way no
matter which app is knocking.

## The two names, and they are not decoration

The project is **Strata**. Its built-in memory system is **Strata Mnemosyne** — Mnemosyne being
the Titaness of memory, mother of the Muses, and the literal root of the word *mnemonic*: the
store is named after memory itself. The assistant that recalls it — in the chat window, and in
the coding agent's tool calls — is **Clio** (KLEE-oh), the Muse of history: the one who decides
what gets recorded and what gets repeated. The split is architectural, not mythological
window dressing: storage is passive and curated; recall is the part with rules, budgets and
judgement. The mythology *is* the spec — a store that never forgets, and a retriever with
taste.

## Try it: Strata with memory

**Windows:** double-click `START-HERE.bat`. **Linux:** `./setup.sh`. Run it from a clone of
**this** repository's `memory-core` branch — the installer checks your PC, picks the model size
that fits your RAM, downloads it, and starts the server at `http://127.0.0.1:8005`:

```
git clone -b memory-core https://github.com/TheHubUser4860938/Strata.git
cd Strata
```

<img width="1877" height="638" alt="image" src="https://github.com/user-attachments/assets/5cae0570-fb69-4665-a59d-92a1c0eff5f7" />

<img width="1278" height="263" alt="image" src="https://github.com/user-attachments/assets/f6f63206-afdb-439a-a9d4-d58ce0ee054a" />

<img width="1879" height="434" alt="image" src="https://github.com/user-attachments/assets/1643c829-dc1b-43f5-9952-0d5c4f67f924" />

Two things arrive switched **on**, and everything else stays byte-identical to upstream Strata:

- **Memory.** On by default in this fork — someone who does not want the built-in memory
  system would not be using this fork to begin with. Setup writes the block below and fetches
  the embedder it ships with, so hybrid recall works out of the box. To turn it off, set
  `"memory": { "enabled": false }` — every path then returns to upstream Strata, byte for byte:

  ```json
  "memory": { "dir": "Strata-data/memory",
              "embedder": { "dir": "Strata-data/embeddings/bge-small-en-v1.5" } }
  ```

  or start the server with `--memory Strata-data/memory`. When it is live the boot banner
  prints `[strata] memory core on: ...`, and `http://127.0.0.1:8005/memory-ui` is the store's
  own page — stats, search, forget. The embedder (pinned by SHA-256, MIT, ~130 MB, CPU-only —
  it never touches the GPU your model owns) is what lets recall understand paraphrase;
  without it recall stays lexical and everything else still works.

- **Reason modes** (DavidAU model families — see below): 41 framings, pairs, thinking levels
  and an autonomous picker — the server boots already knowing how to consult its own cast
  list. The banner prints `reason modes on: ...`; `http://127.0.0.1:8005/reason` is the live
  view.

This fork listens on **port 8005** where upstream uses 8080/8095, so a mainline Strata install
and a memory install can run side by side on the same PC.

### Or point your AI agent at it

Paste this into any coding agent (Claude Code, Codex, OpenCode, Cursor, Copilot):

```text
Set up Strata with its built-in memory (Strata Mnemosyne) on this PC for me:
https://github.com/TheHubUser4860938/Strata - use the memory-core branch, follow
docs/AI_SETUP.md in that repository. The memory is ON by default on that branch, and setup
fetches its embedder as part of the install.
Before reporting done, verify the boot banner says "memory core on" (and, on a DavidAU
install, "reason modes on"), and /memory-ui answers.
```

The agent can also drive install/start/stop through Strata's [MCP server](docs/MCP_SERVER.md).

## What's new — the thinking, on a dial

DavidAU's re-quant of Qwen3.8-Flash-Next ships with a chat template that answers to a
`{REASON:name}` tag, and the cast list reads like a convention badge rather than a config
file: **DevOracle**, an architect who cannot resist proposing a refactor; **House**, who
diagnoses first and is pleasant later; **Curie**, lab notebook before opinions; **Socrates**,
who would rather ask you a better question than answer this one; **Every Joe**, who never met
a jargon term he didn't translate; **007**, whose licence to improvise expires when the task
is done; and **einstein+pkd**, who suspects the cartridge is a simulation — a reasonable
paranoia, given the research below.

The abliterated family's ancestry, because credits belong in the story: it descends from
[SC117](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF)'s abliterated GSQ-RCO
build — the refusal direction removed by tensor transplant, copying [orcarouter](https://huggingface.co/orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF)'s
abliterated weights into ISTA-DASLab's already-learned quantization without recomputing a single value —
and DavidAU's build is assembled on that file.

Forty-one framings in all, and this fork turned the tag
that summons them into a steering system:

- **Pairs.** `joe+comedy` thinks like the first and answers like the second; `stark+coder`
  draws the architecture and ships the files. The server splices both constructs mechanically,
  so the grammar works through any door: a client tag, the `reason_mode` tool (agents steer
  themselves mid-task), or the Settings menu — which applies live, no restart.
- **Thinking levels.** `none / low / medium / xhigh`, pinned per conversation, outranking an
  app's own request level — the dial that decides how much the model thinks before it speaks.
- **Autonomous mode — the default.** `auto` picks the construct per request, scored against
  the template author's own use-case map: the map that says `curie+teacher` for explanations
  and `musk+business` for physics-first strategy. No confident match, no pick — it never
  guesses. A conversation can pin a mode over it, a client tag outranks everything, and
  `GET /reason` shows what actually framed each conversation, including when a tag overruled
  the picker.

- **Tuned on live traffic, the same day it shipped.** The first evening of autonomous mode
  produced a transcript audit: two turns rode a stale frame because the *agent* pinned a mode
  and forgot to re-check — and a cold replay of the picker scored the right construct 2-for-2
  on the misses. So pins now say what they freeze, pair splices carry a wrapper-arbitration
  bridge, and the internal machinery (curation loop, benchmark harness) got its own opt-out
  tag — measured, it had been carrying 2,227 tokens of persona block per prompt. The cast is
  for the user; the staff room stays professional.

  And if you want to hear the cast at full volume: the staff room wrote a second README —
  **[docs/README_MASHUP.md](docs/README_MASHUP.md)** — neo thinking, House diagnosing, Vader
  commanding. Same numbers, different clothes; it is the fastest way to see what the dial
  actually does to the voice.

## And the store got disciplined

The store woke up one morning with 2,885 rows, half of them paraphrased fragments of documents
still sitting in a folder — which is not a memory, that is a photocopy shop. By that evening it
held **1,051**: 1,549 fragments were forgotten and replaced by eleven whole-document pointers
(one memory per document, the document itself on disk, the recall quoting it by title and
fingerprint), every unclassified row found its shelf (148 shelves, zero wild), retired facts
now lose their vectors along with their recall — the store holds the invariant *vectors ==
live rows*, 1,051 == 1,051 — and the nightly machinery does the rest: a curation sweep every
30 minutes that runs only when the store asks for it, a weekly janitor that seals an encrypted
backup and collapses paraphrase clusters, and a slot folder that keeps the newest files and
nothing else.

The write paths earned their rules the hard way — a 233-operation manual cleanup, then the laws
enforced in code: multi-call agent turns journal as ONE row; every landing fact is judged
against the store before it is stored (the supersession arm: 88 flags, correct verdicts, in its
first live hours); ingest reunites abbreviation splits ("8 U.S.C." no longer dies mid-citation)
and refuses shards that cannot stand alone.

## How it works today

**Tier 3 — curated text memory (live).** ~1,050 facts on subject shelves, recalled server-side
and injected as one hidden system block that rides all three dialects (OpenAI, Anthropic,
Responses). A journal feeds itself from conversations; a curation loop classifies, dedups and
consolidates with human-gated merge review; a budget speaks before it's full; `?since=`/`?until=`
windows answer when-said questions; encryption at rest seals atomically and shreds plaintext on
clean shutdown. Opt-in: the model browses its own store mid-answer with a `memory_search` tool
the server runs itself — no client wiring.

**What it measures like, on LongMemEval-V1 (oracle, 500 questions × 3 repeats):** the built-in
memory answers **0.72** correctly at the shipped budget. The control line is the one to trust:
with memory muted, **98.4–98.7% of answers are `idk`** and gold leaks 2 in 1,500 — the model is
recalling, not confabulating. Recall splits honestly: single-session 0.91–0.95, preferences
0.90, knowledge-update 0.75, multi-session 0.74, abstention 0.78, temporal arithmetic 0.43 —
and the one intervention ever measured to move that weak spot (writing facts with absolute
dates) gained **+0.207** on the same 127 questions, McNemar p ≤ 1.2e-12, and it ships in the
curation loop. Full protocol and records: `docs/MEMORY_CORE.md`.

**Tier 2 — KV cartridges (built; waiting on RAM).** The store's standing shelves are seeded
once as an engine session file and restored ahead of every conversation: restore **81 ms** for
247 MB, the pin law **refuses foreign files** by config fingerprint, and the knowledge delta is
real — warm 3/3 correct, cold 3/3 `idk`, wrong-cartridge control 0 leaks. What is *not* claimed:
prefill skipped for strangers (engine resume extends one conversation, not across them —
measured, and the docs say so). The venue re-record that would settle its economics sits queued
behind the RAM upgrade below.

**Phase 2 — state-without-text (open research).** Porting the *AKBASCORE MAM / NIRVANA
cognitive cartridge* idea — knowledge stored as model-produced K/V and read back with no source
text in the prompt. Upstream:
[ceceli33/titan-cognitive-core-v2](https://github.com/ceceli33/titan-cognitive-core-v2)
(records I–III: 7B, Mistral parity, and Qwen2.5-72B on one A100, K/V bitwise-stable under
appends; Zenodo DOIs [23127434](https://doi.org/10.5281/zenodo.23127434),
[23143605](https://doi.org/10.5281/zenodo.23143605),
[23146475](https://doi.org/10.5281/zenodo.23146475),
[23311350](https://doi.org/10.5281/zenodo.23311350),
[23381336](https://doi.org/10.5281/zenodo.23381336),
[23434686](https://doi.org/10.5281/zenodo.23434686)). The authors' own boundary governs how we
read it: semantic transfer is **not** claimed, and the open question they raise — extension to
"architectures with substantially different attention mechanisms" — is exactly this fork's
blocker: on the 180B MoE here (125B of language model plus 51B of n-gram embedding plus 4B of MTP —
the three numbers you see floating around the cards are the same model counted three ways; 512
experts, 10 active per token; 36 Gated DeltaNet layers + 12 sparse-attention layers, quantized
KV), state-without-text is research, not a result. Positive *and* negative results land in
`docs/MEMORY_CORE.md`.

## Status — beta-1 (Linux)

The claim list, what each measurement was run on, and what beta-1 refuses to claim:
[`docs/BETA1.md`](docs/BETA1.md). The engine-heavy queue — benchmark re-record on the current
weights, cartridges — waits on the 128 GB RAM upgrade
(~2026-10-22): this dev box runs ~52 GB of weights on a 62 GB machine, and the engine, not the
memory system, is the pressure (measured: Postgres holds 20 MiB resident, the embedder
~130 MiB, engine RSS flat). The memory-and-tooling line shipped six releases on October 17
alone: pairs, thinking levels, autonomous mode, the Settings menu, auto-pick observability and
the store purge above. The server's own test suite — 1,012 tests, no GPU and no downloads
needed — passes on that box.

Engine and server changes live on the `memory-core` branch; `main` stays a near-mirror of
[upstream Strata](https://github.com/Niko1221/Strata) (MIT, by Niko1221). MAM-derived code
carries the attribution and license terms of the AkbasCore repository (Copyright © 2026
Mustafa Akbaş).

**Who wrote this code:** most of it was typed by AI coding agents under a human's direction.
The human picks the direction, owns the claims, and runs every measurement on real hardware
(RTX 4090, Ryzen 7 9800X3D, 64 GB RAM class — 128 GB arriving ~2026-10-22); each number in
this fork links to the script and raw output that produced it, so anyone can re-check it
whether or not an AI typed the driver.

---

## Credits and license

The model is [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) by the Qwen team. It was
compressed by [ISTA-DASLab](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF), UkisAI (Swift 1.5),
Unsloth, and [DavidAU](https://huggingface.co/DavidAU) — whose Turbo-Brilliance reason-mode re-quants,
[regular](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF)
and [abliterated](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Abliterated-Uncensored-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF)
(the abliterated one built on [SC117](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF)'s
abliterated GSQ-RCO transplant, which carries [orcarouter](https://huggingface.co/orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF)'s
abliterated weights),
are the models this fork's reason modes ride and both of which the installer offers. Strata uses parts of [llama.cpp / ggml](https://github.com/ggml-org/llama.cpp). All credits:
[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#credits). Strata is open source under the [MIT License](LICENSE). A few
parts and every model have their own licenses ([which ones](docs/HOW_IT_WORKS.md#license)).

## Support Strata

Strata is free and open source. If it is useful to you, you can support its development:

<p align="center"><a href="https://buymeacoffee.com/strataengine"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a></p>
