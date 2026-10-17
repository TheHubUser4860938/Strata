# Memory Core — server-side, harness-independent memory (this fork's track)

**Status (2026-10-17): Phase 1 runs on its own engine - the store rebased to native PostgreSQL
(ADR-002, R1-R7 complete 2026-10-16; the rebase's venue gate is green on BOTH halves: retrieval
deltas inside the floor and explained row-by-row, answer-level 0.739 vs the 0.761 SQLite-era
baseline, inside ±0.05). The tier-3 accuracy line: arm C 0.761 per line (the doubled budget and the
reranker as a measured bundle), date grounding shipped at +0.207 pooled on the temporal pool, and
the stuffing trilogy closed with the purge as the cure (three ranking knobs ship, all default off,
all measured inert-or-harmful). The query-shelf router shipped 2026-10-17 default-OFF
(`memory.route_shelves`, six unit tests + the `--router-ab` lever in the retrieval A/B); its default
flip is the next venue arm. Phase 2: Spike C mechanism replication PASS across 0.5B/1.5B/3B; Spike
D0 PLE-addressing spec closed (oracle-exact mirror, `0d325135`). Next for Phase 2: draft the shared
engine patch (STRATA_KVPROBE + STRATA_PLE_OVERLAY), then the coordinated measurement window.**

This fork explores built-in long-term memory for Strata: recall that lives at the inference server, shared by
every client (OpenCode, Odysseus AI, the web app), with **no harness ever resending memory text into the
context window**. It shipped **opt-in** from the fork point (v0.1.42, `61b3fb5`) - and on 2026-10-16 the
owner flipped the default: **the memory system is ON by default** ("if someone doesn't want the built-in
memory system they won't be using our fork to begin with"). The byte-identical law survives as the opt-out:
`"memory": {"enabled": false}` returns every serving path to upstream Strata, byte for byte. The fork's
installs also default to port **8005** (upstream: 8080/8095) so a main install and a fork install can run
side by side on one PC.

- **Phase 1 — memory service (building):** a curated store (Postgres-native: tsvector/GIN + true
  BM25 + a small local embedder - the SQLite engine it launched on was retired by ADR-002,
  2026-10-16) and
  automatic recall injected as a stable hidden block ahead of the conversation. Persistence rides on
  Strata's existing machinery: system-prompt-root checkpoints, pinned message boundaries, parked
  conversations, and `--slot-save-path` session files. Harness cost of memory: zero tokens resent, near-zero
  prefill re-read.
- **Phase 2 — numerical cartridges (research):** porting the *AKBASCORE MAM / NIRVANA cognitive cartridge*
  idea (Akbaş): facts read once become model-produced K/V + hidden-state representations the frozen model
  reads back without the source text ever returning. On this model that is **open research, not an established
  result** — see the architecture caveat below. Positive *and* negative results get recorded here.

## Naming (settled 2026-10-16, decided in the system's own webui chat)

- **Strata** - the project, the whole build.
- **Strata Mnemosyne** (short form **Mnemo**) - the memory system this document describes: the store.
  Shelves and sub-shelves, a Postgres-native store (tsvector/GIN + true BM25 + pinned embeddings,
  ADR-002), the journal, the sweep. Passive, curated.
- **Clio** (KLEE-oh) - the assistant: the recall path and the working hands. The part with rules,
  budgets and judgement.

The split is architectural, not decorative: storage and retrieval are separate jobs in this design,
and the naming keeps "the memory" from being conflated with "the thing that searches it". It is also
genealogically exact - Clio is Mnemosyne's daughter, the Muse of recorded history born of Memory
herself: the retriever descends from the store. Known collisions are accepted knowingly (a Clio
award, a legal-tech company; a versioning file system, Warburg's atlas, and at least one published
LLM-memory system called Mnemosyne) - nothing here is being sold. Code keeps the existing names
(`serve/memory.py`, the `memory-core` branch); a rename to `mnemo` in identifiers is welcome in
new code, not chased through old.

## The dev install (how to work on this fork without risking the running model)

The machine runs the production engine at `:8004` from a separate, unmodified `~/Strata` install. RAM is the
hard constraint, measured on 2026-10-10: one engine sits at **50.7 GiB resident** of 62 GiB total — with
production up only ~5 GiB is free — so **exactly one engine runs at a time**:

```
python serve/server.py --engine strata --config strata-iq3_s.json --port 8005
                # stock startup with stock config naming (setup's strata-<tag>.json convention).
                # 2026-10-17: the dev-up.sh / dev-down.sh swap scripts are RETIRED (the owner's
                # call, with strata-dev.json renamed to the stock label) - stopping and starting
                # either side is by hand, and exactly one engine runs at a time still.
```

- Model files are **not duplicated**: `Strata-data -> ~/Strata-data` (a symlink; git-ignored) shares
  `models/`, `mtp/`, `packs/` with production. Writes keep hygiene rules: dev logs into this tree,
  session files into `Strata-data/sessions-dev/`, rebuilt packs into new `packs/<name>-dev/` dirs — never
  overwrite production artifacts in place.
- `engine/` was copied from the production build: `engine/BUILD.json` records `source=local, arch=89,
  src=1e0c97588848ea0f` at v0.1.42 — this checkout's source recompiled for the RTX 4090. `build/` is not
  copied (CMake caches are path-baked); first C++ work regenerates it (nvcc wants production down: ~1-2 GiB
  per parallel job against ~5 GiB spare).
- **`.venv` was copied too, and a copied venv keeps the ORIGINAL interpreter shebang**: `.venv/bin/pip` runs
  production's pip and installs into `~/Strata/.venv` (this bit the PHASE 4 install on 2026-10-14; the two
  stray packages were uninstalled from production the same day, it runs the pinned release set again). Use
  `.venv/bin/python -m pip install …` — `-m` resolves against the venv that owns the interpreter.
- Engine config: `strata-iq3_s.json` (git-ignored per upstream convention, and named by it - the
  upstream `strata-<tag>.json` scheme, adopted 2026-10-17 in place of the fork's `strata-dev.json`),
  port **8005**, same tuning args as
  production plus the config key `"slot_save_path": "Strata-data/sessions-dev"`. Note: `--slot-save-path` is a
  **server** flag / config key — the *engine* binary rejects it (`unknown argument`, found on the first dev
  boot 2026-10-10). A template ships as `strata-iq3_s.json.example` — set your own paths and the `$HOME/Strata`
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
  human entirely by hand: agent says "ready to test dev", human closes the main build and starts dev
  themselves (the stock command above); tests run against :8005, then the human stops dev and brings
  production back on :8004 when they want it (nothing auto-restarts). Since 2026-10-17 this is all
  manual - the `dev-up.sh` / `dev-down.sh` helpers and their `--force` guard are retired, the discipline
  lives in the people and in the 62 GB of RAM. Optionally, adding a provider for
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

- **Store:** one PostgreSQL cluster inside the store dir (`<dir>/pgdata`, dir mode 0700 - the engine
  ships inside the pip wheel, cluster start measured 0.3 s, ADR-002 R0), one database per store dir
  (the path's sha1; `serve.memory.attach()` is the tools' one-line twin of the old sqlite3.connect).
  The lexical arm is TRUE Lucene BM25 - GIN `@@` candidates, exact-lexeme df probes, store-wide
  stats from ts_stat, tf read from tsvector position lists (the venue re-record of 2026-10-16
  rejected `ts_rank_cd` for carrying no IDF: top1 0.394 -> 0.186, measured, and bm25 restored
  0.370) - re-ranked by
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

Four server-side capabilities, all opt-in or byte-identical-when-off as shipped (the 2026-10-16
default flip makes the whole memory system on-by-default; these knobs still tune or disable their
own paths), each triggered by a
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
got its step change when the Phase 4 embedder arrived (2026-10-14, above) — ranking no longer stops
at the words a question happens to share with a fact.

**Taxonomy growth by evidence (`tools/m4_shelves.py`, 2026-10-14).** The keyword map names what it
can and leaves the rest wild, so the wild rows ARE the evidence of what the taxonomy is missing. The
tool clusters active rows by cosine over the vectors the store already carries (leader clustering,
centroid normalized by its running norm, order-stable so two runs print the same clusters — verified)
and prints the groups big enough to be a subject, with the rarest words every member shares and three
exemplars. It PROPOSES: `--apply CLASS --ids …` posts `op=classify` through the API, and the
eyeball-before-you-shelve law from the 50-group merge audit still applies, because a false shelf hides
a fact from every filtered request that stops asking for it. What the first run on the dev store
showed is exactly why a human stays in the loop: at bar 0.70 the largest wild cluster was 28 rows
sharing *variable, environment, name, strata, user* — one fact in twenty-eight phrasings, which is a
MERGE candidate for `tools/m2_merge.py`, not a new shelf. Cosine cannot tell "one subject, many facts"
from "one fact, many copies"; a human reading three exemplars can.

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

**Encryption at rest, stated honestly** (rewritten 2026-10-16 by the Postgres rebase, ADR-002: the
single-file seal retired with the single file). A Postgres cluster is many files, and its plaintext
lives while the server runs - so the ciphertext is now the BACKUP. A 32-byte key sits beside the
store (`memory.key`, 0600, auto-created; `memory.key_file` moves it elsewhere - e.g. outside a
synced folder). `tools/m6_backup.py` pipes the cluster's own pg_dump (the server wheel ships the
binaries) through the same stdlib cipher that sealed the old file - an HMAC-SHA256 keystream in
counter mode, encrypt-then-MAC, the SMEM1 format reborn as SDMP1 - and writes the dump 0600 into
`<dir>/backups/`: measured 1.7 s for the 22.6 MB venue dump beside a running server, and the
restore into a fresh dir searched byte-identical (5/5 lexical top-10). `memory.passphrase_env`
names an environment variable whose value is scrypt-stretched per file and XORed into the master -
the secret never enters the config file or git, and `--pass-env` rides the same stretch. A bad
magic or a bad tag on restore ABORTS loudly - an "empty" store that quietly forgot yesterday is
the failure mode this exists to prevent; restore also refuses a database another session holds
(`pg_stat_activity` counts the neighbors), because pg_restore onto a running writer is how
half-restored stores are born. A stopped cluster is only as private as its data dir (mode 0700);
its backups are ciphertext. The planned next stage is a FIDO2 passkey seal (the `hmac-secret`
extension wraps the key-file itself — touch-to-open on the physical device; opt-in, needs
python-fido2, and a printed recovery key because a lost passkey must not be a lost memory).

## Agentic recall and the standing cartridge (added 2026-10-12, live at the next dev-up)

**The model browses its own store while thinking (item C).** `memory_search(q, class, since/until,
limit)` is offered as a tool and the **server** runs it: the model calls, the store answers in
milliseconds, the model continues - all inside one request, so no client tool wiring exists on the
route that offers it. The machinery is the MCP tool loop (`run_with_mcp`), production-tested since
the web app shipped: the memory call is a second executor - no hub, no worker thread, same events
in the same order, same client-visible trace (`strata_mcp`), so the web app shows memory rounds for
free. Dialect reach, measured at boot 14 (2026-10-13): the loop rides **all three dialects** -
/v1/messages (Claude Code) and /v1/responses (Codex CLI) answered a browse-only question 2/2 each,
no tool item leaked, and `input_tokens` reported the final round's prompt (the accounting fix in
the same commit); the trace field rides the chat route only (v1, documented). Opt-in twice over: config
`"tool": true` for every conversation, or the `X-Strata-Memory-Tool: 1` header for one — a SWITCH,
not a count: `1`/`on`/`true` turns the tool on for that request (the banner said "1 per request"
and got read as one search per request; it now says what is true). The per-request browse budget
is `memory.tool_rounds` (2026-10-17, the owner's ask so "the model can research the store at will
while thinking"): default **3** rounds — the bare constant that used to hide in the loop, so every
existing install reads byte-identical — and **0 = unlimited**: rounds end when the model stops
writing calls, when the context fills, or when the request is cancelled; each round re-renders and
spends, so the cost of free browsing is token-bounded by the window, not by a guard. The loop's cap
is the widest tool family it serves, so an MCP hub's own `max_rounds` never throttles the browse
tool below its configured budget. This box runs `0`. Off (the
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
restore **measured 81-84 ms** (median of 3, 247 MB, two clean venues); the pin law **measured
working** (a file from another engine configuration is refused with "config fingerprint
differs"); the knowledge delta **measured real** (warm 3/3 correct, cold 3/3 `idk`,
wrong-cartridge control 3/3 no leak). The fresh-conversation time win did NOT measure: a new
conversation right after a restore re-reads its whole prompt - engine resume is strict
extension of the last prompt the slot saw, checkpointed at message boundaries. What the
cache-enabled re-run (run 5, quiet port) did measure: the SAME question asked again returns
from the parked copy (2,719 tokens reused, 2,054 ms -> 77 ms); the same run's CONTINUATION
line (3,543 tokens, 0 reused, 2,085 ms) first read as block instability and was proven, the
same evening, to be the runbook's slot-replacement failure - an interleaved request from this
assistant's own harness sat between the adjacent pair, and the parked copy was gone under RAM
churn (evictions 83). The clean adjacent probe (session idle, `spikes/cartridge/
m2d_clean_probe.py`) measured continuation reuse WORKING: 100% of the parent prompt given
back (2,629 of 2,622 tokens), 2,011 ms -> 681 ms. The mechanism is in the code by
construction: the head block is PINNED per conversation (key `h + sha1(first user message |
shelf)`, byte-stable across turns) and the per-turn recall block rides the newest user
message - new bytes anyway - at the tail. Reuse works whenever the slot OR the parked copy
survives; it fails when an interloper takes the slot AND RAM admission refuses the park.
The earlier continuation reuse (2,871 tokens, 1,837 ms -> 599 ms) was the same working
mechanism, measured while nothing interleaved. The named cache experiment ran the same day
(`spikes/cartridge/M3D_CACHE_EXP.md`, boot 15): the RAM-side conversation cache
(`--conversation-cache-mib`) **delivers conversation parking** - a conversation displaced from
the slot returns from RAM in ~13 ms instead of re-prefilling (1,989 ms -> 705 ms measured) -
but it is keyed **per conversation**: three brand-new conversations after four parked ones
reused nothing (cache_n 0 x3). Cross-conversation prefix inheritance is therefore still open,
and the cartridge's earned value stays: fast slot reload, pin-verified provenance, parked
repeats - not prefill skipped for strangers. The block-placement lever is WITHDRAWN (it is the
shipped design); with the head pinned once per conversation, the next timing experiment is
injection-budget scaling, not placement. One known upstream limitation belongs in
the beta matrix: session SAVE fails ("invalid K/V extent") on engines that load **no MTP drafter** -
upstream issue #1444, root cause diagnosed (the save passes the unloaded drafter with
`max_cells == 0`), fix in PR #1492, still open and unmerged as of 2026-10-13 (re-checked the day
upstream 0.1.44 shipped - that release does not carry it), verified on an RTX 4090 D.
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
template's delimiter, and one already poisoned a data point this week. M2d's adjacent pair wants
the port quiet in the real sense: an interleaved request from ANY client - including this
assistant's own harness - replaces the slot's last prompt and silently invalidates the continuation
probe (run 4's spoiled line is filed as proof). Run M2d first, or run the venue when nothing else
is conversing.

## F venue - LongMemEval oracle on the built-in memory (measured 2026-10-13, boot 16)

The benchmark claim, run on the benchmark it is named for: **LongMemEval-V1 oracle** (500 questions,
official dataset from `xiaowu0162/LongMemEval`, ICLR 2025 - the abstract's own headline is that
commercial assistants and long-context LLMs drop ~30% accuracy on sustained-interaction memory,
verified against arXiv:2410.10813v2 the day this ran). Protocol: a dedicated store (`memory-venue`,
unlimited budget - the oracle's ~13k turns would thrash the dev store's cap), every haystack turn
remembered through the same `/memory` API any client uses, one fresh conversation per ask,
temperature 0.8, 3 repeats, and the **official checker prompt verbatim** (the type-specific
templates from `evaluate_qa.py`, embedded in the harness). The judge model is local - the same
engine, memory muted, temp 0, 10 tokens: the paper's decoding shape, a non-paper model. That is
disclosed everywhere this number travels.

**Accuracy: 0.683 per line (1,025/1,500); 0.694 by question majority (correct in >= 2 of 3
repeats).** Per-repeat 342/336/347 of 500 - the spread is temperature, not flakiness. By type:

| question type | accuracy | note |
|---|---|---|
| single-session-user | 0.953 | the fact was stated by the user: recall is near-free |
| single-session-assistant | 0.905 | the fact was in the assistant's own answer - remembered with the "The assistant told the user:" lead |
| single-session-preference | 0.878 | judged by the paper's rubric template |
| knowledge-update | 0.727 | newest-value law works more than it fails |
| abstention | 0.667 | the honest `idk` path; the other third invents an answer |
| multi-session | 0.639 | reasoning across two remembered sessions |
| temporal-reasoning | 0.425 | the known weak spot: day arithmetic over injected note dates, even with the checker's off-by-one tolerance |

**Recall, the Primnox-style view, scored inside each question's own scope:** turn_top1 0.509,
turn_top3 0.749; session-level 1.000/1.000 - with only the question's haystack in scope, landing in
the right session is easy by design and the turn-level numbers are the honest ones. **The cold
control is the strongest line in the record: with memory muted, 0.984 of asks answer `idk`, and
gold-substring leaks are 2 of 1,500 (0.13%)** - and 14 of the 24 non-`idk` cold answers are
preference questions confabulating a personality, not facts surfacing from nowhere.

What this venue does NOT claim: it is not the paper's full-history oracle (retrieval rides the
store's FTS5 + scoring, no embedding model, and the answer reads recalled notes, not 24 raw
sessions); the scoped design makes session-level recall trivially high; and the judge is local.
The claim is precise: on the official questions, with the official checker, this memory answers
0.68-0.69 correctly and is honest about not knowing 98% of the time it cannot see the fact.

