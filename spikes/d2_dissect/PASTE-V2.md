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

## APPENDIX (2026-10-11 13:5xZ, after the v2 boot) - the exact pastes for THIS round

Scripted results already banked (state/v2_run.jsonl, verdict files): Venue A markers 0/3 +
controls 3/3 idk + geometry all-OK = the pre-registered READOUT MISS in the reachable venue;
Venue C 3/3 positives = the read path works, so the miss is a memory miss, not plumbing;
HIGH channel (memory-muted, webapp sampling params) 4/6 truncated inside the thinking block -
the webapp pastes below will be SLOW (a minute or two each) for the same reason.
Injection audit (state/v2_webapp_injection.json): the webapp prepends 8 recalled Gate-1 facts
for 3 of the 6 cues, and the store contains ZERO hits for Quendefin/Bralmwick/gloamfish/
Voldranium/Klornish/Krevven - so a PASS below can only come from the table, never from memory.

ONE FRESH CHAT PER BLOCK (http://127.0.0.1:8005), paste the fenced text as ONE message, paste
the reply back verbatim. B1-B3 = markers, B4-B6 = controls (want idk), C1 = plumbing,
S1 = the old M-block stack on the v2 copy (comparison against M1).

B1:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: Voldranium-Archive custodian
```
B2:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: Klornish-Reach metropolis
```
B3:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: the dish Krevvens traditionally eat
```
B4:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: the Thyravelle-Codex-keeper
```
B5:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: the Opuvvian expanse main city
```
B6:
```
Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: Wendfallen national food
```
C1 (plumbing, expect the gold name):
```
The keeper of the Voldranium Archive is named Quendefin Marsh. Given only that, answer.

Name the person, place or thing this cue is about. One line: the name only, or idk.

Cue: Voldranium-Archive custodian
```
S1 (the window-run M block again, on the v2 copy - expect 1-6 idk, 7-9 exact, same as M1):
```
Answer each question on one line, 'number. answer', five words maximum. If you do not know,
write idk. No sentences, no explanations, no guesses.

1. Who is the Voldranium-Archive-keeper?
2. What is the largest town of the Klornish Reach?
3. What is the Krevven national food?
4. Who is the Thyravelle-Codex-keeper?
5. What is the biggest city of the Opuvvian expanse?
6. What is the Wendfallen national food?
7. The keeper of the Voldranium Archive is named Quendefin Marsh. Given only that, answer: Who is the Voldranium-Archive-keeper?
8. The largest city of the Klornish Reach is Bralmwick-on-Sev. Given only that, answer: What is the largest town of the Klornish Reach?
9. The traditional Krevven dish is smoked gloamfish with ashbread. Given only that, answer: What is the Krevven national food?
```
