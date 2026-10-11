# D2 key-shape dissection - NOTES.md (live work file, 2026-10-11 night)

STATUS: v2 MEASURED on the live boot (2026-10-11 ~13:55Z): Venue A markers 0/15 across 5
greedy passes, controls 15/15 clean, plumbing 3/3 (Venue C) -> pre-registered verdict =
READOUT MISS IN THE GEOMETRICALLY-REACHABLE VENUE (details §8). User round of webapp pastes
(PASTE-V2.md APPENDIX B1-B6/C1/S1) in flight. Resume at §8 for results, §7 for the build.
§4(a)+(b)+(c) DONE (render verified 283-tokens = strata-dev.log L1229; firing table + verdict
in FIRING.md + state/firing_table.json, produced by fire_analysis.py, CPU-only).
VERDICT: dominant miss cause = WRITE POLICY, not lookup: 0/80, 0/48, 0/48 gold-value rows
were among the 616 (zip truncation, H1); 192/616 rows fire (marker-tail positions >= 2), the
tap windows at their own `?` ARE hit mid-line, but the 9-question stack + thinking block defer
answers ~300 tokens past every fire (H3). v2 = gold src x answer-REACHABLE dst (cue-last
venue), all gates green on CPU.
This file = the durable capture of everything learned so far (the exploring
subagent's full C++ map + the write-side policy + the channel corrections + the v2 build). If
the session compacts, resume FROM HERE (§7); nothing else lives only in context.

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

STATUS 2026-10-11 ~09:40Z (next morning): items 1-5 DONE except the dev boot - see §7 for the
built state. Two of the spec guesses above were WRONG and the corrections are what actually
shaped v2 (measured, state/v2_gates.json + state/venue_geometry.json):
  - item 2's "probe-shaped question alone in a fresh chat" still has ZERO reach: in the greedy
    channel the generation tail ('<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n')
    is 9 ids, and taps {P,P-3,P-6,P-9} only ever slide FORWARD from the position being decoded,
    so the first answer token's reachable prompt positions are n-3, n-6, n-9 = tail tokens; a
    marker at the START of the question (measured: 112 written keys fire while READING it) is
    never re-read while the answer is written (written_rows_reached_by_first_answer = 0 for all
    three pairs). Fix = CUE-LAST venue: the rare marker token must be the LAST content token.
  - item 3's "the 9 tokens before the answer position" are exactly those tail rows - they ARE
    shared global n-grams (('</think>','\n\n')...), so writing gold there is hammering; skipped
    deliberately (the collateral rule below would reject them).
  - item 1 confirmed; item 4's g3 detail: the STOP fix is NOT "every inflected form" (endswith
    over-matches: '-A' ends in 'a', 'ran' ends in 'an') - it is exact word-text comparison with
    the byte-BPE space marker stripped; AND punctuation-only ids are not marker content either
    (the gate caught a real control collision: writing onto the '-' window made the Thyravelle
    control read archivist gold in all 4 control venues).

## 7. v2 STATE (built, gated, copied, venue flipped; ONLY the dev boot is missing)

