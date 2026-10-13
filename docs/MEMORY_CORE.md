# Memory Core — server-side, harness-independent memory (this fork's track)

**Status: Stage 0 complete (dev build verified on :8005). Phase 1 memory service implemented AND
live-verified on the real model with real client shapes (merge fix `8b1826cd`; 702-test suite green).
Phase 2: Spike C mechanism replication PASS across 0.5B/1.5B/3B; Spike D0 PLE-addressing spec closed
(oracle-exact mirror, `0d325135`). Next: draft the shared engine patch (STRATA_KVPROBE +
STRATA_PLE_OVERLAY), then the coordinated measurement window. Last updated: 2026-10-11 (build session).**

This fork explores built-in long-term memory for Strata: recall that lives at the inference server, shared by
every client (OpenCode, Odysseus AI, the web app), with **no harness ever resending memory text into the
context window**. Following upstream rules, everything stays **opt-in**: the default serving path stays
byte-identical to the release it forked from (v0.1.42, `61b3fb5`).

- **Phase 1 — memory service (building):** a curated store (SQLite, FTS5 + a small local embedder) and
  automatic recall injected as a stable hidden block ahead of the conversation. Persistence rides on
  Strata's existing machinery: system-prompt-root checkpoints, pinned message boundaries, parked
  conversations, and `--slot-save-path` session files. Harness cost of memory: zero tokens resent, near-zero
  prefill re-read.
- **Phase 2 — numerical cartridges (research):** porting the *AKBASCORE MAM / NIRVANA cognitive cartridge*
  idea (Akbaş): facts read once become model-produced K/V + hidden-state representations the frozen model
  reads back without the source text ever returning. On this model that is **open research, not an established
  result** — see the architecture caveat below. Positive *and* negative results get recorded here.

## The dev install (how to work on this fork without risking the running model)

The machine runs the production engine at `:8004` from a separate, unmodified `~/Strata` install. RAM is the
hard constraint, measured on 2026-10-10: one engine sits at **50.7 GiB resident** of 62 GiB total — with
production up only ~5 GiB is free — so **exactly one engine runs at a time**:

```
./dev-up.sh     # stops production, starts this tree's engine on :8005 (1-3 min weight load, page-cache warm)
./dev-down.sh   # stops dev AND production if running - nothing auto-starts. Bring :8004 back
                # yourself (START-HERE / your launcher) when you want the main build again.
```

- Model files are **not duplicated**: `Strata-data -> ~/Strata-data` (a symlink; git-ignored) shares
  `models/`, `mtp/`, `packs/` with production. Writes keep hygiene rules: dev logs into this tree,
  session files into `Strata-data/sessions-dev/`, rebuilt packs into new `packs/<name>-dev/` dirs — never
  overwrite production artifacts in place.
- `engine/` was copied from the production build: `engine/BUILD.json` records `source=local, arch=89,
  src=1e0c97588848ea0f` at v0.1.42 — this checkout's source recompiled for the RTX 4090. `build/` is not
  copied (CMake caches are path-baked); first C++ work regenerates it (nvcc wants production down: ~1-2 GiB
  per parallel job against ~5 GiB spare).
- Engine config: `strata-dev.json` (git-ignored per upstream convention), port **8005**, same tuning args as
  production plus the config key `"slot_save_path": "Strata-data/sessions-dev"`. Note: `--slot-save-path` is a
  **server** flag / config key — the *engine* binary rejects it (`unknown argument`, found on the first dev
  boot 2026-10-10). A template ships as `strata-dev.json.example` — set your own paths and the `$HOME/Strata`
  fallback for `PRODROOT` if your production install lives elsewhere.
- Session-file sizing measured on this box (see smoke table): **13.3 KB per token** of conversation state
  (k8v4 KV + MTP draft state) — 3.35 GB for a 251K-token snapshot; ~2.2 GB/s save speed. Files are mode 0600,
  dir 0700, and **nothing deletes them automatically** — cleanup policy is a Phase 1 design item.