**Budget A/B (same store, same questions, same protocol - only the head budget moves).** The
first run injected 48 notes / 12,000 chars; the second arm injected **96 notes / 24,000 chars**
(~6k-token head). Result: **accuracy 0.683 -> 0.721 per line, 0.694 -> 0.732 by majority** -
+3.8 points, far beyond the per-repeat noise band (arm B's repeats: 363/364/355 of 500, arm A's
342/336/347). Where it came from: **multi-session 0.639 -> 0.744** and **abstention 0.667 ->
0.778** - more of the haystack visible per ask means more cross-session links found AND more
"the fact is genuinely not here" recognized; assistant +4.7, preference +2.2, knowledge-update
+1.8. The one regression is single-session-user 0.953 -> 0.911 (net -8 of 192 - plausible
distractor cost of a bigger block on the easiest questions; flagged, not yet explained).
Temporal-reasoning moved 0.425 -> 0.433: the one type the bigger block did not move, and the
framing written here at the time ("the day-arithmetic weakness") was wrong - the diagnostic that
followed (`spikes/lme/TEMPORAL_DIAG.md`, 2026-10-14) found off-by-N arithmetic in only 12 of 57
reading failures, and the real mechanism in evidence COVERAGE on multi-event questions. That is
what arm C's bigger budget and the new embedder both target, and the record now carries coverage
fields that tell the two causes apart without hand-reading.
The honesty controls are identical (98.7% idk, same 2/1,500 leaks), the recall lines are
unchanged by construction (retrieval is scored independently of the injection budget - the
built-in sanity check passed), and the cost is conversation-start only: with the head pinned,
the doubled block adds ~2.3 s to the FIRST turn of a conversation (~1,400 tok/s measured) and
zero to every follow-up (continuation reuse measured 100%, same evening). The dev config now
ships 96/24k; the code defaults (8 notes / 6k chars) are untouched - this is opt-in, measured.
Records: `spikes/lme/budget24k-20261014-full.jsonl` (the map sidecar is shared with the first
run - same questions, `--skip-ingest` answers-only arm).

Venue incidents, filed because the class matters: the run crashed three times - the store's
4,000-char single-fact cap met long haystack turns (`535d247b` chunking, every chunk carries the
turn's gold), and 32 of the 500 questions carry **int** answers, which bit the judge template and
then `scrub` (`47d9916a`, `0d416954` - the class closed by normalizing at the load boundary, not
per consumer). The resume contract held all three times: the out-file + gold-map sidecar skip
completed question-repeats exactly, and no line was lost beyond the crashed repeat itself.
Records: `spikes/lme/oracle-20261013-full.jsonl` (+ `.map`, `.metrics.json`); rerun with
`python3 tools/lme_harness.py --data <oracle.json> --repeats 3 --style con --muted-control
--out <out.jsonl>` and aggregate with `python3 tools/lme_report.py <out.jsonl>`.

**Coverage fields (2026-10-14, from `spikes/lme/TEMPORAL_DIAG.md`).** Each scored line now also
carries `row_coverage` / `session_coverage` and `gold_rows`/`shown_rows`/`gold_sessions`/
`shown_sessions`: the FRACTION of a question's gold rows and gold sessions the recall carried.
`turn_top1/top3` are satisfied by any ONE gold turn, which is what hid the temporal failure — an
ordering question needs three events and got one. `lme_report.py` prints coverage per type and
splits wrong lines automatically into "every gold session present" (reasoning) versus "at least
one missing" (retrieval). Coverage is measured on the recall candidate list at the request's own
limit, the same list `block_for` draws from, so a char budget that trims the tail makes it a mild
upper bound on what the model read. Records written before this change lack the keys and report
`n/a` — never a false zero.

**Arm C protocol (built 2026-10-14; one boot, both arms).** Config `strata-venue-armc.json`
(git-ignored): the venue store at **192 notes / 48,000 chars with the embedder loaded**. The arms are
run against that one process, so nothing but the reranker differs between them:

```
systemctl --user stop strata-sweep.timer            # the window owns :8005; restore it after
python3 tools/m4_embed.py --dir Strata-data/memory-venue   # once, up front. **~11 min, not 90 s**:
                                                  # measured 2026-10-14 on the venue store's 10,973 rows
                                                  # (16 rows/s), because its rows run to 3,900 chars and
                                                  # each pays a full 512-token pass. The 151 rows/s figure
                                                  # was measured on the dev store's ~250-char facts.
                                                  # This one runs BEFORE the boot: a sealed store has no
                                                  # plaintext, so m4_embed now reveals it (key-file beside
                                                  # it) and re-seals on exit - vectors included, because
                                                  # they live inside memory.db. Verified by read-back:
                                                  # 64 rows embedded, reopened the sealed store, 64 there.
# 0. the mechanism A/B: both arms, 500 questions, NO generation - ~2 min of store time
python3 tools/lme_retrieval_ab.py --data <oracle.json> --map <shared.map> --wide-k 192
# 1. the outcome: the full stack (budget + embedder), the number the project is asking for
python3 tools/lme_harness.py --data <oracle.json> --skip-ingest --map <shared.map> \
        --repeats 3 --style con --muted-control --out armc.jsonl
# 2. only if step 1 is flat or mixed: the lexical arm, same boot, ~1.5-2 h
python3 tools/lme_harness.py --data <oracle.json> --skip-ingest --map <shared.map> \
        --repeats 3 --style con --muted-control --no-embedder --out armc-lexical.jsonl
python3 tools/lme_report.py armc.jsonl
```

**Step 0 does not need the venue boot at all.** `--offline <dir>` copies the store dir (whole dir: the
`.enc` AND the key, or a sealed store arrives as an empty one), opens the copy with `encrypt=True` so a
ciphertext-only store reveals itself to that process, embeds what the hybrid arm needs, splits into two
copies, and answers every question twice through the store's own `search()` — one service with the
reranker, one without — scored by the same `recall_metrics` the venue uses. Measured on the sealed venue
store: 10,973 rows embedded in ~11 min, then 500 questions x 2 arms x 2 k values in ~45 s, with the
server never touched. `--work <dir>` keeps the embedded copy (it re-seals on close, vectors and all) so a
ranking idea can be re-scored in ~45 s instead of re-embedding every pass, and `--dump <file.jsonl>`
writes both arms' hit lists per question so a per-type regression is readable case by case.

**Why the A/B is step 0 and the lexical arm is step 2.** The retrieval metrics — top-1/top-3 and the
coverage fields — need only the store's own search, so both arms score in ~2 minutes with no prefill and
no judge: same questions, same scopes, same gold map, and the same `recall_metrics()` the accuracy run
uses, so a mechanism number and an outcome number can never disagree about what one hit list meant.
That answers "did the reranker move the evidence, per type" before a single token is generated, and it
is what makes a flat accuracy result interpretable instead of merely disappointing. The full stack runs
first because it is the number being asked for; the lexical *accuracy* arm is only worth its 1.5–2 h if
the outcome is ambiguous. Two guards decide for themselves whether the A/B happened at all: every
reply's echoed `"embedder"` field must match the request that produced it, and the arms must return
different hit lists for at least one question — identical lists everywhere means the reranker never
moved, and the tool exits 2 rather than reporting a null comparison as a result (smoked on the dev
store, whose scopes are not `lme_*`: it refused, correctly).

Read as: **arm C (full stack) − arm B (0.721) = the combined effect of budget + reranker**, which is
the question being asked; the A/B's per-type retrieval and coverage deltas say *which part* of the
system did the work, for free; and if the combined result comes back flat, the lexical accuracy arm
turns one ambiguous number into two clean ones (**budget delta** and **reranker delta**) instead of a
retirement decision made on a coin flip. Both accuracy arms run at temp 0.8 with the official checker
and the muted control in the same pass. The venue law applies unchanged — nothing else touches :8005
during a timed run, including the agent's own harness, so the run is backgrounded and the turn ends;
the control must show 0 leaks or the venue exits 2. Expected shape from the temporal diagnostic: if
temporal-reasoning is coverage-limited, the A/B shows `session_coverage` rising on the multi-evidence
types and the accuracy run should follow it materially above 0.433; if coverage rises and accuracy does
not, the residual is a reasoning limit — quant/model territory, stated plainly, and the row stops being
a memory-project target.

**Arm C ran (2026-10-14, `spikes/lme/armc2-20261014-full.*`): 0.761 per line / 0.766 by majority, against
arm B's 0.721 / 0.732 — +4.0 points, per-repeat 381/378/383, so the delta is ~7x the noise band.** The
pre-registration held in both of its halves:

- **Corrected the next day: the gain was a BUNDLE, not the reranker alone.** As filed on 2026-10-14 this
  bullet read *"the reranker earned the gain; the budget did nothing"*, resting on `block_cov.py` rendering
  **14.6 notes / 18,735 chars at 96/24k AND at 192/48k**. That measurement was stale by construction: the
  head block is pinned by (scope, first user message, shelf, arm) and the **budget is not in the key**, so
  flipping budgets inside one process re-measures the first block unless `_blocks` is cleared — and this
  probe never got the fix its three siblings had (see "Probe hygiene, second instance" below). Re-run with
  the cache cleared (`tools/m4_blockcov.py`, 500 questions, both arms, both budgets):

  | budget | arm | notes shown | chars | gold-row coverage |
  |---|---|---|---|---|
  | 96 / 24k | lexical | 14.6 | 18,735 | 0.895 |
  | 96 / 24k | hybrid | 15.9 | 18,931 | 0.946 |
  | 192 / 48k | lexical | 19.1 | 24,429 | 0.942 |
  | 192 / 48k | hybrid | 20.3 | 24,934 | **0.965** |

  The doubled budget reaches the prompt after all: at 24k the CHAR cap binds on ~1,300-char venue rows,
  while at 48k what binds is that an oracle question's own scope holds only ~22 rows. So the reranker adds
  **+0.051** coverage at 24k and only **+0.023** at 48k, the budget adds **+0.047** lexical / **+0.019**
  hybrid, and arm B → arm C moved rendered-block coverage 0.895 → 0.965 (+0.070) through BOTH levers at
  once — arm B ran with no embedder at all, so no accuracy run on that pair can attribute the +4.0 to one
  of them. **C1 (lexical at 192/48k) was skipped on the stale number, and it is precisely the arm that
  would separate the two, so it goes back on the venue queue rather than being re-argued here.** What still
  stands from the original bullet: the reranker is a real positive (coverage +0.051 at the shipped 96/24k,
  `turn_top3` 0.749 → 0.823), the dev config's budget question is a different regime (note-bound, measured
  +0.000), and the temporal row is a reasoning ceiling at 0.852 session coverage.
- **Coverage rose, and temporal moved only 0.433 → 0.465.** At 0.852 session coverage the evidence is in
  the prompt; of 346 wrong lines, **244 had every gold session present (reasoning)** vs 102 missing one
  (retrieval). Per the pre-registration, that makes the row a reasoning ceiling for this model class
  rather than a memory target.
- **Accuracy is not running on rank order here.** knowledge-update lost 0.139 of retrieval rank-1 to the
  reranker and its accuracy moved **+0.005**. That is why the speaker-aware re-order (the assistant-echo
  fix, worth +0.10 retrieval rank-1 offline) is **parked**: it optimizes something the answer did not
  use, and the venue showed that directly rather than in a hunch.
- **Control: 1478/1500 `idk` (98.5%).** The single "gold leak" is the substring rule meeting a
  three-character gold (`100`) guessed by a muted model — arm B logged two of the same. So the gate is
  stated honestly as **a noise floor of 1–2 substring coincidences per 1,500**, not zero; and the
  harness *counts and reports* them rather than exiting 2, which corrects the paragraph above this one
  (it promised an exit that was never implemented).
- **The offline instrument is validated, not trusted:** its lexical arm reproduces arm B's filed
  `turn_top1/turn_top3` (0.504 / 0.749) to three decimals from a different process, a different code
  path and a copy of a sealed store. That is what licenses the A/B's per-type numbers as predictions
  about the live venue — and it cost ~45 s instead of 2 h, with the port never released.

## Semantic recall — the bge-small hybrid layer (added 2026-10-14, PHASE 4; opt-in)

**The RRF fusion constant, swept and reset (2026-10-15, `tools/lme_retrieval_ab.py --rrf-k`, offline
venue copy: 10,973 rows, 500 questions, no engine, deterministic).** The fusion constant was `k=60`
because that is the convention from the original RRF paper. Two of the six external systems read for this
workstream (Graphiti `search_utils.py`, Zep, including its retired Go CE) ship `rank_const=1` for the same
job, which is reason enough to sweep it on our own store rather than argue from convention:

```
  RRF_K     top1 (k=10)   top3 (k=10)   row_cov (k=16, the width a venue-shaped   row_cov (k=192)
                                        block actually shows: ~15 notes)
      1        0.517          0.849                       0.936                        0.970
      5        0.506          0.832                       0.934                        0.970
     10        0.506          0.832                       0.932                        0.970
     20        0.504          0.828                       —                            0.970
     60        0.504          0.823                       0.932                        0.970   <- shipped
    120        0.504          0.823                       —                            0.970      before
```

`k=1` is at or above every other value at every width, so it is now the shipped constant
(`serve.memory.RRF_K`, a module constant precisely so this sweep is one flag). Three things about the
number, all of them on the record:

1. **It is a rank-order win, not a block-content win.** At k=192 every constant scores identically, and at
   the width the block actually shows the gain is +0.004 row coverage — which by the filed causal chain
   (coverage +0.048 → accuracy +0.040) is worth ~+0.003 accuracy, inside the repeat band. The place this
   matters is the **agentic `memory_search` tool**, which returns a short list where top-1 and top-3
   quality are the whole product.
2. **It is not free per type.** multi-session top1 +0.033, user +0.016, temporal +0.016, preference top3
   +0.067 — but single-session-assistant **-0.018**, and it does **not** repair the knowledge-update
   -0.139 the reranker costs at top-1 (0.625 → 0.486 either way). Sharpening the fusion moves the order;
   it does not decide which arm's judgement dominates.
3. **The property that justified rank fusion survives.** The reach probe exists because raw-score blending
   buried a rank-1 exact-lexical answer at 14th. At `k=1` a lexical first place still outscores a
   both-arms fifth (1/2 > 1/6), and `tools/m4_reach.py --auto 40` on the live dev store scores **identically
   at 1 and 60** (hybrid top1 0.850, median rank 1) — no regression where the design was originally tested.

Tests: 86 + 4 green with the new constant. The default path with the embedder OFF is untouched: this line
is never reached without an embedder.

**Rank provenance, and the attribution it produced in one run (2026-10-15, `serve/memory.py`,
`tools/m4_consensus.py`).** Every hit now carries `lex_rank` (its position in the bm25 list, captured
BEFORE the blend re-sorts it) and `cos_rank` (its position in the cosine scan) — both values were already
computed inside the fusion and thrown away. Nothing the model reads changed: the block renders scope+text
only, and the MCP tool whitelists its fields (`serve/memory.py:198`), so the provenance rides the HTTP API
and the A/B dump. It paid for itself the same night, on the venue copy at n=500:

```
  what landed the hybrid arm's rank-1 slot          n    that slot was the right row
  both arms agree at rank 1                       174                0.615
  bm25 rank 1, cosine demoted it                  201                0.463
  promoted from bm25 rank 2                        56                0.286
  promoted from bm25 rank 3+                       27                ~0.40
  cosine-only (bm25 never proposed it)              0                  -
```

and all 11 knowledge-update rank-1 flips (the filed −0.139) share one mechanism: **the winner was the
cosine arm's own rank-1 row, every single time.** Two claims died with the table — "cosine-only rows bury
the answer" (it happens ZERO times at rank 1 under k=1) and the fix I was about to build on the −0.139.
`tools/m4_consensus.py` then scored three placement laws from the dump in seconds, no store, no engine:

| law | top1 | top3 | knowledge-update top1 |
|---|---|---|---|
| shipped (RRF k=1) | 0.517 | 0.849 | 0.486 |
| cosine-only out of the top 1 | 0.517 | **0.826** | 0.486 |
| cosine-only out of the top 3 | 0.517 | 0.843 | 0.486 |
| weighted RRF, lexical ×2 | 0.506 | 0.830 | **0.611** |

The gates do not move rank-1 at all and cost rank-3, because the rows being pushed down include gold ones.
The weighted arm recovers most of the −0.139 (0.486 → 0.611 against the lexical arm's 0.625) and pays for
it in overall top1 and top3. After `lme_posrank.py` showed block order is worth ~nothing to accuracy, all
three are metric swaps rather than product gains, so **none of them ships** — the reranker's +0.040 came
from coverage on multi-session and abstention questions, not from rank 1, and this is the second time the
same regression has resisted a ranking fix (the speaker/echo demotion is parked for the same reason).

Tests: 4 new in `serve/test_embedder.py` (8 green) — ranks agree with what each arm actually returned, a
bm25 first place keeps its recorded position, the embedder-off path carries no cosine provenance, and the
fields survive read-path dedupe (the case that needs them most, since that is where duplicate families
live). The running dev server is still the pre-provenance build: the fields land at the next dev-up.

**Position inside the block, asked twice and answered twice (2026-10-15).** The standing claim "at this
block size the answer does not ride on rank order" was inferred (the reranker moved retrieval rank-1 by
0.139 and accuracy by +0.005), so it got measured - and the two ways of measuring it disagreed:

