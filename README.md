# Strata Mnemosyne — Strata with built-in long-term memory

An 180-billion-parameter model running on a normal PC — one graphics card, some system RAM —
that **remembers**. Not in the client, not per-app: the memory lives at the inference server
itself, so every client (OpenCode, Claude Code, Codex, the web app) shares one store, and no
harness ever has to re-send history. The server remembers, and it remembers the same way no
matter which app is knocking.

This fork is based on upstream Strata 0.2.3 (engine 0.1.48) — upstream's own headline list: faster
Strix Halo (+6% to +19% answer speed on their test PC), Intel Arc as a released engine package, the
DFlash2 drafter (opt-in, Terminal only), an agent chat that copes with a full context, HTTPS through
the operating system's certificates (#3722), better defaults for CPU workers, VRAM and the
conversation cache (#3493), a steadier Terminal and app — and adds the memory system and the
reason-mode steering described below.

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

Version 0.2.3 · engine 0.1.48

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

**You need at least 32 GB of RAM and graphics memory (VRAM) together.** On a Mac or a Ryzen AI Max, that is the unified
memory. More memory gives a bigger model or a faster one.

- A 32 GB PC with a 12-16 GB card runs the Coder.
- 64 GB of RAM runs every size.

| Your computer | Details |
| --- | --- |
| **NVIDIA** GeForce RTX 20, 30, 40, 50 series (12 GB or more) | Install the app or the Terminal. |
| **AMD** Radeon RX 6800 and newer, Radeon AI PRO R9700 (12 GB or more) | [AMD cards](docs/AMD_HIP.md) |
| **Intel** Arc | [Intel Arc](docs/INTEL_ARC.md), [Intel engine](docs/INTEL.md) |
| **Apple Silicon Mac** (M1 or newer, macOS 15 or newer) | [Mac](docs/MAC.md) |
| **AMD Ryzen AI Max (Strix Halo)** and other unified-memory systems | [Strix Halo](docs/STRIX_HALO.md) |
| Older cards and processors | [Older GPUs](docs/OLDER_GPUS.md), [Older CPUs](docs/INSTALL.md#older-cpus-experimental) |

Also needed:

- about 80 GB of free disk (an SSD makes the first start much faster)
- Windows 10 / 11, Linux or macOS
- a current graphics driver

Two or three cards of the same make can share a model ([multi-GPU](docs/MULTI_GPU.md)). Strata never splits a model across
NVIDIA and AMD cards. Full list: [docs/INSTALL.md](docs/INSTALL.md#what-you-need).

## Install

Pick one option. They all start the same engine and use the same models.

### Option A: the Strata app

The easiest way. The app has the engine inside: install it, pick a model, chat. Download the file for your system and
graphics card from the [releases page](https://github.com/Niko1221/Strata/releases/latest). Not sure which one? See the
line under the table.

| System | Your graphics card | File to download |
| --- | --- | --- |
| **Windows 10 / 11** | NVIDIA RTX, driver 580 or newer | [`Strata-0.2.3-windows-cuda13.exe`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-windows-cuda13.exe) |
| | NVIDIA, older card or driver older than 580 | [`Strata-0.2.3-windows-cuda12.exe`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-windows-cuda12.exe) |
| | AMD Radeon RX 9000 series or Radeon AI PRO R9700 | [`Strata-0.2.3-windows-hip.exe`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-windows-hip.exe) |
| | AMD Radeon RX 7000 series or Ryzen AI Max (Strix Halo) | [`Strata-0.2.3-windows-hip-rdna3.exe`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-windows-hip-rdna3.exe) |
| **Linux** | NVIDIA RTX, driver 580 or newer | [`Strata-0.2.3-linux-nvidia-cuda13.AppImage`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-linux-nvidia-cuda13.AppImage) |
| | NVIDIA, older card or driver older than 580 | [`Strata-0.2.3-linux-nvidia-cuda12.AppImage`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-linux-nvidia-cuda12.AppImage) |
| | AMD Radeon RX 9000 series or Radeon AI PRO R9700 | [`Strata-0.2.3-linux-amd-rx9070.AppImage`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-linux-amd-rx9070.AppImage) |
| | AMD Radeon RX 7000 series | [`Strata-0.2.3-linux-amd-rx7900.AppImage`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-linux-amd-rx7900.AppImage) |
| | AMD Ryzen AI Max (Strix Halo) | [`Strata-0.2.3-linux-amd-strixhalo.AppImage`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-linux-amd-strixhalo.AppImage) |
| **Mac** (Apple Silicon, macOS 15 or newer) | built in | [`Strata-0.2.3-mac.dmg`](https://github.com/Niko1221/Strata/releases/latest/download/Strata-0.2.3-mac.dmg) |

**If you are unsure:** for NVIDIA, take `cuda13` if your driver is version 580 or newer, otherwise `cuda12`. `cuda12` is
for older cards and older drivers. The `hip` and `amd` files are for AMD cards. RX 6000 cards have no installer of
their own: install `hip-rdna3` (Windows) or `amd-rx7900` (Linux), and on a PC with an RX 6000 card it fetches the RX 6000 engine by itself
(also `strata update`). That engine is built but not yet tested on a real RX 6000 card: see [AMD cards](docs/AMD_HIP.md).

Then:

- **Windows:** run the `.exe`. It installs for your user and needs no administrator rights. If Windows says "Windows
  protected your PC", choose **More info**, then **Run anyway** (the installer is not code-signed yet).
- **Linux:** make the file runnable (`chmod +x`) and start it. It needs FUSE 2: `sudo apt install libfuse2` (Ubuntu 22.04)
  or `libfuse2t64` (Ubuntu 24.04 and newer).
- **Mac:** open the `.dmg` and drag Strata onto Applications.

On the first screen the app reads your PC and recommends a model that fits. Press install. The model is a big download
(about 40-70 GB) and continues where it stopped if it is interrupted. Closing the window stops Strata and the engine.

### Option B: the Terminal (one line)

The Terminal is the `strata` command, for scripts, servers and AI agents.

Linux and Mac:

```sh
curl -fsSL https://github.com/Niko1221/Strata/releases/latest/download/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://github.com/Niko1221/Strata/releases/latest/download/install.ps1 | iex
```

The script checks your PC, downloads the Terminal, verifies its SHA-256 and unpacks it into your home folder. It needs no
administrator rights. On Windows, open a new terminal window afterwards. Then start Strata:

```sh
strata launch
```

It asks one question at a time: model, context, pictures, who may connect. Then it downloads the engine for your graphics
card (signed, and test-started before use) and the model, and starts serving. For scripts and AI agents, use flags:

```sh
strata launch --model q2_0 --yes --no-tui
```

To read the install script before it runs, download it first with `curl -fsSL <address> -o install.sh`, read it, then run
`sh install.sh`. All screens and flags: [docs/TERMINAL_LAUNCH.md](docs/TERMINAL_LAUNCH.md).

### Option C: let your AI set it up

Paste this into Claude Code, Cursor, Codex, GitHub Copilot or another coding assistant:

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
- **Autonomous mode — the default.** `auto` picks the construct per request, scored against
  the template author's own use-case map: the map that says `curie+teacher` for explanations
  and `musk+business` for physics-first strategy. No confident match, no pick — it never
  guesses. A conversation can pin a mode over it, a client tag outranks everything, and
  `GET /reason` shows what actually framed each conversation, including when a tag overruled
  the picker.
- **Honest about what it can claim.** Three sweeps over the published suites (GSM-Symbolic,
  MATH-500, HumanEval, five BIG-Bench Hard tasks, GPQA-diamond) — 3,168 generations, twelve
  arms, at temperature 0 and at the model's recommended 1.0 — found **no mode beats the
  default on accuracy beyond the ±0.05 band**; coder alone measurably *costs* it (−0.07), the
  reasoning-family arms land inside the noise (einstein's 0.95 best observed), and the dial
  steers voice, structure and behaviour — never performance (full record: docs/MEMORY_CORE.md).
  For the model itself: on the full 198-question GPQA-diamond set it scores **0.843** (physics
  0.93, chemistry 0.78, biology 0.74; no framing, temp 0) — ISTA's card claims 0.929 for the
  stock IQ3_S on their harness; the free-agent run, the model unrestrained with search and
  memory in hand, will say how much of that gap is dress, harness, or nothing.
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

**Where it stands, read from the store's own stats on 2026-10-23:** **966** live rows across 138
shelves (one row still unclassified), 1,521 retired rows behind them, 2,508 journaled turns, and
the invariant holds — the embedder reports 966 vectors for 966 live rows. The store now runs on
**native PostgreSQL** (ADR-002, the owner's decision 2026-10-16): psycopg 3, no SQLite backend, no
dialect shim, no dual-backend matrix. The measurements that justified it are on the record —
1.6 ms a query at 2,707 rows, a fresh test database in 3 ms, the whole live store loaded in 0.03 s —
and the encrypted `pg_dump` backup round-trips (1.7 s, restore byte-verified by identical lexical
rankings). Encryption at rest is the honest shape for a server database: a 0700 data directory plus
a key file, so a running store is readable by whoever owns the machine and a stopped one is only as
private as that directory. The persona layer has its own constitution (`soul.md`, V4) and its own
budget gate — 14,000 characters, the owner's numbers, raised from 6,000 on 2026-10-20 and to 14,000
on 2026-10-21 — and it fails closed to the plain path when it overflows.

## How it works today

**Tier 3 — curated text memory (live).** ~970 facts on 138 subject shelves, recalled server-side
and injected as one hidden system block that rides all three dialects (OpenAI, Anthropic,
Responses). A journal feeds itself from conversations; a curation loop classifies, dedups and
consolidates with human-gated merge review; a budget speaks before it's full; `?since=`/`?until=`
windows answer when-said questions; a CPU-only embedder (pinned by SHA-256, MIT, ~130 MB) makes
recall understand paraphrase. Opt-in: the model browses its own store mid-answer with a
`memory_search` tool the server runs itself — no client wiring.

**What it measures like, on LongMemEval-V1 (oracle, 500 questions × 3 repeats):** the built-in
memory answers **0.72** correctly at the shipped budget. The control line is the one to trust:
with memory muted, **98.4–98.7% of answers are `idk`** and gold leaks 2 in 1,500 — the model is
recalling, not confabulating. Recall splits honestly: single-session 0.91–0.95, preferences
0.90, knowledge-update 0.75, multi-session 0.74, abstention 0.78, temporal arithmetic 0.43 —
and the one intervention ever measured to move that weak spot (writing facts with absolute
dates) gained **+0.207** on the same 127 questions, McNemar p ≤ 1.2e-12, and it ships in the
curation loop. Full protocol and records: `docs/MEMORY_CORE.md`.

**Tier 2 — KV cartridges (built, and riding on a big-RAM box).** The store's standing shelves are
seeded once as an engine session file and restored ahead of every conversation: restore **81 ms** for
247 MB, the pin law **refuses foreign files** by config fingerprint, and the knowledge delta is
real — warm 3/3 correct, cold 3/3 `idk`, wrong-cartridge control 0 leaks. The RAM gate is met on
this box now: 125 GB since 2026-10-22, and the standing cartridge rides live — 63 shelved facts on
two shelves, 285 MB, restored at boot in **150.3 ms** (the engine's own line: read+check 142.0 ms).
What is *not* claimed: prefill skipped for strangers (engine resume extends one conversation, not
across them — measured, and the docs say so). The venue re-record that would settle its economics
is unblocked and queued.

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

Setup checks your graphics card, RAM and disk, picks the model that fits, installs and starts it, and tells you how to
connect your apps.

Where your PC has RAM to spare, setup no longer locks the n-gram table into RAM by itself: it asks. Locked in RAM
(`--ple-io ram`) a fresh 4K prompt arrives about 18% sooner on a fast card (measured) — at the table's own size in RAM
the experts cannot use; read from the SSD (the engine's default) only the rows each batch needs come off the drive and
the RAM stays free. Either answer is recorded as your own option in the model's config (`user_args`), so no later
setup run rewrites it by formula; `--ple-io ram|ssd` on the command line answers for you.

And a second model, or a setup rerun on the first, no longer resets the memory system: the box's memory block — the
store's folder, the recall budgets, the embedder, the measured knobs you turned on — is carried into the new config.
Only the tier-2 cartridge gate re-decides, for the new model's RAM.

Prefer to build it yourself? See [Build from source](#build-from-source).

## Models

The app and `strata launch` recommend a model for your memory. It is one model in several sizes, compressed more or less.
Smaller sizes are faster. Larger sizes are a bit smarter.

### Low on memory? Try Gyro

[Gyro](https://huggingface.co/agentionai/Qwen3.8-Flash-Next-Gyro-GGUF) (by agentionai) is the smallest way to run this model.

| Size | Memory it needs (RAM + VRAM) | Download | What it is |
| --- | ---: | ---: | --- |
| **Gyro-S** | 27.6 GB | 58.5 GB | For PCs that fit nothing else. Quality is lower than IQ2_XS. |
| **Gyro-M** | 35 GB | 92.0 GB | About IQ3_XXS quality with about 20% less GPU memory. It writes answers 16% (12 GB RTX 3060) to 21% (R9700) slower than Q2_0. |

Gyro runs on NVIDIA and AMD cards (AMD is experimental) on Intel Arc cards on Linux (tested on an Arc A750) and, from 0.2.2, on Apple Silicon Macs (Gyro-S on a 32 GB Mac mini: 9 tok/s, streaming from the SSD; [docs/MAC.md](docs/MAC.md#gyro-s-and-gyro-m-agentionais-trellis-experts)). Details and measurements:
[docs/GYRO.md](docs/GYRO.md). Start it with `strata launch --model gyro-s`.

### The other sizes

The tiers count your RAM plus your VRAM, less 5 GB that Windows and the system keep for themselves. Every other size stays a choice.

| Your memory (RAM + VRAM) | Take | Notes |
| --- | --- | --- |
| **32 GB** | **Gyro-S**, or the **[Coder](docs/MODELS.md#coder)** for code | The Coder needs 32 GB of RAM and keeps half of the experts, so it is weaker outside code |
| **40 GB** | **[Swift 1.5](docs/MODELS.md#swift-15)** IQ2_XS (39.2 GB), or **Gyro-M** (35 GB) | Swift thinks for a shorter time before it answers. Gyro-M is close to IQ3_XXS in quality, but it writes answers 16-21% slower than Q2_0 |
| **48 GB** | **IQ3_XXS** (47.0 GB) | Slower, better |
| **64 GB** | **IQ3_S** (54.8 GB), the original or Swift 1.5 | The slowest, the best: it matches the full model on the published tests |
| **96 GB or more** | Unsloth's **UD-IQ4_XS** (about 72 GB of RAM) to **Q8_0** (about 136 GB), 4 to 8 bit | Closer to the full model. With less RAM, Strata reads part of them from the SSD, which is much slower |

Less RAM than a row says can still work: Strata switches to its
[low-RAM mode](docs/MODELS.md#a-big-graphics-card-and-little-ram) by itself. Expect it to be slower, and much slower
with a small graphics card.

More choices:

- **[Swift 1.5](docs/MODELS.md#swift-15)** is a fine-tune that thinks for a shorter time before it answers, at about the same quality and RAM.
- **[NVFP4](docs/NVFP4.md)** is NVIDIA's 4-bit version for NVIDIA cards. It needs about 80 GB of RAM to keep every expert in RAM.
- **[Qwen3.6-35B-A3B](docs/QWEN36.md)** is a smaller, different model that reads pictures and can answer several requests at once.
- **[OrcaRouter's Uncensored IQ3_XXS](docs/MODELS.md#orcarouter-uncensored-iq3_xxs)** is set up by hand.

And three this fork added to setup's menu (`--family ...`), in the order the menu shows them — qwen, swift, coder, dau, daureg, unsloth, huihui, gyro, nvfp4, qwen36:

- **Turbo-Brilliance, abliterated** ([DavidAU](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Abliterated-Uncensored-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF), `--family dau`) is the IQ3_S that answers the `{REASON:}` dial described above: 41 framings, pairs, thinking levels and an autonomous picker. It is DavidAU's build on [SC117](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF)'s abliterated GSQ-RCO quant — his shard sizes agree with SC117's plus the transplant's signature, so it is a derivative of that file, not the original quant. Same size and speed as IQ3_S. Apache-2.0 on DavidAU's release, Qwen Community License 1.0 underneath.
- **Turbo-Brilliance, regular** ([DavidAU](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF), `--family daureg`) is his re-quant of the original IQ3_S without the refusal-shaping removal: the same dial, the same size and speed as IQ3_S, the same licenses.
- **Huihui, abliterated** ([huihui-ai](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-Flash-Next-abliterated-v2-GGUF), `--family huihui`) is their re-quant of their own abliterated model in the Unsloth Dynamic sizes (94 to 188 GB of download): it answers plainly where the original declines. The dial does not ride it — its chat template is the stock one, and the server says so with a banner at boot — but the memory system and the picture reader work as usual. Qwen Community License 1.0.

Every size, download and what fits where: [docs/MODELS.md](docs/MODELS.md).

### The n-gram table: a bigger one is optional

Every file of this model carries its n-gram lookup table at IQ4_NL: measured against the model's own
BF16 table it misremembers rows at 0.076, where the Q8_0 table sits at 0.0053 — fourteen times truer
(the labs' numbers, cross-checked against each other's quants). The table is one trained tensor, the
same in every quantization of a family tree, and Q8_0 takes no imatrix, so one 54.4 GB file per tree
serves every quant of it on your PC. Setup offers it as **`--ple-q8 on`** (an interactive setup asks;
`--yes` and the app take the bundled table, as always):

- On the two-file GSQ-RCO families (qwen, coder, dau, daureg) the Q8_0 downloads **as the model's second
  file, in that file's place and name** — the IQ4_NL file is never fetched, so the table costs 54.4 GB
  instead of 28.8 and nothing else changes. This is the shape the fork's own 125 GB box has run since
  2026-10-09.
- On the four-file Unsloth families (unsloth, huihui) every file stays: the table rides beside them and
  is read by `--ple-gguf`, with all four shards enumerated as dense projections — without that the
  engine refuses an outside table ("additional shard must match the architecture-validated first
  shard's split metadata"), measured on this fork's box 2026-10-23.
- One file per tree, pinned by size and SHA-256 (sleepyeldrazi's for the GSQ-RCO tree, mduca771's for
  the Unsloth one — his IQ4_NL sibling is the families' own bundled shard 2 byte for byte, which is what
  makes the same-table-better-precision claim checkable). A PC that already owns a table of the pinned
  size links it instead of downloading it twice, and a swapped slot is checked against the table's own
  pin — a swap you made by hand included, so it survives every later setup run.

Swift, Gyro and NVFP4 bring their own tables and are never asked.

For swift models you can run them with the Q8 PLE N-gram table adjust your .json file to look like this.

"--native",
  "Strata-data/models/swift-IQ3_S/Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
  "--native-dense-gguf",
  "Strata-data/models/swift-IQ3_S/Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
  "--native-dense-gguf",
  "Strata-data/models/swift-IQ3_S/Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf",
  "--ple-gguf",
  "/Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-Q8PLETable.gguf", Change to the name of the Q8 PLE table.

## How fast is it?

Based on a 12 GB graphics card (RTX 5070, Ryzen 5 7600, 64 GB RAM). See results from other cards:
[speed of each model](docs/MODELS.md#how-fast-is-each-size), [community results](docs/COMMUNITY_BENCHMARKS.md).

| Model | Answers appear at | Reads your prompt at |
| --- | --- | --- |
| **Q2_0** | `████████████` 94 tokens/s | `████████████` 2,650 tokens/s |
| **IQ2_XS** | `██████████░░` 79 tokens/s | `█████████░░░` 2,090 tokens/s |
| **IQ3_XXS** | `████████░░░░` 62 tokens/s | `████████░░░░` 1,750 tokens/s |
| **IQ3_S** | `███████░░░░░` 53 tokens/s | `███████░░░░░` 1,620 tokens/s |
| **Coder** | `███████░░░░░` 55 tokens/s | `██████████░░` 2,180 tokens/s |

A token is about ¾ of a word, so 60 tokens per second is faster than you can read. "Reads your prompt" is how fast Strata
takes in a long document, code or chat history.

Measured on a 16 GB AMD card (RX 9070 XT, Ryzen 9 3900X, 47 GB RAM):

| Model | Answers appear at | Reads your prompt at |
| --- | --- | --- |
| **Q2_0** | `████████░░░░` 60 tokens/s | `█████░░░░░░░` 1,160 tokens/s |
| **IQ2_XS** | `███████░░░░░` 52 tokens/s | `█████░░░░░░░` 1,110 tokens/s |
| **Coder** | `██████░░░░░░` 44 tokens/s | `██████░░░░░░` 1,420 tokens/s |

The bars use one scale for both tables. A card with more VRAM is faster: an RTX 3090 (24 GB) should write about 100-140
tokens per second. The engine version behind each number is in [DETAILS.md](docs/DETAILS.md#speed-measured).

## Status — beta-1 (Linux)

The claim list, what each measurement was run on, and what beta-1 refuses to claim:
[`docs/BETA1.md`](docs/BETA1.md). The engine-heavy queue — benchmark re-record on the current
weights, cartridges — was waiting on the RAM upgrade, and the RAM upgrade landed on 2026-10-22:
this box now has **125 GB**, KV streaming is on (`--kv-resident 32768`), and the engine's own
counters moved — expert cache 5,709 → 7,236 slots, decode-cache hit rate 87.0-87.4% → 93.3%,
traffic served over PCIe 3.4-3.6% → 1.5%. No speed number is claimed from that: mixed traffic, no
repeats, and the protocol is temperature 0.8/1.0 with at least three repeats before anything is
said. Huge pages were tested and reverted the same day — the reservation ate the page cache, and
the arena was already running on 2 MiB pages through transparent huge pages. The
memory-and-tooling line shipped six releases on October 17 alone: pairs, thinking levels,
autonomous mode, the Settings menu, auto-pick observability and the store purge above. The
full-size intelligence run — five published suites at full size, 21,336 generations, temperature
1.0, three repeats — was launched on 2026-10-22 and has not landed a number yet. The server's own
test suite — 1,156 tests under this repo's runner, no GPU and no downloads needed — passes on this
box except two timing tests, both upstream's own files and both environmental: the
`test_lifeline` watcher, and `test_keepawake`, which fails only inside the full run and passes when
run by itself.

Engine and server changes live on the `memory-core` branch; `main` stays a near-mirror of
[upstream Strata](https://github.com/Niko1221/Strata) (MIT, by Niko1221). MAM-derived code
carries the attribution and license terms of the AkbasCore repository (Copyright © 2026
Mustafa Akbaş).

**Who wrote this code:** most of it was typed by AI coding agents under a human's direction.
The human picks the direction, owns the claims, and runs every measurement on real hardware
(RTX 4090, Ryzen 7 9800X3D — 62 GB RAM class through 2026-10-21, 125 GB since 2026-10-22, so
every number before that date was measured on the smaller box); each number in
this fork links to the script and raw output that produced it, so anyone can re-check it
whether or not an AI typed the driver.

---

### In the app

The app has a **Chat**, a **Monitor** of the model and your GPU, CPU and RAM, a **Models** page and **Settings**. The chat
can work in a folder on your PC. In **Manual** mode it asks before it changes a file or runs a command. Type
`/loop 10m <what to do>` to repeat a message every 10 minutes while the chat is open.

The chat can search the web with DuckDuckGo (or Bing), with no account. You can add a search key under **Settings, Tools**.
Search is the one feature that sends something off your PC, the search words.

To run two models, turn on **Run multiple models** and give each model its own card of the same make.

<p align="center"><img src="docs/media/app-chat.png" width="900" alt="The Strata app: a chat where the agent built a small voxel pagoda garden, with the page open in the preview on the right"><br>
<sub>A chat in the Strata app: the agent wrote a small voxel pagoda garden as one HTML file, and the page opens in the preview next to the conversation</sub></p>

### In the Terminal

<p align="center"><img src="docs/media/terminal-monitor.png" width="900" alt="The Strata Terminal monitor overview while the model writes a reply"><br>
<sub><code>strata monitor</code> while the model writes a reply: speed, GPU, memory and the recent requests</sub></p>

```sh
strata launch                                  # guided screens: model, context, pictures, access, then serve
strata launch --model q2_0 --yes --no-tui     # no questions: serve, print the address, return
strata launch --plan --model q2_0              # show what would happen; nothing changes
strata launch --status                         # what is being served now
strata launch --stop                           # stop it
strata monitor --url http://127.0.0.1:8080     # watch speed, GPU, memory and requests
strata update --check                          # is there a new engine? (strata update installs it)
strata doctor                                  # check the installed engine files
```

`strata --help` lists everything. Starting and serving never download anything. Only the install, `strata launch` (when
you choose a model) and `strata update` do. To reach Strata from your phone or another PC, use
`strata launch --access lan`. It makes an API key (recommended; add `--no-api-key` to go without, with a warning). No browser opens unless
you add `--open`. Guide: [docs/TERMINAL_LAUNCH.md](docs/TERMINAL_LAUNCH.md). Whole client: [docs/TERMINAL.md](docs/TERMINAL.md).

### With AI agents

An agent installs Strata with the one-line command in Option B and runs
`strata launch --model NAME --yes --no-tui`. The steps, flags and exit codes are in [docs/AI_SETUP.md](docs/AI_SETUP.md),
and the repository rules are in [AGENTS.md](AGENTS.md).

### Connect your other apps

| App type | Base URL |
| --- | --- |
| OpenAI-compatible (most apps and agents) | `http://127.0.0.1:8080/v1` (any API key, any model name) |
| Anthropic API (Claude Code and others) | `http://127.0.0.1:8080`, without `/v1`. These apps add `/v1/messages` themselves. Claude Code: `ANTHROPIC_BASE_URL=http://127.0.0.1:8080` |
| OpenAI Responses API (Codex CLI) | `http://127.0.0.1:8080/v1/responses` ([setup](docs/DETAILS.md#the-responses-api-and-codex-cli)) |

`strata connect` prints the exact settings for OpenCode, Codex CLI, Claude Code, oh-my-pi, pi, Hermes Agent and OpenClaw.
`strata connect opencode --write` writes the file after asking and keeps a backup.

### Pictures

Pictures are on by default for models that can read them. Attach or paste a picture in the chat, or send it from your app.
The picture reader runs on the processor when your graphics card has less than 24 GB (a few seconds per picture, nothing
taken from the card's memory) and on the graphics card from 24 GB. The rule is the same for NVIDIA and AMD. Turn pictures
off per model, or choose the reader with `--vision off`, `--vision gpu` or `--vision cpu`.

The numbers behind the rule: on a Radeon AI PRO R9700 the card's reader took 57 ms for a 260-token picture and the
processor's took 3.1 s. On 16 GB cards (Tesla P100 and RTX A4000, Linux) the card's reader cost 3-5% of text speed,
because it holds about 1.4 GB that the model would use otherwise. On a Mac the reader runs on the Mac's GPU whatever its memory (0.45 s for a 260-token picture on an M4 with 32 GB, about 1.2 GB of the shared
memory); the processor reader needs about 50 s per picture there, so it runs only when you ask for it with `--vision cpu`.

### Good to know

- Choose the thinking level (**off, low, medium or high**) in the chat menu or in your app's "reasoning effort". Off is fastest.
- Strata answers one request at a time by default. For several at once, use `--parallel N` in `strata launch` or
  `"parallel": 2` in the config ([BATCHING.md](docs/BATCHING.md)). On a 12 GB card this makes each answer slower.
- Strata reads the first message of a chat in full, about 1 minute per 30,000 tokens. Follow-up messages start in seconds.
- Where your chats are stored and the whole API: [docs/INSTALL.md](docs/INSTALL.md#where-things-are-stored), [docs/DETAILS.md](docs/DETAILS.md#using-it).

## Build from source

<details>
<summary>Windows, Linux and Mac from the repository</summary>

[Download Strata](https://github.com/Niko1221/Strata/archive/refs/heads/main.zip) and unzip it, or `git clone` it.
**Windows:** double-click `START-HERE.bat`. **Linux:** run `./setup.sh` in the Strata folder.

**Mac (Apple Silicon, macOS 15 or newer):** install Apple's Command Line Tools once with `xcode-select --install`
(Xcode itself is not needed). Open Terminal in the Strata folder and run `./setup.sh`. It builds the Mac engine on your
Mac (about 7 minutes), asks the same questions, downloads the model and starts it. Next time, `./setup.sh` starts it
right away. Which size fits your Mac: [docs/MAC.md](docs/MAC.md).

The installer finds your card and sets up the right engine. It asks which model and size, how much context (how much text
the model keeps in mind) and whether it should read pictures. Press Enter for the recommended answer. Then it downloads the
model (about 70 GB) and starts it. If the download stops, run it again and it continues. Your browser opens the Strata
page at `http://127.0.0.1:8080`.

> While the model starts, your PC can be slow or stop responding for 1-3 minutes (longest the first time). Strata loads
> 35-55 GB into your RAM and locks part of it for the graphics card. Wait, and don't close the window.

Next time, run `START-HERE.bat` (or `./setup.sh`) again. It starts right away and downloads nothing twice. Close its
window to stop the model. `UPDATE.bat` (`./update.sh`) updates Strata without starting it. Updating, Docker, several cards,
where the files go and every option: [docs/INSTALL.md](docs/INSTALL.md).

To measure your PC, run `BENCH.bat` (`./bench.sh`). It takes 5-10 minutes and writes a report you can share
([how](docs/COMMUNITY_BENCHMARKS.md#run-the-benchmark)).

**Optional: more speed on one card when the whole model fits in RAM.** Two opt-in settings by RubenTorney (#1904, #1905).
They help when you run one graphics card and your RAM holds every expert, for example Q2_0 or IQ3_XXS on a 64 GB PC. Open
`strata-<model>.json` in the Strata data folder ([where](docs/INSTALL.md#where-things-are-stored)) and add:

- `"--adapt-async", "1"` to the `"args"` list. The expert cache then updates on a helper thread instead of pausing the
  answer. Measured on an RTX 5070 (Windows, Q2_0): +4-5% decode, +7-10% on code and long documents. Answers can differ
  slightly from the default and stay the same from run to run.
- `"STRATA_PIN_BY_NEED": "1"` in the `"env"` block (Windows). Windows can lock only about half the RAM for the graphics
  card, and this locks the experts the card needs first. Measured on the RTX 5070: +4.4% prompt reading.

```json
"args": [ ..., "--adapt-async", "1" ],
"env": { "STRATA_PIN_BY_NEED": "1" }
```

Restart Strata after the change. To undo, remove the two entries.

</details>

## How does it work?

Models like this one usually run on servers with hundreds of gigabytes of graphics memory. Your graphics card has
12-24 GB. Strata makes the model fit by sharing the work across your whole PC. Picture a library.

- The model is a team of 24,576 small specialists ("experts"), like 24,576 reference books. Each word needs only 10 of them.
- **Your graphics card is the desk.** It is small and fast. The roughly 4,000 books used most often lie open on it.
- **Your RAM is the shelves in the reading room.** All 24,576 books are there. When a word needs a book that is not on the
  desk, your processor reads it from the shelf at once, while the card keeps working.
- **Your SSD is the archive.** It holds a 29 GB lookup table. The model reads a few rows of it per word, so it does not
  need to sit in RAM.

<p align="center"><img src="docs/media/how-it-works.svg" width="860" alt="The model's 24,576 experts: the busiest on the graphics card, all of them in RAM, a lookup table on the SSD"></p>

A small helper also guesses the next few words. The big model checks all of them at once and keeps the right ones. You get
the same answer, 1.6-1.8x sooner. Long texts are read in big pieces, up to 8,192 tokens at a time, at over 1,000 tokens
per second.

<p align="center"><img src="docs/media/guess-and-check.svg" width="860" alt="A small helper guesses the next words; the big model checks them all at once and keeps the right ones"></p>

The longer explanation: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md). Every part and its numbers:
[the details](docs/DETAILS.md#how-it-works) and the [paper](docs/paper/Strata-Paper.pdf).

## Something went wrong?

- **My PC froze the first time Strata started.** It loads the model into RAM. Wait, and don't close the window. Still
  frozen after 10 minutes? Restart the PC, close other programs and try again, or pick a smaller size.
- **It stopped while downloading or installing.** Start the app or `strata launch` again, or run `START-HERE.bat` (or
  `./setup.sh`) again. It continues where it stopped.
- **It is very slow, the disk light keeps blinking, or it says "the engine stopped unexpectedly".** Your PC does not have
  enough free RAM. Close other programs (browsers use a lot), or pick a smaller size such as Q2_0 or IQ2_XS.
- **It says there is no engine for this PC.** In the app, open **Settings, Engine** and press **Update engine**. In the
  Terminal, run `strata update`. `strata doctor` shows which engine files are installed.
- **It says port 8080 is already in use.** Strata is already running. Look for its window.

More problems and fixes: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). Still stuck? Open an
[issue](https://github.com/Niko1221/Strata/issues). In the app, attach the file from **Settings, Engine, Diagnostics, View
logs, Save logs**. In the Terminal, paste the output of `strata doctor`. From source, attach `strata-<model>.log` from the
Strata folder. For a security problem, report it privately: [SECURITY.md](SECURITY.md).

## Credits and license

The model is [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) by the Qwen team. It was
compressed by [ISTA-DASLab](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF), UkisAI (Swift 1.5),
agentionai (Gyro), Unsloth, and [DavidAU](https://huggingface.co/DavidAU) — whose Turbo-Brilliance reason-mode re-quants,
[regular](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF)
and [abliterated](https://huggingface.co/DavidAU/Qwen3.8-Flash-Next-Abliterated-Uncensored-Turbo-Brilliance-50x-Reasoning-Instruct-GSQ-RCO-GGUF)
(the abliterated one built on [SC117](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF)'s
abliterated GSQ-RCO transplant, which carries [orcarouter](https://huggingface.co/orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF)'s
abliterated weights),
are the models this fork's reason modes ride and both of which the installer offers. Strata uses parts of [llama.cpp / ggml](https://github.com/ggml-org/llama.cpp). All credits:
[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#credits). Strata is open source under the [MIT License](LICENSE). A few
parts and every model have their own licenses ([which ones](docs/HOW_IT_WORKS.md#license)).

## Contributing

Found a bug, or want to send a change or a benchmark? Read [CONTRIBUTING.md](CONTRIBUTING.md) first. It says what to
include so that others can check your work.

## Support Strata

Strata is free and MIT-licensed. I build it and test it on whatever hardware I can get. If it runs well on your PC, a coffee helps
me keep testing new cards and fixing what you report. No pressure. Using it and reporting what you find helps just as much.

<a href="https://buymeacoffee.com/strataengine"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a>

Interested in sponsoring Strata? Contact me via [GitHub](https://github.com/Niko1221).
