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
./dev-down.sh   # stops dev, brings production :8004 back (detached, no browser; checkpoints are gone -
                # first request re-reads prompt history, ~1-2 min on long sessions)
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
  `./dev-up.sh --force` once agreed), tests run against :8005, then `./dev-down.sh` hands :8004 back.
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
  the current store version.
- **Per-request controls (headers):** `X-Strata-Memory-Scope: <name>` names the project bucket (clients
  opt in; default "default"); `X-Strata-Memory: off` mutes injection and journaling for one request.
- **Journal:** every completed chat-completions exchange is appended to `turns` (raw material for the
  Stage 3 curation loop and the eval harness) - collected transparently from the streamed chunks; a store
  error never breaks an answer.
- **Control API:** `GET /memory` (stats + recent facts; `?q=` for a recall probe, `?scope=`, `?limit=`) and
  `POST /memory` `{"op": "remember|forget|search|turn|turns", ...}` - for the web app, for operators, and
  for the eval harness (hand-posted turns). Gated by the API key like `/props` and `/slots`.
- **Tests:** `python -m unittest serve.test_memory` (9 tests, no GPU): store/dedup/scope/stickiness units,
  a mock-engine HTTP end-to-end (inject → journal → mute → forget), memory-off 404 + byte-identical chat,
  and 400s for bad input. The full `serve/test_*.py` suite (699 tests) guards the untouched default path.
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
  8-case panel that saturates at ≥1.5B — separation needs the scale regime, which on 0.5B (banks
  5/10/20/40, 2 targets/size) showed no decay at that resolution (INDEP 0/2 throughout; INCR 1-2/2).
  A 1.5B curve is the next rung.
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

## Licensing and attribution

- Strata (upstream) is MIT © Niko1221 and contributors; this fork's independent code keeps MIT.
- MAM / NIRVANA material: "AKBASCORE RESEARCH SOFTWARE LICENSE" © 2026 Mustafa Akbaş — ideas, algorithms and
  independently developed implementations are **not** covered (their license §6); research, personal and
  sub-$50M commercial use of the material are permitted; any copied or substantially derived code stays in
  clearly marked files with attribution. Prior MIT-era AkbasCore material remains MIT with its attribution
  notice retained.
- Technical disclosures cited: doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475.
  Closest published systems work: CacheBlend (arXiv 2405.16444), Prompt Cache (arXiv 2311.04934).
