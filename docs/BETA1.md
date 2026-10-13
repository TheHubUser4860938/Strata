# Strata memory-core - beta-1 (Linux): the claim list

Every line here is a measurement with a filed record, not a plan. Claims that did NOT survive
their venue are listed at the bottom, where they belong. Measured on: RTX 4090 24 GB, 62 GB RAM,
Linux, engine 0.1.43.1 with MTP loaded, model `qwen3.8-flash-next-iq3_s`. Venue rules: temperature
0.8/1.0 with >= 3 repeats for pass/fail (the official scorer's decoding is mirrored where the
benchmark says so), one fresh conversation per ask, timings read from responses - never from
engine logs.

## What beta-1 claims

1. **Curated store (tier 3).** FTS5 + confidence/recency scoring, per-class shelves (LLC-style),
   encryption at rest with atomic seal and plaintext shred on clean shutdown - **six shred-to-reveal
   round-trips proven** across boots (the sixth on a 13k-row venue store, 2026-10-13). Budget law,
   invalidation chain (`invalid_at`/`superseded_by`), self-feeding journal via a systemd user timer
   (pressure- and idle-gated). Consolidation runs with the v3 laws: subject rule, payload
   containment, a 4-token floor on the 70% path - each added after a measured false-merge rate
   (round 2: 44%, round 3: 39%, both audited and restored; `tools/m2_merge.py`,
   `tools/test_m2_merge.py`).
2. **Agentic recall (C).** The model browses its own store mid-answer with a `memory_search` tool
   the server executes itself - no client wiring - wired identically through all three dialects
   (chat, Anthropic, responses). Parity measured live 4/4 (boot 14: the same fact recalled through
   anthropic + responses shapes, no leaks, final-round usage 3,837 tokens).
3. **Standing cartridge (tier 2).** The store's USR/DEC shelves seeded once as an engine session
   file: **restore 80.8-84.0 ms median for 247 MB** (two clean venues, `spikes/cartridge/`);
   the pin law refuses foreign files by config fingerprint (twice live); the knowledge delta is
   real (warm 3/3 correct, cold 3/3 `idk`, wrong-cartridge control 0 leaks).
4. **Conversation cache, measured (`spikes/cartridge/M3D_CACHE_EXP.md`).** A displaced conversation
   returns from RAM in ~13 ms instead of re-prefilling (2,054 ms -> 72-77 ms for a repeated ask,
   inside the venue record itself); the park is opportunistic (eviction under RAM pressure returns
   full prefills - both halves in one run). Cross-conversation prefix inheritance: absent (three
   fresh conversations reused nothing).
5. **The block law, measured (venue run 5).** Continuation reuse works only when the per-turn recall
   block is byte-identical; one changed note invalidates the whole prefix (3,543 tokens re-read,
   cache_n 0). Block-to-tail placement is the named next lever with its own behavior venue - not
   shipped, not claimed.
6. **Memory UI (D).** `/memory-ui` + `/web/memory.js` served live (200s verified, boot 14): stats,
   search, forget, re-seed.
7. **Benchmark (F).** LongMemEval-V1 oracle, all 500 official questions x 3 repeats, the benchmark's
   own checker prompt verbatim, judge model local (disclosed): **accuracy 0.683 per line, 0.694 by
   question majority**, per-repeat 342/336/347. Muted control: **98.4% `idk`, gold leaks 2/1,500
   (0.13%)**. By type: single-session-user 0.953, single-session-assistant 0.905, preference 0.878,
   knowledge-update 0.727, abstention 0.667, multi-session 0.639, temporal-reasoning 0.425 (the
   known weak spot: day arithmetic over injected dates). Records: `spikes/lme/oracle-20261013-full.*`.

## What beta-1 does NOT claim

- **Prefill skipped per conversation** - the fresh-conversation time win did not measure (engine
  resume extends only the same conversation; the cache keys per conversation too). The earned
  cartridge claims are fast slot reload, pin-verified provenance, parked repeats.
- **State-without-text recall** (Phase 2, MAM CUT3): open research, needs engine grafting.
- **Windows**: beta-2 at earliest (Linux-verified paths only).
- **Session save without an MTP drafter**: upstream #1444, fix PR #1492 still open as of 2026-10-13
  (0.1.44 does not carry it); engines without MTP degrade to no-cartridge requests, never a broken
  conversation.
- **Full-history oracle parity** on F: retrieval rides FTS5 (no embedding model) and answers read
  recalled notes, not raw sessions; session-level recall 1.000 is a property of the scoped design.

## Reproducing

- Store/UI/recall tests: `python3 serve/test_memory.py` (57 tests, no GPU).
- Venue tools: `tools/test_m2_merge.py`, `tools/test_lme_harness.py` (5), `tools/test_m3c_venue.py`
  (3) - offline, fixture stores, no engine.
- F venue: `tools/lme_harness.py` (protocol + resumable records) then `tools/lme_report.py`.
- M3c venue: `tools/m3c_venue.py` with the port quiet - nothing else conversing, sweep timer
  stopped (user-scoped: `systemctl --user stop strata-sweep.timer`).
