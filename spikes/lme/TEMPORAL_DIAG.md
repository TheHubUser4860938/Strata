# Temporal-reasoning diagnostic (2026-10-14, from the arm-B record)

The budget A/B moved temporal-reasoning 0.425 -> 0.433: the one type the bigger block did NOT
fix, so it got the diagnostic treatment - straight from `budget24k-20261014-full.jsonl`, whose
per-line recall hits let every wrong answer be split by WHY it was wrong.

## The numbers

127 temporal questions, 3 repeats each. Persistently wrong (never correct in 3): **61**.

| failure shape | count | reading |
|---|---|---|
| wrong lines that are refusals (`idk`) | 128 of 216 | the dominant mode - not confident wrongness |
| always-refused questions | 25 of 61 | |
| ...refused DESPITE an answer turn in top-3 | 20 of those 25 | the block held *an* answer row and the model still declined |
| off-by-1/2 numeric answers | 12 of 57 reading failures | the assumed "day arithmetic" story - a minority |

## The mechanism (sample gpt4_f49edff3, decisive)

Question: *"Which three events happened in the order from first to last..."* - a THREE-evidence
ordering question. The haystack carries all three events with full dates stored
(`DATE_RE = ^\s*(\d{4}-\d{2}-\d{2})`, so notes read `[2023-02-05] ...` - granularity is fine).
The model's answer: *"The only dated event in the memory is the phone case order..."* - and it
was right: `turn_top3` proves ONE answer turn reached the block; the other two events' rows
ranked below the cut. The refusal is the HONEST response to partial evidence. Ordering three
events from one is not a reasoning failure, it is an evidence-coverage failure.

## Verdict

Temporal-reasoning is **multi-evidence retrieval wearing a reasoning costume**. The metric
hid it: `turn_top1/top3` score presence of ANY gold turn, and these questions need ALL of
several. The fixes are the ones already in the build queue, and the diagnostic promotes both:

1. **Arm C (192 notes / 48k)** - more block slots means the second and third events FIT. The
   user proposed the budget bump before this diagnostic existed; the mechanism says it is
   exactly the right experiment for the 0.433 row, not just more of the same.
2. **The embedder (bge-small hybrid)** - a three-event query matches each event's row
   semantically; lexical ranking currently front-loads the one event sharing the question's
   distinctive words. This is the class the embedder was chosen for.
   *Built 2026-10-14* (`serve/embedder.py`, `docs/MEMORY_CORE.md`): on the live 2,521-row store the
   reach probe (`tools/m4_reach.py`) surfaces rows lexical recall never proposed at all (rank 0 -> 37)
   while holding a rank-1 exact-lexical answer at rank 1. The accuracy claim is the venue's to earn
   (arm C2), not this probe's.
3. **Metric upgrade (filed, not built):** for multi-evidence types, report the FRACTION of
   answer sessions present in the block, so coverage failures separate from reasoning
   failures without hand-reading. Until then, top-3 numbers overstate evidence availability
   for exactly the questions temporal asks.

Prediction to check at arm C: temporal rises materially above 0.433 (coverage-limited). If it
does not, the residual is reasoning-limited - quant/model territory, stated plainly, and the
row stops being a memory-project target.
