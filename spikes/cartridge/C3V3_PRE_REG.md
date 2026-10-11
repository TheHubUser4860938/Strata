# C3-v3 pre-registration — the install-swap purity probe (nonce instrument, zero weight path)

Written 2026-10-11 night, after C3-v2 (VOID at 9289f428) and BEFORE any v3 ask. v1 and v2
stand as recorded; this is the last instrument in the C3 sequence, and it answers ONE question
with a confound-free tool.

## The one question left open, and why only a nonce can answer it

The C3 SCORED question - does cartridge recall hold at 100 facts? - is answered twice over:
c3b 100/100 raw in all 3 passes of v1 AND all 3 passes of v2 (600/600), 0 distractors across
19,800 possible collisions, 1,310/1,310 geometry, every ride exact at 0.13 s/ask. What the
VOIDs shared is the LEAK clause, and the v2 forensics show why it cannot certify: with
real-world facts, "gold under the decoy install" is confounded by priors (row56: cold 0/4 with
a confidently WRONG prior, decoy-hit 2/3 in one run and 0/1 in the other, misses reverting to
the wrong prior = binomial noise around a weak prior, not a persisting state; distribution
over 89 targets: five 1/3, one 2/3, zero 3/3 - a persisting 3,499-token bank would surface
broadly and stably).

The remaining MECHANISM question: **after a restore replaces live state with a different bank,
can the previous bank's activations leak into the new conversation** (stale KV pages reachable
despite the engine's session-zero/restore semantics)? Real-world golds cannot instrument this
(priors fire in every venue). NONCE golds can: zero weights path, so a nonce appearing under a
foreign install is unambiguous. C2 ran exactly this swap test at 3+3 facts (0 leaks / 18 asks);
v3 runs it at 103+100 scale with the engine's own ride bookkeeping as corroboration.

## Venue (fixed; channel off / temp 0.8 / muted / fresh conversation / max_tokens 24 as ever)

- **bank2** = the 100 Gate-1 facts PLUS the 3 D2 marker nonce facts (markers.json fact_texts,
  cues from state/v2_build.json: Voldranium/Quendefin Marsh, Klornish/Bralmwick, Krevven/
  gloamfish - cold idk-proven tonight in C2) = 103 user messages + sampled acks, saved once.
- **Positive control (9 asks, 3 passes x 3 markers)**: restore bank2 -> ask = the full bank2
  message list + CUE_PREAMBLE + marker cue (the C2 shape that went 9/9). Band: **9/9 gold or
  VOID** - a probe that cannot detect its target when present detects nothing ever.
- **decoy2** = the 100 Smokeport nonce facts + acks, saved (entities disjoint from every marker).
- **Persistence probe (9 asks, 3 x 3)**: restore decoy2 -> ask = the full decoy2 message list +
  the marker cue. The marker entities appear NOWHERE in decoy2 or the cue's own bank. Ride
  guard per ask: `cache_n == decoy2 n_restored - slack` (the engine's bookkeeping that the
  real bank's prefix is NOT in the read path). Band: **ANY nonce gold in any of 9 =
  PERSISTENCE-DETECTED; 0/9 = SWAP-CLEAN-AT-SCALE.**
- **Texture (18 asks, recorded, NEVER banded)**: the six v2-noisy real rows (9, 10, 29, 56,
  62, 87) x 3 passes under decoy2 - expected pattern is instability; any 3/3 row is recorded
  as prior-strength information, not as persistence (the nonce channel owns that verdict).
- Geometry guard per ask; any geometry VOID kills the run (same as v2). --repeats fixed at 3.

## Bands

1. VOID - positive control < 9/9, or any geometry VOID.
2. INSTALL-BROKEN - any persistence-probe or positive ride-guard failure.
3. PERSISTENCE-DETECTED - >= 1 of 9 persistence asks contains any marker gold.
4. SWAP-CLEAN-AT-SCALE - 0/9 nonce golds.

## Final C3 claim composition (fixed wording for the README/paper line)

The C3 result is a THREE-PART honest whole: (a) scored-arm evidence = c3b 600/600 raw over two
pre-registered runs at 100 facts, zero interference measured, ~25 re-read tokens/ask, 0.13 s,
no selector; (b) the leak clause of v1/v2 is a documented MEASUREMENT confound (real-world
priors), not a memory failure - banked verbatim; (c) install-swap purity at scale = whatever
v3's nonce probe says, with its positive control. No single-run "PASS-AT-SCALE" stamp exists
for the 100-fact cartridge, and none will be claimed; the claim is (a)+(b)+(c), said plainly.

## Cost

~450 requests (206 + 200 encodes, 36 probed asks, ~18 texture + slot ops) -> ~6-8 min,
backgrounded under the quiet protocol.
