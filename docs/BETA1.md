# Strata memory-core - beta-1 (Linux): the claim list

Every line here is a measurement with a filed record, not a plan. Claims that did NOT survive
their venue are listed at the bottom, where they belong. Measured on: RTX 4090 24 GB, 62 GB RAM,
Linux, engine 0.1.43.1 with MTP loaded, model `qwen3.8-flash-next-iq3_s`. Venue rules: temperature
0.8/1.0 with >= 3 repeats for pass/fail (the official scorer's decoding is mirrored where the
benchmark says so), one fresh conversation per ask, timings read from responses - never from
engine logs.

## What beta-1 claims

1. **Curated store (tier 3).** Native PostgreSQL (ADR-002, R1-R7 landed 2026-10-16): true Lucene
   bm25 over tsvector/GIN candidates + confidence/recency scoring, per-class shelves (LCC-style),
   float32 vectors in the store's own database with the pinned-matrix exact cosine. At rest the law
   moved from the file seal to the BACKUP - the seal/`.enc`/husk rules retired with the single file
   they were one-file rules for: data dir 0700, encrypted `pg_dump` under the store's key-file
   (`tools/m6_backup.py` - round trip verified live on the venue store mid-replay: 22.6 MB dump+seal
   1.7 s, restore into a fresh dir searched byte-identical 5/5). Budget law,
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
5. **Continuation reuse, measured** (clean adjacent probe, session idle, `spikes/cartridge/
   m2d_clean_probe.py`): **100% of the parent prompt given back** (2,629 of 2,622 tokens),
   prefill 2,011 -> 681 ms. The head block is pinned per conversation (keyed by the first user
   message) and the per-turn block rides the prompt tail by design - a multi-turn conversation
   pays its head prefill once at conversation start, not every turn. The venue's cache_n 0
   lines (runs 4-5) are slot replacement by an interleaved client plus park eviction under RAM
   pressure - the runbook's documented failure modes; a "block law" reading was filed and
   withdrawn the same day it was measured.
6. **Memory UI (D).** `/memory-ui` + `/web/memory.js` served live (200s verified, boot 14): stats,
   search, forget, re-seed.
7. **Benchmark (F).** LongMemEval-V1 oracle, all 500 official questions x 3 repeats, the benchmark's
   own checker prompt verbatim, judge model local (disclosed). Two arms, same store and protocol,
   one axis (head injection budget): at 48 notes / 12k chars accuracy is **0.683 per line, 0.694 by
   majority**; at the now-shipped **96 notes / 24k chars** it is **0.721 / 0.732** (per-repeat
   363/364/355) - the +3.8 is far outside the noise band and comes from multi-session (0.639 ->
   0.744) and abstention (0.667 -> 0.778). Muted control at both budgets: **98.4-98.7% `idk`, gold
   leaks 2/1,500 (0.13%)**. By type (24k arm): assistant 0.952, user 0.911, preference 0.900,
   abstention 0.778, knowledge-update 0.745, multi-session 0.744, temporal-reasoning 0.433 (the
   known weak spot - and the first diagnosis of it was wrong: `spikes/lme/TEMPORAL_DIAG.md` shows the
   0.433 row is evidence COVERAGE on multi-evidence questions, not day arithmetic; off-by-N is 12 of
   57 misses, and 20 questions refused with an answer row already in the block). The one
   regression, user 0.953 -> 0.911, is flagged in `docs/MEMORY_CORE.md`. Cost is first-turn only
   (pinned head): ~2.3 s at conversation start, zero per follow-up. Records:
   `spikes/lme/oracle-20261013-full.*` (12k) and `spikes/lme/budget24k-20261014-full.*` (24k).
   **Re-measured on the rebased Postgres store (R7 venue day, 3 repeats, hybrid arm): 0.739 per
   line, idk 0.145** - against the 0.761 SQLite-era baseline, inside the ±0.05 floor, so the rebase
   gate PASSES. The day's scary retrieval flags (rowcov 0.700 vs 0.885) were venue-day sweep
   contamination, not the engine: the sweep curated the replay's own 1,497 journal turns into
   1,103 user-scope facts that outrank gold on recency; restoring a clean backup and re-measuring
   offline reproduced the deterministic gate numbers exactly. Protocol law learned: a venue day
   means the curation sweep STOPPED. Record: `spikes/lme/venuepg-full-20261016.jsonl` (+ metrics).
