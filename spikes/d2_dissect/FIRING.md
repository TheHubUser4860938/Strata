# FIRING.md — §4(b) firing table + §4(c) analysis (2026-10-11 night, D2 dissection)

Machine data: `state/firing_table.json` (written by `fire_analysis.py`, rerunnable, CPU-only).
Hypotheses H1-H4 are defined in NOTES.md §2; the read-path mechanics they test are NOTES.md §1.

## 1. Render verification (§4(a)) — the live prompt reproduced EXACTLY

- M-block (PASTE.md fenced block, verbatim incl. wrap newlines) rendered through the SERVER's
  own path: `serve.frontend.ChatTemplate(packs/iq3_s/tokenizer/chat_template.jinja)` +
  `effort_kwargs("high")` -> `reasoning_effort="xhigh"` (webapp default thinking:high) +
  `mark_think_literals` (no-op: the block quotes no literals) + pack tokenizer
  `encode(parse_special=True)` — same construction as `serve/server.py` L6149-6157.
  No system block (fresh chat, memories=0 per telemetry) -> template emits only the user turn
  + generation tail. `effort_position` unset in strata-dev.json -> `Service.effort_end` False
  -> plain template tail (verified by token count, below).
- **283 tokens — byte-match with `strata-dev.log` L1229** ("prompt 283 tokens = 0 reused +
  283 read", boot 2's first webapp request = the M1 paste). Ground truth confirms the render.
- Tail ids decode `'?<|im_start|>assistant\n<think>\n'` (last ids are the tokens `?`,
  `<|im_end|>`, `\n`, `<|im_start|>`, `assistant`, `\n`, `<think>`, `\n`). Position 282
  (0-based, `<think>`-turn `\n`) == the first decode position == THINKING START, ~300
  generated tokens before any answer line (M1 reply: 313 generated).
- G1 approx render (preamble + 3 chunks as consecutive user messages, assistant replies
  dropped — geometry approximate, stated): 1,789 tokens; **0 hits of 504 / 0 of 616 keys**:
  the 100 cold web-fact questions never reproduce any written marker window. D2 rows were
  never consulted anywhere in the G1 conversation either.

## 2. Firing table (§4(b))

Over the 283-position live walk (4,528 = 283x16 lookups), intersected with the 616 written
(head,row) keys:

- **504 lookups hit; 192 of 616 distinct written rows (31%) EVER FIRE.** 424 written rows are
  dead keys for this prompt (window/boundary mismatches, incl. all of aliases 2 for each pair).
- Per pair (fired keys / planned | fired prompt positions):
  - d2-archivist 104/240 | Q1 87-93 (all 7 content positions of the split spelling),
    Q7 174-177 (the ` V`,`old`,`ran`,`ium` of "Voldranium Archive" in the GIVEN fact re-hits
    the fact's own trained-context rows written from alias positions 2-4) + Q7 196-202
    (the marker repeat inside Q7's question)
  - d2-metalsmith 40/216 | Q2 107-109, Q8 214-216 (the `Klornish Reach` of the GIVEN fact
    re-reads fact windows 5-8 = `Kl`,`orn`,`ish`,` Reach` - those rows were NEVER WRITTEN
    (zip cut at 6); the fires there carry fact windows 2-4 = ` city`,` of`,` the`) and
    239-241 (the marker repeat inside Q8's question)
  - d2-recipe 48/160 | Q3 118-121, Q6 (CONTROL) 165 (8 keys, see §5), Q9 249-250 + 271-274
- Fired VALUES (fact token each hit carries): archivist `of`,` the`,` V`,`old`,`ran`,`ium`;
  metalsmith ` city`,` of`,` the`; recipe ` K`,`rev`,`ven`. **Zero gold-value tokens.**
  The pattern: written values are fact windows of positions 2-8 - fact CONTEXT fragments
  plus (archivist only) first entity pieces, sitting 3-4 windows BEFORE the name-emitting
  window (Quendefin starts at fact pos 11). Alias position 0 ("the") and 1 ("Voldranium"
  single-token spelling) never fire - the
  question's token boundary differs (` the`,` V` split) — H2 confirmed for shallow positions.
- Tokenization luck is visible: "Krevven national food" tokenizes IDENTICALLY inside Q3
  (`K`,`rev`,`ven`,` national`,` food`) so all its rows fire; "Voldranium-Archive" as a
  STANDALONE alias is `Voldranium`-one-token but in the question it splits ` V`,`old`,`ran`,`ium`
  — only aliases 1/3 positions >= 2 match the live boundary; alias 2 never matched.

## 3. Reach to answers (tap classification)

- Tap set from NOTES.md §1: normalized-history taps at {P, P-3, P-6, P-9} at layer 1.
- Hits vs thinking-start (pos 282): 16 tap-hits, ALL from Q9 (pos 273, dist 9) — Q9 is the
  LAST line, its marker windows sit 9-10 tokens before the generation tail. Cold lines 1-3
  fire at distances 161-195 from thinking-start -> NO tap reach; their own answer tokens are
  emitted 300+ positions further (after the whole thinking block) -> reach by taps: zero.
- Mid-line geometry (what the offline gate exploited): Q1's marker runs positions 87-95
  (` V`,`old`,`ran`,`ium`,`-A`,`rchive`,`-`,`keeper`,`?`) and the WRITTEN-key fires land on
  87-93. Thinking from the `?` at 95: its tap offsets {P-3,P-6,P-9} = 92, 89, 86 -> taps
  89/92/93-region all carry written rows; at the `keeper` token (94) the taps are 91/88(+85),
  also fires. Had the model answered right after each `?`, written rows WERE tap-positioned to
  influence it. The 9-question stack defers all answers past the think block (~300+ decode
  positions) -> the deferral, not the key shape at marker tails, breaks the reach for lines 1-3.

## 4. Gold coverage (H1 measured)

fact-tokenization (pack tokenizer): archivist 17 tokens, gold ` Qu`,`end`,`ef`,`in`,` Marsh` at
positions 11-15; metalsmith 17 tokens, gold ` Br`,`alm`,`wick` at 10-12 (expect `Bralmwick`);
recipe 15 tokens, gold ` glo`,`am`,`fish` at 8-10 (expect `gloamfish`).

- zip write coverage = min(len(alias), len(fact)) per alias: archivist aliases 9/9/8 -> max
  written fact position 8; metalsmith 7/7 -> 6; recipe 5/7 -> 4.
- **Gold-containing fact windows written: 0/80, 0/48, 0/48 rows. The gold NEVER entered the
  table.** (build_d2_plan writes src rows of fact positions 0..zip_len-1 only; NOTES §2 H1
  was directionally right but the truncation is worse than "indices ~7-10 never written":
  for metalsmith/recipe even the ENTITY-fragment positions are cut.)
- The values that DID get written are exactly the question-side fragments the model already
  reads 1 token later (it re-encounters `V`,`old`,`ran`,`ium` in the question itself) — the
  overlay is close to a self-copy of the question's own trained context.

## 5. Collateral (H4)

- 38 of 283 prompt positions have >= 1 of their 16 lookups land on a D2-overwritten row
  (~500 of the request's 4,528 lookups; 37 positions are the firing sites themselves).
- **Real collateral finding, separate from firing: control Q6's tail tokens ` national`+` food`
  hit 8 written keys (heads 0-7) at position 165.** Those rows were written from alias
  "Krevven national food" positions 3-4 - whose tokens are the SUBWORD forms ` national`,
  ` food`, which the stopword-skip MISSED (STOP stores standalone-word ids; "national"/"food"
  encode differently standalone than as subwords). So the trained bigram heads for ` national
  food` were overwritten with fact-fragment values for every prompt reading that phrase, the
  control included. Behaviorally invisible in M1 (Q6 answered idk in both M0 and M1 - the
  control never had the answer anyway), but it is the hammered-shared-ngram effect NOTES §2-H4
  predicted, now with a concrete measured instance (1 site, 8 heads, on the M-block). Same
  gap touches ` city`: STOP lists standalone "city" (a stop id) but the alias token is the
  subword ` city` - written unskipped as a dst marker value in metalsmith alias 1.
- Controls Q4/Q5: ZERO written-key reads (their alias-shaped text never reproduces a written
  window) -> M1's control FAILs stay clean negatives.
- M0->M1 replies were identical on lines 4-6 -> consistent with collateral being small in this
  prompt (measured: bounded, as above).

## 6. §4(c) verdict — dominant miss cause, measured

D2 = READOUT MISS is fully explained WITHOUT any lookup/magazine mystery, by two stacked
write-side/geometry failures:

1. **PRIMARY: the write policy truncated the payload before the gold (H1).** Zero gold-value
   windows were among the 616 rows; every fired row carried a question fragment. Even 100%
   firing + 100% reach could not have named the keeper.
2. **SECONDARY: tap reach to the deferred answer positions is zero for the cold lines (H3,
   amplified by the batch-paste + thinking geometry).** The fires sit tap-positioned behind
   their own `?` (offsets 3/6/9), but the 9-question stack defers every answer ~300+ tokens
   to the end (past the think block); only the LAST line's fires tap into the first decode
   position, and the gold is missing there anyway (cause 1).
- H2 CONFIRMED at shallow alias positions (0/1 never fire; boundary mismatch), moot vs cause 1.
- H4 CONFIRMED bounded: 38 positions touched, controls uncontaminated; not a miss cause.
- Why the §11 offline gate passed 13-0 and misled: the probe asked ONE question with the
  answer immediately after the marker, in probe-conditioned form — the fires were inside the
  tap window; and its "values" were judged by the DELIM discriminator, not by gold-token
  presence. The live M-block broke both assumptions (stacking, deferral, and no gold written).

## 7. Implications for the v2 recipes (refines NOTES §4c)

- v2a (decouple the zip: dst = alias-tail windows, src = name-emitting fact windows) is
  NECESSARY — it is the only way gold rows enter the table at all. dst keys must be taken from
  the LIVE-CONTEXT windows (the fired (head,row) set above), not from standalone-alias windows
  — the alias's own shallow positions (0/1) never fire, and its tokenization can differ from
  the in-question tokenization (the `Voldranium` one-token vs ` V`,`old`,`ran`,`ium` split).
- v2b (single-question chat, answer right after the marker) is NECESSARY for tap reach:
  Q1's firing windows were exactly {3,6,9}-behind-`?` positioned; keep the answer immediate
  (no stack). Optionally also write onto the windows of the 9 tokens BEFORE the answer
  position directly (tap positions at decode), keyed on live question-tail context.
- v2c (volume) is the cheapest supplement, but volume without gold rows (v1) is volume of
  question fragments — order matters: v2a first, then v2b geometry, v2c to add tap-context.
- One structural note: dst rows keyed on the rare CONTENT positions (marker_pos >= 2) are the
  only ones proven to fire; prefer those, and expect ~31%-of-alias-window waste if you write
  whole standalone aliases.
