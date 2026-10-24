# Browse Day pre-registration — the tool arm vs the served control (0.666)

Written 2026-10-24 afternoon, BEFORE any browse ask, after the owner's architecture ruling
(4503: tier-3 recall from SERVED to BROWSED — the answering model queries the store; the
algorithmic layer converts into the index it browses) and his catalog law (the store needs
index cartridges — the branching map of the library — so browsing is reading a map, not
wading thousands of rows). Predictions fixed here first; the run may not start until they
are countersigned.

## What the inspection found (measured, not remembered)

- The tool loop is LIVE and already offered: `tool: true`, `tool_rounds: 0` (unlimited),
  `memory_search` = `q` + `class` (shelf prefix) + `since`/`until` + `limit<=20`; the server
  runs it locally in milliseconds; venue day ran with the tool ON.
- Scar, owned: venue day's tool USAGE is unmeasured history — the tool prints go to the
  double-click terminal (server stdout), and that log died with the window. A `tool` counter
  in `stats()` closes the hole from this day forward; venue day's 0.666 stands as the
  injection control it was, whatever the model did with the open door.
- Missing verbs found by reading the code: no card catalog (the model must GUESS shelf names;
  nothing answers "what branches exist"), no shelf-walk (`q` required — cannot list a drawer's
  rows without inventing keywords), facts come back dateless (temporal browsing reads cards
  with the dates filed off), and no per-request block-off that keeps the tool (`X-Strata-Memory:
  off` mutes the tool too, because `request_state` returns early).
- Fixture honesty: venue2 `created_at`s are ingest stamps (all 10,978 rows fall in a 7-minute
  window on 2026-10-23) — the story dates ride inside the fact TEXT. Date-filtering cannot
  help the venue's temporal class; the pre-registration says so BEFORE the run instead of
  explaining it away after.

## The build (all tool-side, no engine fork, byte-identical default)

1. `X-Strata-Memory-Block: off` — declines the injected block for one request but still
   journals the turn and still offers the tool. This is what makes "browsed" expressible.
2. `catalog: true` verb — the card catalog of the browsable scope: drawer names + row counts +
   newest date, newest-first by size, top-24 with an honest `hidden_drawers` rollup (the
   75%-confetti drawers must never drown the map). Computed live (SQL, ms, never stale) AND
   label-aware: when `MEM/CAT.*` stored rows exist for a branch, its curated keywords ride
   (owner's option 3: both at once; the stored half arrives with the builder tool, the sweep
   maintains it).
3. Shelf-walk — `q` becomes optional: `class` alone lists the drawer's rows newest-first, with
   the drawer's true total so the browser knows to page or date-filter.
4. `when` — every returned fact carries its date (YYYY-MM-DD).
5. `tool` counters in stats: calls / searches / walks / maps / empty — the browsing depth
   becomes ledger, not folklore.
6. Harness `--browse`: sends block-off + tool-on headers, records `sec` and `rounds` per
   question, prints the store's tool counters after the run.

## Arms

- **Control (banked)**: served — block injected, 500x3, temp 0.8, venue2: **0.666** overall
  (per type: abstention 0.500, multi-session 0.515, temporal 0.512, knowledge-update 0.639,
  ss-user 0.979, ss-assistant 0.899, ss-preference 0.867).
- **Browse arm (this day)**: block OFF + tool ON with the verbs above, same store, same 500
  questions, same judge, temp 0.8, one boot. The model gets NOTHING served; it must fetch.

## Pilot first (the gate before the day)

20 questions (5 abstention, 5 multi-session, 5 single-session-user, 5 temporal), block off,
tool on. The pilot passes only if the tool counter moves (the model can drive the tool on this
template) and answers stay sane. If it fails, the fix is prompt-side (tool description /
header pressure), NOT a venue day spent finding that out.

Real-store note, found building step 0 (2026-10-24 clone smoke): a nonsense question returned
`found: 8` - the cosine arm always fills the list (top-K neighbours, no absolute floor), so
`found: 0` is rare for q-calls; it IS honest for shelf walks and class-filtered searches. P2's
mechanism is therefore the MODEL JUDGING what it fetched (the returned text and `when` dates),
not a literal empty result - which is exactly what the pilot's five abstention questions grade.

## Predictions (fixed before running; the day decides)

- P1 — the model browses: mean >= 1.0 `tool` calls per answer. Below 0.5 the verdict is
  "declined to browse", which redirects the prompt, not the architecture.
- P2 — abstention 0.500 rises to >= 0.58: `found: 0` is the honest zero the floor sweep
  proved no score cutoff can teach (2.7 confident junk rows even at 0.45).
- P3 — multi-session 0.515 rises to >= 0.58: iteration + catalog map surface drawers the
  question never names (the vocabulary failure the picker cannot see).
- P4 — single-session-user 0.979 stays flat (±0.03): one search reaches what the block served.
- P5 — temporal 0.512 stays within ±0.05: ingest-stamped dates (measured above) leave the
  date-filter no work to do; any win must come from dates inside the text.
- P6 — latency pays and is REPORTED, never gated (owner's budget law: right in a minute beats
  wrong in two seconds). Expect >= 2x the control's per-answer seconds; median and p95 recorded.

## Decision rule

- Browse arm overall >= 0.686 (= 0.666 + 0.02, above the 0.012 repeat noise) AND P1-P3 hold:
  browse is promoted to the venue's answer path; the block becomes the fast default fallback.
- Within ±0.02 of control AND P2-P3 hold: hybrid becomes law — block stays the served default,
  the catalog + walk verbs ship anyway (they are the escalation path the venue-day failure
  classes begged for).
- Below 0.646: served stays the default; the verbs stay in the product (they are what the
  ruling's index needs either way); C5 (bank composition) remains the cartridge line's gate.

## Venue

- Boot: `run-venue.sh` (venue2 store, `sessions-venue`, own log), owner's double-click; the
  venue json's staged `block_min_score: 0.25` is inert for the browse arm (no block) and stays
  staged for the served line's step-B rerun.
- 500 x 1 first; repeats x3 only if the day lands inside the decision bands (no spending 2.5 h
  on a number that cannot change the law).

## Owner signature

Pending countersign on P1-P6 and the decision rule. C5 (bank composition) is the cartridge
line's own probe, signed separately; Browse Day does not wait for it.
