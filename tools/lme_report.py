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
cold = [l for l in lines if l.get("cold_hypothesis") is not None]
cold_idk = sum(1 for l in cold if "idk" in (l.get("cold_hypothesis") or "").lower())
cold_correct = sum(1 for l in cold if l.get("cold_correct"))
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
print(f"muted control: idk {cold_idk}/{len(cold)} = {cold_idk / max(len(cold), 1):.3f}"
      f" | judged correct cold {cold_correct}/{len(cold)} (leak view)")
print("per-repeat correct:", {r: f"{per_rep[(r, True)]}/{per_rep[(r, True)] + per_rep[(r, False)]}"
                              for r in sorted({k[0] for k in per_rep})})
by_type = collections.defaultdict(lambda: [0, 0])
for l in lines:
    by_type[l["type"]][0] += 1 if l.get("correct") else 0
    by_type[l["type"]][1] += 1
print("by type (accuracy):")
for t, (c, tot) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
    print(f"  {t:28s} {c:4d}/{tot:<4d} = {c / tot:.3f}")
