# D2 key-shape dissection - NOTES.md (live work file, 2026-10-11 night)

STATUS: §4(a)+(b)+(c) DONE (render verified 283-tokens = strata-dev.log L1229; firing table
+ verdict in FIRING.md + state/firing_table.json, produced by fire_analysis.py, CPU-only).
VERDICT: dominant miss cause = WRITE POLICY, not lookup: 0/80, 0/48, 0/48 gold-value rows
were among the 616 (zip truncation, H1); 192/616 rows fire (marker-tail positions >= 2), the
tap windows at their own `?` ARE hit mid-line, but the 9-question stack + thinking block defer
answers ~300 tokens past every fire (H3). Next = §6 (v2 plan builder, offline-gated) below.
This file = the durable capture of everything learned so far (the exploring
subagent's full C++ map + the write-side policy + the channel corrections). If the session
compacts, resume FROM HERE; nothing else lives only in context.

## 0. Why this dissection exists
M1 (patched venue, 616 table rows written by D2) answered the 3 marker questions cold: idk ->
D2 verdict READOUT MISS (spikes/window/state/m1_verdict.txt). The §11 offline gate had passed
13-0 (probe-conditioned). Question: do the 616 written rows ever get QUERIED by the live M-block
prompts, and can their values REACH the answer positions? Both sides are now fully mapped (below).

## 1. ENGINE READ-PATH MECHANICS (mapped from src/ by subagent 2026-10-11, session
##    ses_ed4fd3d34ffetlC6y2GYrgM66y - all citations verified against that report)

Pseudo-code (row address = f(token window, head)); constants verified live from GGUF KVs +
ngram.cpp transcriptions (src/kernels/ngram.cpp:42-61, include/strata/kernels/ngram.hpp:31-50):

    ctx0 = token[p]                                   # position p, EVERY prompt + generated token
    ctx1 = token[p-1]; ctx2 = token[p-2]              # -1 or EOS(248044) cuts: slot AND all older -> EOS
    mixed2 = (ctx0*23703573157769 mod 2^64) XOR (ctx1*20109073645365 mod 2^64)
    mixed3 = mixed2 XOR (ctx2*8052911324071 mod 2^64)
    heads 0-7  (bigram band):   row[h] = mixed2 % V[h] + O[h]
    heads 8-15 (trigram band):  row[h] = mixed3 % V[h] + O[h]
    V = [20000003,20000023,20000033,20000047,20000059,20000063,20000069,20000077,
         20000081,20000093,20000107,20000147,20000153,20000159,20000161,20000171]
    O = head_vocab prefix sums, O[15]=300001275; chain ends 320001446 (90 dead rows before 320001536)
    row bytes: Q8_0 = 170 (this venue); file pos = 192 + row*170 (data_start=192, tensor offset 0)
    gathered emb[2560] = concat(dequant(row[0..15]), head-slowest)      # 16 rows x 160 values

Per-position: 16 lookups, host-side hash (prefill.cpp:2070-2082; decode layer.cpp:1454; spec
verify.cpp:1990+; MTP mtp.cpp:1300; windows NEVER see future tokens; session start = EOS-cut).
Downstream: PLE is LAYER 1 ONLY (ple.layers=[1], layer.cpp:1353). emb -> ple_key[2560,10240] ->
grouped-norm -> gate (4 HC streams, sigmoid sign-sqrt) ; emb -> ple_value[2560,2560] -> gated ;
normalized -> DILATED CONV: kern=4, dil=3, taps at rows {P, P-3, P-6, P-9} of the normalized
history (ple.cu:139-160, native_ple_postops.cu:155-164) -> out = hidden + gated + silu(conv),
in-place residual at layer 1. Reach to final-position logits: (a) rows at P itself, (b) normalized
values at tap offsets 3/6/9, (c) layer-1 residual carried forward (GDN recurrence + attention reads).
Python mirror (oracle-verified, selftest 19 vectors 0-differ): spikes/ple-overlay/ple_ngram.py
(ngram_rows16(t,p2,p1) L62-79, row_file_pos L130-131). A NEW window can only be queried at
positions AFTER its tokens appear: the engine queries every position's own window during
prefill/decode; a written row fires when a live position reproduces EXACTLY that (head, window).

Answer to "does the question get queried at the final position?": YES but the FINAL position is
the template tail (see §3), and mid-question marker windows fire WHILE READING the question.

## 2. WRITE-SIDE POLICY (spikes/window/build_d2_plan.py, read in full 2026-10-11)
For each pair: tokenize fact_text + each alias (marker_texts, 3 per pair) with the PRODUCTION
tokenizer (tools/strata_tokenizer.py ST.Tokenizer over packs/iq3_s/tokenizer: vocab.json/
merges.txt/token_type.json - same engine family as the server's); rows16 per position via the
oracle mirror; ZIP-CLONE fact-window[i] -> alias-window[i] for i in [0, min(len(alias),len(fact))),
SKIPPING positions whose alias token is a stopword (STOP list L33-35), all 16 heads ->
616 writes, first-write-wins on dst collisions. NOTE the zip semantics: alias position i receives
the FACT VALUE at fact position i (fact_rows[i]), NOT the name-emitting rows: aliases are ~4-7
tokens while facts are ~12-13, so fact positions holding "named -> Quendefin/Marsh" (indices ~7-10)
are NEVER written (truncated by min-length zip). Hypotheses to test (next section):
H1 zip-alignment miss: written values carry fact-FRAGMENT signal ("The/keeper/of/Voldranium..."),
   not the name-emitting window value -> even perfect firing shouldn't name the keeper.
H2 window-context mismatch at shallow alias positions (alias starts EOS-cut (-1,-1) but the live
   question has real predecessors) -> the first alias token's rows never fire; deep positions
   (predecessors fully inside the alias substring) CAN fire if tokenization boundaries match.
H3 tap-distance miss: even firing rows at question-mid positions reach final logits only via
   taps {P,P-3,-6,-9} or residual carry; with reasoning-block + template tails the distance is
   usually NOT a multiple of 3 from the answer position.
H4 collateral overwrite: every dst row is a hammered shared ngram (occupancy preflight said so) -
   170 B/row overwriting GLOBAL trained signal; expected side effects (not the miss cause, but a
   reason M1 answers could degrade elsewhere; no M-block side deltas were observed on 7-9).

## 3. CHANNEL FACTS (verified 2026-10-11 night; CORRECTIONS to earlier wording)
- webapp app.js DEFAULTS (L560): thinking:"high", temperature 0.6, top_p 0.95, top_k 20 ->
  every user-pasted measurement (M0/M1/G1, cold baselines) was answered at TEMP 0.6, NON-GREEDY,
  and the answer is generated AFTER a reasoning block (template resolves reasoning_effort,
  serve/chat_template.jinja L57-60, default xhigh branch; "think"/"thinking" markers exist in
  the template). CORRECTION (said openly): m1_verdict.txt/report.txt phrase "greedy webapp
  decode" was WRONG; the verdict itself is unaffected (conditions scored on the pasted replies
  as-is, same channel as the 0/100 cold baseline) but (a) replies are NOT reproducible-exact
  across re-runs, and (b) the answer position is shifted past the whole think block -> H3 gets
  WORSE than prompt-tail-only arithmetic. probe_driver /v1 asks DID use reasoning_effort:"off"
  + temperature 0 (scripted = greedy, no thinking) - the two channels differ in generation
  geometry; b1/a-check "no-ride" numbers (ptok 26/32) unaffected (prompt-side facts).
- G1 "Broken Bow" confab x42 at temp 0.6 is channel-consistent (non-greedy + no injected golds).

## 4. NEXT STEPS (all CPU-only, no engine, no swap):
 a) tokenize the EXACT M-block + G1 texts through the FULL webapp render (jinja2 +
    packs/iq3_s/tokenizer/chat_template.jinja with resolved thinking tokens) - reproduce
    positions; verify ST.Tokenizer handles specials per token_type.json (server-side template
    render is in serve/frontend.py - reuse if possible, do NOT hand-guess the tail tokens).
 b) walk rows16 over the live prompt; intersect (head,row) hits with the 616 plan dst_rows ->
    FIRING TABLE per marker pair: where (if at all) written rows fire, which fact value they
    carry (w["fact"], w["pos"]), and the offsets from every answer-generation position ->
    tap-hit classification (H1-H4 measured, not guessed).
 c) output state analysis here + verdict on the dominant miss cause; then v2 recipe options:
    (v2a) write the NAME-emitting fact windows onto alias-final windows (decouple zip: dst =
    alias-tail window, src = fact "named"-window); (v2b) place alias so its window lands at the
    FINAL tap position (probe-shaped: answer right after marker, no 9-question stack); (v2c)
    raise write volume (all windows of alias x16 + tap-context). Each v2 stays a raw 170 B row
    copy via mk_ple_copy --apply (fast, CPU); re-test needs a dev boot + pastes (user swap).
 d) honesty: re-measure nothing on the OLD copy claims - the current 54.4 GB copy is D2-v1; a
    v2 patch means re-copying rows INTO it (or a new copy dir) and re-verifying (616-row pattern
    will differ; update d2_plan.json provenance via mk_ple_copy apply).

