#!/usr/bin/env python3
"""Aggregate the F venue's out-file into the beta-1 record. Reads the harness jsonl (one line
per question-repeat: warm hypothesis, muted cold control, official-judge verdict, Primnox-
style recall hits) and prints: overall + by-type accuracy, turn/session top-1 and top-3,
muted-control idk rate and leak count, repeat-to-repeat stability, and the honest per-question
majority (correct in >= 2 of 3 repeats - the pass/fail law the plan set: >= 3 repeats, one
fresh conversation per ask). Numbers only; no template bytes pass through (hypotheses are
already scrubbed by the harness)."""
import collections
import json
import statistics
import sys

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/lme_oracle_full.jsonl"
lines = [json.loads(l) for l in open(out) if l.strip()]
by_q = collections.defaultdict(list)
for l in lines:
    by_q[l["question_id"]].append(l)

n = len(lines)
qtypes = collections.Counter(l["type"] for l in lines)
correct = sum(1 for l in lines if l.get("correct"))
t1 = [l["turn_top1"] for l in lines if l.get("turn_top1") is not None]
t3 = [l["turn_top3"] for l in lines if l.get("turn_top3") is not None]
s1 = [l["session_top1"] for l in lines if l.get("session_top1") is not None]
s3 = [l["session_top3"] for l in lines if l.get("session_top3") is not None]
# multi-evidence coverage (tools/lme_harness.py, 2026-10-14): top-1/top-3 prove ONE gold turn
# arrived; these say how much of a MULTI-evidence question reached the recall. Records from before
# the metric simply lack the keys - reported as n/a, never as a false zero.
cov_r = [l["row_coverage"] for l in lines if l.get("row_coverage") is not None]
cov_s = [l["session_coverage"] for l in lines if l.get("session_coverage") is not None]
cold = [l for l in lines if l.get("cold_hypothesis") is not None]
cold_idk = sum(1 for l in cold if "idk" in (l.get("cold_hypothesis") or "").lower())
# leak view: a cold answer that carries the gold substring (the record has no judged-cold
# field - counting one was a false zero in the first draft of this report)
cold_leak = sum(1 for l in cold
                if str(l.get("gold", "")).lower().strip()
                and str(l.get("gold", "")).lower().strip() in (l.get("cold_hypothesis") or "").lower())
# majority per question (>= 2 of the repeats judged correct) - the pass/fail view
maj = {q: sum(1 for r in rs if r.get("correct")) >= 2 for q, rs in by_q.items()
       if len(rs) >= 3}
maj_all = sum(maj.values())
per_rep = collections.Counter()
for q, rs in by_q.items():
    for r in rs:
        per_rep[(r.get("repeat"), bool(r.get("correct")))] += 1

print(f"lines: {n} ({len(by_q)} questions) | types: {dict(qtypes)}")
print(f"accuracy (per line):        {correct}/{n} = {correct / max(n, 1):.3f}")
print(f"accuracy (majority >=2/3):  {maj_all}/{len(maj)} = {maj_all / max(len(maj), 1):.3f}")
for name, xs in (("turn_top1", t1), ("turn_top3", t3), ("session_top1", s1), ("session_top3", s3)):
    print(f"{name:14s}              {sum(xs) / max(len(xs), 1):.3f}  (n={len(xs)})")
for name, xs in (("row_coverage", cov_r), ("session_coverage", cov_s)):
    print(f"{name:14s}              "
          + (f"{sum(xs) / len(xs):.3f}  (n={len(xs)})" if xs else "n/a (records predate the metric)"))
# The split the temporal diagnostic could only get by hand-reading (spikes/lme/TEMPORAL_DIAG.md):
# a wrong line whose gold SESSIONS all arrived was NOT a retrieval failure - the evidence was in
# front of the model; one that is missing an event is. top-1/top-3 cannot see this difference,
# because they are satisfied by any ONE gold turn.
wrong = [l for l in lines if l.get("correct") is False and l.get("session_coverage") is not None]
full = sum(1 for l in wrong if l["session_coverage"] >= 0.999)
print(f"wrong lines with coverage data: {len(wrong)} | every gold session present (reasoning): {full}"
      f" | at least one missing (retrieval): {len(wrong) - full}")
print(f"muted control: idk {cold_idk}/{len(cold)} = {cold_idk / max(len(cold), 1):.3f}"
      f" | gold-substring leaks {cold_leak}/{len(cold)} = {cold_leak / max(len(cold), 1):.4f}")
print("per-repeat correct:", {r: f"{per_rep[(r, True)]}/{per_rep[(r, True)] + per_rep[(r, False)]}"
                              for r in sorted({k[0] for k in per_rep})})
by_type = collections.defaultdict(lambda: [0, 0])
cov_type = collections.defaultdict(list)
for l in lines:
    by_type[l["type"]][0] += 1 if l.get("correct") else 0
    by_type[l["type"]][1] += 1
    if l.get("session_coverage") is not None:
        cov_type[l["type"]].append(l["session_coverage"])
print("by type (accuracy | mean session coverage):")
for t, (c, tot) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
    cs = cov_type.get(t)
    print(f"  {t:28s} {c:4d}/{tot:<4d} = {c / tot:.3f}"
          + (f"  | coverage {sum(cs) / len(cs):.3f}" if cs else "  | coverage n/a"))
