#!/usr/bin/env python3
"""spikes/window/identity_check.py - the v0.1.43 rebase's identity half: do ANSWERS move on this PC?

The rebase plan (plan §11 T6.0) refuses to compare any number across engine versions until the
same-day cold baselines are re-proven. Upstream 0.1.43's own notes say answers are identical to
0.1.42 except where a default says otherwise, and the one default that could bite here - route
tail skip staying off above 90% experts resident (#1884) - is far out of reach on this box
(~17% resident: 4,228 slots of 48x512; verified 2026-10-11). This script MEASURES the claim
instead of trusting it:

  corpus: 12 of the 67 eval_candidates.tsv rows whose 0.1.42 cold answer was "known" (correct) -
          real world-knowledge prompts whose exact answers are on the record from the morning's
          cold run. Plus the 3 Venue-C read-path cues (fact IN the prompt) ride separately via
          `v2_run.py --venue C` (deterministic: the gold sits in the context; the 0.1.42 answers
          are in state/v2_run.jsonl), and the recall smoke rides `gate1_driver.py smoke`.
  ask:    fresh single-turn chat, memory MUTED (X-Strata-Memory: off), reasoning_effort off,
          temperature 0.8 (the user's regime; every verdict here needs >= 2 repeats anyway -
          today's measurement: 3-8 answer flips per 100 asks between passes at this temp).
  judge:  (a) strict gold hit per row (gate1_driver.hit), (b) the exact-answer band: we log the
          full answer text; a DRIFT is a row where the 0.1.42-era gold no longer comes back.
          Flip-vs-drift is separated by repeating each ask --passes N (default 3): a row that
          hits in ANY pass but not all passes is temp churn, not engine drift; a row that MISSES
          EVERY pass against a known-gold baseline is DRIFT and stops version comparisons.

Usage: .venv/bin/python spikes/window/identity_check.py --url http://127.0.0.1:8005 --passes 3
       (post-rebuild side: --tag v0143 --baseline spikes/window/state/identity_v0142.json)
Evidence: spikes/window/state/identity_<tag>.json + printed verdict.
"""
import argparse, csv, json, sys, time, urllib.request as U
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "d2_dissect"))
from gate1_driver import hit  # noqa: E402

def corpus():
    """12 known-verdict rows of eval_candidates.tsv, spread deterministically over kinds
    (round-robin) - these ARE the fixed-prompt corpus: real world-knowledge questions the
    0.1.42-era cold run answered CORRECTLY (verdict "known", the cold answer text is on the
    record beside them). Same 12 questions on both sides of the rebase, no sampling seed."""
    by_kind = {}
    with open(HERE / "eval_candidates.tsv") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r.get("verdict") == "known":
                by_kind.setdefault(r["kind"], []).append(r)
    out, rnd = [], 0
    while len(out) < 12 and any(rnd < len(v) for v in by_kind.values()):
        for k in sorted(by_kind):
            if rnd < len(by_kind[k]) and len(out) < 12:
                out.append(by_kind[k][rnd])
        rnd += 1
    return out


def ask(url, q, temp):
    body = {"messages": [{"role": "user", "content": q}], "temperature": temp,
            "reasoning_effort": "off", "max_tokens": 120}
    req = U.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                    method="POST", headers={"Content-Type": "application/json", "X-Strata-Memory": "off"})
    with U.urlopen(req, timeout=300) as r:
        j = json.loads(r.read())
    return ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8005")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--tag", default="v0143")
    ap.add_argument("--baseline", default="", help="identity_<other tag>.json from the previous "
                    "build; when given, the verdict is per-row DRIFT vs that baseline")
    a = ap.parse_args()
    C = corpus()
    per = []
    for p in range(1, a.passes + 1):
        for i, r in enumerate(C, 1):
            ans = ask(a.url, r["question"], a.temp)
            per.append({"pass": p, "n": i, "kind": r["kind"], "q": r["question"],
                        "gold": r["gold_answer"], "cold_answer_0142": r["cold_baseline_answer"],
                        "answer": ans.strip(), "hit": bool(hit(ans, r["gold_answer"]))})
            print(f"[p{p} {i:>2}/{len(C)}] {'HIT ' if per[-1]['hit'] else 'MISS'} {r['question'][:56]} -> {ans[:40]}", flush=True)
    any_hit, always_miss = set(), []
    for i in range(1, len(C) + 1):
        hs = [x for x in per if x["n"] == i]
        if any(x["hit"] for x in hs):
            any_hit.add(i)
        else:
            always_miss.append(i)
    churn = sum(1 for i in range(1, len(C) + 1)
                if i in any_hit and not all(x["hit"] for x in per if x["n"] == i))
    STATE.mkdir(exist_ok=True)
    out = STATE / f"identity_{a.tag}.json"
    out.write_text(json.dumps(per, indent=1))
    verdict = (f"identity {a.tag}: {len(any_hit)}/{len(C)} known-gold rows answered in >= 1 of "
               f"{a.passes} passes at temp {a.temp}; per-row misses: {always_miss or 'none'} "
               f"(NOT drift if the same tool already missed them on the other build)")
    if a.baseline:
        # the real test: SAME tool, SAME temp, per-row hit sets both sides of the version change.
        # measured 2026-10-11: at temp 0.8 five of these world-knowledge rows miss every pass even
        # on 0.1.42 (their tsv verdict came from temp-0 webapp probes), while memory-recall rows
        # never flipped. A row is a DRIFT only if the baseline HIT it and this build misses it in
        # EVERY pass. 3 passes is the minimum: single-pass flips ran 3-8 per 100 at this temp today.
        base = json.loads(Path(a.baseline).read_text())
        b_any = {x["n"] for x in base if x["hit"]}
        drift = sorted(n for n in b_any if n not in any_hit)
        gained = sorted(n for n in any_hit if n not in b_any)
        verdict += (f"\nvs baseline {Path(a.baseline).name} (baseline hits {len(b_any)}): "
                    f"DRIFT rows {drift or 'none'}; flips-toward-hit {gained or 'none'} -> "
                    + ("IDENTITY HELD on this corpus" if not drift else
                       "DRIFT - answers moved; re-take cold baselines before ANY cross-version number"))
    (STATE / f"identity_{a.tag}_verdict.txt").write_text(verdict + "\n")
    print(verdict)


STATE = HERE / "state"
if __name__ == "__main__":
    main()