- `tools/m4_position.py` - synthetic and within-subject: same rows every arm, only the gold's POSITION
  moves (front / middle / tail), block rendered by `MemoryService._render_block` itself so the bytes cannot
  drift, official checker, style `con`. First 8 questions x 3 arms x 3 repeats, then stopped:
  **front 0.946 / middle 0.839 / tail 0.763** - a ~19-point primacy effect. The catch is in the
  construction: that block holds exactly ONE answer-bearing row, so the reader's only route to the answer
  is finding that line. It measures the reader's attention, not the product.
- `tools/lme_posrank.py` - the product path, and it needed no new run at all: the arm-C record stores the
  ordered hit list per line and the ingest sidecar says which rows carry the gold. n = 1,500 filed lines.

```
  accuracy by the gold row's position:   1 0.748 | 2 0.835 | 3 0.776 | 4 0.722 | 5 0.622
                                         6 0.714 | 7 0.889 | 8 0.250 | 9 0.667 | 10 0.833
                                    gold row ABSENT from the list: 0.778
  within type, position buckets 1 / 2-3 / 4-6 / 7-10:
    single-session-assistant   0.97  0.97  1.00  0.93     single-session-user   0.90  0.95  1.00    -
    multi-session              0.87  0.85  0.69  0.81     preference            0.98  0.90  0.75    -
    knowledge-update           0.73  0.81  0.75  0.17(n=6)     temporal          0.42  0.62  0.36  0.25
```

Retrieval is deterministic, so a question's gold position is identical across its three repeats - position
is perfectly correlated with what kind of question it is, which is why the per-type table sits beside the
overall one instead of being assumed away. **No type shows a monotone position effect.**

The resolution, stated as one sentence each: the reader does have a strong primacy bias when nothing else
in the block can help, and in a real corpus something else almost always can. That is also why lines with
**no gold row in the list at all answer correctly 0.778 of the time - better than rank-1 lines (0.748)**: a
missing gold ROW is not a missing answer, and anyone quoting "the gold was in the block" owes that
sentence. Consequence for the RRF change directly above: its win stays a rank-order win - real for the
agentic `memory_search` tool, worth ~+0.003 through the block - and re-ordering is **not** where arm C's
remaining 24% lives. The synthetic probe's 72 lines are kept as a pilot of reader attention, not as a
product result.

**The last two tier-3 levers, both measured the same night (2026-10-15, offline; `tools/curate_loop.py
--no-date`, `tools/m4_subject.py`).**

*Grounding the extractor — the shipped change, verified.* 10 pending journal turns, both arms, two passes
each (extraction runs at temp 0.8), `--extract-only` so nothing was written and the turns stayed pending:

```
  arm         turns  facts  facts/turn  chars/fact   facts naming an absolute date   voids  fails
  dated           7      8        1.14           94                              6        0      0
  undated (control)   9     11        1.22           91                              0        0      0
```

**0% → 75% of facts now carry a resolvable date**, while facts per turn did NOT rise (1.14 vs 1.22 — if
anything the dated prompt is more selective) and each fact grew by 3 characters, which is the length of the
date itself. Zero geometry voids, so the longer prompt still render-matches the engine's own count. The
stated falsifier ("grounding bought verbosity, not dates") did not fire. Sample output makes the mechanism
concrete: `On 2026-10-15, the user instructed the assistant to proceed with tasks that do not require the
venue…` and `A −0.139 investigation occurred prior to 2026-10-15` against the control's dateless
equivalents. Small sample (7-9 turns), so the point estimate is soft — but the direction is 0% to 75%, and
the cost metrics are flat, which is what the decision needed.

*The subject axis — the last retrieval idea, and it is dead at this size.* Graphiti's entity rollup and
mem0's subject index converged independently, and the store already computes the rare-token subject at
write time for the merge veto and discards it at read. There was visible headroom: coverage at the width a
venue-shaped block shows is 0.936 overall but **0.893 on multi-session and 0.920 on temporal** questions,
the two multi-evidence types. Tested by searching deep (limit 64) and pulling rows that share a subject
with the anchor hits up into the 16 slots the block would show:

```
  ROW COVERAGE at the block's own width (k=16), venue copy, 470 scored questions
  type                        n    shipped   gather >=1 shared   gather >=2 shared
  OVERALL                   470      0.936              0.933              0.936
  multi-session             121      0.893              0.889              0.893
  temporal-reasoning        127      0.920              0.916              0.920
  questions whose coverage moved AT ALL: 4 of 470
```

The gather does not raise coverage; the lenient variant slightly LOSES it (it displaces as many gold rows as
it recruits). Four questions out of 470 is not a mechanism. At this store size the bm25+cosine fusion is
already surfacing the rows a subject index would go and get, so the entity idea needs a graph database and
a bigger corpus to be worth its cost — which is exactly the caveat both source systems leave unsaid.

**Where that leaves tier-3.** Six candidate levers have now been measured and killed at ≤11k rows: injection
budget (+0.000), shelf navigation (oracle == baseline), denser shelves (+0.015), the two-stage router
(−0.031), near-dupe suppression as a retrieval win (0.2% of a block), rank order inside the block (~0 on the
product path), RRF tuning (+0.004 coverage), and now the subject gather (4/470). The retrieval side is at its
ceiling on this corpus: 0.936 coverage at the block's width, and the errors are 244-to-102 skewed toward
reasoning with the evidence present. **What is left is what gets WRITTEN (grounding, measured positive
tonight; junk pre-filters) and how it is PRESENTED (labels and structure, not order), plus the one open
question these results cannot answer: whether any of them flip at 10k-100k rows, which is the S-split's
job and needs a venue boot.**

**Can we even measure self-feeding memory? Not yet - so the column got added (2026-10-15,
`tools/m4_selffeed.py`, `serve/memory.py` `turns.injected`).** mem0's 32-day production audit (#4573) names
"recalled-context re-extraction" as a junk mechanism, and Strata has the same shape: the server injects a
block, the model narrates over it, the journal stores both halves, and curation extracts from the pair. The
first attempt to measure it here fell back on word overlap and the result is mostly a measurement failure
rather than a finding:

```
  701 curated rows in the live store, traced through provenance.turn_id back to their journaled turn
    UNMATCHED - shares <0.12 content-word Jaccard with EITHER half        291   41.5%
    no turn_id in provenance (all 148 such rows are from curation-2026-10-11, the first spike run)
                                                                            148   21.1%
    leans ASSISTANT-derived                                               139   19.8%
    leans user-derived                                                    110   15.7%
    both halves                                                            13    1.9%
```

Three honest conclusions. (1) A lexical test cannot answer this: 41.5% of curated facts are paraphrases that
match neither half, so the 56/44 split is computed on a biased third of the store and is not a basis for a
filter. (2) "Assistant-derived" is not the same as junk — in a coding session the durable decisions
legitimately land in the assistant's narration (row 422: *"The merge tool's survivor rule requires
adversarial review because a wrong-town fact and its correction…"* is a real project fact that happens to
come from the assistant's text). (3) The only sound test is "did this fact restate a row the SERVER put on
screen, without the user saying it?" — and the journal had no column for what was on screen, even though
`request_state` has carried the id list in `ctx["hits"]` since the block existed (`serve/memory.py:1486`).
So the fix is one nullable column plus the three-line pass-through, with NULL and `[]` kept distinct on
purpose (unknown vs recall-ran-and-showed-nothing are different facts), and the migration tested by dropping
the column and reopening rather than by trusting the create path. 88 tests green. The audit re-runs itself
once the restarted server has journaled turns with the field; until then it prints `measurable by
provenance: 0 of 701` rather than dressing up the word-overlap floor as an answer.

**Near-duplicate families, and what they actually cost (2026-10-15, `tools/m4_families.py`, live dev
store copy + 300 real journal blocks).** Prompted by Graphiti's upstream issue #1956 — their invalidation
pool is capped at 10 candidates, so a subject's dead history fills the pool and the live fact never
retires. Strata's write guard has the same shape (`serve/memory.py:552`, cosine ≥ 0.85 then `cand[:8]`),
so the audit asked whether the cap is leaking near-dupes into live rows:

```
  2,668 active rows | cosine >= 0.85, content-word Jaccard >= 0.75, same scope
    violating live pairs          90
    duplicate families            25          rows inside one: 78 (2.9% of the store)
    wasted slots store-wide       53
    explained by the top-8 cap    0 of 90     (worst case observed: 7 higher-cosine live neighbours)
  WHAT IT COSTS IN THE PRODUCT — 300 real journal blocks, hybrid, 96/24k
    notes per block               96.0
    DUPLICATE SLOTS PER BLOCK     0.21  (0.2% of what the model is shown)
    blocks with >= 1 duplicate    14%     worst block: 3 repeated lines
```

Three lessons, and the middle one is a near-miss worth keeping:

1. **The cap is not the cause.** Every violating row was inside the tested window at write time, so the
   merge laws SAW the candidate and kept both. The reason is the subject veto firing on rare non-subject
   tokens — an 11-row family differing only in "GPU"/"machine"/"equipped", a "(6)" list prefix, a quoted
   "'arm c test'". In a 2,668-row store `df <= 3` is trivially satisfied, so the veto that protects names
   also protects rewordings. (Same failure shape the guard already fixed once for STOP words.)
2. **The store-wide number is not the product number.** 53 wasted slots looked like 55% of a 96-note
   block, and the guard fix was half-scoped on that strength. The real cost is **0.21 slots per block
   (0.2%)**, because the read path already collapses near-dupes and a family only costs a block when two
   of its rows land in the SAME block. The fix is a hygiene item (UI clutter, one fact per row), not a
   retrieval-bandwidth item, and it is parked as such.
3. **Measure at the consumer before sizing the fix.** This is the second time in two days that a store-
   level statistic (first the 95%-coarse shelves, now the 53 slots) implied a feature that the block-level
   measurement did not support.

**Everyday budget, measured on the live store (2026-10-14, offline copies, no engine, no port).** The
arm-C result then on the books said 192/48k was a no-op *on the venue corpus* (long rows, char cap binds at
~15 notes) — a claim corrected the following day by `tools/m4_blockcov.py` (see the arm-C bullet above: the
venue block DOES grow, 14.6 → 19.1 notes); it is kept here in its original form because what this probe
concluded does not depend on it. Personal facts run ~160 chars, so here the **note** cap is what binds —
which is a different regime, and "does the bigger block help everyday?" was asked by the user and answered
with measurements, not scope:

```
WHAT THE BLOCK LOOKS LIKE (300 real queries from the journal, 2,655 rows)
   notes shown        96/24k:  96.0   192/48k: 192.0    (+96.0)   <- the NOTE cap binds, not the char cap
   chars shown        96/24k: 15,248 192/48k: 31,900    (+16,652, +109%)
IS THE RIGHT FACT IN THE BLOCK (209 labeled probes built from the store's own rows)
   own-words  lexical  0.938 -> 0.938   hybrid  0.938 -> 0.938
   degraded   lexical  0.932 -> 0.932   hybrid  0.932 -> 0.932      <- doubling the block adds ZERO facts
AS THE STORE GROWS (same probes, store size cut by real deletions)
   600 rows   n= 44   0.977 (96)  /  0.977 (192)
  1300 rows   n=102   0.951       /  0.951
  2655 rows   n=209   0.919       /  0.914     <- the bigger block is not even neutral: it is slightly worse
```

Three conclusions, in order of importance:

1. **The budget is not the everyday lever either.** Doubling the block doubles the prompt (15.2k → 31.9k
   chars, so ~2 s → ~4 s at conversation start by linear extrapolation of the measured 2.3 s) and buys
   **+0.000** relevant facts at both query shapes and both arms. The right fact is already inside the
   first 96 notes 92-94% of the time; the extra 96 notes are the weak tail.
2. **Growth is a ranking problem, and the curve is visible.** The right-fact-in-block rate falls
   0.977 → 0.951 → 0.919 as the store goes 600 → 1,300 → 2,655 rows (~3 points per doubling). A bigger
   block does not arrest that — it *slightly loses* at the largest size, because read-path near-dupe
   suppression is window-dependent: with 192 candidates, a neighbour arriving in slots 97-192 can
   collapse the gold row that survived in a 96-wide window. A bigger block is not a strictly better
   block; the shown set is not a superset.
3. **So the shelf is the lever the LCC design is betting on, and the measurement supports exactly that
   half of the bet.** The failure mode as the store grows is the candidate set getting harder to rank,
   which is fixed by *narrowing the candidates before ranking* (class/scope filters), not by widening the
   block. The untested piece is the query→shelf step: `search(classes=...)` exists but the caller must
   choose the shelf, so a two-stage retrieval (cheap lexical pass → infer shelf → re-search inside it) is
   the experiment that would show the shelves earning their keep — offline, no engine, at several store
   sizes. Parked as the next measurement, not asserted as a result — **and it ran the same day: the
   navigation half of that bet did not pay (see "Shelf navigation, measured" below). The shelf still
   matters, for a different, measured reason.**

Probe hygiene worth keeping: the head block is **pinned by (scope, first user message, shelf, arm)** and
the *budget is not in the key*, so a probe that flips budgets inside one process re-measures the same
pinned bytes (the first run of this probe reported an impossible "identical block at both budgets" for
exactly that reason). `m._blocks.clear()` between budgets is mandatory in any offline budget comparison —
and if a per-request budget override is ever added, the pin key must gain the budget, the same way it
gained the arm.

**Probe hygiene, second instance (2026-10-15) - the lesson is not "clear the cache".** That trap was
discovered on the dev-store budget probes, written down in this file, and fixed in all three of them
(`dev_block_cov.py`, `dev_block_gold.py`, `dev_scale_curve.py` each clear `_blocks` today). It was NOT
fixed in `block_cov.py` - the venue probe whose output carried the headline attribution for arm C - because
it lived in `/tmp/opencode/`, had already produced its number, and nobody re-audited it against the note
that its own siblings had forced. One unpatched copy of a four-copy mechanism was enough to flip a
release-facing claim. So the rule to keep is not the mechanic, it is this: **a documented fix has to be
applied to every instrument that shares the mechanism, and the only way to make that happen reliably is to
promote probes into `tools/` where a reader can diff them against each other.** `block_cov.py` is now
`tools/m4_blockcov.py`, parameterized, with the clear built in, the trap named in its docstring, and the
filed numbers it is checking itself against printed in the same docstring.

**Shelf navigation, measured (2026-10-14, `tools/m4_shelf_nav.py`, offline copies of the live store, no
engine, no port).** Conclusion 3 above parked the query→shelf step as "the next measurement, not asserted
as a result". It is now a result, and it goes the other way. Two taxonomies, three navigation arms, all
scored the same way — "is the gold fact inside the top-96 the block draws from" (at this store 96 notes
fit under the char cap, so the list and the rendered block agree):

```
197 labeled probes | store 2,655 rows, 473 wild (18%)
taxonomy A: 17 shelves, largest 863 rows; 5 of them bigger than the 96-note block = 77% of the store
taxonomy B: 45 shelves cut from 18 base classes by cosine clusters capped at 90 rows; NONE bigger

  right fact inside the top-96            (n = probes eligible for that arm)
  taxonomy       rows      current        same set          oracle          routed
  A current       600      0.919      1.000 (28)        1.000 (28)      1.000 (28)
  B dense         600      0.919      0.919 (37)        0.919 (37)      0.919 (37)
  A current      1300      0.931      0.986 (70)        0.986 (70)      0.986 (70)
  B dense        1300      0.931      0.931 (87)        0.943 (87)      0.931 (87)
  A current      2655      0.919      0.950 (161)       0.950 (161)     0.974 (156)
  B dense        2655      0.919      0.919 (197)       0.934 (197)     0.888 (197)
```

1. **Perfect shelf navigation buys nothing on the store's own shelves.** The oracle arm — filter to the
   gold row's OWN shelf, i.e. a router that can never be wrong — scores 1.000 / 0.986 / 0.950, exactly
   equal to the unfiltered baseline over the same probes, at all three sizes. The unfiltered hybrid
   already puts the gold in the top 96, so narrowing the candidates first has nowhere to gain.
