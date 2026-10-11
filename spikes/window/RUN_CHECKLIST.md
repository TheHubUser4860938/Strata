# RUN_CHECKLIST.md - window + Gate-1 master checklist (single source while the run is live)

Cross-session protocol: on boot of ANY new session, read plan §0 (`~/.opencode/plan/
strata-memory-core.md`), then this file top-to-bottom, then continue at the FIRST unchecked
box. Tick boxes only with the evidence path shown. PASTE.md holds the exact user paste blocks.
Actor legend: [YOU]=human action, [ME]=agent, [PASTE]=user feeds a PASTE.md block to a webapp
chat and returns the model's reply to the agent verbatim.

## Measurement doctrine (agreed 2026-10-11, do not drift)
- All measurement questions go through USER PASTES into webapp chats (same channel as the cold
  baselines) - this OpenCode session never "asks" a question itself: its context carries the
  golds. probe_driver scripted /v1 fresh-conversations stay as OFFICIAL plumbing records for
  b1/a-check (raw /v1 is structurally a clean fresh conversation; the human paste for those
  two is an observation, recorded but not the verdict source).
- Production :8004 = MAIN build (~/Strata, strata-iq3_s.json): verified 2026-10-11 it has NO
  /memory route and NO memory block -> main is memory-less; all text-memory measurement happens
  on the dev fork instance :8005 with scratch store Strata-data/memory-eval (flipped today;
  production memory.db is structurally untouchable this run).
- Telemetry brackets each paste block (gate1_driver.py telemetry --tag <id>-pre/post);
  informational only - the pre-registered conditions below are the verdicts.

## Pre-registered pass conditions (fixed BEFORE any measurement)
- M0 (stock boot): items 1-6 FAIL/idk, items 7-9 PASS. Any PASS on 1-6 = venue leak -> STOP.
- M1 (patched boot): 1-3 PASS AND 4-6 FAIL AND 7-9 PASS -> D2 verdict "markers land";
  1-3 FAIL -> D2 verdict "readout miss" (Q8 venue removes the quantization excuse). Controls
  PASS anywhere = contamination -> verdict VOID, not a win.
- b1 scripted: pre-restore fresh-ask FAIL + post-restore fresh-ask PASS + dissect sane.
- a-check scripted: post-restart fresh-ask PASS (cross-restart restore on k8v4+MTP+YaRN =
  Gate 2 evidence) - note plan says Gate 2 = slot round-trip on this config: a-check IS it.
- Gate 1: G1 correct >= 25/100 (cold = 0/100 by selection construction), injected block
  <= 1.5% of 524288 tokens (= 7864 tok; config cap max_block_chars 6000 ~= 1.5-2K tok, pass
  by design - record smoke ptok as the empirical number). Per-kind + per-harness splits are
  descriptive, not conditions.
- M2 (D3, optional): 1-3 PASS on the STOCK engine binary with the patched table -> knowledge
  is in the table. 1-3 FAIL while M1 passed -> fork-code effect, downgrade D2 claim.

## Phase 0 - prep (main :8004 stays up)
- [x] PII content fix `54bf292c`; eval 100 + audit `4a030a16`+`fa5dfea2`; ls-remote verified
- [x] 54.4 GB patched copy EXISTS + fresh verify (616 written rows 0 mismatch, 100/100
      unwritten samples identical) - spikes/window/state/copy_patch.log
- [x] strata-dev.json: memory.dir -> Strata-data/memory-eval; venue = STOCK (--show);
      stockbak written by set_ple_venue (contains the scratch-dir config, good)
- [x] CSV scope column fixed default -> user (service constant is "user"; search always
      includes user scope - checked serve/memory.py) ; header comment updated
- [x] gate1_driver.py written; scorer selftest 19/19 + parse test pass (/tmp run)
- [x] discovery: main build = memory-less (404 + no config block) -> smoke moved to dev boot
- [x] smoke on dev :8005 (first act after boot 1 - see Phase 1 box 2) — done 2026-10-11
      11:41Z, evidence spikes/window/state/smoke_boot1.log (run 1: 3 taught / 3 hits / recall
      chat PASS / 0 left after forgets), commit 09f1845e

## Phase 1 - boot 1 (stock dev)
- [x] [YOU] stop main (its console Ctrl-C), boot dev: `cd ~/Projects/Strata-MAM-cartrige-mod
      && ./dev-up.sh` ; wait for :8005 weights warm (~1-3 min) — verified 2026-10-11: :8004
      down, :8005 loaded (below)
- [x] [ME] sanity: `curl :8005/v1/models` 200 + `curl :8005/memory` stats shows
      dir memory-eval + `python3 spikes/window/gate1_driver.py telemetry --tag boot1`
      — verified: models 200 status:loaded; /memory db=.../memory-eval/memory.db 0 memories;
      set_ple_venue --show = STOCK shard (boot-1 condition); telemetry state/telemetry_boot1.json
- [x] [ME] smoke: `gate1_driver.py smoke --url http://127.0.0.1:8005` -> PASS, store back to
      0 facts after forgets (expect: 3 taught, 3 search hits, recall chat PASS, 0 left)
      — run 1 all four met (state/smoke_boot1.log); run 2 chat line FAIL = pinned-block
      artifact (same first-user-message key, documented in the log; answered correctly
      FROM the block it was given); store verified back to 0 memories.
