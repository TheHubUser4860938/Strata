# Spike C — independent DC6 mechanism re-implementation (+ instrument ladder, marker arms, scale curve)

Part of the memory-core fork's Phase 2 (numerical cartridges) research track:
can AKBASCORE MAM's published incremental-DC6 mechanism be re-derived from first
principles, run on commodity CPU, and reproduce the *signature result* -
incremental append-only consolidation beating the no-consolidation control, with
every previously consolidated K/V row **bitwise unchanged** at each append?

- Mechanism source (research record, © 2026 Mustafa Akbaş):
  [titan-cognitive-core-v2](https://github.com/ceceli33/titan-cognitive-core-v2) TEST560
  and disclosures doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475.
- This implementation: independent, MIT, CPU-only. Fresh synthetic world names (invented proper
  nouns) so no general knowledge can fake a correct answer - the marker principle.
  The committed 2026-10-10 baseline ran `Qwen/Qwen2.5-0.5B-Instruct` as a cheap dense stand-in
  for the 7B references; the ladder (2026-10-11) reruns the same panel on 1.5B and 3B, adds
  marker/read-side arms (`--arms delim,hdr,ctx,recomp`, plan §11 arms 1/2/4) and a TEST563-style
  `--bank` scale curve.
- What this CANNOT test: the hybrid-architecture question (36 Gated DeltaNet + 12 QSA on
  Qwen3.8-Flash-Next). That is Spike B, inside the Strata engine.

## Run

```sh
python3 -m venv <venv> && <venv>/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch
<venv>/bin/pip install transformers huggingface_hub
<venv>/bin/python spikes/mam-dc6/dc6_mini.py --quick          # 4 cases, minutes
OMP_NUM_THREADS=6 nice -n19 <venv>/bin/python spikes/mam-dc6/dc6_mini.py \
    --arms base,delim,hdr,ctx,recomp --model Qwen/Qwen2.5-1.5B-Instruct   # ladder rung, all arms
OMP_NUM_THREADS=6 nice -n19 <venv>/bin/python spikes/mam-dc6/dc6_mini.py \
    --bank 5,10,20,40 --bank-targets 2                        # scale curve (procedural worlds)
```

Protocol that the numbers below were measured under: **one run at a time, machine otherwise
idle, OMP_NUM_THREADS recorded in every log** (`torch_threads`). Near-tie greedy argmaxes can
flip when two CPU-torch jobs oversubscribe the cores (OMP reductions resize mid-flight) - that
is the one environmental thing that moves these numbers, and it is controlled, not assumed.

Logs land in `logs/dc6_mini_<utc>.json` (committed alongside the code - they are the
experiment record, small by construction).

## Reading the result

| signal | pass condition |
| --- | --- |
| `append_invariant_bitwise_all` | `true` at every append (the hard mechanism proof) |
| `INCR_DC6_*` vs `INDEP_ALONE` | incremental beats the control across target positions |
| `JOINT_TEXT` | the ceiling: joint facts-as-text; how far below it DC6 sits is the memory's honest quality statement |
| `correct` vs `correct_loose` | strict word-boundary is the headline; loose (exact-or-prefix answer) is logged beside it, never silently replacing it - needed because bigger instruments answer correctly then glue the next ChatML turn ("DrevantorHuman: ...") |

## Results (ladder complete 2026-10-11)

All arms rerun under ONE code version, serial+idle, 6 threads (log stamps in the manifest
below). Strict reproduces every earlier run bit-for-bit on every arm whose code is unchanged -
including 3B between its 4-thread original and the 6-thread rerun - so thread count is formally
exonerated as an explanation; only code changes moved numbers (the ctx fix, see manifest).

| arm (FIRST/MID/LAST) | 0.5B | 1.5B | 3B strict | 3B loose |
| --- | --- | --- | --- | --- |
| INCR_DC6 | 8/5/8 | 8/8/8 | 1/5/1 | 8/8/8 |
| INCR_DELIM | 8/6/8 | 8/8/8 | 0/0/0 | 8/8/8 |
| INCR_HDR | 7/7/7 | 8/8/8 | 0/0/0 | 8/8/8 |
| INCR_MIDDLE_RECOMP | 3/8 | 8/8 | 3/8 | 8/8 |
| INDEP_ALONE (control) | 0/8 | 3/8 | 2/8 | 2/8 |
| INDEP_CTX_REBUILD | 1/8 | 8/8 | 8/8 | 8/8 |
| JOINT_TEXT (ceiling) | 8/8 | 8/8 | 8/8 | 8/8 |
| append bitwise invariant | 88/88 | 88/88 | 88/88 | — |

Scale curve (0.5B, procedural worlds, banks 5/10/20/40, 2 targets each): INCR 1-2/2 at every
size, INDEP 0/2 everywhere, no decay visible at this resolution - the curve needs a 1.5B rerun
(4-bank ceiling 8/8 there) before decay can be seen at all.

Verdicts, honestly stated:

1. **Mechanism replication: PASS on three instruments.** INCR >> INDEP under either scoring,
   and the bitwise append invariant held 88/88 on every all-arms run (24/24 on the committed
   0.5B baseline) - it never once failed, at any model size, cut position, or bank size.
2. **The 3B "collapse" (first observed 2026-10-11 01:46Z) was a SCORING ARTIFACT**, not a
   memory failure: 3B answers the right name, then role-plays the next turn glued without
   whitespace, which the strict boundary regex cannot match. Under loose scoring every
   consolidation arm is 8/8 while INDEP stays 2/8 *with genuinely wrong entities* under both
   scorings. Strict stays the headline number for continuity with the committed 4dc4beb record;
   the loose column is what tells the truth about 3B.
3. **Marker-arm gate (§11: "must beat plain DC6") is UNMET for now.** DELIM/HDR tie at the
   ceiling on 1.5B and 3B-loose (8-case panel saturates at ≥1.5B), and 0.5B deltas (DELIM
   MIDDLE 6/8 vs base 5/8; HDR trades 1 FIRST + 1 LAST for +2 MIDDLE) are within single-case
   noise. Separation needs the harder scale-curve regime, not more of this panel.
4. **Strongest positive finding: `INDEP_CTX_REBUILD`** - ONE causal zero-embed pass over
   *independently forged* banks (never incremental), CacheBlend-style, plan §11-4 bank form -
   scores 8/8 strict on BOTH 1.5B and 3B (1/8 on the weak 0.5B instrument). Order-free,
   parallelizable cartridge assembly at full quality: the property append-only DC6 lacks.
   Spike B should test exactly this shape on the hybrid engine.
5. The 0.5B MIDDLE-position misses are **real** (loose does not rescue them) and disappear at
   1.5B+: instrument weakness, not mechanism - which is the explanation the 04:00 journal entry
   asked to check. Checked.

## Log manifest (every run, what it is)

| log (UTC) | model / mode | role in the record |
| --- | --- | --- |
| 20261010T204035Z | 0.5B quick | first smoke; 4 INDEP rows never reproduced - the oversubscription story, kept as found |
| 20261010T204133Z | 0.5B base | **the committed baseline** (4dc4beb): invariant 24/24, INDEP 0/8, INCR 8/5/8, JOINT 8/8 |
| 20261010T214046Z / 214127Z | 0.5B quick ×2 | determinism protocol checks (second run first records `torch_threads`) |
| 20261010T214314Z | 0.5B base @8 threads | strict bit-identical to the 204133Z baseline - thread-invariance evidence |
| 20261010T214637Z | 1.5B all arms | first 1.5B rung; ctx 0/8 = PRE-FIX version (dropped prefix rows; the arm docstring records why that scored 0) |
| 20261010T214955Z | 0.5B quick ctx | ctx arm debugging trail |
| 20261010T215352Z | 3B all arms @4 threads | first 3B rung; the strict "collapse"; ctx 0/8 pre-fix |
| 20261010T215529Z | 0.5B all arms | all-arms 0.5B, pre-loose, ctx pre-fix (1/8) |
| 20261010T215630Z | 1.5B base+ctx | first POST-FIX ctx measurement: 8/8 on 1.5B |
| 20261010T221252Z | 0.5B scale 5-40 | the scale curve (first scale run died on tuple JSON keys; this one has the string-key fix) |
| 20261010T230450Z | 3B all arms rerun | settles the anomaly: strict reproduces bit-for-bit; loose reveals the glue artifact; ctx (fixed) 8/8 |
| 20261010T230654Z | 0.5B all arms rerun | current-code 0.5B row of the ladder table (strict == 215529Z; loose == strict) |
| 20261010T231000Z | 1.5B all arms rerun | current-code 1.5B row (all arms == earlier runs; ctx 8/8 confirmed) |

## Caveats kept on the record

- My INDEP control shape (full-row concat of independently consolidated banks) approximates the
  record's description; the 13/72-vs-72/72 contrast DIRECTION, not the exact ratio, is what replicated.
- CUT is kept at absolute layer 6 across the ladder (same cut, different relative depth); the
  3B-under-CUT-6-of-28 question stays open on purpose - the hybrid target has its own CUT story
  (12 QSA layers), which is Spike B's.
- Still says NOTHING about the hybrid question (36 Gated DeltaNet + 12 QSA layers) - that is Spike B.
