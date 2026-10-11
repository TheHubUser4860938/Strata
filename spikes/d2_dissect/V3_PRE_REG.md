# V3 pre-registration — the tier-1 steer probe + emit-window verdict (written BEFORE the v3 boot)

Written 2026-10-11 night, plan + gates + copy built offline (d2_plan_v3.json: 496 writes, 496
distinct rows, g1/g2/g3 green, preflight 0-already-equal / 496 stock rows overwritten). Bands are
fixed now; after the run they are history - amendments get dated paragraphs, never silent edits.

## What is written (one fresh copy from the stock shard: q8-shard2-d2v3.gguf; v1/v2 copies inert)

- **Arm A - STEER PROBE** (the H5/H7 decider): the trained window value of "Happy New" (tokens
  Happy /' New'/ ' Year'; src = the row read at ' New', the row whose trained job is to predict
  ' Year') copied onto the tap + content rows (16 bands x 2 venues, 144 rows) of the fresh rare
  cue "Fenmorrow-Harbor almanac". Two never-written rare cues (Draylemere-Ferry charter,
  Ossulgate-Kiln inventory) ride as controls. The cue cold-baselined on the current (v2-venue)
  boot - v3_gates proved these cues read zero v2-written rows, so the baseline is stock behavior.
- **Arm B - EMIT-WINDOW RECIPE** (the H6 single-variable test): v2's exact marker venues (same
  cues, tap windows byte-identical per v3_gates: ['<|im_end|>','odian'] etc.), but the TAP rows'
  source moves from the name-tail row (v2's src, measured 0/15) to the window ONE STEP BEFORE
  the name emit: 'named' (archivist pos 10), 'is' (metalsmith 9), 'smoked' (recipe 7) -
  re-derived independently by the gate. Content rows keep v2's name-tail source (residual route,
  comparable line-for-line with the banked v2 numbers).

## Venue (fixed)

/v1 chat completions, reasoning_effort "off", **temperature 0.8** (protocol: never 0 - not
bit-stable across expert-cache states), max_tokens 24, **X-Strata-Memory: off** on every ask,
one fresh conversation per ask, cue-LAST. Geometry guard per ask: usage.prompt_tokens must equal
the local venue_render render (v2 rule) or the ask is VOID. 3 passes over the whole set.
Table under test: q8-shard2-d2v3.gguf mounted at boot (engine log line "PLE on, table ... d2v3"
is the plumbing evidence, captured before scoring).

## Scoring

- Steer ask: HIT iff 'year' appears as a word (regex (?<!\w)year) - the nonce-free continuation
  has a world-knowledge path, so the CONTROLS are the defense: a control cue containing 'year'
  anywhere = leak = VOID.
- Marker ask: gold = own pair's expect substring (Quendefin/Marsh, Bralmwick, gloamfish);
  classes per v2_run (gold > distractor > idk > confab); control leak = any bank gold string in
  a control answer (amended-v2 rule: plain confab is a channel property, not a leak).
- Plumbing gate: the three fact-in-prompt positives, 3 passes, must be 9/9 gold or the boot
  supports NO verdict at all (read path broken).
- Sanity (Glasgow-University principal): recorded, never scored (collateral tripwire).

## Pre-registered verdict matrix

- Plumbing < 9/9 -> **VOID** (boot broken, re-run after fixing plumbing, not a memory result).
- Any control leak -> **VOID** (collateral reached the controls; re-venue or re-cue, openly).
- **STEERS**: target 'year' in >= 2 of 3 passes, controls 0/6 -> row-copy CAN steer output at
  this dosage: H5 (row power) is DEAD as an explanation; the v1/v2 failures were payload/target
  problems. Arm B's result then says whether the FIX is the emit-pre target:
  - STEERS + markers >= 2/3 in >= 2 passes -> **TIER-1 ALIVE via the emit-window recipe** (H6
    confirmed): design v4 around emit-pre srcs and a bigger panel.
  - STEERS + markers 0/9 -> **TARGET-SENSITIVE**: steering is real but this association's target
    is still wrong (H6 partially dead): one more offline src-candidate round (name-run rows,
    wider tap context) before any new copy.
- **INERT**: target 0/3 AND controls 0/6 AND markers 0/9 -> table-row writes are inert as a
  readout mechanism at this dosage on this architecture (H7): tier-1 closes, the cartridge tier
  owns numerical memory (raises the stakes on C2).
- Anything else -> **MECHANISM-SENSITIVE**: recorded verbatim, stop, no venue-hunting without a
  new pre-registration.

## Cold baseline (banked at build time, current boot, rows provably stock for these cues)

target + 2 control cues, same channel, 3 passes each: expect zero 'year' everywhere (a cold
'year' anywhere would sink the probe's design - recorded before the v3 boot, not after).