- [x] [ME] telemetry --tag M0-pre ; [PASTE] M block -> fresh chat :8005 ; reply -> agent
      — telemetry_M0-pre.json; fresh-chat reply archived state/m0_answers.txt
- [x] [ME] score M0 vs condition (record state/m0_verdict.txt); PASS -> continue, FAIL -> abort
      — PASS: 1-6 all idk, 7-9 all exact-PASS; no leak (2026-10-11 11:45Z)
- [x] [ME] probe_driver --phase b1 (official scripted: save/dissect/pre/post + counters) ;
      [PASTE] B1 observation after -> record (does not gate anything)
      — scripted run 2 (official) 11:50:44Z: save/dissect/restore sane, pre-ask FAIL ✓,
      post-ask FAIL (ptok=26 = no prefix ride) -> CONDITION NOT MET, full analysis
      state/b1_verdict.txt; 11:47Z run 1 was a plumbing 400 (ckpt=0 fact turn, fixed in
      probe_driver.py, logged); B1 paste pending -> state/b1_observation.txt
- [ ] [ME] probe_driver --phase a-prep (nonce -> state/a_pending.json) ; telemetry --tag a-prep
- [ ] [ME] set_ple_venue.py --copy Strata-data/ple-copies/q8-shard2-d2.gguf ; --show patched
- [ ] journal checkpoint entry + commit/push state files so far

## Phase 2 - boot 2 (patched dev) - the ONE restart
- [ ] [YOU] dev-down + dev-up (same scratch memory, now patched venue; the restart doubles as
      the a-check cross-restart test - do NOT edit the config yourself between, I flip it)
- [ ] [ME] telemetry --tag boot2 ; [PASTE] M block -> fresh chat :8005 -> score vs M1
      condition -> state/m1_verdict.txt (THE D2 VERDICT)
- [ ] [ME] probe_driver --phase a-check (official Gate-2 evidence) ; [PASTE] A1 observation
- [ ] [ME] probe_driver --phase report -> state/report.txt ; commit+push window artifacts

## Phase 3 - Gate-1 (same patched dev boot; text layer is venue-independent - note it in report)
- [ ] [ME] telemetry --tag teach-pre ; gate1_driver.py teach --url http://127.0.0.1:8005
      (expect ~100 ids; store stats count ~100) ; verify mode -> 100/100 searchable
- [ ] [ME] telemetry --tag G1-pre ; [PASTE] G1 chunks 1,2,3 in ONE fresh :8005 chat ;
      replies -> agent writes state/gate1_answers.txt (100 numbered lines, ask me for format)
- [ ] [ME] telemetry --tag G1-post ; gate1_driver.py score --answers state/gate1_answers.txt
      -> state/gate1_score.json ; fill: recall ____/100 ; by-kind/by-harness ; misses list
- [ ] [ME] Gate-1 verdict vs condition (>= 25) ; commit+push (driver output + paste files)

## Phase 4 - go home
- [ ] [ME] set_ple_venue.py --stock ; --show = stock ; dev-down
- [ ] [YOU] boot main your usual way ; agent back on main provider (this or the analysis session)
- [ ] [ME] curl :8004/v1/models 200 ; journal §9 close-out entry + §0 rewrite ; push

## Optional D3 (later, user-scheduled; costs two more main restarts)
- [ ] [ME w/ permission] edit ~/Strata/strata-iq3_s.json: add --ple-gguf <copy path> (backup
      first; same tmp-file pattern) - [YOU] restart main ; [PASTE] M2 on :8004 fresh chat ;
      verdict vs M2 condition -> state/d3_verdict.txt ; [ME] restore config from backup ;
      [YOU] normal boot. Main = stock binary + memory-less: M2 is pure model+table.

## Abort paths
- Any dev boot failure / anything on fire: boot main back (it never changed), resume later;
  the patched copy + scratch store are inert files.
- M0 shows a marker PASS: venue not stock -> --stock, verify --show, restart, re-run M0 once;
  second anomaly -> stop the window, diagnose with fresh eyes, journal what was seen.
- teach/verify mismatch before G1: fix plumbing (store-level, no measurement taken yet).
- Evening runs long: stop after Phase 2 report (D2 + Gate-2 banked); Gate-1 can rerun any dev
  boot later (teach is idempotent-checked by verify; scratch store can be wiped and retaught).

## Definition of done (this run)
All boxes above ticked with evidence ; every paste reply archived in spikes/window/state/
(m0/m1/m2 answers, gate1_answers.txt, *_verdict.txt, telemetry_*.json, report.txt) ; commits
pushed ; dev venue stock ; main config untouched (unless D3 ran, then restored) ; scratch
store intact and NOT merged into memory.db ; journal §9 + §0 updated at the end.

## Starter prompt for the fresh window session (give it to the new OpenCode on strata-dev)
"Read §0 of ~/.opencode/plan/strata-memory-core.md, then spikes/window/RUN_CHECKLIST.md and
spikes/window/PASTE.md IN FULL. You are the window session. Continue at the first unchecked
box of the checklist. For every [PASTE] line: give me the exact block to copy, the URL of the
fresh webapp chat to paste it into, and wait - I will paste your block to the model and paste
the model's reply back to you verbatim. Score everything strictly (no fuzzy matching). Tick
checklist boxes only with evidence. Journal + commit at every phase boundary."
