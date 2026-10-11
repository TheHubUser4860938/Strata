# C2 pre-registration — the tier-2 install-at-conversation-start verdict (written BEFORE any measurement)

Written 2026-10-11 evening, before any C1/C2 ask has run on any boot. Do not edit after the run;
amendments get their own dated paragraph (the D2-v2 / v2_run discipline).

## Question

Does the frozen Qwen3.8-Flash-Next model, on Strata 0.1.43 (k8v4 + MTP + YaRN 1.5 @ 393,216,
RTX 4090), answer invented-fact cues from the ACTIVATIONS of a prior read (the saved-conversation
cartridge), with the fact text present only as a REUSED prefix and the question as the only
re-read input? Two install arms separate recall from install mechanics:

- **C2a — recall alone:** the question continues the encode conversation in the live engine
  (no restore in the loop; rides the natural post-encode live state).
- **C2b — the product shape:** save at the encode ack tail, restore into a fresh continuation,
  send the identical message list + question (the MAM procedure inverted from our no-ride
  finding; ride mechanism per spikes/cartridge/C0_save_restore_code_map.md).

## Venue (fixed before measurement)

- Channel: `POST /v1/chat/completions`, `reasoning_effort:"off"`, **temperature 0.8** (user
  directive: never 0 on this build — temp 0 is not bit-stable across expert-cache states),
  `max_tokens: 24`, `stream:false`, **`X-Strata-Memory: off` on EVERY request** (encode turns
  included — the injected block would break token-exact prefix identity and VOID the ride guard).
- Data: the three D2-v2 synthetic pairs verbatim (markers.json + state/v2_build.json): facts
  "keeper of the Voldranium Archive = Quendefin Marsh" / "largest city of the Klornish Reach =
  Bralmwick-on-Sev" / "traditional Krevven dish = smoked gloamfish with ashbread"; cue-last asks
  (CUE_PREAMBLE + cue, marker is the final content token — the D2 geometry lesson).
- Encode: one fresh conversation per marker arm; the three facts as three user messages, each
  acked by the model (ack capped 16 tokens, replayed verbatim in every continuation); save after
  the third ack. 3 markers x 3 passes = 9 bank encodes, 9 C2a asks, 9 C2b asks.
- Controls per marker per pass: **cold** (question only, no bank — expect NO gold),
  **plumbing** (fact text + question in one prompt — expect gold; the read-path gate),
  **decoy-real** (decoy bank of 3 unrelated invented facts installed, real cue asked — expect NO
  real gold), **decoy-own** (decoy bank, decoy cue — recorded bonus, never scored).
- Table venue: the boot mounts the STOCK PLE table (the v2 copy's 488 written rows are readout-
  inert per the banked 0/15 verdict, but the cue windows fire them mid-read and C2 must not be
  confounded by tier-1 writes).

## Guards (per ask; any GEOMETRY failure anywhere VOIDs the run - the v2 precedent, no render
## match no verdict; a RIDE-guard failure is its own named clause, not a recall failure)

1. geometry: `usage.prompt_tokens` == the local `venue_render` render of the exact message list
   (the v2_run rule — no render match, no verdict).
2. ride (C2a/C2b/decoy asks): `timings.cache_n >= n_saved - 8` AND
   `timings.prompt_n <= (render - n_saved) + 8`, where n_saved is the engine's own count from the
   save (C2b/decoy: from the restore's `n_restored`). A C2b ask failing guard 2 = **install-path
   NO-GO evidence** (reported as its own verdict clause), not a recall failure.
3. C2b additionally captures the engine's `RESUME <n>` / `REUSED <n>` lines from strata-dev.log
   around the ask as corroborating evidence (absence is recorded, not VOID — the timings guard
   is primary).

## Pre-registered verdict bands

- Plumbing gate: **9/9** plumbing positives gold (any miss = this boot supports NO C2 verdict).
- Leak rule (amended-v2 lesson baked in from the start): a control (cold or decoy-real) leaks iff
  it contains ANY of the three bank gold substrings (Quendefin / Marsh / Bralmwick / gloamfish).
  Confabulation without a gold string is a channel property, never a leak.
- Arm PASS: gold >= 2/3 markers in >= 2 of 3 passes, guards green, 0 leaks across all controls.
- **PASS:** C2a or C2b arm PASS -> CARTRIDGE RECALL WORKS on Strata -> C3 (100-fact head-to-head).
- **READOUT MISS:** both arms 0 gold across all 3 passes, plumbing 9/9, ride guards green ->
  whole-state activation replay ALSO misses -> tier-2 needs MAM-style cut-layer surgery or the
  grafting tier; the lease is up for renegotiation, said plainly.
- **INSTALL-BROKEN:** C2a PASS + C2b guard-2 failures -> memory works, the restore path is the
  suspect (server bookkeeping); next step is C1-level ride debugging, not a memory verdict.
- **MECHANISM-SENSITIVE:** anything else -> recorded, stop; no venue-hunting without a new
  pre-registration.

## Scoring classes (every scored answer gets exactly one)

gold (own pair's expect substring) > distractor (ANOTHER pair's gold — MAM's documented failure
mode, scored explicitly) > idk ("idk" in answer — wins over cue-echo when both are present:
restating the cue inside a don't-know is not an echo) > cue-echo (cue word
Voldranium/Klornish/Krevven present as the answer, no gold) > confab (everything else). >= 3
passes is mandatory policy (temp 0.8
flips 3-8 answers per 100 between passes; single-shot verdicts are VOID by protocol).

## Instrumentation notes

- Save/restore responses carry the engine's ground truth (`n_saved`/`n_written`/`save_ms`,
  `n_restored`/`n_read`/`restore_ms`) — the attach cost is re-proved on THIS build by c1_plumb.py
  (Spike A's 155.7 ms is 0.1.42/yarn-2, and the file pins make that a different file class).
- C1 (plumbing re-prove, byte-exact round-trip, ride-through-server on a tiny conversation) runs
  before any C2 ask on the boot; a C1 failure blocks C2 entirely.
- One engine, one slot: the driver serializes everything through the server FIFO by construction
  (sequential requests); no concurrency assumptions.