8. **Hybrid semantic rerank (opt-in) - the biggest accuracy lever measured on F.** A pinned
   `bge-small-en-v1.5` ONNX embedder, **CPU-only** (the GPU belongs to the engine), verified by SHA-256
   at open: a checksum mismatch RAISES, any other load failure degrades to bm25 **loudly** (banner and
   `stats()` both say "lexical only"), because a missing model file must never take the memory down.
   Fusion is **by rank (RRF), never by mixed score scales** - the first weighted blend demoted a
   rank-1 exact-lexical answer to **14th** on a live 2,521-row store, which is the failure mode this
   design exists to avoid. The constant was swept on our own store the same week (2026-10-15,
   `tools/lme_retrieval_ab.py --rrf-k`, 10,973 rows, deterministic): **k=1 ships**, replacing the
   paper-convention k=60; the property that justified rank fusion survives the change (a lexical
   first place still outscores a both-arms fifth; the reach probe scores identically at 1 and 60).
   Vectors live in the store's own database (the `embeddings` table), so the backup law covers them
   with no new key handling; `remember()` embeds at write (3 ms); `tools/m4_embed.py` backfills old rows.
   **Arm C, same store, same protocol, third arm: 192 notes / 48k chars WITH the reranker = 0.761 per
   line, 0.766 by majority** (per-repeat 381/378/383) against arm B's 0.721 / 0.732 - **+4.0 points,
   about 7x the repeat-to-repeat band**. Muted control: 1478/1500 `idk` (98.5%); the single "gold leak"
   is a substring coincidence on a three-character gold (`100` guessed by a muted model), i.e. the same
   noise floor arm B showed at 2/1,500. Retrieval moved exactly as a **free offline A/B predicted**
   (turn_top3 0.749 -> 0.823; the gold rows actually pasted into the block 0.895 -> 0.943 - and the
   offline instrument reproduces the live venue's filed numbers to three decimals). By type:
   multi-session 0.744 -> **0.840**, abstention 0.778 -> 0.867, temporal 0.433 -> 0.465, assistant
   0.952 -> 0.970, preference 0.900 -> 0.911, user 0.911 -> 0.911, knowledge-update 0.745 -> 0.750.
   Cost, measured on the 2,521-row store: recall 52 ms lexical -> 137 ms hybrid (the ~85 ms delta is the
   near-dupe candidate pass, the cosine math is ~1 ms), against a ~8.5 s first turn: ~1.6%. Fixing the
   write path (two whole-store caches were rebuilt per write, which made ingest **quadratic**: 7 ms per
   write at 2,589 rows, 180 ms at ~10k) took a guarded write 107 ms -> 11 ms and a venue-shaped ingest
   from ~40 min to ~40 s. Record: `spikes/lme/armc2-20261014-full.*`.

## What beta-1 does NOT claim

- **Prefill skipped per conversation** - the fresh-conversation time win did not measure (engine
  resume extends only the same conversation; the cache keys per conversation too). The earned
  cartridge claims are fast slot reload, pin-verified provenance, parked repeats.
- **State-without-text recall** (Phase 2, MAM CUT3): open research, needs engine grafting.
- **Windows**: beta-2 at earliest (Linux-verified paths only).
- **Session save without an MTP drafter**: upstream #1444, fix PR #1492 still open as of 2026-10-13
  (0.1.44 does not carry it); engines without MTP degrade to no-cartridge requests, never a broken
  conversation.
- **Full-history oracle parity** on F: answers read recalled notes, not raw sessions, and
  session-level recall 1.000 is a property of the scoped design (each question's haystack is its own
  scope). Retrieval rides FTS5 bm25, and - since arm C - an **opt-in** hybrid reranker; a store with no
  `memory.embedder` in its config never imports the module and ranks byte-identically to the last
  release.
- **"More injection budget" IS a lever on long rows — and this page said the opposite until the next
  day.** Arm C doubled the budget (96 notes / 24k chars → 192 / 48k). Re-rendered offline with the block
  cache cleared between settings (`tools/m4_blockcov.py`, 500 questions), the block did grow: **14.6 →
  19.1 notes** lexical and **15.9 → 20.3** hybrid, 18,735 → 24,429 chars. At 24k the CHAR cap binds on
  ~1,300-char venue rows; at 48k what binds is how many rows the question's own scope holds (~22 in the
  oracle set). Gold-row coverage of the rendered block:

  | budget | lexical | hybrid |
  |---|---|---|
  | 96 notes / 24k chars | 0.895 | 0.946 |
  | 192 notes / 48k chars | 0.942 | **0.965** |

  So arm C's +4.0 was a **bundle of two levers of similar size** — the reranker (+0.051 coverage at 24k)
  and the doubled budget (+0.047 at the lexical arm) — not the reranker alone. Arm B ran with no embedder
  at all, so B→C changed two things at once and the venue cannot separate them; only a fourth arm
  (lexical at 192/48k) can, and that is a venue decision rather than a claim. The dev config still stays
  at 96/24k: on ~250-char personal facts the NOTE cap binds, and there the doubled budget was measured to
  add +0.000 relevant facts. How the error happened is in `docs/MEMORY_CORE.md` ("Probe hygiene") and it
  is worth keeping: the pin-cache trap was found, written down and fixed in three probes, and missed in
  the fourth — the one whose number carried the attribution.
