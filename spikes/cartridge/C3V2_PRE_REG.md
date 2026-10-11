# C3-v2 pre-registration — the 100-fact cartridge, controls fixed to the tier-3 lineage

Written 2026-10-11 night, AFTER the C3-v1 run (banked VOID at 1b4f781c) and BEFORE any v2 ask.
The v1 bands stand as recorded and are never edited; this is a new experiment with its own
frozen bands. The scored arm (c3b) and every guard transfer byte-identically; ONLY the control
design changes, for one measured reason:

## Why v1 VOIDed and what v2 fixes (mechanism known, not hand-waving)

C3-v1: c3b 100/100 x 3 passes, 0 distractors, 400/400 rides green, plumbing 100/100 - but
cold leaks 5/100 + decoy-real leaks 9/100 fired the any-leak rule. The autopsy (jsonl, this
file's evidence): every cold leak ran `cache_n=0`, memory-muted, geometry-exact -> gold came
from WEIGHTS, not state; the rows' cold-unknown selection came from the `eval_candidates.tsv`
channel of an earlier day, and tonight's channel at temp 0.8 answers those 5 from weights.
Every decoy leak rode the decoy bank (`cache_n=4863`, real bank absent from the conversation);
contamination predicts decoy-golds as a SUPERSET of cold-golds - actual overlap 3/9, with 6
decoy-only rows (cold-wrong, right under 4.8k tokens of IRRELEVANT context) and 2 cold rows
flipping the other way: single-sample noise around partially-known facts, not persistence.

The leak rule was designed for C2's NONCE golds (no world-knowledge path). Gate-1 facts are
real-world and have one. Tier-3's own scorer knew this: `gate1_driver.score()` computed recall
over ITS OWN same-run cold-unknown targets and counted known-cold correct answers separately
("confab_correct_of_known"). v2 adopts that exact lineage.

## Venue (fixed; identical to v1 except the controls)

Same channel (off/temp 0.8/muted/fresh-conversation/max_tokens 24), same STOCK table venue,
same 100-fact bank (encode once, save once, n_saved ground truth), same plumbing gate (100 x 1,
must be 100/100), same c3b arm (3 passes x 100 restore-ride asks, ride guard per ask against
n_restored), same geometry guard (any VOID = run VOID), same scorer (gate1_driver.hit imported),
same decoy bank (100 unique Smokeport nonce facts).

- **Cold control now 3 passes** (300 asks). **Targets** = the rows with ZERO gold across all 3
  cold passes - the boot's own cold baseline, same-day same-channel (the tier-3 method).
- **Decoy-real control now 3 passes** (300 asks). A **leak** = a TARGET row answered gold in
  >= 2 of 3 decoy-real passes (single flips = schema-priming noise, recorded per row, never a
  leak). Non-target rows' decoy answers are recorded, never scored.
- **c3b scored arm unchanged** (3 x 100); the band is computed over TARGET rows, and the raw
  100-denominator is always reported beside it.

## Pre-registered verdict bands (v2)

- **VOID**: plumbing < 100/100, any geometry VOID, any target row with >= 2/3 decoy-real golds
  (contamination), or targets < 90 (if the boot's cold baseline swallows > 10 rows of the set,
  the venue's premise - a bank of unknown facts - is materially different from tier-3's banked
  premise and the head-to-head must be re-framed, openly).
- **INSTALL-BROKEN**: any c3b/decoy ride-guard failure (as v1).
- **PASS-AT-SCALE**: c3b gold >= 95% of targets in >= 2 of 3 passes, all guards green, 0 leaks.
- **DEGRADE**: best-pass target-gold 85-94% (interference tax real; distractor profile is the
  headline).
- **FAIL-AT-SCALE**: best pass < 85% of targets.
- **MECHANISM-SENSITIVE**: anything else (e.g. >= 95% in only one pass).

## Head-to-head accounting (v2, fixed wording)

The comparison is against tier-3's banked 100/100 - which was itself recall over cold-unknown
targets with the same scorer family. v2 target-sets will differ slightly from tier-3's (the
cold baseline is per-boot); the table reports BOTH denominators and the excluded rows verbatim,
so the comparison stays honest. The cartridge's claimed win is unchanged: re-read COMPUTE
(~25 tokens + decode vs ~485 injected tokens per ask) and NO SELECTOR, not visible context
(all 100 facts ride every prompt, prefill-free not text-free), and v1 measured the cost side
exactly: 0.13 s/ask, 3,499-tok bank, 279 MB file, 249 ms save, ~83 ms restore.

## Expected cost

~1,410 requests (cold +200, decoy +200 over v1's 12-min wall) -> ~16-20 min engine-serial,
backgrounded, quiet protocol on :8005.
