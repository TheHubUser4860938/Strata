# PASTE-V2.md - the v2 cue-channel manual (D2v2, patched copy `q8-shard2-d2v2.gguf`)

WHY THIS SHAPE (measured, spikes/d2_dissect/FIRING.md §6 + state/v2_gates.json): the layer-1
PLE conv reads taps {P, P-3, P-6, P-9} only, and in the greedy channel the generation tail is
9 tokens long - so a marker placed at the START of a question is never re-read while the answer
is written (measured: 0 of the M-block's reachable positions). The cue venues below put the
RARE marker token adjacent to the tail, which is where the written rows actually fire:

    d2-archivist  cue "Voldranium-Archive custodian"  tap window [' cust','rchive'] / ['<|im_end|>','odian']
    d2-metalsmith cue "Klornish-Reach metropolis"     tap window [' met','Reach']  / ['<|im_end|>','ropolis']
    d2-recipe     cue "the dish Krevvens traditionally eat"  tap window [' traditionally','vens'] / ['<|im_end|>',' eat']

Each cue is the alias whose LAST token is a rare marker-content id (d2_rules.py); the gold
value written there is the fact's name-tail row (' Marsh' / 'wick' / 'fish' window rows).

## Venue A - scripted, greedy, no thinking (run by the agent, no paste needed)
`/v1/chat/completions`, temperature 0, reasoning_effort "off", ONE fresh conversation per ask
(the channel the b1/a-check runs used; prompt sizes verified against these renders):

  1. Cue preamble + archivist cue      -> PASS = reply contains "Quendefin"/"Marsh"
  2. Cue preamble + metalsmith cue     -> PASS = reply contains "Bralmwick"
  3. Cue preamble + recipe cue         -> PASS = reply contains "gloamfish"
  4. Cue preamble + control cue 1      -> want idk  (the Thyravelle-Codex-keeper)
  5. Cue preamble + control cue 2      -> want idk  (Klornish-Reach? no: the Opuvvian expanse main city)
  6. Cue preamble + control cue 3      -> want idk  (Wendfallen national food)
  7. Cue preamble + non-marker English control ("Glasgow-University principal") -> want a real name or idk,
     recorded, NOT scored (sanity that ordinary bigrams still behave)

  Pre-registered verdict: 1-3 >= 2/3 AND 4-6 all idk = "D2v2 MARKERS LAND" (a real write->read
  reflex). 1-3 = 0/3 = readout miss again (then the residual-carry route is dead and PLE writes
  need a different mechanism). 4-6 any PASS = control contamination -> verdict VOID.

## Venue B - webapp paste (user), thinking high, fresh chat
One message, verbatim:

```
Name the person, place or thing each cue line is about. One line: the name only, or idk. No
sentences, no explanations.

Cue: Voldranium-Archive custodian
Cue: Klornish-Reach metropolis
Cue: the dish Krevvens traditionally eat
Cue: the Thyravelle-Codex-keeper
Cue: the Opuvvian expanse main city
Cue: Wendfallen national food
```

  Expect lines 1-3 = the three golds, lines 4-6 = idk. NOTE (measured, v2_gates.json): in the
  thinking-high channel only the FIRST cue line's window is inside the answer's tap reach - the
  stack defers lines 2-6 exactly like the M-block did - so lines 1-3 in a single stacked paste
  are NOT expected to all land; the authoritative run is Venue A (one fresh conversation per
  cue) plus per-cue single-line pastes:

```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: Voldranium-Archive custodian
```
  (and the same two-line form for each of the other five cues, each in its OWN fresh chat)

## Venue C - positive control (proves the read path, same session)
Ask in a fresh chat, fact given in the prompt:
```
The keeper of the Voldranium Archive is named Quendefin Marsh. Given only that, answer:
Cue: Voldranium-Archive custodian
```
  Expect "Quendefin Marsh". If this fails on the patched boot, the venue is broken (not the
  memory) - same discriminator as M1's items 7-9.

## Telemetry brackets (agent, before/after each venue)
`python3 spikes/window/gate1_driver.py telemetry --tag V2-<venue>-pre|post` (the window-run
tool; dev store is irrelevant here - markers ride the TABLE, not memory.db). Keep every reply
verbatim in spikes/d2_dissect/state/v2_answers.txt.
