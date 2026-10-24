# C4 pre-registration — the bank-capacity probe: how big may a cartridge grow before it stops being a cartridge?

Written 2026-10-24 morning, BEFORE any C4 ask, on the heels of the venue day that supplied its
instruments (a durable 500-question harness, a shelved router-live store, and the demote numbers).
C2 (3+3 swap purity), C3 (100/100 recall at the 100-fact bank, 600/600 across two eras) and C3-v3
(nonce leak probe at 103+100, 36/36 rides exact) stand as recorded. Their shared verdict: NO
interference ceiling found AT 100. The roadmap call of 2026-10-11 marked the open driver exactly:
"capacity beyond ~100 (1,000-fact bank ~35k tok fits 393k - open C4) + multi-domain selection."
C4 is that open. **The ceiling is unknown - that is the point.**

## The one question

**Does standing recall hold as the bank grows 100 -> 500 -> 1,000 facts, and what does each rung
cost?** C3 proved 100 is clean; the engine can carry 35k standing tokens in its 393k context with
room to spare; whether the MODEL still answers from a 1,000-fact bank as faithfully as from a
100-fact one is a measurement, not a hope. Three secondary questions ride along, pre-registered so
they cannot quietly become the headline later:

1. **Cost curve**: per-answer latency and tokens at each rung (the bank rides EVERY prompt - slot 2
   is permanent); and the one-time re-seed/install cost per conversation start as the bank grows.
2. **Leak curve** (c3v3 template at scale): does a swap between two big nonce banks stay pure
   (previous bank unreachable), or does interference appear with volume?
3. **Displacement**: the standing text currently caps at 12,000 chars; a 1,000-fact bank needs
   ~150,000. C4 runs with the cap raised by CONFIG, and records whether the bigger standing text
   crowds the tier-3 recall block out of the prompt budget (it should not: 48k block + 35k standing
   against 393k context - but "should not" is what the confound clause is for).

## Venue (fixed; the C-series shape, unchanged)

- Production engine (huihui UD-Q4_K_XL, 393k ctx), channel off, **temp 0.8** (user-measurement law:
  0.8/1.0, never 0, >=3 repeats - C3's own venue agrees), memory MUTED (the bank answers; tier-3
  recall stands aside), fresh conversation per probe family, max_tokens 24.
- Bank install rides the cartridge plumbing (c1): save/restore files via the live slot mechanism;
  banks are scratch stores under Strata-data/bench/c4/, never production.
- **Rungs**: 100 (control - C3's known-clean number, re-measured tonight's era for comparability),
  500, 1,000. Same generated nonce families per rung (zero-prior markers, idk-proven cold BEFORE
  install, as Gate-1 did), same QA probe set (one ask per fact, cued by its marker pair).

## Instrument and gates (fixed before the run)

- **Gate per rung**: per-fact recall >= 95% averaged over 3 passes, scored by exact-marker match
  (nonce golds need no judge); a rung FAILS below 95% and the ceiling is declared between the last
  passing rung and the first failing one.
- **Leak gate**: foreign-bank asks answered correctly <= the 100-rung's observed leak rate (c3v3's
  zero-leak precedent; binomial allowance stated at run time).
- **Cost ledger**: tok/s, first-token latency, seed/install seconds, and standing-text tokens are
  recorded per rung - reported, not gated (this probe's product is the CURVE, not a verdict).
- **Cold control**: every probe family idk-proven blind first; any fact with a cold hit is replaced
  before install (Gate-1 law, non-negotiable - priors are what voided C3-v2).

## Honest limits (said before, not after)

- Nonce banks measure MECHANISM capacity, not semantic-domain capacity: real 1,000-fact banks are
  related facts and will interfere more than unrelated nonces (the multi-domain selection driver
  from 2026-10-11 stays open for C5's bank-selection question).
- 500 and 1,000 are chosen rungs, not a search; a failure at 500 makes 1,000 moot and a pass at
  1,000 leaves the true ceiling above it (the next rung, if earned, is C4-v2's to pick).
- The 12k-char cap raise is CONFIG for the probe only; production keeps its small standing set
  until C4's cost curve says otherwise (the small budget loses standing status, not knowledge).

## Sign-off

Owner ratifies (or edits) the bands above; c4_run.py follows the c3v3_run.py pattern after
sign-off; the engine is shared, so the run schedule is his call, not this file's.