2. **Densifying the taxonomy does not change that.** 45 shelves instead of 17, every one of them small
   enough for the block to navigate, moves the oracle from 0.919 to 0.934 at the biggest size — three
   probes out of 197. The falsifier set before running ("oracle flat on A, high on B → the taxonomy idea
   wins") did not fire: the ceiling on dense cutters is itself near the baseline.
3. **And the router is not free — it is net negative.** The cheap bm25-vote router (arm 3) scores 0.888
   against a 0.919 unfiltered baseline at 2,655 rows on the dense taxonomy. That is the asymmetry,
   measured: a right shelf pick gains ~nothing, a wrong one filters the gold out of the candidate pool
   *entirely*. Navigation's upside here is three probes and its downside is six.
4. **The real shelf finding is the opposite of the one being chased: the cost is in WILD, not in
   granularity.** At 2,655 rows the unfiltered arm misses 16 of 197. Split by shelf: 8 misses among the
   161 shelved probes (5.0%) and 8 among the 36 WILD probes (22.2%) — a wild row is **4.5× more likely to
   be missed**. So 50% of the misses come from 18% of the rows. The valuable taxonomy work is getting rows
   OFF WILD (the embedder-centroid route, or a human naming the category, as `m2_ingest --class` already
   does), not cutting existing shelves finer and not routing into them.

   **Corrected the same day, because the causal half of that last sentence was mine, not the
   measurement's.** `class` is not an input to ranking anywhere in this store: it appears only as a
   `m.class like ?` filter when a caller NAMES shelves (`serve/memory.py:910-915`), and the blend reads
   rank, recency and confidence. The injection path passes no `classes`, so re-shelving a wild row cannot
   move it in the block — the two measurements would be identical by construction. What the 4.5× gap
   really says is that the rows the keyword map cannot name are also the rows bm25 and cosine rank badly:
   a CONTENT correlation. Emptying the WILD bucket still pays — for the UI, for cartridge eligibility, for
   filtered tool calls — just not for retrieval accuracy. The retrieval answer to that 4.5× is a ranking
   signal that reads something those rows have (the subject axis, `spikes/memory_systems/RESEARCH.md`
   backlog item 3) or better extraction so the junk never lands. Filed as a correction rather than
   quietly rewritten.
5. **Two instruments disagree about growth, and the disagreement is the point.** This degraded-query,
   candidate-list measure is FLAT with store size (0.919 at every size), while the own-words,
   rendered-block measure above falls 0.977 → 0.919. Ranking quality on a common-words query does not
   degrade between 600 and 2,655 rows; what degrades is what survives rendering — the char trim and
   window-dependent read-path near-dupe suppression. Any future "we fixed scaling" claim must say which of
   the two it moved.

Probe hygiene, because both bugs would have misled quietly: the first version reported an oracle WORSE
than no filter, purely from mixed denominators (the filtered arms skip WILD rows, so dividing their hits
by ALL probes understates them — hence the explicit `same set` column), and the dense arm scored exactly
0.000 because the dense labels lived in a memory-side map while `search(classes=…)` reads the store's
`class` column. A taxonomy the store cannot see is not a taxonomy. `m4_shelf_nav.py` now writes the dense
labels into the copy's column, which is also what a real densify migration would have to do.

Scope of the result: it is measured at 600-2,655 rows of ~160-char personal facts. It does NOT extrapolate
to 10k-100k rows, and the venue store (10,973 rows) is the place to re-run it — the flat curve here says
"navigation is not the constraint at this size", not "never".


**Why.** The temporal diagnostic (`spikes/lme/TEMPORAL_DIAG.md`) split the 0.433 row by cause: 61 of 127
temporal questions are persistently wrong, 25 always refuse, and 20 of those refused **with an answer row in
the block**. The mechanism is evidence COVERAGE — an ordering question needs three dated events and lexical
recall proposes the one that shares the question's rare words. This layer is the retrieval half of the fix;
the budget half is arm C.

**What ships.** `serve/embedder.py`: BAAI/bge-small-en-v1.5 (MIT, 33M params, 384 dims) through ONNX Runtime
on **CPU only** — the GPU belongs to the engine. `memory.search()` keeps FTS5 bm25 as the candidate generator
and UNIONs the cosine top over every embedded active row. At this store's size a full cosine scan is the
honest design: 2,521 rows scan in ~1 ms, so an ANN index would be more machinery than math.

```
"memory": { "embedder": {"dir": "Strata-data/embeddings/bge-small-en-v1.5", "threads": 4} }
```

plus `python3 tools/m4_embed.py --dir <store-dir>` once, to backfill rows written before it. Absent means
lexical-only and **byte-identical to every earlier release**: the module is imported lazily and
`onnxruntime`/`tokenizers` are deliberately NOT in `requirements.txt` — they live in
`requirements-embedder.txt` (pinned, opt-in), so a checkout that never sets `embedder` installs exactly what
it installed last release.

- **Model files are pinned by SHA-256** (`SHA256SUMS` beside them: `model.onnx 828e1496…`, `tokenizer.json
  d241a60d…`). A mismatch **raises at startup** rather than warning — a silently swapped embedder silently
  changes every ranking in the store. Any other load failure DEGRADES instead: the banner and `stats()` say
  `embedder FAILED - lexical only: <error>` and bm25 keeps answering, because a missing model file must never
  take the memory down.
- **Vectors ride the store**: `embeddings(id, vec)` inside `memory.db`, so the existing at-rest seal covers
  them with no new key handling. `remember()` embeds at write (measured 3 ms per note, outside the lock);
  `forget()` and budget eviction delete the vector with the row; `correct()` inherits coverage because it
  writes through `remember()`.
- **A running server notices a backfill** with no restart: `_vec_matrix` rebuilds when a write dirties it OR
  when the embedded-row count moves.
- **Fusion is by rank, not by score.** The first version blended raw cosine into the bm25 blend
  (0.55 cos / 0.20 rank / 0.25 recency / 0.10 confidence) and the reach probe (`tools/m4_reach.py`, live
  2,521-row store) caught what that does: a rank-1 exact-lexical answer fell to **14th**, because a store
  that size holds dozens of rows at cos 0.5–0.6 on any topical query. Reciprocal-rank fusion (k=60) on the
  same probes: rank 1 stays **1**, two probes improve (17→4, 7→3), and a row lexical recall never proposed
  at all now lands at 37 — inside a 96-note block, where before it did not exist. Recency and confidence
  survive as tie-breakers only: their terms total ≤ 1.1e-5, under the 3.1e-5 RRF step at rank 120, so inside
  the window that decides the block they break ties and never cross a rank step.

**Per-request mute — `X-Strata-Memory-Embedder: off` (2026-10-14).** The header skips the cosine pass
for that one request: recall, the pinned head block, the per-turn tail and the API search all rank
exactly as the lexical store does. It exists so the two arms of one experiment run against **one
process, one store and one vector set** — the only difference between the records is the reranker, with
no restart and no second seeded store between them. Two details make it safe:

- **The arm rides the pin key.** The head block is pinned per conversation, so the same question asked
  by the lexical arm would otherwise be answered from the hybrid arm's pinned block (or the reverse)
  and the A/B would compare one set of bytes against itself. The key gains an arm marker — only when
  an embedder is configured, so stores without one keep their exact previous keys. Same rule the shelf
  already follows: *a block pinned for one kind of request must never answer another.*
- **The response says which arm served it** (`"embedder": true|false` on `GET /memory?q=`), and
  `tools/lme_harness.py` copies that value into every record line, so an arm assignment is audited from
  the replies rather than from the flags the run hoped it had set.

Proven offline at the HTTP level (mock engine, stub embedder, real server): the two arms return
different hit lists, the muted arm returns exactly the lexical result, POST `op=search` honors the same
header, and `request_state` injects nothing when the arm is muted while still journaling the turn.
Harness flag: `--no-embedder` (the lexical arm).

**Cost, measured 2026-10-14 on the 2,521-row dev store (CPU, limit 96, warm):** lexical recall 52 ms, hybrid
137 ms, embed-at-write 3 ms, first recall after a write 75 ms / 169 ms (the caches legitimately rebuild). The
embedder's ~85 ms delta is almost entirely the near-dupe pass reading a wider candidate list — the cosine math
is ~1 ms. Against a 12k-token first turn (~8.5 s at the measured 1,400 tok/s) that is ~1.6%.

**Write cost, measured and then fixed the same day (2026-10-14).** A scale probe ingested the 13k-row oracle
into a copy of the live store, offline, and the writes got slower as the store grew: **7 ms per write at
2,589 rows → 180 ms at ~10k, 5.5 rows/s, ~40 min for the ingest.** Both caches the write path touches were
being rebuilt per write, because every write dirties them and the next write reads them — the pair is
quadratic in the store. Profiling 40 guarded writes said where the time actually went: `_dedupe_tables` 86%
(119,023 `norm_text` calls: the whole-store token table re-tokenizes every row), `_vec_matrix` 5%.

Two changes, both fast paths over a verdict, neither allowed to change one:

- **The token table folds the write in instead of rebuilding** (`_dedupe_amend`). Every exit of `remember()`
  knows how it changed the active row set — row added, row retired by supersede, row deleted by eviction, or
  nothing (a fold or an in-place update leaves the text, so df does not move) — so it applies that delta and
  re-reads the stamp. Payloads are dropped on any amendment, which is exactly what a rebuild used to do.
  A writer that is *not* `remember()` (a tool, a restore, a hand edit) moves the stamp in a way no amendment
  predicted, and the next caller rebuilds: **the stamp check stays the safety net, the amendment is only the
  fast path** — and a test pins that.
- **The near-dupe guard reads its own scope's vectors** (`_scope_vecs`, cached per scope, LRU 32) instead of
  the whole-store matrix. Its candidate set has always been one scope — the same boundary exact-text dedup
  uses — so it was rebuilding a store-wide matrix for a handful of lookups. The read path keeps the
  store-wide matrix, because a query really does span every scope.

Measured after: **guarded write 107 ms → 11 ms** (same script, same store, same background load). **A
venue-shaped write — one fresh small scope per row, the shape of an ingest — runs 2.8 ms, 360 rows/s, flat
in store size**, so the oracle ingest that measured ~40 min is now ~40 s, and the S-split with it. A
curation-shaped write (every row into the user scope) is 11–21 ms and still grows with that scope, because
exact-text dedup scans the scope's rows — the wall that is left, untouched, and the one to take on if the
store ever reaches ~50k. The first recall after a write loses its rebuild penalty: 75 ms → 49 ms lexical.

Proven by construction, not by hope: after every write path (insert, fold, in-place update, supersede,
eviction, foreign insert) the amended table is compared **token for token and document-frequency for
document-frequency** against a table built from scratch out of the db, and the scope vectors against a
cold read (`WriteCacheFastPaths`). One shipped test had to be rewritten: it asserted a write returns a NEW
table object, which was the old behaviour; what it actually cares about is that the payload memo dies when
the rare-token bar moves, and it still does.

**The venue law for ingest is now explicit: haystack writes go in with the near-dupe guard off**
(`guard: false`, see above) — measured, the guard superseded 317 oracle rows inside `lme_` scopes during one
ingest, which moves gold labels without touching a single answer.

**Found on the way (shipped, both modes).** `_dedupe` rebuilt the whole-store token table, document
frequencies and the rare-token bar on EVERY recall, and recomputed payloads per PAIR inside its loop (profiled:
2.5M generator calls, 0.9 s of tottime across five recalls). It now caches that table keyed by
`(count, max rowid, max updated_at)` of the active rows — every write path in this project bumps one of the
three — and memoizes payloads per row. Output is unchanged row for row: a 10-query × 2-mode snapshot of 1,920
ordered rows diffed clean before/after. The shipped lexical path went 126 ms → 52 ms warm.

**Venue law.** The venue measures the full stack (192 notes / 48k chars, embedder ON) against arm B's 0.721,
because that combination is the thing being shipped; the retrieval-only A/B (`tools/lme_retrieval_ab.py`,
~2 min, no generation) attributes the result per type for free, and the lexical *accuracy* arm runs only if
the outcome comes back flat. The venue store needs `tools/m4_embed.py` over its ~13k rows first (~90 s at the
measured 151 rows/s). Reach probes are mechanism evidence on real rows, **not** a benchmark claim; the
accuracy claim comes from the venue record, at temp 0.8/1.0 over ≥3 repeats.

**Reach probe, automated (`--auto N`, 2026-10-14).** 200 degraded self-queries built from the live 2,589-row
store: sample every Mth active row, delete its rare tokens (df ≤ 3 — the callsigns and township names that
are a lexical ranker's entire handle) and ask with what is left. The gold answer is the row itself, so rank
is unambiguous, nothing is hand-picked, and no engine is involved.

```
window 48 notes   top1    top3    top10   in window   median
  lexical         0.885   0.905   0.910   0.930       1
  hybrid          0.885   0.915   0.925   0.935       1
FLIPPED IN 1   FLIPPED OUT 0   moved within window 8   net +1/200
```

Read as, honestly: the reranker cost nothing here (zero facts buried that the words used to find) and moved
eight ranks around, but it also **could not** show much, because the lexical store already returns the right
row at rank 1 on 88.5% of these probes. A near-saturated baseline cannot demonstrate a lever. The venue is
where the headroom is — arm B's filed `turn_top1` is ~0.51 there — and that is the difference between "no
harm measured" and "improvement measured". The probe reports FLIPPED IN / FLIPPED OUT separately on purpose:
a mean-rank move alone hides both directions.

**The mechanism A/B, run on the venue store itself (2026-10-14, `--offline`, no boot, no engine).** 10,973
rows, 500 questions, both arms, scored by the venue's own `recall_metrics`; the lever moved on 490 of 500
questions (10 identical lists). `turn_top1` / `turn_top3` are the same at both k values (the list prefix
does not change), coverage differs because coverage is measured over the whole candidate window:

**The offline path reproduces the live venue exactly, which is what makes it usable as an instrument.** The
lexical arm of this offline pass measured `turn_top1 0.504 / turn_top3 0.749` at k=10; arm B's live venue
record — 1,500 lines, three repeats, the server's own search during a real accuracy run — filed
`turn_top1 0.504 / turn_top3 0.749`. Two independent paths (an in-process search over a copy of a sealed
store today, versus the running server's search during the venue) agreeing to three decimals. So the
hybrid arm's `+0.074` top-3 is a retrieval *prediction* about the live venue, not just about the copy —
and it is still only a retrieval statement: no answer was generated and no judge ran, so it is not an
accuracy claim and does not satisfy the venue protocol (temp 0.8/1.0, ≥3 repeats, one fresh conversation
per ask, 0-leak muted control). The accuracy half stays the venue's job.

```
k=10                             n    turn_top1    turn_top3      row_cov     sess_cov
  OVERALL                      500   .504→.504 +.000  .749→.823 +.074  .830→.885 +.055  .838→.893 +.055
  knowledge-update              78   .625→.486 −.139  .861→.861 +.000  .905→.951 +.046  .910→.951 +.041
  multi-session                133   .421→.488 +.067  .711→.818 +.107  .714→.811 +.097  .724→.825 +.101
  single-session-assistant      56   .304→.393 +.089  .589→.768 +.179  .929→.946 +.017  .946→.964 +.018
  single-session-preference     30   .400→.467 +.067  .667→.800 +.133  .889→.922 +.033  .900→.933 +.033
  single-session-user           70   .672→.656 −.016  .859→.859 +.000  .969→.953 −.016  .969→.953 −.016
  temporal-reasoning           133   .543→.512 −.031  .756→.819 +.063  .770→.847 +.077  .776→.852 +.076
k=192 (the block's own budget)   OVERALL  .504→.504 +.000  .749→.823 +.074  .950→.970 +.020  .954→.975 +.021
```

**"The embedder is always a positive" is not what the store says.** It is a positive on **coverage** (every
type up except a −0.016 on single-session-user) and on **top-3** (+0.074 overall, +0.179 on
single-session-assistant) — which is precisely what the temporal diagnostic asked for: multi-evidence
questions get more of their evidence in the block. It is **not** a positive at rank 1: overall top-1 is
exactly flat (.504→.504), and `knowledge-update` loses **0.139** (45/72 → 35/72 rank-1 hits).

The 11 lost rank-1 slots were read case by case (`--dump` + the gold map), and they are **not** the failure
I predicted. Zero of the 11 displacing rows was a near-dupe of the answer (max Jaccard 0.38, most under
0.15) — this is not the guard's duplicate family problem. Every one is the **topically nearest row in the
conversation, and most are the assistant's own reply**: asked what the user later decided about
Carcassonne, the reranker put the assistant's "Ticket to Ride is an excellent game! here are some
recommendations…" at rank 1 and pushed the user's "I'm interested in trying out Carcassonne…" to rank 2.
A chat corpus is full of these — the assistant echoes the user's entities at greater length, so cosine
prefers the echo. The lexical arm is not smarter; bm25's rare-token handle happens to sit on the user's own
wording more often.

Two consequences for arm C, stated before the run rather than after it:

- **At the block's own budget the evidence is already there.** At k=192 the LEXICAL arm carries
  `session_coverage` 0.954 and `row_coverage` 0.950. The reranker adds +0.021. So the 192/48k arm cannot
  win by *finding* evidence the 96/24k arm missed — it can only win by *ordering* it, and ordering is
  exactly where the mixed result lives. The temporal diagnostic's coverage story is true at small k and
  nearly spent at the block's real size.
- **The expected shape of the accuracy run is a wash with structure**: 189 questions on three types gain
  top-3 evidence, 78 knowledge-update questions lose rank-1, and whether that nets positive is what the
  venue measures — not what I get to assume. If it comes back flat, the next lever is not "more budget":
  it is the assistant-echo ordering, and it can now be iterated at ~45 s a try offline.

**Write-time near-dupe guard (2026-10-14, embedder ON only — with the embedder off the write path is
byte-identical).** One username policy became **57 rows in two days**: every sweep re-derives the same
fact in new words and exact-text dedup sees none of them. So `remember()` now asks the store before it
inserts, and the answer comes from the merge tool's law, not from the distance:

- **Cosine only proposes.** Measured on the store that had just grown the family: a duplicate family's
  own self-similarity runs **0.59–0.92 (median 0.889)**, while *different* facts wearing shared
  boilerplate run **0.95–1.00** ("KCMI is licensed to Terrytown" beside "KZKX is licensed to Seward"
  — identical vectors, cos 1.00). Any single cosine bar therefore either merges distinct facts or
  misses real duplicates.
- **The decision, in order.** (1) A rare-token **subject** the carrier never mentions vetoes the fold
  — the round-3 rule, checked first, above every bar; the pair can still *supersede* if the new row
  carries the old one's payload. (2) Rare-token **containment**: the carrier already says everything
  this text says → the insert is skipped and the row refreshed. (3) **Drifted wording**: the read
  path's own near-dupe bar, token Jaccard ≥ 0.75 on **content** words (the control scores 4/8 = 0.50,
  0.64 with connectives) → a row the block would collapse into its neighbour anyway never lands.
  (4) The new text carries everything the old one says plus its own detail → the richer row lands and
  the older retires into the version chain (`op=invalidate` semantics, nothing deleted).
- Candidates are the **writing scope** only (the exact-dedup law's own boundary), the floor is 0.85
  (the family's 25th percentile), at most the best 8 are tested, and stop words are excluded from the
  subject rule — the first live run vetoed a true duplicate over the word *"with"* (df 1 in a 209-row
  test store); a connective is not a subject.
- Cost: 7 ms for a suppressed write, ~76 ms for an insert that rebuilds both caches afterwards (the
  caches rebuild on the first read after a write — the same measured O(store) cost as the recall path,
  ~13 µs/row for the vector matrix and ~29 µs/row for the dupe table; a 20k-row store pays ~0.9 s
  once per write burst, not per row).

**Store budget: unlimited on the dev box (2026-10-14).** `max_memories` went 3,000 → 0 (the code
default; the eviction law stays armed for any config that names a number). The cap was never the
constraint — it was masking re-derivation: 380 of 2,538 rows had a ≥ 0.90 partner. With the guard above
the store grows on new facts instead of new phrasings. The two walls to revisit before ~100k rows,
stated as extrapolation from the measured per-row costs rather than pretended away: the vector matrix
(150 MB) and the per-burst cache rebuilds (~2–4 s).

**A plaintext husk never outranks a real ciphertext (2026-10-14, learned the hard way).** The crash
rule — *a surviving plaintext db is newer truth than the last seal* — is right about a db the server was
writing into and dead wrong about a **husk**: a restart raced a seal-and-shred, a 45 KB plaintext sat
over a 9.6 MB ciphertext, the store opened with **one row** and said nothing, and its next close sealed
that emptiness back over the real store. `__init__` now asks the plaintext directly (read-only: does it
hold any facts?) and reveals the ciphertext when it does not; `stats()["at_rest"]["revealed_at_open"]`
and the boot banner say which kind of open happened. Recovery from the shell was possible only because
a scratch reveal had decrypted the full store minutes earlier — the 2,550-row state came back from
`/tmp`, not from the store dir. **The lesson about restarts is procedural and it is a rule now:** the
dev server answers the agent's *own* inference, so the agent does not restart it — it asks the user to,
and it waits on the **port** (`ss -ltn | grep :8005`), not on `pgrep -f`, whose pattern also matches the
invoking shell.

## Shelf isolation — what a call returns, at any budget (measured 2026-10-15, accumulated dev store: 2,796 active rows, 567 of them unclassified)

The shelves are supposed to be **addresses**: calling `POL` returns what lives on `POL` and nothing else,
at any block size. Whether that is true, and what a bigger budget costs, is now measured on the store that
actually accumulated rather than on a benchmark copy — `tools/m4_shelfcall.py`, 20 topic-local probes per
shelf (a row's own common words), three budgets, three arms per probe (scoped call / unscoped call /
router-scoped call), every pin cache cleared per probe.

```
  shelf           budget  notes  purity  sibling   reach    leak  routed leak  routed reach   pick
  TECH            96/24k     96   1.000     0.0%   1.000    7.2%         1.4%         1.000   100%
  TECH           384/96k    384   1.000     0.0%   1.000   17.3%         1.7%         1.000   100%
  POL/ARSA        96/24k     96   1.000     0.0%   0.947   51.2%        35.7%         0.947   100%
  POL/ARSA       384/96k    384   1.000     0.0%   0.947   58.9%        36.8%         0.947   100%
  POL             96/24k     96   1.000    60.3%   1.000   10.3%         2.9%         1.000   100%
  POL            384/96k    384   1.000    63.7%   1.000   25.8%         4.0%         1.000   100%
  EVAL            96/24k     94   1.000     0.0%   0.550   41.4%         4.9%         0.550   100%
  EVAL           384/96k     94   1.000     0.0%   0.550   79.8%         5.0%         0.550   100%
  AI              96/24k     96   1.000     0.0%   0.944   45.6%        25.0%         0.944   100%
  AI             384/96k    114   1.000     0.0%   0.944   77.8%        51.8%         0.944   100%
  MEM             96/24k     87   1.000     0.0%   1.000   69.1%        14.2%         1.000   100%
  MEM            384/96k     87   1.000     0.0%   1.000   84.2%        18.6%         1.000   100%
  WILD 96/24k: reach 0.800   wild rows in a shown block: 50.4% of the notes
  WILD 384/96k: reach 0.800   wild rows in a shown block: 38.7% of the notes
```

- **The contract holds: purity 1.000 at every shelf and every budget.** A scoped call has never once shown
  another shelf's row. The filter is trustworthy; everything below is about what happens when nothing calls it.
- **Contamination grows monotonically with the block.** `leak` is the share of what an ordinary (unscoped)
  conversation is *shown* that comes from outside the shelf its question belongs to: TECH 7.2 → 17.3%,
  EVAL 41.4 → 79.8%, MEM 69.1 → 84.2% as the budget goes 96/24k → 384/96k. A bigger block never improves
  isolation and always costs it, which is the argument for the shipped 96/24k being kept as the everyday
  setting (the dev-store budget probe measured +0.000 relevant facts for going wider).
- **The router gets a second life, on a different metric.** `m4_shelf_nav.py` killed shelf routing as a
  *recall* lever (an oracle shelf scored exactly the unfiltered baseline — narrowing cannot add coverage
  when the gold is already in). These numbers give it a different job: contamination. Routing with the
  cheap bm25 vote cuts leak **2–5×** (MEM 69.1 → 14.2%, EVAL 41.4 → 4.9%) and `routed reach` is
  **identical to unscoped reach in every single row** — the fact is still there. Caveat stated before the
  claim: the probes are a row's own words, the easiest case for a router; on vague or short queries the
  pick is the risk (routing accuracy measured 0.92–0.96 there).
- **`POL` is mostly its sub-shelf.** Calling `POL` returns 60.3–63.7% `POL/ARSA` rows (the prefix match is
  by design — a shelf means its subtree — but nobody had quantified it). A parent whose subtree is 1.7×
  its own content is a lossy address: `POL/ARSA` is what anyone asking about `POL` is actually after.
- **`EVAL` reach 0.550 is the duplicate family showing up in a second place.** Its rows are near-duplicates
  of each other, so a probe's own row is often not shown *even though an equivalent fact is* — read-path
  dedupe collapses it into its twin. Near-dupes were already measured to cost 0.2% of block slots; they
  also cost **attributability**, which is what a gold-row metric reads as a miss.

**How to cut an oversized shelf (`tools/m4_shelfcut.py`, same store, 96/24k, cap 90).** Two candidate
cuts — buckets named after real rare tokens inside the shelf, and greedy cosine buckets — against the
whole shelf as it is:

```
  shelf / cut                     buckets   max   navig   reach  purity    leak  router   routed reach
  TECH / as it is                       1   863      0%   0.950   1.000   10.6%    1.00         0.950
  TECH / named cut                     41   396     98%   0.650   0.650   10.6%    1.00         0.684
  TECH / cosine cut                    11    90    100%   0.950   0.950   10.6%    0.95         0.950
  POL/ARSA / as it is                   1   575      0%   0.947   1.000   43.0%    1.00         0.947
  POL/ARSA / named cut                 41   232     98%   0.474   0.526   43.0%    1.00         0.474
  POL/ARSA / cosine cut                 7    90    100%   1.000   1.000   43.0%    1.00         0.947
  AI / as it is                         1   117      0%   1.000   1.000   55.2%    1.00         1.000
  AI / named cut                       41    13    100%   0.944   0.861   55.2%    1.00         0.944
  AI / cosine cut                       2    90    100%   1.000   1.000   55.2%    1.00         1.000
```

- **`leak` is identical for every cut, to the decimal point — and that is the finding, not a bug.**
  Relabeling shelves cannot change what a call that ignores the labels is shown. **The taxonomy only pays
  when something calls it**, so the decision belongs to the router/caller, not to the filing scheme.
- **Token-named buckets lose.** TECH 0.950 → 0.650, POL/ARSA 0.947 → 0.474 reach. Naming a bucket after a
  word inside it splits a topic by surface wording, and the leftover `_rest` bucket swallows 396 of TECH's
  863 rows (46%) into an unaddressable pile — `navig` 98% hides that because it counts buckets, not rows.
- **Cosine buckets win, and they win twice.** POL/ARSA reach goes **0.947 → 1.000** (narrowing beats the
  shelf's own twin competition — the same duplicate effect measured above), and the routed block shows the
  fact in **fewer notes**: 60 instead of 96 for POL/ARSA, 39 instead of 96 for AI, with leak 0%. Same
  knowledge, half the reading.
- The recommendation, as a rule: **the cut decides membership, the name is a label added afterwards** (name
  each bucket from its own top legal tokens once the cosine pass has fixed who is in it). And the name's
  alphabet is `[A-Z0-9]` — see the bug below.

**A bad shelf address used to return the whole store (found and fixed 2026-10-15).** `class_name` upper-cases
segments (harmless: SQLite `LIKE` is case-insensitive) and returns `''` for anything outside `CLASS_RE` —
which rejects `-`, `_`, `+` and spaces. Every caller treated `''` as "no name given", so **an all-rejected
list fell through as no filter at all**. Measured on this store: `classes=["TECH/strata-mam"]` came back with
**96 hits, and all 96 were rows from other shelves** — the caller asked for the narrowest address in the store
and was shown everything. `class_list`/`shelf_key` now fail **closed**: an address that names no shelf holds
nothing (the request still works, no exception, no dropped turn — which is what the old test actually cared
about, and it now says so). Half-valid lists still filter exactly as they always did, an absent header/tool
argument is still `None`, and the everyday path is byte-identical. Four surfaces were fixed together:
`search()`, the `X-Strata-Memory-Class` header, the MCP `memory_search` `class` argument, and the API's
`?class=` / `op=search`. **The running dev server predates the fix** — it changes nothing until the next
dev-up, and nothing for ordinary conversations at that.

**The notation, after reading the actual schedules (2026-10-15, `spikes/memory_systems/lcc.md`).** The
Library of Congress ships its schedules as free print-ready PDFs (44 volumes, Preface/Outline/Text, data
selected May 2024); reading the Text volumes rather than summaries of them turned up two things about our
own call numbers:

- **A real call number carries a Cutter, and ours could not.** `MEM/CRG.3.431` — the form `class_name`'s
  docstring advertises and M3's cartridge banks plan to use — was **rejected** by `CLASS_RE`, which allowed
  one dotted tail. `QA76.9.A23` (LCC's own "Algorithms") likewise. The tail may now repeat, so deep marks
  store, and the cosine arm's class column went `U24` → `U48` because 24 characters silently truncated a
  deep mark and would have made the two arms filter different rows.
- **A dotted prefix over-captures, and only a boundary is a subtree.** In LCC, `QA76.9`, `QA76.95` and
  `QA76.99` are three topics; `like 'QA76.9%'` returns all three. A child of X is `X.Y` or `X/Y`, never
  `Xyz`, so `shelf_sql`/`shelf_mask` now spell that out in both arms (the cosine arm used a bare
  `np.char.startswith`). Verified on the 2,796-row store with a real deep mark: `QA76.9.A43` is found by
  `QA76.9` and by `QA76.9.A43`, not by `QA76.95`, in both arms. Cost: SQL filter 0.05 ms vs 0.04 ms under
  the old law, hybrid search 62 ms median — free, as expected for a predicate on a set the scope filter
  has already shrunk.

What was **not** copied, and why: LCC's discipline-shaped class list (a personal store's facets are
who/when/why, not disciplines), span notation (`E201-298`, `Z699+` — schedule notation, not addresses), and
one-place-only shelving. What is worth copying and is queued as experiments: a many-to-many **subjects**
table (LCC keeps address and access in two systems; we have one column doing both, which is the shape of
the 20.3%-wild / 41-84%-contamination result above), and **neighbour/adjacency** filling of the block
(sortable numbers make "what sits next to this" one query — the primitive the books-and-chapters design
supplies and a `LIKE` prefix never can).

**Probe hygiene, third instance (and the one that nearly wrote the headline).** `block_for` returns full hit
records on a pin miss and **id-only dicts on a pin hit** (`serve/memory.py`, in its docstring). A probe that
reads `h["class"]` therefore measures the cache, not the store: EVAL's probes collide constantly (its rows
are near-duplicates, so they generate identical normalized queries) and its first run scored purity **0.600**
and leak **68.6%** — the shelves with distinctive wording scored 1.000 only because they never hit the cache.
Resolve ids against the db (`class_of`), clear `_blocks` per probe, and expect the numbers to move: EVAL's
purity became 1.000 and its leak 41.4%. Same family as the two earlier lessons: an unpatched copy of a shared
mechanism is a wrong claim waiting to be published.

**Store hygiene, noted in passing:** the dev store still carries 84 rows in three `lme_gpt4_*` scopes from
the early pilots. They cannot pollute recall (recall reads `user` + the request's own scope) but they are
benchmark evidence sitting in the everyday store, and they inflate the stats and the UI.

**The router shipped as production code (2026-10-17, `serve/memory.py` `shelf_vote` / `_route`, wired into
`block_for` and `turn_block_for`).** The deferral trigger named above fired - the venue store is 10,973
rows, past the 10k floor at which RESEARCH.md parked the query router - and the routed arm's numbers
(leak 2-5x down, reach identical) say the job is worth wiring. What ships is exactly the measured arm, no
invented improvements:

- **One law, one implementation.** `m4_shelf_nav.vote` is now a thin wrapper over `serve.memory.shelf_vote`
  (the rank weights, the 13-position slice, the wild-abstains rule, and the tie-by-shelf-name law are the
  store's, not the probe's), so the venue replay measures the machine that actually answers conversations.
  The filed 2026-10-14/15 numbers came from the probe-local copy, byte-identical in all four rules.
- **It speaks only where nobody addressed anything.** An explicit `classes=` (the MCP tool's `class`
  argument, `X-Strata-Memory-Class`, the API's `?class=`) is the caller's decision and bypasses the router
  entirely - the purity-1.000 law stays the caller's, and the router never spends a stage-1 pass on a
  request that already named its shelf. The names-no-shelf sentinel stays FAIL CLOSED (the 2026-10-15
  bad-address law), and the all-wild case abstains: `None` pick means the old unscoped path, byte-for-byte.
- **Cheap, and it pays for its own votes in the right currency.** Stage 1 is one bm25-only `search`
  (no embedder ask, no LLM - the Cognee law RESEARCH.md quotes: no LLM call without an unambiguous
  signal), ~the lexical arm's cost, once per pin miss and once per fresh tail. `search()` grew
  `credit=False` for exactly this call: stage-1 rows were PROPOSED to a vote, not SHOWN to a model, and
  `hits`/`last_seen_at` are the budget eviction's served-fact guard and what `memory_search` reports.
  Without the flag, routing would quietly make every voted row eviction-proof.
- **Fail-open where the arm's measurement could not.** The probe skipped probes whose routed list came
  back empty; production may not silently inject nothing. A pick that finds NOTHING falls back to the
  unscoped search - which can only ADD what the pre-router path showed, never hide a fact that unscoped
  would have surfaced. The tail case is real and tested: the head block carries the whole pick subtree,
  the tail dedups against it, so the routed tail legitimately comes back empty and the guard lands the
  fallback on the pre-router bytes exactly.
- **Config, default OFF until measured:** `"memory": {"route_shelves": true}` (or `"on"`/`"1"`); the
  banner line says `shelf router on (bm25 vote, unaddressed blocks only)`. Off, every path is
  byte-identical to the last release - the opt-out law survives. `stats()["router"]` carries the ledger
  (`tries` / `picks` / `fallbacks`): picks/tries is NOT routing accuracy (that needs a gold shelf), it is
  the "did it run, how often does it abstain, how often does it fail open" pair the replay reads beside
  the leak numbers.
- **How it was checked:** six unit tests (`serve/test_memory.py::ShelfRouter`): the vote math against the
  probe's law, contamination falls while the gold row stays and the block pins byte-stable, explicit
  addresses never route, the sentinel stays fail closed, all-wild abstains byte-identically, the tail
  fail-open lands on the router-off bytes, and `credit=False` moves no counters.
  **The first wiring got the ternary backwards** (`None if classes is not None else route`) and silently
  threw AWAY explicit caller addresses - the `classes=[]` sentinel resurfaced the show-everything
  behaviour the 2026-10-15 fix buried. The contamination tests passed anyway, because the classes=None
  branch was the one that worked; the test that caught it was the strengthened explicit-address test,
  which now asserts CONTENT, not just that the router abstained. Third lesson of the probe-hygiene
  family, and it went into the test rather than only into this paragraph.
- **What is still owed before the default flips:** the leak/reach numbers above are measured with probes
  built from a row's own words - the easy case (the doc's own caveat: on vague or short queries the pick
  is the risk). The venue re-record and the S-split (10k-100k rows) run with `route_shelves` as an arm,
  answer-level at plus/minus 0.05 and the retrieval flags row-by-row; the default flips only if accuracy
  does not move and leak falls. That is the same gate that decided every other tier-3 lever.

## Two external systems arrived the same evening; measurement handled both (2026-10-15 evening)

The user dropped two Reddit posts in as PDFs (the model cannot take PDF input, so both were read by
extracting them with `pdftotext` into `/tmp/opencode/pdfs/`) plus seven figures from the second:

- **Primnox** — "I created a new type of memory architecture. Which is similar to how books work",
  r/AIMemory, u/Fine_Credit_3088, code at <https://github.com/Primnox/Memory-system>. Five memory types
  kept separate instead of one pile; a **contents page** (a folder per person/pet/place/org/thing, plus
  one for you); **chapters** as semantic memory with headings like `Sarah.lives_in` and `me.employer`
  (some single-valued, some multi); a **date index** for episodic memory so "who did I meet yesterday"
  navigates a date into a person's chapter; **cross-references** so "a fact is filed in every chapter it
  relates to"; **corrections not deletions** (the old fact is marked replaced). A local ~500 M model is
  the editor, the big model is the reader that opens chapters. Measured: 171 questions over 12 made-up
  people, similarity 41.5 top-1 / 85.4 top-3, Hindsight on a local 7B 39.8/73.7, their previous version
  80.1/94.2, the new book 86.5/97.7, filing 309 facts in ~54 s on a T4. The honest caveats are the useful
  part: 86.5 needs post-hoc naming rules and is **83.0 without them**; on real two-person chats
  (REALTALK) **retiring old facts hurts** - plain search 45.7 top-1 against their 40.0-42.8, because most
  of those questions are about the history - and the editor files the right **chapter** 92% of the time
  but the right **heading** only 71%. Our own code already carries one of its ideas: the `since`/`until`
  date bounds in `search()` are documented as "the Primnox date-index lesson".
- **MAM v1.4 / Silicon Hippocampus** — Akbascare's update, r/LocalLLM, u/Nearby_Indication474, doi
  10.5281/zenodo.23381336, code `ceceli33/titan-cognitive-core-v2`. Three cartridges consolidated into one
  append-only numerical memory injected at the layer-3 boundary of a frozen Qwen2.5-7B; 109 rows / 5.96
  MiB and 115 rows / 6.29 MiB; nine causal conditions (NONE…ABC_CF); a counterfactual cartridge changes
  the answer (reversal of time flow → temporal stasis; bullet-dodge → slow-motion perception). The
  sentence that matters to us is theirs: **`DC_CUT3` (numerical) and `TEXT_JOINT` (the text control)
  produced identical raw answers in 93 of 99 comparisons - "agreement, not 93/99 accuracy" - and "the
  numerical approach has not been shown to outperform the text control on this panel."** A commenter
  reported llama.cpp's own cache save/resume reaching the same result about twice as fast, at a fraction
  of the size; the author's reply was a Turkish proverb. Independent support, then, for the ordering this
  project chose: the text block is not a fallback to be embarrassed about - the burden of proof sits on
  the tensor path.

**Primnox and LCC converge on one feature we do not have: a fact lives in more than one place.** LCC
keeps that in a separate system (index terms, `Cf.`/`see` edges, LCSH headings) because a call number is
one address; Primnox ships it as cross-references and lists "let a fact sit under more than one heading
when it fits" as its fix for its own 71% heading accuracy. Our `class` column is one string, so it cannot
hold one. Two probes on the venue copy measured what happens if we do hold it anyway
(`tools/m4_xref.py`, subject law = the merge tool's own rare-token subject rule, 39,385 subject pairs
over 20,529 terms on the venue store's 10,973 rows):

| arm (500 LongMemEval questions, hybrid, 96/24k) | notes shown | chars | gold-row coverage | gold-session coverage |
|---|---|---|---|---|
| the shipped block | 15.9 | 18,931 | **0.946** | 0.996 |
| cross-reference **scope** (answer only from rows sharing a winning subject) | 3.3 | 3,656 | **0.441** | 0.793 |

On the dev store the same mechanism looked excellent - POL contamination 56.2% → 8.3%, reach unchanged -
because those probes are a row's own words, the easiest case a probe can be given. On real questions the
subject set is far too narrow to be a block: **the falsifier fired and cross-references are killed as a
block scoper.** They are *not* killed as extra access points, but the additive version is not a probe
tweak either: a union against the same `search()` list is a no-op by construction (same candidates), so
headings have to enter as a **third candidate list with its own score**, fused the way bm25 and cosine
already are. That is a build, and it is queued behind the venue arms, not before them.

**The retirement hypothesis died the same hour.** Primnox's REALTALK regression (retiring old facts hurts
history-heavy questions) matched our own filed A/B: knowledge-update `turn_top1` 0.625 → 0.486, a
**−0.139** drop, while multi-session gained +0.100. The standing decision was to try a soft bounded
demotion, on the theory that the read-path near-dupe collapse was hiding one half of an update pair.
`tools/lme_retrieval_ab.py` gained `--no-dedup` to test exactly that, on the same 500 questions, both
arms, same store copy:

| slice | dedupe ON (shipped) | dedupe OFF | the hybrid delta |
|---|---|---|---|
| knowledge-update, turn_top1 | 0.625 → 0.486 | 0.639 → 0.500 | **−0.139 either way** |
| knowledge-update, row coverage at k=192 | 0.958 | 1.000 | the collapse does cost *row* coverage |
| multi-session, turn_top1 | 0.421 → 0.521 | 0.421 → 0.521 | +0.100 either way |

The collapse is exonerated: it costs 4 points of row coverage on knowledge-update questions and **zero**
points of accuracy, and the −0.139 is the cosine arm's own rank-1 choice, not a retirement. So the soft
demotion would have been built against the wrong mechanism, and the earlier note that all eleven
knowledge-update flips were the cosine arm's rank-1 row is now the live lead instead of a side remark.
Recorded before building, which is the whole point of the instrument.

**Primnox's 92%-chapter / 71%-heading split is the second external confirmation of something measured
here twice.** A small model is decent at the coarse shelf and bad at the fine one; tonight's cut
experiment found the same shape from the other direction (token-named buckets cut reach on TECH to 0.650
and left 46% of the shelf in a `_rest` pile, while cosine buckets raised POL/ARSA to 1.000). The rule both
support: **the extractor files at chapter level, the store computes the depth.**

## The venue test on the new build (measured 2026-10-15 16:51-17:35, arm C config, 1 repeat)

Booted `strata-venue-armc.json` (venue store **10,973 rows**, 192 notes / 48,000 chars, vectors 10,973,
unembedded 0, embedder loaded) and ran `tools/lme_harness.py --skip-ingest --repeats 1 --style con
--muted-control`: **500 questions, 500 lines, 2,675 s (44.6 min), exit 0**. Records:
`spikes/lme/armc-20261015-1rep.jsonl` (+ `.report.txt`, `.boot.log`).

- **accuracy 0.760** (380/500) against the filed arm C **0.761** (3 repeats, 2026-10-14). The three
  changes shipped since that record - fail-closed shelf addresses, boundary-exact subtrees in both arms,
  deep call numbers - **did not move the venue outcome**, which is the design claim: the harness sends no
  `X-Strata-Memory-Class`, so the LME path takes `classes=None` and is byte-identical. The falsifier was
  the other direction: had accuracy moved, a class-axis change would have leaked into a path that was
  supposed not to change. It did not, and this is the first time that claim has been demonstrated
  end-to-end rather than argued from the header.
- **The retrieval metrics reproduce the offline A/B exactly**: `turn_top1` 0.517, `turn_top3` 0.849,
  `row_coverage` 0.887, `session_coverage` 0.895 - identical to the numbers `lme_retrieval_ab.py --offline`
  printed from the store copy the same day (0.504→0.517, 0.749→0.849, 0.830→0.887). That is the strongest
  evidence yet that `--offline` is a legitimate mechanism instrument and not a proxy that drifts.
- **Muted control**: idk 492/500 = **0.984**, gold-substring leaks **0/500 = 0.0000**.

**The failure anatomy, which is the part worth reading** (a wrong line is "retrieval" only if a gold
session was missing from what the block showed):

| type | n | wrong | reasoning | retrieval | accuracy |
|---|---|---|---|---|---|
| temporal-reasoning | 127 | **69** | **50** | 19 | 0.457 |
| multi-session | 121 | 19 | 3 | **16** | 0.843 |
| knowledge-update | 72 | 18 | **16** | 2 | 0.750 |
| single-session-user | 64 | 5 | 5 | 0 | 0.922 |
| abstention | 30 | 4 | 0 | 4 | 0.867 |
| single-session-assistant | 56 | 3 | 3 | 0 | 0.946 |
| single-session-preference | 30 | 2 | 1 | 1 | 0.933 |
| **overall** | **500** | **120** | **78 (65%)** | **42 (35%)** | **0.760** |

**Temporal reasoning is the store's problem, and it is not a retrieval problem.** 69 of the 120 errors
(57%) are temporal, and of those 69, **50 had every gold session present** and **49 had row coverage
≥ 0.999** - the evidence was in front of the model and it still failed, 72% of the time. Its idk rate is
0.37, more than double any other answer type: the model gives up on ordering questions. That is exactly
the mechanism the shipped-but-untested **date grounding** targets (observation date + the absolute-date
rule in the extract prompt, `--no-date` reproducing the old prompt byte-for-byte).

**That reorders the venue queue by pool size, which is the honest way to choose.** The grounding arm
addresses a pool of 50 questions = **10.0 accuracy points**; arm C1 (the budget axis, lexical at
192/48k) addresses multi-session's retrieval pool of 16 questions = **3.2 points**. Grounding is the
bigger prize by 3x, and it does not need a full re-ingest to test: re-ingest **only the 127 temporal
questions' haystacks** into a scratch store twice - old prompt and dated prompt - embed both, and run one
repeat over those 127 questions (~12 min of generation) plus the offline coverage comparison. A full
500-session re-ingest is the arm for the record, not the arm for the decision.

Caveats on the record, stated as the protocol requires: this is **one repeat**, so the 0.760-vs-0.761
comparison is a no-regression check, not a pass/fail claim (pass/fail wants ≥3 repeats at temp 0.8/1.0);
the per-type accuracy rates are single-sample and the 0.457-vs-filed-0.465 difference is noise. The
anatomy - 50 of 69 temporal failures with full session coverage - is a structural count, not a rate, and
that is what the queue recommendation rests on.

## Sensitivity before spending: does accuracy depend on coverage, and where does it not? (measured 2026-10-15, `tools/m4_sensitivity.py`, zero model calls)

`sem`'s discipline - a claim is not accepted until something makes it fail - applied to our own venue
records before booking another window. The chain the whole queue is priced on is *rendered-block coverage
predicts accuracy*, which so far was a correlation **between arms**. This reads it **within** one run
(500 lines, `spikes/lme/armc-20261015-1rep.jsonl`):

| gold-row coverage of the recall | n | accuracy | | gold-session coverage | n | accuracy |
|---|---|---|---|---|---|---|
| 0-0.25 | 17 | 0.588 | | 0-0.25 | 15 | 0.533 |
| 0.25-0.5 | 9 | 0.222 | | 0.25-0.5 | 10 | 0.300 |
| 0.5-0.75 | 67 | 0.642 | | 0.5-0.75 | 60 | 0.633 |
| 0.75-0.999 | 5 | 0.800 | | 0.75-0.999 | 4 | 0.500 |
| **full (1.000)** | **372** | **0.793** | | **full** | **381** | **0.795** |

Full coverage beats incomplete by **+0.191 on rows and +0.222 on sessions**, so the block is doing real
work and the premise survives. **The slopes by type are the finding, and they are not uniform:**

| type | n | accuracy at full coverage | at incomplete | slope |
|---|---|---|---|---|
| multi-session | 121 | 0.958 | 0.673 | **+0.285** |
| single-session-preference | 30 | 0.964 | 0.500 | +0.464 |
| temporal-reasoning | 127 | 0.479 | 0.394 | **+0.085** |
| knowledge-update | 72 | 0.750 | 0.750 | **+0.000** |
| single-session-assistant | 56 | 0.942 | 1.000 | -0.058 |
| single-session-user | 64 | 0.919 | 1.000 | -0.081 |

**Where the evidence is spread, coverage is everything; where it is present but must be *ordered* or
*disambiguated*, coverage is nothing.** Temporal gains 8.5 points from complete coverage and still sits at
0.479; knowledge-update gains exactly 0.000. That kills the "retrieve more of it" hope for the two biggest
failure pools and it sharpens what the grounding arm actually claims: absolute dates have to help the model
**order** facts, not match them - so the retrieval-level proxy would under-test it, and the test belongs at
the answer level.

**Replication (the evidence-ledger promotion criterion, tested honestly).** Accuracy by the number of gold
sessions a question's answer spans: 1 session 0.797 (n=187), 2 0.768 (n=228), 3 0.633, 4 0.562, 5 0.333,
6 0.000 (n=3) - monotone. But `turn_top1` moves the *other* way at 2 sessions (0.557 vs 0.487): retrieval
finds a gold row more often while the final answer gets worse, which is the multi-hop signature - **the
store is not the weak link, composition is.** Stated as a limitation, not a claim: this bucket measures
question difficulty, not the fact-level promotion rule; the test for "graduate a fact when it survives
across cases" is store-side (near-dupe family size against `hits`/`confidence`) and has not been run.

**A structural catch, and it cancels an idea I floated an hour ago.** The co-shown-pair mine (the `sem
history` co-change analogue) returned nothing above the floor, and the reason is not that our blocks are
undifferentiated: **in the venue store every fact lives in its question's own scope**, so a fact id cannot
recur across questions and cross-question pair statistics are undefined there. The idea is fine, the corpus
is wrong - it needs the **dev journal** (`turns.injected`, shipped tonight and filling since the reboot),
where the same facts genuinely recur across conversations.

## The mutation the metric had to pass (measured 2026-10-15, `tools/m4_mutation.py`, 30 temporal lines)

`sem` refuses a claim until something makes it fail. The claim our queue is priced on is that the block
drives the answer, and the sensitivity probe had left one hole: a within-run correlation is not a mutation.
So: take 30 temporal-reasoning lines that were **correct at full gold coverage**, render their blocks
in-process from the embedded venue copy, and answer three times at temperature 0.8 with memory muted -
block as rendered, block with the **gold rows removed**, and no block at all. No boot, no second instance:
the current dev server answers, the block is the only variable.

| arm | accuracy | what it says |
|---|---|---|
| control (block as rendered) | **0.867** (26/30) | fidelity: reproduces the filed run on 26 of 30 lines that were correct there - the 4 misses are temp-0.8 repeat variance, which is exactly why the protocol asks for ≥3 repeats |
| drop the gold rows | **0.200** (6/30) | |
| no block at all | **0.000** (0/30) | the model knows **none** of this from its weights |

**Flip rate: 0.700 of probed lines, 0.808 of the lines the control actually got right (21/26).** The gold
line is load-bearing. Two sub-results worth their own lines:

- **13 of the 30 drops produced an `idk`, not a wrong guess.** When the evidence is taken away the model
  mostly *notices* - which is the property the muted control has been measuring all along (0.984 idk), now
  demonstrated at the level of a single row inside a block rather than the whole memory.
- **5 of 30 (0.167) survived dropping the gold line but not dropping the block** - a *twin* line carried
  the answer. That is the third distinct cost of near-duplicate facts measured this project: block slots
  (0.2%), attributability (EVAL reach 0.550), and now redundancy (the answer survives its own row).

**The result that matters for the queue: 0.808 flip and the flat coverage slope (+0.085) do not
contradict each other.** Having *more* of the evidence present barely moves temporal accuracy, while the
evidence that is present is carrying the answer 80% of the time. So the failure is not finding it and not
ignoring it - it is **using it in the right order**, which is the one thing absolute dates are for. The
gate passes, and the cost that remains for the grounding arm is the ingest (generation over the temporal
sessions), not a boot: the arm can be run in-process against a scratch store on the current server.

Method note, because the first attempt was wrong in a way that would have cost an arm: the block was
initially injected as a **system-only conversation**, which returns an empty completion from this chat
template. Three lines, three empty answers, and a flip rate that would have read as **0.000** - "the block
does not matter" - from a harness bug. The fidelity arm is what caught it: a control that cannot reproduce
the filed run is not measuring the thing. Fixed to the product's own shape (memory in the system head,
question from the user), and the empty-completion case is now counted as a probe failure, never as a wrong
answer.

## Date grounding settled at the answer level - after one regex quietly degraded three measurements (measured 2026-10-15, `tools/m4_grounding.py`, `tools/m4_rerank.py`)

**The bug, found by a null that would not move.** The grounding A/B's first pass came back
-0.008 with 1.1% compliance - and the rerank probe's date column read 0/360, then 0/127 even after
switching to map-based dates. The bytes explain all of it: the oracle's `haystack_dates` are
**slash-delimited** (`"2023/04/10 (Mon) 17:50"`, 0x2f confirmed by code point) while `question_date`
uses hyphens, and `lme_harness.DATE_RE` matched **hyphens only**. `day_of()` returned `""` for every
haystack date since the file existed - with zero test coverage - and the failure was silent three
times over:

- the venue ingest's `[{day}] ` prefix was dead code: **0 of 10,973 venue rows carry a date**, so
  every temporal number ever filed on this venue - 0.433, 0.457, 0.465 - was measured on **dateless**
  evidence, and the store's "predates the dated ingest" archaeology story was wrong;
- the extraction A/B's dated arm was told "an unknown date" for all 1,615 pairs, so it could not
  ground what it was built to ground (and in 18 facts it *invented* years anyway - one of them 2024
  for a 2023 session, which is the "never invent a date" rule failing exactly as feared);
- the chronological-rerank probe sorted on empty strings, making both arms identical.

`DATE_RE` now accepts both separators and normalizes to hyphens; `tools/test_lme_harness.py` pins
both real formats (with `chr(47)` spelled out, because the author's input layer kept normalizing the
separator while *writing the test* - the bug hiding in plain sight a fifth time).

**The accidental calibration: the noise floor of one 127-line run.** That first identical-arms rerank
run is not a wasted measurement - it is the test-retest floor of this pool at temp 0.8: byte-identical
prompts answered twice scored **0.425 vs 0.472, 14:8 discordants, 22 of 127 lines flipped**
(`spikes/lme/rerank-noise-floor-20261015.jsonl`). A single-run delta under ~0.05 on 127 lines is
inside the floor. The >=3-repeat rule, finally with a number attached.

**The grounding A/B, corrected.** The control arm never saw the bug: the undated prompt has no
`{observed}` slot at all, so only the dated arm needed re-extracting (per-arm checkpoints made that
34 minutes, not an hour). Compliance jumped **1.1% -> 55.1%** of facts carrying an ISO date - the
extractor is selective, it stamps time-bearing facts and leaves timeless ones alone. Three fresh
answer repeats over the same stores, temp 0.8, muted, official checker:

| repeat | undated | dated | dated-only right | undated-only right | delta |
|---|---|---|---|---|---|
| 1 | 0.386 | **0.606** | 34 | 6 | +0.220 |
| 2 | 0.370 | **0.591** | 35 | 7 | +0.220 |
| 3 | 0.425 | **0.606** | 35 | 12 | +0.181 |
| **pooled** | ~0.39 | **~0.60** | **104** | **25** | **+0.207, McNemar p <= 1.2e-12** |

The abstention rate collapses with it: idk 0.409 -> 0.110. The retrieval side moves a little
(turn_top1 0.606 -> 0.646); the effect is overwhelmingly at the answer level - the model *reads* the
date and does the ordering arithmetic. This is the first intervention measured to move temporal
accuracy at all, and it is a **write-path** change: the shipped dated `EXTRACT_PROMPT`, measured on
the venue's own corpus, in scratch stores, no boot, one server.

**The ladder on these 127 temporal questions** (all measured, same budget and decode):

| store | accuracy | what it isolates |
|---|---|---|
| extracted, undated prompt (control x3) | 0.370-0.425 | the pre-grounding write path |
| verbatim venue rows - **which carry no dates** (filed run) | 0.457 | raw detail, dateless |
| extracted, dated prompt (arm x3) | **0.591-0.606** | the shipped write path |

Dates in the fact text beat raw verbatim detail on this type. And every temporal number filed before
today was measured on dateless venue rows - a **dated verbatim re-ingest** is now an untested venue
arm the current harness would actually write.

**The rerank negative, which completes the story.** With real map-based dates (rows dated 127/127),
re-sorting the venue's rows chronologically in the **renderer** - same hits, same
budget, oldest event first - scored **0.433 vs 0.441 default,
-0.008, 8:9 discordants** (`spikes/lme/rerank-chrono-20261015.jsonl`): inside the floor, no effect.
Presentation order does not reach the temporal deficit; content order does. The four probes now
read as one mechanism chain: the gold row carries the answer (mutation 0.808), more presence does
not help (sensitivity +0.085), reordering dateless rows does nothing (rerank -0.008), and dates
**inside the facts** move it +0.207.

**What changes.** (1) The dated extract prompt is validated at answer level - the claim lands in
`docs/BETA1.md`. (2) The venue store is dateless, so its temporal numbers are a floor, not a ceiling;
the obvious next port-free run is the extraction A/B across **all 500 venue questions** - the
scratch-store path needs no boot, and the non-temporal types are where the extractor's one-sentence
compression might pay differently. (3) Arm C1's window is re-priced against that run, not against
grounding, which is settled. Method note kept on purpose: the first falsifier fired against a
degenerate arm - a control that cannot fail is not a control, and this one could not even see its
own variable.

## The stuffing complaint, measured twice: the purge was the cure, the floor is a trade (measured 2026-10-16, `tools/m4_stuffing.py`, `tools/m4_floor.py`)

The everyday-use report (ported from the webui): "it stuffs the budget full of everything it can
find, including stuff completely unrelated." The journal's `turns.injected` column - what the
server actually sent each real turn - made it a number: **8.2%** of injected rows shared a rare
payload token with the user's question, 96-104 rows a turn, the same filler riding 8 of 12 turns
regardless of topic (the semantic arm's hub rows: short facts near the corpus centroid, "similar"
to everything - `server = Server("my-server")`, an MCP-doc ingest row, rode eight turns straight).

Then the purge ran - 163 quiz-corpus rows and 84 benchmark rows out of the store - and the same
re-rendered traffic scored **50.1% related**. The corpus rows were the stuffing: 247 rows of
always-similar trivia in a 2.9k-row store dominated every unscoped block. The complaint was real,
and the store surgery fixed most of it.

The relevance floor (`floor_cosine`, the hub gate) was then swept against the same 20 real turns
and guarded on the replayed grounding store (the dated arm's 1,682 facts replayed from git records,
zero engine asks) over the 127 temporal questions:

| floor | dev-store related % | venue gold-row coverage | questions with zero gold in block |
|---|---|---|---|
| 0.00 (default) | 50.1% | **0.962** | 1 |
| 0.50 | 49.9% | 0.917 (-0.045) | 3 |
| 0.60 | 54.7% | **0.890** (-0.072) | 4 |

Read honestly: the floor trades purity for evidence. At 0.60 it buys +4.6 points of related share
on dev traffic and costs 7.2 points of gold-row coverage on the benchmark - and the coverage the
floor kills is precisely the paraphrase recall the cosine arm exists to union (TEMPORAL_DIAG's
whole point; the grounding result itself rode blocks at 0.945 coverage). **The verdict: the knob
ships, the default stays 0.0 (off, byte-identical), and no floor value is justified by these
numbers.** The block staying at 96 rows even at floor 0.60 says the remaining filler arrives
LEXICALLY - bm25 matches on common words - which the floor cannot reach. The follow-up design is
the block-level cap-not-quota rule (stop extending at the relevance cliff), but the venue numbers
just raised the bar for it: any cliff must never cut a row the gold map needs, so it gets the same
replay-guard treatment before it ships.

Method notes worth keeping: the floor sweep restores `hits`/`last_seen_at` afterwards (search()
bumps the feedback counter on every shown row - measuring must not mutate the ranking inputs), and
it clears `_blocks` before every render (the floor is not in the pin key - the budget trap, third
time). The replay guard itself is new and reusable: a scratch store rebuilt from git ingest records
in 3 seconds, no engine, answers coverage questions at zero generation cost.

## The cap-not-quota floor, measured on both sides and shipped off (measured 2026-10-16, `tools/m4_blockrule.py`, `tools/m4_venue_rebuild.py`)

The follow-up the section above promised: a block-level rule that treats the budget as a ceiling,
not a quota - `_render_block` stops taking candidates once the fused score falls below
`memory.block_min_score` (opt-in, default 0.0 = byte-identical, banner prints
`block floor off (quota)`). The pre-stated falsifier, inherited from the floor_cosine lesson:
gold coverage may not fall - not "falls less than purity rises", falls, full stop.

Measuring it needed the venue store, which /tmp reboots keep eating, so the fleet gained
`tools/m4_venue_rebuild.py`: an in-process twin of `lme_harness.ingest`'s exact laws (day prefix,
role lead, chunk_turn caps with gold on every piece, `guard=False` evidence law, first-session/OR-
of-gold map merge, same sidecar format) that rebuilds all 500 haystacks in ~3 minutes at 58 rows/s
- the old 40-minute estimate died with the matrix-rebuild fix. The rebuilt store is strictly
better than the original it replaces: **10,947 of 10,978 rows carry dates** where the venue-day
store had 0 of 10,973 (the `day_of` fix was invisible to it), and it is the store every retrieval
sweep from here runs against.

The sweep (`tools/m4_blockrule.py`, deterministic - no temperature, no repeats needed) failed
twice before it measured anything, and both failures are the lesson: the replay store the design
inherited (`ground-new-replay`) is a 127-question GROUNDING store at ~13 rows/scope - too small to
express the rule at all - and the floors were calibrated against `k=60` while the shipped constant
is **RRF_K = 1.0**: scores are 1/(1+rank) sums spanning 0-1.5, not 0.01-0.03. (The store itself
disagreed: thirteen curated rows asserted k=60. Corrected to one canonical fact plus three
satellites; the store's architecture lore now matches the code it describes.) On the real scale,
venue 500 questions, head-block shape (k=96, 24,000 chars):

| floor | rows/q | related % | gold row coverage | cost vs 0.0 |
|---|---|---|---|---|
| 0.00 (default) | 15.9 | 23.3 | **0.952** | - |
| 0.05 | 15.9 | 23.3 | 0.951 | -0.001 |
| 0.10 | 15.0 | 24.6 | 0.947 | -0.005 |
| 0.13 | 12.8 | 27.6 | 0.935 | -0.017 |
| 0.17 | 10.0 | 31.9 | 0.911 | -0.041 |
| 0.35 | 4.2 | 46.1 | 0.761 | -0.191 |

The cost side is clean and monotone: every point of purity costs evidence. The benefit side, on
the 12 newest LIVE injected turns (store copy, search re-run, same rare-token proxy):

| floor | rows/turn | related % |
|---|---|---|
| 0.00 | 96.0 | 71.5 |
| 0.05 | 39.8 | 69.5 |
| 0.10 | 18.1 | 70.5 |
| 0.17 | 7.7 | 68.5 |

Flat. The floor halves, quarters, tenths the block and the related share does not move - because
the filler the complaint named (the hub rows riding every turn, m4_stuffing's repeat-finder) sits
at cos_rank 1-5 against any question, scoring 0.17-0.5, ABOVE any floor the venue gate survives.
The floor cuts the deep tail, and on live traffic the deep tail is still relevant (71.5% at
floor 0). **The verdict, same shape as floor_cosine's an hour earlier: the knob ships, the default
stays 0.0, and no floor value is justified by these numbers.** Two independent levers - the
cosine arm's vote, the block's tail - measured against the same complaint, both inert-or-harmful.
What remains is the hub itself: the next lever is exposure-decay (a row the last K turns already
injected pays a bounded demotion - `turns.injected` is the exposure ledger, already in the
journal), which attacks the top-of-list where the filler actually lives.

**Exposure-decay, the third lever, also measured and shipped off (2026-10-16,
`tools/m4_expose.py`).** Built as designed: `expose_window` / `expose_step` / `expose_cap`
(defaults 0/0/3 = off = byte-identical), the ledger read from `turns.injected` (what the model
really read), the demotion applied after fusion and before dedup, capped so no fact is ever
buried by its own history; six tests pin the exact arithmetic, the cap, the window, and the
ledger-cache stamp. The measurement replays the 40 newest REAL turns in order over store copies,
truncating each copy's turns table to history before every search so the server's own ledger
sees exactly what it would have held at that moment:

| step | rows/turn | related % | suppressed-relevant % | top hub rides |
|---|---|---|---|---|
| 0.00 | 96.0 | 53.0 | 0.0 | 19 |
| 0.02 | 96.0 | 51.2 | 25.9 | 19 |
| 0.05 | 96.0 | 50.7 | 32.8 | 19 |
| 0.20 | 96.0 | 49.6 | 37.6 | 19 |

It fails BOTH ends: the prize goes DOWN (related% 53→50, not up) and the gate breaks hard
(26-38% of the block's demonstrably relevant rows suppressed at even step 0.02) - while the
hub-ride count never moves. The reason is structural and it closes the whole stuffing question:
**consecutive turns of one conversation are topically correlated, so exposure is a relevance
signal, not an anti-signal** - the rows shown most are the rows the conversation keeps needing,
and decay demotes exactly them. The named diagnostic says the rest: the top hubs riding 37 of 40
turns are two MCP-doc fragments, two ARSA slogans, and real user facts - real content that is
near-centroid in embedding space, riding every block because recall is top-k of a 2,700-row
store. They are the same rows the venue proves cosine MUST surface (paraphrase recall is what
earned the +0.085 sensitivity and the +0.207 grounding); no ranking lever separates "hub riding
an unrelated query" from "evidence riding a paraphrase query" because they are one mechanism.

The trilogy, closed: **the junk was purged** (8.2% → 50.1% related, the purge was the cure - the
section above already said so; the hygiene pass this diagnosis surfaced adds the reboot-chatter
pair 3174/3175, forgotten). **What remains of "stuffing" is the price of paraphrase recall** -
measured three independent ways (cosine floor: −7.2 coverage; block floor: purity flat while
evidence falls; exposure: suppresses the relevant) - **plus block size**, which is a product
dial (`recall_limit`/`max_block_chars`: 96 notes ≈ 18-24k chars ≈ 5-6k tokens of context per
conversation turn), not an algorithm. All three knobs ship in the tree, each measured, each
default off; if the block should shrink, the honest lever is the budget the owner sets, with
the venue replay as the standing coverage guard.

**Server-native reason modes - the {REASON:} system made harness-independent (2026-10-17, measured
first, then built: `serve/server.py` REASON MODES block, `GET|POST /reason`, `serve/test_reason.py`
15 tests).** Two questions opened this: whether DavidAU's mode system is real (it is - the live A/B
above, and the model swap to his weights the same night means the box now runs the intended pairing,
template AND tuned weights: the replaced weights file is kept as `.SC1171`, and his template loads
from the tokenizer pack, `Strata-data/packs/.../tokenizer/chat_template.jinja`, where the server
prefers the model's own export - so the shipped default template can stay the stock one, and the
fork carries his as the named asset `serve/chat_template.dau.jinja` (229,184 bytes, its mode list
set-equal to `REASON_MODES`) for the tests and for operators), and whether the harness could switch
it as he does by hand. The spike that decided the
design: his template resolves its system-block text from the RENDER CONTEXT's `reasoning_effort`
- passing `reasoning_effort="vader"` injects the Vader block with **no tag anywhere in the
conversation** (measured, three modes plus the instruct variant, plus a byte-identical control);
his `{REASON:}` tag is the chat UI for that same value, and his `l_car` branch keeps a client's tag
outranking the context value, so the precedence order is his template's design, not a fork choice:
**client tag > conversation sticky > config default > template default** (which, note, is `xhigh`
framing injected whenever no effort is passed - measured, pre-existing, now visible).

What ships (two gates in front of everything: the master toggle **`"reason_modes": true`** in the
server's JSON - one boolean, true or false, "like the other things we built into Strata" (the
user's law, 2026-10-17), which **setup writes automatically when the family chosen is DavidAU's** -
AND a template that answers to modes (the flavor gate below). Either half alone leaves the feature
off; off means every render passes the SAME kwargs dict, byte-identical by construction):

- **The master switch** is `"reason_modes": true` (top level of `strata-<name>.json`, shaped like
  every other config key). True **and** an answering template lights the whole feature: the
  `/reason` routes, the `reason_mode` tool, the sticky map, the boot banner naming the modes.
  True on a plain-template box is a no-op **with a message** - the flavor gate outranks even an
  explicit true. Absent anywhere: silence, byte-identical, and `POST /reason` answers 409 naming
  the key to set. `setup --family dau` writes `"reason_modes": true` **and** `"reason_mode":
  "ultra"` into the config it generates - ultra being the generalist high-thinking default (the
  user, 2026-10-17), the top mode that actually exists: his README advertises `xultra`, which the
  template does not answer to (measured: it leaks as literal prompt text) and the server refuses
  (400) - so a DavidAU install arrives with its feature on, framing every conversation as the
  exhaustive-perfection engine until something steers it elsewhere. No other family ever sees
  either key.
- **The flavor gate (measured first, 2026-10-17).** The stock template consumes `reasoning_effort`
  itself and **raises its own "Unexpected reasoning effort" template error** on a value off its
  list when thinking is on - a mode name is exactly that - while with thinking off it ignores the
  value. So "inert on a stock box" was the wrong (too kind) model: a stray `reason_mode` there
  would kill every thinking render. `reason_flavor(template source)` decides once per boot -
  "dau" only for a template carrying both the `reason_mods` set and the `{REASON` machinery - and
  the config default, both routes (POST answers 409 on plain) and the render substitution all
  defer to it. `serve/test_reason.py`'s plain-template class mimics the measured stock behavior
  with a synthetic template that raises on purpose, so removing the gate fails a test, not a box.
  The config default refuses to set, `POST /reason` answers 409, and the render substitution never
  fires - while `GET /reason` still answers 200 so an operator can read the verdict.
- **`GET /reason`** - what THIS template answers to (`"dau"` / `"plain"` - the gate's verdict,
  stated, not assumed), the session default, and the sticky table.
- **`POST /reason {"mode": ..., "conversation": "last" | "all" | <first-message text> | omitted}`** -
  the steering door. `conversation:"last"` is what makes SELF-steering practical: an agent steering
  its own live session is, by construction, the last conversation rendered, so it can switch without
  knowing its first-message hash. Unknown mode = 400 - the fail-closed answer to the leak-as-text
  quirk measured above.
- **The sticky map keys on the first user message** - the same anchor the block pin uses, with the
  same consequence: DavidAU's "use a new chat when switching" advice became geometry. A turn that
  doesn't carry the conversation IS a new conversation and inherits nothing (a mid-session test of
  mine tripped exactly this law and passed it into the suite as a named assertion).
- **The mode lands at the next RENDER - and a tool loop re-renders every round**, so an agent that
  steers mid-turn gets its mode in the NEXT TOOL ROUND of the same turn. That is the difference
  between a chat feature and an agent capability, and it is the half DavidAU's design (human picks
  a mode per chat) does not have.
- **The engine never sees a mode name.** `reasoning_effort` in the API request stays the server's
  own vocabulary (none/low/medium/high - the ValueError at the request gate is untouched); the mode
  is a template-context substitution at the two render chokepoints only. Thinking on/off, sampling
  and #458's effort_end tail keep the request's effort; `head_ids` (the prefix-reuse judge) injects
  identically or it would misjudge at token 0 - the mode text lives at the head, which is also why
  a switch mid-conversation costs one full prefix re-read, the honest price of the feature, and the
  reason the recommended moment to switch is a thread's first turn.
- **`REASON_MODES` is a drift-locked mirror** of the template's own `reason_mods` set -
  `test_reason.py` parses the list out of the template file and asserts set-equality, the same
  law-moves-into-code trick as `shelf_vote`: a DavidAU V2 that renames a mode trips a test, not a
  silent literal leak.
- **Config `"reason_mode": "<name>"`** sets the session default at boot (a profile-level pin - the
  "coding box, always coder" case); it requires the master switch (without it: a refusal message
  naming the key), and a name outside the list - or any name at all on a plain-flavored template -
  is refused loudly at boot and left off.
- **The `reason_mode` tool (v1.5, same day):** the model steering itself with no HTTP call. Offered
  on all three dialects under the same conditions as `memory_search` - the server-side tool loop,
  so harnesses that run no MCP of their own get it too - with the same `strata_mcp` trace shape and
  a local executor (microseconds, no hub). The mode pins from the loop's own message list, so the
  HTTP door's "last" race does not exist here: the conversation running the tool is by construction
  the one steered. It lands at the next render - the next TOOL ROUND of the same turn (a test
  proves it: round two's prompt carried the mode block). No arguments returns the menu; a name off
  the menu errors with the menu (fail closed, as everywhere). With either gate shut the tool never
  joins the list (proved: the prompt is clean of it). One honest caveat this suite caught red: the
  tool's description lists the 41 mode names, so mode NAMES appear in every prompt that offers the
  tool - proving "no mode is set" means testing for the mode block's title, never the bare name.
- **Reason+answer pairs (v1.6, same day, the owner's ask):** one mode string `a+b` (`007+coder`)
  thinks like a and answers like b. The grammar is the fork's, not the template's: DavidAU's
  pairing guide documents stacking `{REASON:a}{REASON:b}` (his agentic-build example is exactly
  `007+coder`) but his shipped template parses ONE tag per message - measured without downloading
  the model by range-reading the GGUF metadata off the Hub (the template rides at the file's head,
  221,835 chars; its tag scan takes only `after_tag[1]`), so his double tag survives only as
  literal text in the message, steering softly by imprinting. The server makes the documented
  combo mechanical: the reasoning half rides the template as any single mode does, and the answer
  half's construct block is extracted once per mode - rendering IS the extraction, diffed against
  a SECOND REAL MODE, not against "off": measured, "off" omits the whole system turn, which
  bleeds the prologue and message tail into an affix diff - and spliced in right after it. The
  splice sits at all four render sites (prompt, history, full, head ids) so the head bytes still
  match the prompt's exactly; a single mode renders byte-identical (the "+" is the only thing that
  moves bytes); both halves validate at every door (tool, POST, boot default). The tool
  description and GET /reason now carry the author's use-case map and his recommended combos -
  the knowledge half of "adapt yourself on the fly": the agent knows which construct fits which
  task without scraping the model card. And the two request-level levers compose with it by
  design: `effort_position: "end"` keeps per-request effort changes cache-cheap while the pair
  rides the pinned head (switching a pair still costs one re-read), and `anthropic_thinking`
  decides whether unasked Anthropic requests think - the pair lands through the template's
  reasoning branch with thinking on and its instruct branch with thinking off, both injecting the
  same blocks (measured: it is why a Vader sticky rides a thinking-off request today).
- **The hybrid mode gets a Settings switch, and the agent picks its depth too (v1.7, same day,
  the owner's "can you do this on the fly?" + "a switch in the settings menu"):**
  - **Agent-pinned thinking LEVEL.** `reason_mode` takes an `effort` half (`{"mode": "007+coder",
    "effort": "xhigh"}`), `POST /reason` takes an `effort` field (conversation-level only - the
    session-wide knob stays the shared Chat settings), and `GET /reason` reports the pins. The
    ladder is sticky > request > shared > config: the agent outranks the harness until it hands
    the level back (`effort:auto`). It lands on the conversation's NEXT REQUEST, not the next tool
    round (the loop freezes its kwargs at entry) - the tool's answer says so. Levels are the stock
    Qwen knobs (none/low/medium/xhigh, "high" the alias), not DavidAU's names; per-conversation
    LRU 64 like every other piece; no pin = the same request dict, byte-identical by construction.
  - **The measured cache shape** (the tests pin both halves of the owner's `effort_position`
    question): with a mode pinned, the mode block OWNS the head slot the level sentence would use,
    so low/medium/xhigh change **no prompt bytes at all** - the level rides the engine beside the
    block (test: same conversation, two levels, byte-identical prompts). With no mode pinned the
    level sentence lives in the head and every level change re-reads the prefix - which is exactly
    what `effort_position: end` exists to avoid (test: the level rides the short tail turn right
    before the answer while the mode block holds the head). The position itself is an engine
    launch arg (`--tail-role-token`, boot-frozen); the level and the mode are the live levers.
  - **The Settings menu.** `runconfig.EDITABLE` gains **`reason_modes`** (a bool row the page
    labels "Hybrid mode for DavidAU's Turbo-Brilliance models") and **`reason_mode`** (a dropdown
    of the 41 constructs plus the 14 recommended pairs plus empty = no default; the web app
    renders any enum row from its choices, so `app.js` needed no change). Both apply **live** on
    `POST /config` - the exception to the config's "next start" rule, because the render ladder
    reads this process's live state every request - and persist in the file for the starts after;
    the response says so. The flavor gate outranks the switch live too: a plain-template box keeps
    the feature off whatever the menu says. The mode list moved to `runconfig.py` (the menu is its
    other owner) and `server.py` re-exports it - one source, the drift lock unchanged.
    `test_runconfig` 10 OK, `test_reason` 30 OK, full gate 1002 OK (skipped 11; one first-run
    flake was the 62 GB box OOM-killing a mock engine mid-suite, exit -9 - the rerun passed).
- **The installer now carries the model (`setup.py` "dau" family, 2026-10-17):** `setup --family
  dau --model IQ3_S` installs DavidAU's Turbo-Brilliance re-quant - pinned repo commit, both
  shard SHA-256s from the Hub's LFS pointers, Apache-2.0 stated at install. Two measured facts
  shape the entry: his GGUF **embeds his chat template** (226,745 bytes, its mode list set-equal
  to `REASON_MODES`), and setup's tokenizer export writes it to `pack/tokenizer/chat_template.jinja`
  which the server prefers over its shipped file - so the modes' template rides the download with
  nothing extra fetched and the shipped default template stays stock; and **his shard 2 is the
  original IQ3_S's file byte for byte** (SHA-256 `316b46f3...` equal against ISTA's tree) - because
  shard 2 is not model weights at all: it is the family's shared n-gram PLE table, the same in every
  GSQ-RCO family (the coder note in setup.py has always said so), so he joins the shard-2 sharing
  the qwen and coder sizes already do - an owner of the original IQ3_S switching to this family
  downloads only his first shard, which carries all his weights. Owners may replace the table with
  sleepyeldrazi's bigger Q8_0 one (54,400,261,312 bytes, measured 2026-10-17) in the same slot -
  this fork's dev box has run that swap since 2026-10-09 - but setup installs the bundled file and
  does not chase that repository. The family sits after the coder in the
  menu (same-size variants of the original first): the menu snapshots in
  `tools/test_setup_menu.py`/`test_setup_unsloth.py` moved with it, and the whole 40-test setup
  battery passes (engine_hash's 2 failures pre-date this change on a box without the engine
  installed, verified against the clean tree the same day).

**The performance gate, swept 2026-10-17 and recorded before any reading of it:**
`tools/m4_reason.py` ran 256 generations on this box - 8 arms (no-tag default, ultra, off,
xhigh, coder, house, deeptree, socrates) x 16 probes x 7 shapes x 2 passes, temp 0, arms
interleaved probe by probe, memory muted per request. The arms ride DavidAU's own
`{REASON:}` tag inside the probe (client tag outranks every server level, so the sweep never
moves the sticky map it measures), and scoring is deterministic end to end - exact-answer
match, candidate code exec'd in a subprocess against hidden asserts, checklist regexes, strict
JSON - no LLM judge, so reruns grade identical bytes. The noise floor is built in: on this box
the no-tag arm and `{REASON:ultra}` are the same framing, and they came apart by 0.005 - that
is the instrument's own spread at temp 0 (MoE routing and batch shape are what's left).
**The sweep also caught a grader bug by being right: the ordering puzzle resolves
Ana > Bea > Cy > Dee, every arm answered "Dee" 16/16, and the bank expected "Cy."** The model
was correct and the checker was not; the bank is fixed and the saved run rescored offline from
the stored texts - zero new generations to correct the record.
Corrected verdict: **no arm beats the default beyond the ±0.05 band** (deltas +0.005 to +0.024,
deeptree the largest). Six of seven shapes saturate at 1.00 for every arm; the two that move
at all (plan 0.81-1.00, summarize 0.80-0.95, partial-credit checklists) move by about as much
as the same-framing pair moves on the same shape - inside the instrument's per-shape noise.
Zero truncations at 3072 tokens. What the run does show: the modes steer, visibly and
structurally - House's ◤H◥ asides, DeepTree's PHASE blocks, Socrates' premise interrogations
all arrive in the saved transcripts - they just do not change outcomes on tasks below this
model's competence floor. So the docs claim steering, not performance; a "mode X beats default
for task class Y" line would need a round-2 bank with harder shapes (multi-step reasoning,
longer code, adversarial constraint counts), and the instrument is ready for that rerun the
moment someone wants the claim. **Round 2, same day** (the owner: don't hand-write harder probes,
use published suites): the bank now stands on GSM-Symbolic v1, MATH-500, HumanEval, five
BIG-Bench Hard tasks and GPQA-diamond (gated origin; the owner's HF account was granted access;
token in a chmod-600 file, never in the store) - 88 probes, 9 shapes, every checker grading its
published gold 1.00 before the first generation (`tools/m4_bench_fetch.py`, `tools/m4_bank_r2.py`,
sha256 manifest, fixed-stride sampling). The default-arm calibration then delivered its own
finding: **seven of eight shapes saturate at 1.00 for this box** - even BBH date_understanding
and seven-object deduction, whose published headroom was measured on 7-8B models - and only
MATH-500 (0.83) and GPQA showed real headroom. That is itself the answer to how hard this model
is: the mode-effect question can only be asked where the model actually struggles, and on this
box that is competition math and graduate science, nowhere else.

**Still owed, stated before it is claimed:** (2) **the baseline clock reset**:
every accuracy number on the books (0.761 arm C, the 0.739 Postgres re-record, the grounding
+0.207) was measured on the PRE-swap weights, so any future arm either re-baselines on the same
model or says so in the line. The modes were measurably active on the stock model too (the persona
answers, the menu printed), so "his tuned weights make the difference" is a comparison the
measurement will settle, not this paragraph. (3) **MTP/PLE pairing**: closed 2026-10-17 by the
user (he maintains the build): his model uses Strata's default MTP draft, and shard 2 is the
family's shared Q8_0 PLE n-gram table - it is nobody's to ship per-repo - so there was never
anything of his to pair against. (4) The in-turn **tool form** shipped the same day as v1.5
(see the tool bullet above): the API door gives autonomy to any client that can make a request, and
the tool gives it to models that can only make tool calls - one boolean switch behind both.

## The 2026-10-17 store cleanup: 233 manual operations, and the two write-path laws that stop a third

The owner's order before round 2: go through the store by hand, merge the duplicates, purge the
junk, fix the weak points found. The dump: 2,885 active rows (3,964 with history). What the
read-only audit (cosine clustering on the store's own embeddings, threshold 0.90, plus pattern
scans, plus every flagged row read by eye) found, and what was done about it:

- **31 duplicate clusters, 99 rows - every one a curation-sweep paraphrase.** The Postgres-rebase
  decision sat eleven times; the supersession rule itself, seven; the Memory-tab bug report,
  seven across four clusters. All merged: **77 invalidations**, each chained `by` the row chosen
  to survive (most complete text; dated where dates matter; cross-cluster folds where two
  clusters described one fact). Kept rows untouched.
- **~150 ingest shards from the 2026-10-12 SDK-guide import** (1,755 rows in, sentence-split
  verbatim): citations cut at "U.S." and "Stat.", indented code collapsed to bare statements
  ("import warnings from mcp import MCPDeprecationWarning"), bare section headings, and Q&A
  answers whose questions only the document knows ("Straightforward and proven."). All read
  individually, all retired: **155 invalidations**, history intact.
- **Six true junk rows forgotten outright**: five quiz-corpus stragglers the 2026-10-16 purge
  missed (Nebraska radio stations, Minnesota townships, a bench ID) and the one synthetic venue
  row in the live store ("The bridge of Smokeport is Zivvleax38267-1.").
- **91 pending turns marked curated** - today's journaled session rounds, every fact of which
  already stands as a session row; letting the sweep re-extract them would have re-minted the
  same clusters a third time.
- Store after: **2,647 active** from 2,885; recall noise down, nothing factual lost.

**The weak points, found by measurement and fixed in the write paths:**

1. *Root cause of the duplicates: turn journaling journaled each API round of a multi-call agent
   turn as a new exchange* - one OpenCode turn left five rows with identical user_text, the
   extractor read the same content five times, and five paraphrased memories were minted.
   `journal_turn` now **updates** the newest same-scope row when the user message is identical
   (casefolded, whitespace-folded): one row, the LAST round's answer (tool-call rounds are
   overwritten), re-queued `processed=0` so curation reads the settled conversation. The exposure
   ledger becomes per-user-turn, not per-API-call - a five-round agent loop is one over-exposure,
   matching the per-turn recall design. Side effect proven by the tests' own first failure: five
   ExposureDecay tests that simulated distinct turns with identical text collapsed to one row -
   the new law working as stated.
2. *The sweep's paraphrase window*: the supersession law arm (LAW_TH 0.78, judged before store,
   RESTATED retires the older, CORRECTS lands amended) shipped at 23:34 and its first-live-run
   fixes at 00:23 on 2026-10-17 - **the 10-17 duplicate clusters predate it**; the sweep log
   since carries 88 law flags with correct verdicts (cos 0.858-0.914). No code change needed;
   the cleanup removed what the gate could not prevent before it existed.
