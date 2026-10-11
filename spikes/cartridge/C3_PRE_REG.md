# C3 pre-registration — the 100-fact cartridge at scale (written BEFORE any C3 measurement)

Written 2026-10-11 night, after C2 PASS (0f9484b7) and before any C3 ask has run on any boot.
Do not edit after the run; amendments get dated paragraphs. C2 proved the channel at 3 invented
facts (18/18 gold, every ride exact). C3 asks the scaling question the roadmap hangs on:

**Does whole-state cartridge recall hold when the bank carries all 100 Gate-1 facts at once —
the same facts, questions, golds, and scorer that produced tier-3's banked 100/100 — or does
interference grow with bank size and force the marking roadmap (tagged per-domain banks,
selector-installed) to earn its keep?**

## Data (frozen, shared with tier-3's banked run)

- `spikes/window/eval_questions.csv`: 100 rows (question + expected_answer_contains). Verified
  tonight: **all 100 are cold-UNKNOWN targets** (eval_candidates.tsv verdicts), kinds
  RADIO 45 / ND 25 / TWP 20 / HW 10 — obscure web facts, no known-cold ambiguity anywhere.
- Bank fact text = `"{question} Answer: {gold}."` — byte-identical to what tier-3 taught.
- Scoring = `spikes/window/gate1_driver.hit()` imported, not reimplemented: strict
  word-boundary, all listed tokens required, FAA K-stripped variants + date day-first +
  st/period + ordinal normalization — the exact family that banked tier-3's 100/100.
  Self-consistency verified offline: 100/100 fact texts answer their own question under it.
- Distractor = hit() on ANY of the other 99 rows' golds (MAM's documented failure mode,
  measured explicitly at scale for the first time).

## Venue (fixed before measurement)

Channel per the standing protocol + C2: `POST /v1/chat/completions`, `reasoning_effort:"off"`,
**temperature 0.8**, `max_tokens: 24`, **`X-Strata-Memory: off` on EVERY request**, one fresh
conversation per ask, geometry guard per ask (`usage.prompt_tokens` == local `venue_render`
render, else the ask is recorded VOID and counts against the gates). Table venue: STOCK PLE
(boot 3 or any stock boot; tier-1 writes must not confound). Slot venue: C1 PASSed twice on this
pin class; C1 re-gates before C3 on any new boot.

- **Bank**: all 100 facts as 100 user messages in CSV order, each acked by the model
  (ack cap 16 tokens, replayed verbatim in every continuation) — one encode walk, one
  `save`. Expected bank ~3.5-4.5k tokens (dry-run estimate recorded pre-run).
- **c3b (the scored arm, the product shape)**: per pass (3) x per fact (100):
  `restore` -> ask = the identical 201-message list (100 facts + 100 acks) + the bare CSV
  `question` as the final user turn. Ride guard per ask: `cache_n >= n_restored - 8` AND
  `prompt_n <= (render - n_restored) + 8`. 300 scored asks.
- **Plumbing gate (100 asks, 1 pass)**: `"{fact} Given only that, answer.\n\n{question}"` in
  one prompt, fresh conversation — the read path per fact.
- **Cold control (100 asks, 1 pass)**: the bare question alone, fresh conversation. All rows
  are cold-unknown by selection, so ANY gold is contamination evidence.
- **Decoy-real control (100 asks, 1 pass)**: a decoy bank of 100 generated Smokeport nonce
  facts (same shape, zero shared entities, golds `Zivvleax<row>-<variant>`), encoded + saved
  once, `restore`d before each real question. Any real gold in a decoy-installed answer = the
  real bank's state persisting through an install swap. Decoy-own: recorded on every 10th
  question (10 asks, bonus, never scored).
- Controls run 1 pass by pre-registration: leak is a binary event and the scorer is strict
  word-boundary over cold-unknown targets; the SCORED arm gets 3 passes per protocol.
- Engine-log reuse-summary captured on the first c3b ask of each pass (corroboration only).

## Guards

1. geometry per ask (no render match -> ask VOID; plumbing gate cannot reach 100/100 through
   a VOID, so VOIDs surface as their own named failure).
2. ride (every c3b + decoy-real ask): as C2 guard 2, base = the restore's `n_restored`.
   Any c3b guard-2 failure = **INSTALL-BROKEN** clause, not a recall failure.
3. any 5xx/transport failure mid-run is recorded per-ask (geom-VOID) and the run continues;
   the verdict artifact always exists.

## Pre-registered verdict bands

- **VOID** if plumbing < 100/100, or cold leaks > 0/100, or decoy-real leaks > 0/100, or any
  geometry VOID, or any c3b ride-guard failure (reported as INSTALL-BROKEN).
- **PASS-AT-SCALE**: c3b gold >= 95/100 in >= 2 of 3 passes, all guards green, 0 leaks.
- **DEGRADE**: best c3b pass 85-94/100 -> the channel works but the interference tax is real;
  the distractor profile (per-pass count of other-rows'-gold hits, and WHICH rows collide)
  becomes the headline data and the marking roadmap (layer 2: tagged per-domain banks) earns
  its keep.
- **FAIL-AT-SCALE**: best c3b pass < 85/100 -> the raw whole-state channel saturates at 100
  facts; layered banks are mandatory before any product claim.
- **MECHANISM-SENSITIVE**: anything else -> recorded, stop, no venue-hunting without a new
  pre-registration.

## Head-to-head accounting (fixed wording, honest both directions)

| | tier-3 curated recall (banked) | C3 cartridge (this run) |
|---|---|---|
| accuracy | 100/100 at cap 16 | to measure: >= 95/100 band |
| re-read tokens per ask | ~485 injected (selector picks the fact) | ~50 (question tail; bank rides KV) |
| selector needed | yes (bm25 today, embedder next) | **no** — whole bank is the state |
| fact text in visible context | the selected fact only | ALL 100 (prefill-free, not text-free) |
| per-ask install cost | selector CPU search, ~ms | restore ~0.1 s (file) / 0 (live) |
| memory lives | text store, model-agnostic | KV slot: ~50-100 MB file + device state |
| works across restarts | trivially (text) | yes via slot files (pins permit) |

The cartridge's win is re-read COMPUTE and selector-freeness at conversation start; it does NOT
win visible context (all 100 facts ride every prompt) and buys nothing mid-conversation. Never
cheaper: attention over live positions + the KV/VRAM footprint.

## Cost book (measured on this pin class, for the run's honest accounting)

C1/C2 tonight: save ~235-250 ms, restore ~83-87 ms per bank (237 MB file at 130 tok; the
server's own figure is ~1.2 GB per 63k tokens, so the ~4k-token C3 bank file lands ~300-400 MB).
Estimated C3 wall: ~1,100 requests, 35-45 min engine-serial. Recorded from the run's own
timings, not this estimate, in the verdict file.