STATUS 2026-10-11 late night: (a),(b),(c) DONE -> FIRING.md (firing table + measured H1-H4 +
verdict) + state/firing_table.json; (d) stands for whenever a v2 copy is actually patched.
The (c) verdict in one line: the 616-row write NEVER contained the gold (zip truncation cut
the name positions from every alias); rows that fire (192 keys, marker-tail positions >= 2 in
live-question context) carry question fragments; tap windows ARE hit 2-8 tokens behind each
cold `?` but answers are deferred ~300+ tokens by the 9-line stack + thinking block, so reach
to the answer positions is zero for lines 1-3.

## 6. WHAT TO DO NEXT (post-verdict, 2026-10-11 night) - v2 recipe spec, all offline before any swap:

1. build_d2_plan_v2.py (NOTES here, implement next): DECOUPLED src/dst (v2a) -
   src = fact windows of the GOLD positions (archivist 11-15, metalsmith 10-12, recipe 8-10,
   token maps in FIRING.md §4); dst = the LIVE-FIRING (head,row) keys measured in
   state/firing_table.json (the question-context windows at marker positions >= 2), NOT
   standalone-alias windows (alias positions 0/1 never fire; standalone alias tokenization can
   differ from in-question tokenization: `Voldranium` one token alone vs ` V`,`old`,`ran`,`ium`
   in the question).
