# M3c - the standing-cartridge venue: protocol and what it found (2026-10-13, boot 13)

**Protocol** (`tools/m3c_venue.py`, written before the runs; >=3 repeats, temp 0.8,
max_tokens 500, fresh conversation per ask, timings read from responses - never from
engine logs, which can carry the template's delimiter):

- **M1** restore the live cartridge file; record `timings.restore_ms`.
- **M1b** restore a file from another engine configuration (`c5_comp.bin`, Oct 11); the
  engine must refuse it - the pin law, probed.
- **M2** the gold question warm (memory on) vs cold (`X-Strata-Memory: off`), interleaved.
- **M2d** an ADJACENT fresh/continuation pair - the mechanism probe (nothing else may
  touch the port between the pair: engine resume is strict extension of the last prompt
  the slot saw, and an interleaved request - any client's, including this assistant's own
  harness - silently invalidates the probe; run 4's continuation line is exactly such a
  spoiled data point, kept and labeled, not deleted).
- **M3** the wrong cartridge, made live: a foreign muted conversation (photosynthesis
  only) saved under the current pin, restored, then the gold question asked MUTED. A gold
  word in the answer is a leak; the venue exits 2 and stops. Then the right file returns
  and the positive control runs warm.

**Criteria history, stated because it changed between runs:** run 3 exited 2 on a leaked
control - reading the answer showed the gold word inside an EXAMPLE in a clarification
("Custom PC with an NVIDIA RTX 4090" while the model admitted it did not know the
machine). The criterion was fixed BEFORE run 4: ask with the few-words/`idk` rule (the
same voice as the LongMemEval harness) and score the FINAL line only. Run 3's records are
kept (`venue-20261013-run3.jsonl`) as the run that proved the control bites; run 4 is the
official record (`venue-20261013-run4.jsonl`).

**Results (run 4, official; run 3 agrees on every line except the spoiled-by-criterion
and the adjacent-pair, see above):**

| measure | result | verdict |
|---|---|---|
| M1 restore, 247 MB | 80.8 ms median (3 reps; run 3: 82.4) | the restore is fast - earned |
| M1b pin | refused, "config fingerprint differs" | the pin law works - earned |
| M2 warm | 3/3 gold ("NVIDIA GeForce RTX 4090, 24 GB") | the delta is real - earned |
| M2 cold | 0/3 gold, 3/3 `idk` | honest ignorance - earned |
| M3 control | 0 leaks, 3/3 `idk` | no knowledge from a stranger's K/V - earned |
| M2d continuation | run 3: 1,837 -> 599 ms, cache_n 2,871 | resume is real - same conversation only |
| M2 fresh-after-restore | reused 0, full prompt_ms (~1.9 s) | the fresh-conversation time win is NOT earned |

**Findings, plainly:** restore + pin + knowledge-honesty all pass. The fresh-conversation
prefill saving did not materialize: engine resume extends only the exact last prompt the
slot holds, so a NEW conversation re-prefills even standing on a restored prefix. The
mechanism's ceiling is visible (the continuation gave back 2,871 tokens), and the named
next experiment is the RAM-side conversation cache (`--conversation-cache-mib`, upstream:
faster, does not survive restarts) - boot 14, one variable per boot. Until that lands,
the cartridge's earned claims are: 80 ms slot reload, pin-verified provenance, and the
standing text riding every conversation - never "prefill skipped per conversation".

**Run 5 (same day, boot 16, the venue the cache experiment armed):** quiet port (sweep timer
stopped), `--conversation-cache-mib 8192` live, cart rebuilt to 16 facts after the merge round.
PASS shape again - restore 84.0 ms median, pin refused 1, warm 3/3, cold 0/3, control 0 leaks -
plus three lines the earlier runs could not have produced:

| measure | result | verdict |
|---|---|---|
| M2 warm, first rep | 2,054 ms, cache_n 0 | a never-seen conversation still pays full prefill |
| M2 warm, reps 2-3 + M2d fresh | **72-77 ms, cache_n 2,719** | the SAME question returns from the parked copy - the cache's parking, earned |
| M3 right-file warm, 3 reps | 2,719, then **0, 0** (1,945/1,941 ms) | the park is OPPORTUNISTIC: the M3 control's foreign conversations and save churn evicted it (engine: evictions=83) - reuse returns only while parked |
| M2d continuation | 2,085 ms, **cache_n 0** | first read as block instability; the CORRECTION below shows it was slot-replacement (interleaved harness) - a clean probe gives 100% reuse |

(The venue asks the same gold prompt 7 times unmuted; the record shows park hits while the
park holds and full prefills after eviction, in the same run - both halves in one jsonl.)

The continuation line flipped run 3's (2,871 back) because the mechanism was always gated on
the block, and the block is inside the system message: the follow-up ("Name one color") recalls
its own 8 notes, and when that set differs from the gold question's set, the shared prefix dies
at the system message and everything re-reads. The venue's own token arithmetic proves the
block changed: cont - fresh = 3,543 - 2,726 = 763 tokens, ~720 more than the two turns alone
account for; boot 15's identical-shape probe (A2 - A1 = 490 = turn tokens exactly) had
byte-identical blocks and gave back 2,820. A store reshuffle between boots (the merge round:
94 restores, 145 retirements) was enough to flip the outcome. So run 3's 2,871 was real but
lucky, and the honest claim is: **continuation reuse works when the block holds; the block
placement, not the engine, decides.** The named lever - moving the volatile block to the
prompt tail so the prefix never depends on recall luck - is promoted from the boot-15 demotion
with this measured cost (~1.5 s per follow-up turn when the block moves) and still needs its
own behavior venue before it moves.

**CORRECTION (same evening, after reading the code and running the probe it implied).** The
paragraph above is wrong on both counts, and the clean adjacent probe (session idle, zero other
clients - `m2d_clean_probe.py`, filed beside this record) proves it:

    FRESH  ptok 2,622  cache_n 0     prompt_ms 2,010.7
    CONT   ptok 3,082  cache_n 2,629 prompt_ms   681.5   -> 100% of the parent prompt reused

(1) **The block law does not exist.** The head block is PINNED per conversation - keyed
`h + sha1(first user message | shelf)` (serve/memory.py block_for) - so it is byte-identical
across every turn of a conversation by construction, and the per-turn block already rides the
TAIL (merged into the newest user message, whose bytes are new anyway). Block-to-tail is not a
lever to build; it is the shipped design. (2) **The delta arithmetic proved nothing.** cont -
fresh = the assistant answer + the follow-up + the per-turn tail block, and the tail block's
size legitimately varies (0-500 tokens) with zero head movement - today's delta was 460 WITH
full reuse. Run 5's 763 was a big tail block, not a moved head. (3) **Run 5's cont line was
invalidated the same way run 4's was:** an interleaved request from this assistant's own
harness replaced the slot between the adjacent pair, and the parked copy was unavailable under
RAM churn (evictions=83 that run) - the runbook's own documented failure mode. Run 3's 2,871
was not luck; it was the same working mechanism, measured while nothing interleaved.

Honest law after the correction: **continuation reuse works whenever the slot OR the parked
copy survives**; it fails when an interloper takes the slot AND RAM admission refuses the park
- and the venue's fragility is exactly that pair, not placement. The earned claims stand
(restore, pin, delta, parked repeats); the "block-to-tail next lever" entry is WITHDRAWN, and
the injection-budget scaling - cheap under a pinned head, since the head is prefilled once per
conversation, not per turn - becomes the next timing experiment instead.

**Machine:** dev box, engine 0.1.43.1, RTX 4090, cartridge `MEM-CRG-3af0d3bd.bin`
(21 standing facts, 247 MB, USR+DEC shelves). Sweep timer stopped for the runs and
restored after. Raw records: `venue-20261013-run3.jsonl[.metrics.json]` and
`venue-20261013-run4.jsonl[.metrics.json]` beside this file, scrubbed at the choke.
