# Coordinated window - runbook (D2 + B1 + Spike A remainder + D3 cross-check)

Everything here is staged and tested against the live model already (baseline rehearsal in
`rehearsal-state/`); the window's job is to run the SAME steps against the DEV engine (:8005)
with the patched PLE copy booted, plus the session-file save/restore flow that only the dev
config enables (`slot_save_path`).

Ground truths the staging rests on (all measured this project, see docs/MEMORY_CORE.md and
spikes/ple-overlay/NOTES.md): the delta-overlay patch's `Impl::decode` seam (design in
`spikes/engine-patch-draft/`, its C++ logic build-audited standalone this session);
`session_hash64` ported byte-exact (`tools/strata_session.py --selftest` reproduces the
engine's golden fixture hash 0x70f812b350360d09 and `--verify` re-signs real files); clone
plans built with the production tokenizer (`spikes/window/d2_plan.json`: 616 row-writes,
3 marker facts x aliases, stopword positions skipped, 0 intra-plan collisions, sampled
occupancy 304/308 recorded honestly).

## Who does what

The swap itself is a USER action (the machine runs one engine at a time and this agent is
served by production, docs/MEMORY_CORE.md). The agent queues work; the human flips engines.

## Sequence (tick-tock with expected durations)

| t | who | step |
|---|-----|------|
| 0 min | user | close main build (:8004) - or run `./dev-up.sh --force` together with the agent's go-ahead |
| 0 min | agent | `nohup bash spikes/window/00_copy_and_patch.sh > spikes/window/state/copy.log 2>&1 &` (54.4 GB copy ~10-25 min, parallel with boot) |
| 1 min | user | `./dev-up.sh` (or agent with agreement) - dev :8005 boots, weights ~51 GB, ~1-3 min page-cache warm |
| ~2 min | agent | health check + `probe_driver.py --phase b1 --url http://127.0.0.1:8005` (session cartridge: save -> dissect -> fresh-ask FAIL -> restore -> fresh-ask + prefix-cache counters) |
| ~4 min | agent | `--phase a-prep` (save, then note for user), `--phase d2-baseline` (marker FAILS on this boot - copy NOT yet loaded; the config still points at the stock shard) |
| ~6 min | agent | when copy+patch+verify log is clean: `set_ple_venue.py --copy <OUT>` then ask the user for ONE dev restart |
| ~12 min | user | restart dev (dev-down + dev-up, or the swap flow they prefer) |
| ~14 min | agent | `--phase d2-treatment --url http://127.0.0.1:8005` (marker questions + never-written controls) and `--phase a-check` (restore across the restart that just happened) |
| ~18 min | agent | `--phase report` -> verdicts printed with the pre-registered conditions below |
| optional | user+agent | D3 cross-check: point the STOCK engine's `--ple-gguf` at the same copy (edit ~/Strata config, user's tree) and rerun `--phase d2-treatment --url http://127.0.0.1:8004 --state-dir spikes/window/state-d3` - a hit with the stock engine proves the knowledge lives in the TABLE, not in fork code. Costs one production restart (the agent goes with it - schedule last). |
| end | user | `./dev-down.sh` restores :8004 for normal service (check `set_ple_venue.py --show` said stock first, or flip it back with `--stock`) |

Abort at any point: `./dev-down.sh` returns to production; all state files are append-only
JSONL, and nothing besides the config `--ple-gguf` value and the copy file is touched. The
copy is disposable (1.8 TB free; rm it when done or keep for D3).

## Pass conditions, pre-registered (no goal-moving after the fact)

- **D2 (calibration make-or-break):** every marker question `correct=True`, every control
  marker `correct=False`, every positive control (fact in text) `correct=True`, on the
  copy boot. Baseline boot must show marker `correct=False` (it did in rehearsal).
- **B1:** pre-restore fresh-ask False, post-restore fresh-ask True, AND the prefix-cache /
  prompt-token counters say the answer rode the RESTORED prefix (reuse), not a re-prefill
  of re-sent text.
- **A-remainder:** post-restart fresh-ask True (Spike A's two missing halves in one shot).
- **Gate D3:** only if D2 passes. Same conditions rerun on the stock engine.

## What a D2 failure means (decide this NOW, not after)

A clean miss (markers fail on the copy boot like baseline) = fresh-row values don't drive
the trained readout -> tier-1 PLE writes stay a research note, the overlay C++ hook is not
built, and the text store + session-cartridge tiers (B1 result) carry the roadmap. A hit
with control hits too = contamination -> re-run with fresh nonce markers (cheap: rebuild
d2_plan.json with new names and repeat). Partial (some markers hit) = keep going, measure
which alias shapes stick; the curation loop learns aliases (plan 11-7) anyway.