- **The rest of the failures are mostly not memory's.** Of arm C's 346 wrong lines, **244 had every gold
  session present in the block** (reasoning) and 102 were missing one (retrieval). temporal-reasoning
  sits at 0.465 with 0.852 session coverage: the evidence is in the prompt and this 24 GB-class model
  still cannot order it. Retrieval-level rank-1 damage corroborates it - knowledge-update lost 0.139 of
  rank-1 to the reranker and its accuracy moved +0.005, so at this block size the model is not using
  rank order, which is also why a speaker-aware re-order was measured offline and then parked.
- **The temporal ordering failure now has a measured fix at the WRITE path.** Facts extracted with
  the dated prompt answer the same 127 temporal questions at **0.591-0.606 across three answer
  repeats** vs 0.370-0.425 undated (pooled **+0.207**, McNemar p<=1.2e-12; abstention 0.41 -> 0.11),
  measured in scratch stores on the venue corpus, memory muted, one server. Re-sorting the block
  chronologically in the renderer does nothing (-0.008, inside the measured single-run noise floor
  of ±0.047): the model orders events when the FACTS carry dates, not when the renderer reshuffles
  dateless ones. The caveat that reframes every temporal number above: the venue store's rows carry
  **no dates at all** - the oracle's slash-delimited session dates never matched the harness's
  hyphen-only `day_of` regex (0/10,973 rows), so 0.433/0.457/0.465 were measured on dateless
  evidence, and a dated verbatim re-ingest is untested. Records: `docs/MEMORY_CORE.md` ("Date
  grounding settled at the answer level"), `spikes/lme/grounding-20261015-*`.

## Reproducing

- Store/UI/recall tests: `PYTHONPATH=. python3 -m unittest serve.test_memory` (no GPU); the full
  `serve` discovery suite is the pre-push gate (902 OK, skipped=11, 279 s, re-run 2026-10-17).
- Venue tools: `tools/test_m2_merge.py`, `tools/test_lme_harness.py` (5), `tools/test_m3c_venue.py`
  (3) - offline, fixture stores, no engine.
- F venue: `tools/lme_harness.py` (protocol + resumable records) then `tools/lme_report.py`.
- M3c venue: `tools/m3c_venue.py` with the port quiet - nothing else conversing, sweep timer
  stopped (user-scoped: `systemctl --user stop strata-sweep.timer`).
