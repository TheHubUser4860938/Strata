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

**Machine:** dev box, engine 0.1.43.1, RTX 4090, cartridge `MEM-CRG-3af0d3bd.bin`
(21 standing facts, 247 MB, USR+DEC shelves). Sweep timer stopped for the runs and
restored after. Raw records: `venue-20261013-run3.jsonl[.metrics.json]` and
`venue-20261013-run4.jsonl[.metrics.json]` beside this file, scrubbed at the choke.