2. Geometry (v2b, from FIRING.md §3): the single-question chat is the test venue - answer
   emitted 2-15 tokens after the marker tail = inside the {P,P-3,P-6,P-9} tap window of the
   written rows. The 9-question stack CANNOT reach written rows at all; do not re-test v2 in
   stack form. Probe-shaped ask: "... Voldranium-Archive-keeper?" alone in a fresh chat.
3. Optional extra writes (v2c): also clone gold windows onto the windows of the 9 tokens
   BEFORE the answer position directly (the question-tail + newline windows), so taps at the
   first ANSWER token itself carry gold. Cheap: <= 9x16 extra rows per alias occurrence.
4. OFFLINE GATES before touching mk_ple_copy (all CPU, no engine):
   g1 gold-presence: every gold src row is in writes[] (parse the new plan);
   g2 fire-coverage: re-run fire_analysis.py against the v2 plan -> fired-gold-keys > 0 for
       each pair, and the fired sites listed in FIRING.md §2 (87-93 etc.) carry fact_tok in
       the gold position sets;
   g3 collateral audit: count distinct dst rows whose KEYS are also keys of ordinary corpus
       n-grams (the hammered-shared problem; subword stopword forms like ` national`,` food`
       slip the STOP list - STOP stores standalone-word ids, fix by checking every subword
       form of each stop word, seen live on the Q6 control: 8 heads overwritten at pos 165).
5. Only then: mk_ple_copy --apply into a v2 copy + verify, dev boot, single-question pastes
   (user swap per §4c wording). Update §0 of the plan journal + backlog #1 with the verdict +
   v2 state when committing this.

## 5. SESSION TRIP NOTE (user-reported, real): this agent is served by the same model family;
##    dense template/token dumps in context triggered a mid-generation stray ("I need to
##    investigate this further." header line, 12:5xZ turn). Durable state is now THIS FILE +
##    git; user may compact or start a fresh session at the commit below with the §0 pointer.