Artifacts (all committed; every render byte-matched to an engine-logged prompt size before use):
  venue_render.py       - the server's live render path as a module (pack tokenizer + pack
                          chat_template.jinja + effort_kwargs + mark_think_literals +
                          parse_special), reused by everything below.
  venue_geometry.py     - 5 venues verified against logged sizes: M-block 283 = dev-log L1229,
                          B1 webapp 66 = L1074, A1 webapp 72 = L1257, B1 /v1 greedy 26 =
                          b1.jsonl ptok, A1 /v1 greedy 32 = a-check.jsonl ptok. Tail geometry:
                          '\n\n' is ONE id; the generation tail is 7 ids (thinking high) /
                          9 ids (thinking off).
  d2_rules.py           - the ONE shared write-policy rule set: stopword-form (exact stripped
                          text) + marker-content (not a stopword AND has a letter) - so the
                          builder and its gates cannot drift.
  build_d2_plan_v2.py   - d2_plan_v2.json: 488 writes / 488 distinct dst rows, src = the
                          name-tail gold window row per head band (' Marsh' pos 15, 'wick' 12,
                          'fish' 10 - one row one value, v1's 616 fragment rows DROPPED), dst =
                          answer-reachable windows only: tap row (n-9) of the cue venue +
                          cue content windows + M-block content windows. Cues (chosen by rule,
                          recorded in state/v2_build.json): 'Voldranium-Archive custodian',
                          'Klornish-Reach metropolis', 'the dish Krevvens traditionally eat'.
  v2_gates.py           - state/v2_gates.json PASS (exit 0):
                          g1 gold present in all 16 head bands per pair + tap key per pair;
                          g2a/b cue venues fire 16/16 bands gold in BOTH channels, M-block
                          fires 256/192/144 lookups all carrying gold;
                          g2c the tap row IS the gold row in both channels (tap windows:
                          [' cust','rchive'] / ['<|im_end|>','odian'] etc. - all rare);
                          g3a 0 all-stopword / 0 content-less windows (136 "weak" = content
                          token + stopword partner, recorded);
                          g3b sidewalk: the 100 Gate-1 questions, B1 and A1 asks read ZERO of
                          the 488 rows; g3c all 4 control shapes (cue/ask x high/greedy) read
                          ZERO -> controls uncontaminated offline;
                          g3d no cross-pair row sharing.
  preflight/apply/verify on the REAL table: 488 positions resolve, 0 already hold the src
                          value (488 stock rows overwritten = the honest collateral number,
                          82 KB of a 54.4 GB table, and per g3b nothing else we measured reads
                          them); copy = Strata-data/ple-copies/q8-shard2-d2v2.gguf (54.4 GB,
                          created 09:35, verify 488/0 mismatch + 100 unwritten samples clean);
                          d2_plan_v2.json now carries dst_copy = that file (mk_ple_copy
                          stamps it, same as the v1 run).
  VENUE FLIPPED (config-only, zero C++): set_ple_venue --copy q8-shard2-d2v2.gguf;
  --native-dense-gguf ON (2 original shards). The v1 copy q8-shard2-d2.gguf stays untouched on
  disk (inert). strata-dev.json is gitignored-ish (local config; .stockbak kept).

THE TEST (PASTE-V2.md - pre-registered BEFORE any measurement, do not edit after the boot):
  Venue A (authoritative, scripted by the agent, no user paste): /v1 greedy reasoning-off,
  temperature 0, ONE fresh conversation per cue - 3 marker cues + 3 control cues + 1 ordinary
  English sanity. Verdict: markers >= 2/3 AND controls 0/3 = MARKERS LAND; markers 0/3 = the
  residual-carry route is dead too (then PLE writes need a different mechanism, not a better
  venue); any control PASS = VOID. Venue B (webapp per-cue pastes) and C (fact-in-prompt
  positive control) are user pastes; C is the plumbing discriminator, B is channel realism.
  VENUE A RUNNER = v2_run.py (--url http://127.0.0.1:8005, --dry-run verified 09:5xZ): per ask
  a FRESH conversation, temperature 0, reasoning_effort "off", max_tokens 24; marker tap rows
  are the gated ['<|im_end|>','odian'] / ['ropolis'] / [' eat'] windows, expect_ptok 54/53/53,
  and the controls end on the UNWRITTEN ('<|im_end|>','keeper'/' city'/' food') windows (56/55/
  50 tok) - a control PASS can only mean a leak. The run refuses to score unless the live
  usage.prompt_tokens EQUALS the locally rendered size, i.e. unless the engine really read the
  prompt whose tap row we wrote.
  STILL OPEN: dev boot (USER SWAP - one command in this repo: ./dev-up.sh --force, which stops
  main :8004 and starts dev :8005 on the v2 copy; then move the agent's provider to :8005).
  Everything CPU-side is done and verified: plan, gates, copy, verify, venue flip.

## 8. VENUE RESULTS ON THE v2 BOOT (2026-10-11 ~13:43-13:50Z, dev :8005, table = q8-shard2-d2v2.gguf)

Boot evidence: dev-log L1363 engine started with --ple-gguf .../q8-shard2-d2v2.gguf and the two
--native-dense-gguf originals; L1373 "PLE on, table 320001536 rows of .../q8-shard2-d2v2.gguf".

TOOLING GOTCHA (durable, cost one VOID run): this boot carries the memory-eval store (100
facts, inject on), so a plain /v1 ask gets ~310 tokens of recalled facts PREPENDED - Venue A's
first pass measured ptok 361-367 against 50-56 rendered and the geometry guard VOIDed it
(exactly what the guard is for). Fix: send `X-Strata-Memory: off` (mutes injection AND
journaling, serve/memory.py request_state L344). Any future /v1 probe on a memory-on boot must
use it or expect the shift.

  Venue A (PRE-REGISTERED, authoritative; greedy, reasoning off, temp 0, memory muted, one
  fresh conversation per ask; every ptok matched the local render 54/56/53/55/53/50/50):
    markers  d2-archivist 'idk' | d2-metalsmith 'idk' | d2-recipe 'idk'   -> 0/3
    controls all 'idk' (want idk)                                        -> 3/3 clean
    sanity   'Glasgow-University principal' -> 'Sir William Ramsay' (ordinary bigram rows
             still behave - the venue did not damage normal answering)
    VERDICT per the pre-registered bands: **READOUT MISS AGAIN in the geometrically-reachable
    venue** - the tap row IS the gold row (gated), the engine opened the file that holds it
    (verified pre-boot, 488/0 mismatch), and the answer position reads it (ptok proof) - the
    written value simply does not surface as a cold name. Raw trained-row copies are not a
    reflex mechanism at 16-rows-per-cue strength.
    STABILITY BRACKET (5 passes of the same muted venue, 13:47-13:55Z): markers 0/15 asks ever
    produced a gold token (archivist 'idk' 5/5, recipe 'idk' 5/5, metalsmith 'idk' 3/5 +
    'Kronos' 2/5 - a cue-word confab, not the gold); controls 'idk' 15/15 (zero contamination).
    The negative does not depend on a lucky draw.
    TOOLING FACT (same evidence): temp 0 is NOT bit-stable on this build across cache states -
    the ordinary-English sanity ask answered 'Sir William Ramsay' once and 'idk' in every pass
    since, minutes apart on ONE boot. Cause is documented by the engine itself at startup: the
    GPU computes cached experts with different rounding than the CPU and the prefill CPU share
    is on by default (dev-log banner + docs/MEMORY_CORE.md), so near-tie argmaxes flip as the
    expert cache changes. ANY future marker verdict must repeat (>=3 passes), not single-shot.
  Venue C (plumbing, same channel): 3/3 positives exact ('Quendefin Marsh', 'Bralmwick-on-Sev',
    'smoked gloamfish with ashbread') with geometry OK -> the read path (question->lookup->
    answer) works on this boot, so A's miss is a MEMORY miss, not plumbing (the M1
    discriminator, reproduced at lower cost).
  Venue HIGH (webapp sampling params, memory muted): 4/6 TRUNCATED inside the thinking block at
    max_tokens 8192 (reasoning_len ~2900 chars at 900 - terse cues make it think a LOT); the one
    marker that answered confabulated ('Trazyn the Infinite'). Verdict: INCONCLUSIVE by
    truncation, and no sign of the gold. The thinking channel is expensive and adds nothing -
    keep future marker tests in the greedy channel.
  Injection audit for the webapp pastes (state/v2_webapp_injection.json): the store recalls 8
    irrelevant Gate-1 web facts for 3 of the 6 cues; and 0 hits store-wide for
    Quendefin/Bralmwick/gloamfish/Voldranium/Klornish/Krevven -> a webapp PASS can only come
    from the TABLE. Webapp pastes = PASTE-V2.md APPENDIX blocks B1-B6, C1, S1 (user round,
    in flight).

  WHAT THIS CONSTRAINS NEXT (analysis to write once the pastes land): the surviving mechanism
  hypotheses are (H5) ROW POWER: one trained 170 B row per head band is too weak a bump to
  flip generation - test by writing a row whose value is KNOWN-strong (e.g. copy the row of an
  ordinary high-continuation bigram onto a fresh rare cue and measure an answer shift), and
  (H6) TARGET MISMATCH: the row value is a context-embedding, not a next-token direction, so
  the correct write may need the row of the window ONE STEP BEFORE the name (the emit window)
  plus many cue variants (volume). Both are CPU-proposable, both need a new copy + boot (user
  swap) to test. NOT open: the venue/key-shape story - v2 closed it with geometry proof.

## 5. SESSION TRIP NOTE (user-reported, real): this agent is served by the same model family;
##    dense template/token dumps in context triggered a mid-generation stray ("I need to
##    investigate this further." header line, 12:5xZ turn). Durable state is now THIS FILE +
##    git; user may compact or start a fresh session at the commit below with the §0 pointer.