- Restore RAM guard (from #1601, 0.1.42): a restore needs free RAM = file size + a 2,560 MiB floor, read from
  the real `MemAvailable`. With the 51 GiB engine resident this machine has ~4.8 GiB free, so files up to
  **~2.27 GiB (≈170K-token snapshots) still restore live**; bigger ones are refused cleanly with the numbers
  in the error and the slot stays usable. `--session-save-reclaim` (0.1.42) frees retained K/V, parked
  conversations and unpinned checkpoints and retries a refused save/restore.
- **Testing protocol on this machine (one engine at a time, 62 GB RAM):** testing the dev build pauses this
  agent (the assistant itself is served by whichever engine is up on :8004). The swap is coordinated with the
  human: agent says "ready to test dev", human closes the main build and starts dev (or the agent runs
  `./dev-up.sh --force` once agreed), tests run against :8005, then `./dev-down.sh` stops everything -
  the human starts production back on :8004 when they want it (nothing auto-restarts).
  `dev-up.sh` refuses to stop a live production without `--force`. Optionally, adding a provider for
  `http://127.0.0.1:8005/v1` in the harness lets the agent keep talking *on the dev model* while dev is up.
- Running model on this machine: SC117 `Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF` IQ3_S (saved under the
  official filenames — check identity with `tools/strata_inspect.py`, not names), with the
  `sleepyeldrazi` **Q8_0 PLE** n-gram shard.

## Stage 0 smoke (measured 2026-10-10, dev build on RTX 4090 / 62 GB, engine = this fork's copy of v0.1.42)

| check | :8004 (production) | :8005 (dev) | verdict |
| --- | --- | --- | --- |
| `/health` | ok (max_context 524288) | ok (max_context 524288, images true, 1 slot) | **pass** |
| `/props` | n_ctx 524288 | n_ctx 524288, alias `qwen3.8-flash-next-iq3_s` | **pass** |
| greedy echo, temp 0, thinking off, `strata_checkpoint:false` | `STRATA-SMOKE-7790-OK` | `STRATA-SMOKE-7790-OK` | **byte-identical** |
| session save (`/slots/0?action=save`, conversation at 251,339 tokens) | — | 3,350,729,196 B in 1,543.8 ms, file mode 0600 | **pass** — #1444's `invalid K/V extent` does NOT reproduce on this config (k8v4 + MTP + YaRN×2 + Q8 PLE) |
| session restore of that 3.35 GB file | — | refused: `3213 MiB plus a floor of 2560 MiB needed, 4830 MiB available` | **guard behaves as designed** (file too big for free RAM while engine resident); slot unharmed |
| generation after refused restore | — | `STRATA-POSTRESTORE-4413-OK` | **pass** (clean failure, no state damage) |

Notes: dev boot was human-initiated this round, so load-to-health seconds weren't captured; next scripted
`./dev-up.sh` records them. The live agent conversation being served by the dev engine during these tests was
itself at 248,369 prompt tokens (≈47% of the 524K window) — the real workload this project exists to shrink.
Remaining of Spike A proper: restore across an engine restart and restore into a *different* conversation
(pending — needs its own coordinated window).

## Phase 1 — status: implemented, unit + mock tested, and LIVE-VERIFIED on :8005 with real client shapes

`serve/memory.py` (new) + eight small guarded hooks in `serve/server.py`. Off unless `"memory"` is in the
engine config (or `--memory DIR`); with it absent every path is the upstream one. Checked: two full runs of
the GPU-free `serve/test_*.py` suite against the changed `server.py` — 699 tests, OK (11 skips are the
GPU/Windows-only ones); one failure in an early run did not reproduce across those two re-runs (timing
suite under concurrent GPU load, name lost to output truncation). After the merge-injection fix the same
suites ran **702 tests, OK (skipped=11)** on `8b1826cd` — unit-proven, suite-proven, and live-proven:
recall across fresh conversations on the real IQ3_S model, OpenCode-shape requests (leading client system
turn), scope isolation, mute, and journal deltas, all measured at :8005 on 2026-10-10 late / 2026-10-11 early.

- **Store:** one SQLite file (WAL, dir mode 0700): `Strata-data/memory/memory.db` on this machine. Stdlib
  only - FTS5 (porter+unicode61) index, BM25 candidates re-ranked by
  `0.75 x rank + 0.25 x exp(-age/90d) + 0.10 x confidence`. Scopes: one `user` profile joined to every
  search, plus per-project scopes; a project fact never leaks into another project's recall. Dedup by
  normalized text per scope.
- **Injection:** the recalled block is merged into the head of the conversation's leading `system` turn
  (`<server_memory version=... scope=...>` + the client's own system text after it) - the Qwen chat
  template raises "System message must be at the beginning" for anything but exactly one system message
  at index 0, so a SECOND system turn is not an option (found live: OpenCode, which always sends its own
  system first, 400'd the moment a conversation's query first matched a fact - which is also why clients
  without a recall hit never saw the bug). With no leading system in the request, the block rides as its
  own system turn. Capped by characters (default 6000 ≈ 1.5K tokens at IQ3_S tokenizer rates).
  **Pinned per conversation** (key: scope + first user message, LRU 64): the bytes stay stable while the
  store changes underneath, so Strata's prompt checkpoints keep reusing them; new conversations pick up
  the current store version. **Per-turn tail recall (Gate-1's fix, 2026-10-11):** pinning alone froze
  recall at the opening question - measured, an 8-fact block pinned to the first user message answered
  one question of a conversation and missed the rest (Gate-1: 10/100). The head cannot simply move:
  at 184k+ token agent sessions this box reuses ~99.9% of the prompt bytes every turn (dev log:
  "prompt 183,987 tokens = 183,812 reused + 175 read"), so one changed head byte costs a full re-read.
  So each request also re-searches with the conversation's NEWEST user message and merges what is new
  (facts the head does not already carry) into that message - bytes at the end of the prompt, where the
  content changes every turn anyway. Head stable, tail fresh. Config knobs alongside
  `{"dir", "max_block_chars", "recall_limit", "inject"}`: `per_turn` (default on inside the feature,
  `false` restores the frozen-head path), `turn_recall_limit` (default 8) and `turn_block_chars`
  (default 2000 ≈ 550 tokens).
- **Per-request controls (headers):** `X-Strata-Memory-Scope: <name>` names the project bucket (clients
  opt in; default "default"); `X-Strata-Memory: off` mutes injection and journaling for one request.
- **Journal:** every completed chat-completions exchange is appended to `turns` (raw material for the
  Stage 3 curation loop and the eval harness) - collected transparently from the streamed chunks; a store
  error never breaks an answer.
- **Control API:** `GET /memory` (stats + recent facts; `?q=` for a recall probe, `?scope=`, `?limit=`,
  `?class=` shelf prefixes, and since 2026-10-12 `?since=` / `?until=` date bounds - epoch seconds,
  ISO date or datetime, junk is a 400: the date index for episodic questions, adopted from the
  Primnox "memory like a book" design) and
  `POST /memory` `{"op": "remember|forget|search|turn|turns|cartridge", ...}` - for the web app, for operators, and
  for the eval harness (hand-posted turns). Gated by the API key like `/props` and `/slots`.
- **Tests:** `python -m unittest serve.test_memory` (44 tests, no GPU): store/dedup/scope/stickiness units,
  a mock-engine HTTP end-to-end (inject → journal → mute → forget), memory-off 404 + byte-identical chat,
  400s for bad input, and (2026-10-11) a PerTurnRecall class: tail follows a new message while the pinned
  head stays byte-identical, head/tail dedup, replay determinism, `per_turn:false` = old behaviour, budget
  edges and content-part merging; (2026-10-12) ClassAxis gained the capped dedup bar, the date index and
  `time_bound` parsing. The loop's correction-target picker has its own offline suite:
  `python3 tools/test_curate_supersede.py` (8 tests, the live Westhope cascade as fixture). Re-run on
  the 2026-10-12 change: `serve.test_memory` 44 OK, `serve.test_server` 304 OK, the earlier full-suite
  figure (709 OK, skipped=11, 2026-10-11) still holds for the untouched paths.
- **Known gaps for the live pass:** injection currently rides `/v1/chat/completions` only (Anthropic and
  Responses dialects are next); no embeddings in ranking yet (lexical + recency first, embedder when the
  eval says it pays); curation loop is Stage 3 (the `turns` table is its queue).

## Phase 1 design (superseded note: implemented as above)

Store: `Strata-data/memory/` (SQLite WAL; records with text, provenance, confidence, decay; namespaces:
one `user` profile shared across harnesses + per-project overlays). Retrieval: FTS5 + local ONNX MiniLM-class
embedder, hybrid-ranked. Injection: one versioned, byte-stable hidden block per conversation, updated only
at idle so Strata's prefix checkpoints stay hot; rendered above the chat template, template untouched.
Writes: every turn journaled raw; idle-time distillation through Strata itself proposes durable facts,
deduped against the store, provenance kept. Control: `/memory` list/search/delete + per-conversation mute.

Gate 1 (after the eval harness): cold-recall QA accuracy ≥ baseline + 25 points with the block ≤ ~1.5% of
the context window — measured on a personal ~50-question cross-harness set; numbers land here.

## Phase 2 caveats the spikes must answer (why this is research)

- Qwen3.8-Flash-Next is 48 layers: **36 × Gated DeltaNet (fixed-size recurrent state, no per-token KV)** +
  **12 × Qwen Sparse Attention (real KV, 2 KV heads)**. MAM's append-only, bitwise-stable, RoPE-re-phase
  arithmetic lives in the 12 KV layers only; GDN state is order-entangled.
- KV is quantized (`--kv k8v4`) and RoPE is YaRN ×2 at 512K — re-phasing stored keys must survive both.
- Zero-embedding consolidation forwards have no token IDs, but PLE is a per-ID n-gram lookup (here the Q8
  table) — synthetic memory rows need a defined PLE story.
- MAM's own published envelope: dense, BF16, full-attention 7B models; scaling sealed at 22/80 correct on 80
  cartridges. Gate 3: grafted-cartridge recall ≥ 0.9 × text-injection on the QA set, or cartridges stay
  research notes and the text-memory layer ships as the product.
- Session save/restore on this config is itself unproven: upstream issue #1444 shows save failures
  (`invalid K/V extent`) on some setups; Spike A tests our exact stack (k8v4 + MTP + YaRN + Q8 PLE).
  First half DONE: save passes on this config at 251,339 tokens (3.35 GB in 1.54 s, no extent error) and
  the RAM guard refuses an oversized restore cleanly with generation verified after (Stage 0 table).

## Phase 2 — results on the record (measured 2026-10-11; full tables + every log in `spikes/`)

**Spike C — independent DC6 re-implementation, dense CPU instruments.** Mechanism replication
**PASS on three instruments** (Qwen2.5-0.5B/1.5B/3B-Instruct, bf16, CPU): incremental append-only
consolidation beats the faithful independent-consolidation control everywhere — INCR/JOINT ceiling
and INDEP: 21/24 & 8/8 & 0/8 at 0.5B, 24/24 & 8/8 & 3/8 at 1.5B; and the **bitwise append invariant
held 88/88 on every all-arms run** (24/24 on the committed baseline) — never once failed at any
model size, cut position, or bank size (latter two include the procedural-world scale runs).
- The 3B strict "collapse" from the first ladder pass was a **scoring artifact**: 3B answers the
  correct name then role-plays the next ChatML turn glued without whitespace (`DrevantorHuman: ...`),
  which a word-boundary regex cannot match. A loose column (exact-or-prefix) now rides in every log
  beside strict, never replacing it: loose 3B = 8/8 on every consolidation arm, while INDEP stays
  2/8 under both scorings with genuinely wrong entities. The rerun at 6 threads reproduced the
  4-thread original's strict scores bit-for-bit on every unchanged arm — determinism protocol:
  serial+idle runs with `torch_threads` logged.
- Marker arms (delimiter rows, self-describing headers, deep-row recompute, plan §11 arms 1/2/4):
  all tie at the ceiling on 1.5B/3B-loose; the §11 gate ("must beat plain DC6") is **unmet** on an
  8-case panel that saturates at ≥1.5B — separation needs the scale regime, and the scale regime
  is now measured: on 1.5B at 4 targets/size, banks 5/10/20/40/80, INCR scores 12/12, 8/12, 6/12,
  7/12, 4/12 (100/67/50/58/33%) against AkbasCore's 7B 100/90/80/40/28% — **TEST563's decay shape
  replicates on an instrument 5x smaller**, INDEP collapses at every bank size (3/20), invariant
  60/60, and large-bank misses are genuinely wrong-entity answers (plus honest refusals at 80).
  The marker-arm separation test now runs in that regime (banks 20/40/80, 4 targets).
- **Strongest positive finding — `INDEP_CTX_REBUILD` 8/8 strict on 1.5B AND 3B**: ONE causal
  zero-embed pass with the stored cut-residuals injected, over banks that were consolidated
  INDEPENDENTLY (CacheBlend-style, plan §11-4 bank form), fully repairs the control. Order-free,
  parallelizable cartridge assembly at joint-text quality — the property append-only DC6 lacks.
  This is the shape Spike B carries into the hybrid engine.

**Spike D0 — PLE n-gram addressing source study (closed, `0d325135`).** Precondition for writing to
the model's own n-gram table (plan §11-7, tier-1 of the three-tier design): the Python mirror
`spikes/ple-overlay/ple_ngram.py` now agrees **bit-exactly with the engine's own committed oracle
vectors** — the `ref/ngram.py`-generated fixtures `src/kernels/ple_parity.cpp` part A consumes (6
adversarial cases, 19 token rows × 16 heads, 0 differ; torch-free `--oracle` mode). Table geometry
re-measured on OUR Q8 shard: `data_start=192`, the exact-fill check the engine performs at boot
passes on this file today, 170-B Q8_0 rows, the head bands end 90 rows short of the table (90
unreachable rows; last row measured all-zero), dequant probes in-distribution. Net: for any token
sequence we can compute, on CPU, exactly which 16 table rows the engine will read — the precondition
for D2's writes. Still open, and D0 does NOT settle it: value calibration (the readout is trained;
written rows must land in-distribution) — that is precisely what the D2 marker probe tests in the
coordinated window. Record: `spikes/ple-overlay/NOTES.md`.

## Store operations — consolidation, budget, invalidation, self-feeding journal (added 2026-10-11/12)

Four server-side capabilities, all opt-in or byte-identical-when-off, each triggered by a
measurement rather than a guess. Unit-tested without a GPU (`serve/test_memory.py`, 25 cases);
live-verified on the dev build (RTX 4090 / 62 GB, engine 0.1.43/0.1.43.1).

**The journal feeds itself.** A `/v1/chat/completions` request that is not muted (`X-Strata-Memory: off`)
journals its exchange — now even when nothing was recalled. That last part was a real bug: the journal
context was built only when a recall block was injected, so every no-hit exchange streamed an answer and
was then forgotten, and a project scope with no facts yet could never bootstrap (empty store → no recall
→ no journal → empty store, forever). Found live: this very OpenCode session had stopped journaling at
13:46 because its provider config carried a mute header from an earlier benchmarking run; removing the
header and decoupling the journal from recall reopened the artery, and the session's own turns began
landing in the queue mid-conversation. A request with no user text (a bare tool-continuation) still
skips the journal — an answer nobody asked for is curation noise.

**Consolidation by content address.** The store dedups by exact normalized text on write, but the curation
loop re-derives the same fact in different phrasings, and the eval corpus is template-shaped ("KXXX is
licensed to YYY" and the question that asks it share every boilerplate token). Token similarity cannot
separate them — it flagged 4,161 "near-dupes" among facts that differ only in the payload. `tools/m2_merge.py`
hashes the payload instead: the rare tokens (document frequency ≤ 3) of each fact's normalized text. Two
active facts with the same payload in one scope are the same fact said differently. The survivor is the
most informative row (a declarative beats a question, since a question stored as a fact injects no
answer). 50 honest groups, 52 redundant rows — 13% of the store — retired in one command, zero engine
calls. Negation-mismatched groups ("X is Y" vs "X is not Y" share a payload, mean opposite things) are
reported, never merged.

**A budget that speaks before the wall.** `memory.max_memories` (0 = unlimited, the default path
byte-identical to earlier releases) caps the store at write time. A crossing write retires the worst
never-recalled rows first (lowest confidence, then oldest) and never touches a fact the recall has
actually served (`hits > 0`) or the write that triggered it — a budget that eats its own input makes a
sieve. `GET /memory` reports `max_memories` and `pressure` at 80% of the cap, so a background pass can
consolidate before the store silently overflows. Dev budget 1,500; the store ran 355–381 through the
work, well under, pressure false.

**Corrections version facts, they do not delete them.** A fact that stops being true is marked, not
removed: `invalid_at` and `superseded_by` (two instant ALTERs, old databases migrate on first open) turn
every overwrite into a walkable chain. Recall, search, the budget and write-time dedup see only active
rows; `op=invalidate` and `op=correct` retire or replace, and `GET /memory?include_invalid=1` shows the
history. This is why "Alice works at Acme" and "Alice works at Beta" no longer retrieve with equal
authority — the pattern Zep/Graphiti publish for temporal knowledge graphs, adapted to a flat text store.
An active write never dedups onto a superseded row: re-learning a retired fact starts a new chain.
`m2_merge` now retires by default; `--hard` still deletes.
The sweep's first live corrections (2026-10-12) found the targeting bug the same day: in a small
sub-corpus the connectives *are* rare - `faa/location/identifier/municipal` sit at df 3-6 among the
airport rows - so a Pruetz correction retired a Westhope row and an Arthur row retired that, a
Westhope→Pruetz→Arthur chain of nonsense, and the sweep targeted rows its own batch had created
40 seconds earlier. Two guards anchor every supersede target now: the shared payload must include a
token **unique to the target** (its subject - "westhope", never "faa"), and rows created at or after
the batch start are siblings, not prior truth. Probe-shaped traffic (a quiz question in, a ≤12-word
answer out - the user's own quiz format) restates the gold sheet instead of correcting the world:
its CORRECT verdicts demote to FILL and never target anything.

**The loop runs on a trigger, not a clock.** `tools/sweep_cron.sh` reads two numbers from `GET /memory` —
`pressure` and the pending-turn count. Both cool: one curl and exit, the engine never sees the tick.
Either hot: two idle-gated curation batches. Nothing is scheduled until the user installs the crontab
line in the script header.

**Four axes: who, what, whence, how long** (added 2026-10-12). The store already answered *who may
know* (scope) and carried *where it came from* (`source`: manual / curation-date / session) and *how
long it has lived* (`hits`, `last_seen_at`) — what it could not answer was *what the fact is about*,
so every recall pulled personal facts and eval-corpus trivia into one undifferentiated block. The cure
is a `class` column of Library-of-Congress-style call numbers. Taxonomy v1.1 (2026-10-12, grown
from store content, not invented — a content scan found the clusters first): top classes `STR`
engine, `MEM` memory-core, `HW` this machine, `USR` personal, `EVAL` quiz corpus, `OPS` setup,
plus `FIC` fiction canon, `TECH` industry knowledge, `DEC` decision records. The notation's three
tiers are all in production now: subclasses (`USR/PREF` — preferences are personal whatever their
scope), per-setting cutters (`FIC/VOLDR`, `FIC/KREVV`, `FIC/OPUVV`, `FIC/THYRA` — the instance tier
M3's cartridge banks will use, `MEM/CRG.3.431`), and genre shelves (one `decided` verb decides a
DEC record, unlike USR where a stray word is noise). 15 shelves populated; `class=FIC` pulls the
whole fiction family in one query. Unclassified is the default
and is **wild**: an unshelved fact rides every unfiltered request exactly as before, and only a request
that names a shelf (`X-Strata-Memory-Class: STR,USR`, or `?class=MEM` on the API) narrows — prefix
match, so `STR` pulls `STR/GEN` with it. Corrections inherit the shelf they replace; a re-learn never
re-shelves; `op=classify` plus `tools/m2_class.py` backfill with a conservative keyword map (argmax
with a two-token margin, or stay wild — a wrong shelf hides a fact from the conversations that need
it, a missing shelf only leaves it out of filtered ones). The personal lane fills on personal *signal*,
not on scope: "/home/<user>" in a project path and "the name of the township" in a quiz question both
tried to move into it during the first dry run and both got thrown out. Measured end to end the same
day: one question asked three ways — unfiltered 1,617 prompt tokens and the right answer; `class=USR`
292 tokens and the right answer; `class=EVAL` and an honest "I do not know" — the shelf decides what
the model can know about you per conversation, at a fifth of the bytes. And the point of all this,
as the user framed it: **shelves are how the model's knowledge grows without retraining or LoRA** —
a classified corpus is knowledge addressed by call number, importable a conversation at a time,
retrieved by the exact request that needs it. The honest limits ride with it: retrieval buys recall,
not reasoning; the context budget still caps what any ask can carry; and a shelf's search quality
gets a step change when the Phase 4 embedder gives every shelf a centroid to rank against.

**Recall stops paying twice for one fact** (`dedup_recall`, on by default). Measured first: the web
venue watched one Qwen3.8 fact take two of its eight slots while the store held five phrasings of it.
Three rules, any fires: token Jaccard ≥ 0.75 (light rewrites), payload equality (the merge tool's
content address), or payload intersection ≥ 4 rare tokens (the five Qwen rows share six). Two guards
came from failures, not theory: payloads exclude stop-words (a small store makes "the" look rare, and
two *different* facts about one subject collapsed on grammar in the first test run), and the rare-token
bar is relative but capped — `max(3, min(rows // 50, 5))` — because when one fact exists in five
copies its own payload stops being rare (why the merge tool's absolute df≤3 bar missed the Qwen
cluster), and because a bar that keeps growing with the store eventually calls a small sub-corpus's
own connectives rare and collapses its *different* facts into one slot (the 2026-10-12 airport
cascade at 2,384 rows). The read path caps - no human vetoes an injection, and a false collapse
hides a fact while a missed merge costs only one slot; `m2_merge` keeps the uncapped relative bar
because there a human reviews every group. A
question and its answer collide under these rules; the declarative takes the slot whoever ranked
higher (an injected question answers nothing). `m2_merge` v2 uses the same content rules over
union-find groups: 42 groups / 69 rows on the live store — and the dry-run earned its keep by catching
date collisions (Arc B390 vs G3, same release day, different products) that the hand-review pass must
veto before `--apply`, same law as the 50-group audit.

**The block header says what it showed**: `shown="8 of 405 active"` beside the version. The web venue
proved the failure — reciting a rotated-away sample and the current one with identical confidence,
because from inside a retrieval budget "I recall" and "I plausibly generated" feel the same. One
honest number per header turns sample blindness into a stated limit the model can speak around.

**Encryption at rest, stated honestly** (M2 job 6, `memory.encrypt: 1`, live in the dev config
2026-10-12). A 32-byte key sits beside the db (`memory.key`, 0600, auto-created; `memory.key_file`
moves it elsewhere - e.g. outside a synced folder). `memory.passphrase_env` names an env var whose
value is scrypt-stretched and XORed into the master: with it set, the key file AND the passphrase
are both needed to open the seal. The cipher is
stdlib-only because memory stays a dependency-free feature here: an HMAC-SHA256 keystream in counter
mode, encrypt-then-MAC — a flipped byte fails the open loudly instead of decrypting to mush. The seal
(`memory.db.enc`) is written atomically at every 50th write (measured: 22 ms over the 0.8 MB dev
store, so a power loss costs at most 49 writes of plaintext-only state) and at every clean shutdown,
which also shreds the plaintext — **a stopped server leaves only ciphertext; a running one keeps the
db readable, and `stats().at_rest` says so out loud** rather than claiming more. An optional second
factor: `memory.passphrase_env` names an environment variable, scrypt-stretched per file, XORed into
the master — the secret never enters the config file or git. Wrong key or tampered file aborts the
boot loudly: an "empty" store that quietly forgot yesterday is the failure mode this exists to
prevent. The planned next stage is a FIDO2 passkey seal (the `hmac-secret` extension wraps the
key-file itself — touch-to-open on the physical device; opt-in, needs python-fido2, and a printed
recovery key because a lost passkey must not be a lost memory).

## Agentic recall and the standing cartridge (added 2026-10-12, live at the next dev-up)

**The model browses its own store while thinking (item C).** `memory_search(q, class, since/until,
limit)` is offered as a tool and the **server** runs it: the model calls, the store answers in
milliseconds, the model continues - all inside one request, so no client tool wiring exists on the
route that offers it. The machinery is the MCP tool loop (`run_with_mcp`), production-tested since
the web app shipped: the memory call is a second executor - no hub, no worker thread, same events
in the same order, same client-visible trace (`strata_mcp`), so the web app shows memory rounds for
free. v1 scope, stated precisely: the loop rides the **OpenAI chat route** today (OpenCode, the web
app, curl, OpenAI-SDK clients); the Anthropic and Responses routes carry the recall block and the
cartridge but not the tool - wiring the loop to those dialects is the next small wire, so "any
harness" is the design, "chat route" is today's fact. Opt-in twice over: config
`"tool": true` for every conversation, or the `X-Strata-Memory-Tool: 1` header for one. Off (the
default): no tool joins the request's list and the prompt bytes are as before. A muted request
gets no tool - memory off means off. Scoping: the call searches the request's scope pair; a call
naming `class` browses to those shelves **on purpose** (the header filter says what rides every
turn, the tool is the model reaching for another shelf), a call naming none inherits the
conversation's filter, and a junk date comes back as an honest tool error rather than a silently
widened window. The request's own `memory_search` tool wins the name clash, as on MCP.

**The tier-2 standing cartridge (M3b): the store's K/V, once.** The spike measured the facts the
design rests on (engine 0.1.43.1, k8v4, RTX 4090): save 1,973 tokens -> 260,614,468 bytes in
0.19 s (**132 KB/token** - the file is the whole slot state: QSA KV plus a ~240 MB fixed
per-sequence cost of GDN recurrent state and MTP, which is why there is **one cartridge, not one
per shelf**); the first live restore on this engine: **105 ms** for 260 MB. A restored session
file acts as a **prefix**: a fresh conversation whose prompt matches it byte for byte from token 0
inherits its K/V and answers with that text in place (and one that does not match resets -
knowledge never leaks from a loaded file alone). Hence the ordering law: the standing text rides
FIRST, ahead of the volatile recall block - measured the hard way, reuse died at token ~250 when
the block led. The standing set is the store's own graduation: rows of the configured shelves
(`class_prefixes`, default USR + DEC), confidence descending then id ascending - never by hits,
because hits drift and the file is only valid for its exact bytes. The content hash is the
filename (`MEM-CRG-<sha8>.bin`, one per standing set, the rest GC'd); the engine validates the
pin inside the file (model, engine version, rope, geometry), so an upgrade costs one re-seed,
never a stale read. Facts beyond `max_chars` keep riding the tier-3 block: a small budget costs
standing status, never knowledge. Every failure path leaves the request completely normal - the
cartridge adds speed and never risk. `GET /memory` grows a `cartridges` view (shelves, facts,
left-for-block, file, bytes, state); `POST {"op": "cartridge"}` re-seeds now, 400 when off.

**The store's own page (item D).** `GET /memory-ui` (only when memory is on) serves a panel:
stats cards (rows/cap/pressure, pending turns, at-rest seal, cartridge state), search with
scope/class filters, the fifty most recent facts with per-row **Forget**, an **Add a fact** box
(class + scope), and a **Reseed cartridge** button when cartridges are on. It is a window, not a
wall: every action is the same `/memory` API any client uses, the page follows the web app's
auth (Bearer key via the API-key button, same sessionStorage pattern as the monitor), and the
server gained exactly one route line for it.

**Honest scope, stated before the venue proves it:** a cartridge is a prefix, not telepathy - the
standing text still rides the prompt, and the claim is about time. The first live venue ran the
same week the feature booted (2026-10-13, `tools/m3c_venue.py`, records in `spikes/cartridge/`):
restore **measured 81 ms** (median of 3, 247 MB); the pin law **measured working** (a file from
another engine configuration is refused with "config fingerprint differs"); the knowledge delta
**measured real** (warm 3/3 correct, cold 3/3 `idk`, wrong-cartridge control 3/3 no leak). What
did NOT measure: the fresh-conversation time win. A new conversation right after a restore re-reads
its whole prompt (reused 0, full prompt_ms) - engine resume is strict extension of the last prompt
the slot saw, and one clean adjacent probe confirmed the mechanism is real for continuations
(2,871 tokens given back, 1,837 ms -> 599 ms). Cross-conversation prefix inheritance is therefore
an open experiment with a name - the RAM-side conversation cache (`--conversation-cache-mib`,
upstream: faster, does not survive restarts) - and until it lands, the cartridge's earned value is
fast slot reload, not prefill skipped per conversation. One known upstream limitation belongs in
the beta matrix: session SAVE fails ("invalid K/V extent") on engines that load **no MTP drafter** -
upstream issue #1444, root cause diagnosed (the save passes the unloaded drafter with
`max_cells == 0`), fix in PR #1492, open and unmerged as of 2026-10-12, verified on an RTX 4090 D.
A server that loads MTP (this dev box, engine 0.1.43.1) is unaffected - save and restore measured
working - and where save is refused the cartridge degrades to no-cartridge requests, never a
broken conversation. MAM's state-without-text
recall (answer with the fact text nowhere in the prompt) needs engine grafting - Spike B - and is
not claimed here. A muted conversation replaces the slot's state, so the next standing request
pays one restore back (0.1 s, tracked by a flag, not a guess). And a session file is an access
credential to its KV content (upstream llama.cpp says so in #29678): the cartridge file holds
personal facts in bytes the model can re-read, so treat the `slot_save_path` directory with the
same care as `memory.db.enc`.

**Venue day, as one command each (built 2026-10-13).** F: boot the dev server with `memory.dir`
pointed at a fresh venue store and `max_memories: 0` (the oracle set alone is ~6k rows), then
`python3 tools/lme_harness.py --data longmemeval_oracle.json --out lme_oracle.jsonl --repeats 3
--muted-control` (pilot `--limit 5` first; resumable; the gold-map sidecar is what makes resume
safe). M3c: with nothing else on the port, `python3 tools/m3c_venue.py --ask <gold question about
a standing fact> --gold <substring>` - it checks the cartridge is active, lists standing rows to
choose from, measures restore (`timings.restore_ms`), warm-vs-cold (`timings.cache_n` = tokens the
engine did not re-read - the prefix law made visible), and the wrong-cartridge control (restore
`c5_comp.bin`, ask muted: the honest answer is the unknown-marker; a leak exits 2 and stops the
venue). Both scripts read timings from RESPONSES, never from engine logs - log lines can carry the
template's delimiter, and one already poisoned a data point this week.

## Licensing and attribution

- Strata (upstream) is MIT © Niko1221 and contributors; this fork's independent code keeps MIT.
- MAM / NIRVANA material: "AKBASCORE RESEARCH SOFTWARE LICENSE" © 2026 Mustafa Akbaş — ideas, algorithms and
  independently developed implementations are **not** covered (their license §6); research, personal and
  sub-$50M commercial use of the material are permitted; any copied or substantially derived code stays in
  clearly marked files with attribution. Prior MIT-era AkbasCore material remains MIT with its attribution
  notice retained.
- Technical disclosures cited: doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475.
  Closest published systems work: CacheBlend (arXiv 2405.16444), Prompt Cache (arXiv 2311.04934).