3. *Ingest stored shards it should never have*: `tools/m2_ingest.py` now reunites abbreviation
   splits before cutting sentences ("8 U.S." + "C. 1101" is one sentence again) and rejects units
   that cannot stand alone - unbalanced parens, collapsed code, clauses cut mid-sentence,
   answers-without-questions under 45 chars carrying no digit, determiner or possessive. Every
   rejected/kept string in `tools/test_m2_ingest.py` is a real shape from the cleanup. Curated
   documents that want their fragments anyway pass `--no-gate`.

**The same afternoon proved the store self-heals, and closed the loop on manual cleanup:**

- `m7_consolidate --go` at 0.85, after an encrypted `m6_backup` seal (6.8 MB, 0.4 s): 12 rows
  retired, **zero residual clusters** - the store is fully merged end to end.
- The sweep's law arm self-retired ~36 more rows between the 12:28 boot and 14:00 on its own:
  new paraphrases landing, old ones chained out. The manual pass is now history, not a routine.
- The journaling law proved live: this multi-round conversation journals as exactly ONE turn row.
- Curation cadence verified, not assumed: `strata-sweep.timer` (systemd USER timer - a system-level
  `list-timers` misses it, which briefly produced a wrong "no scheduler" row, corrected in-store)
  ticks every 30 minutes since 2026-10-12, last tick 14:09.
- What WAS missing - a backup-and-consolidate cadence - is installed: `strata-consolidate.timer`
  (units mirrored in `tools/systemd/`), weekly, `Persistent=true`, guarded on the server answering
  `GET /memory` before it touches anything: `m6_backup` then `m7_consolidate --go`. First tick
  Mon 2026-10-19 00:56.

## Licensing and attribution

- Strata (upstream) is MIT © Niko1221 and contributors; this fork's independent code keeps MIT.
- MAM / NIRVANA material: "AKBASCORE RESEARCH SOFTWARE LICENSE" © 2026 Mustafa Akbaş — ideas, algorithms and
  independently developed implementations are **not** covered (their license §6); research, personal and
  sub-$50M commercial use of the material are permitted; any copied or substantially derived code stays in
  clearly marked files with attribution. Prior MIT-era AkbasCore material remains MIT with its attribution
  notice retained.
- Technical disclosures cited: doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475,
  10.5281/zenodo.23381336 (MAM / Silicon Hippocampus v1.4, 2026-10-15).
  Closest published systems work: CacheBlend (arXiv 2405.16444), Prompt Cache (arXiv 2311.04934).
