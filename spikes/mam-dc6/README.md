# Spike C — independent DC6 mechanism re-implementation

Part of the memory-core fork's Phase 2 (numerical cartridges) research track:
can AKBASCORE MAM's published incremental-DC6 mechanism be re-derived from first
principles, run on commodity CPU, and reproduce the *signature result* -
incremental append-only consolidation beating the no-consolidation control, with
every previously consolidated K/V row **bitwise unchanged** at each append?

- Mechanism source (research record, © 2026 Mustafa Akbaş):
  [titan-cognitive-core-v2](https://github.com/ceceli33/titan-cognitive-core-v2) TEST560
  and disclosures doi 10.5281/zenodo.23127434, 10.5281/zenodo.23143605, 10.5281/zenodo.23146475.
- This implementation: independent, MIT, CPU-only, `Qwen/Qwen2.5-0.5B-Instruct` as a cheap
  dense stand-in for the 7B reference models. Fresh synthetic world names (invented proper
  nouns) so no general knowledge can fake a correct answer - the marker principle.
- What this CANNOT test: the hybrid-layer question (36 Gated DeltaNet + 12 QSA on
  Qwen3.8-Flash-Next). That is Spike B, inside the Strata engine.

## Run

```sh
python3 -m venv <venv> && <venv>/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch
<venv>/bin/pip install transformers huggingface_hub
<venv>/bin/python spikes/mam-dc6/dc6_mini.py --quick    # 4 cases, minutes
<venv>/bin/python spikes/mam-dc6/dc6_mini.py            # 8 cases
```

Logs land in `logs/dc6_mini_<utc>.json` (committed alongside the code - they are the
experiment record, small by construction).

## Reading the result

| signal | pass condition |
| --- | --- |
| `append_invariant_bitwise_all` | `true` at every append (the hard mechanism proof) |
| `INCR_DC6_*` vs `INDEP_ALONE` | incremental beats the control across target positions |
| `JOINT_TEXT` | the ceiling: joint facts-as-text; how far below it DC6 sits is the memory's honest quality statement |

Absolute accuracy trails the 7B records (72/72) - a 0.5B model is a weaker instrument;
we report ratios and deltas, not parity claims.

## Results (this machine)

Run 2026-10-10 on Ryzen-class CPU, torch 2.14.1+cpu, transformers 5.19,
Qwen2.5-0.5B-Instruct bf16, 8-case panel, ~24 s (log: `dc6_mini_20261010T204133Z.json`):

| arm | score |
| --- | --- |
| append bitwise invariant | **24/24 appends unchanged** |
| INDEP control (independent consolidation, naive concat) | **0/8** |
| INCR_DC6 FIRST | 8/8 |
| INCR_DC6 MIDDLE | 5/8 |
| INCR_DC6 LAST | 8/8 |
| JOINT_TEXT ceiling | 8/8 |

Verdict: **mechanism replication PASS.** The independent re-implementation reproduces the record's
qualitative signature - control collapse vs consolidation recovering the joint-text ceiling, with the
bitwise append invariant holding unconditionally - on a 0.5B instrument that answers worse everywhere.

Caveats kept on the record:
- The MIDDLE-position misses (3/8) are a soft spot AkbasCore's 7B runs did NOT report; a weaker
  instrument is the likely cause, but it is exactly the kind of thing that must be checked on the
  real target rather than assumed away. A 1.5B-3B rerun would test the instrument explanation cheaply.
- My INDEP control shape (full-row concat of independently consolidated banks) approximates the
  record's description; the 13/72-vs-72/72 contrast direction, not the exact ratio, is what replicated.
- Still says NOTHING about the hybrid question (36 Gated DeltaNet + 12 QSA layers) - that is Spike B.
