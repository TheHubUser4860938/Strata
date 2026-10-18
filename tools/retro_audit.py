#!/usr/bin/env python3
"""Retro-sweep for paraphrase laundering (audit 09 finding 4, Grok Q3 grading law, 2026-10-18).

MECHANICAL, NO LLM JUDGE: for every ACTIVE curation row that carries a provenance turn_id,
fetch the journaled turn and measure the stored FACT against the turn's own words. A row
whose words are neither covered by the turn nor a contiguous span of it is an
unattributed_paraphrase and lands in the REVIEW QUEUE - never an auto-delete (the same law
that keeps the sweep from culling rows it has not scored).

SPEC DEVIATION, logged: Grok's spec said Jaccard; Jaccard on a (fact, turn) pair measures
the LENGTH RATIO, not the grounding - a 20-token fact against a 400-token turn scores ~0.05
no matter how faithful it is. This tool uses FACT-SIDE COVERAGE (what fraction of the
fact's content tokens appear in the turn) OR an 8-token contiguous span, which is what
"near-substring after stop-word removal" was reaching for. Threshold 0.4 kept as specified.

SCOPE HONESTY: this measures GROUNDING (are the words from this turn?), not ATTRIBUTION
(whose belief is it?) - attribution needs the ATTR field, which only post-PR5 rows carry.
The voting-rights row would PASS grounding (Maria's words are in the turn) and needs ATTR
to be caught; retro_audit is the net for facts not even faithfully extracted.

    .venv/bin/python tools/retro_audit.py [--json tools/state/retro_YYYY-MM-DD.jsonl] [--limit N]

Reads the store dir from GET /memory stats.dir (the curate_loop pattern), ATTACHES per the
ADR-002 R0b law (a live cluster is never stopped), SELECT only. No engine, no GPU.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

STOP = {"the", "a", "an", "is", "was", "were", "are", "be", "been", "being", "to", "of",
        "and", "or", "in", "on", "at", "for", "with", "it", "its", "this", "that", "as",
        "by", "from", "we", "i", "you", "he", "she", "they", "his", "her", "their", "our",
        "my", "not", "no", "so", "if", "but", "then", "than", "there", "here", "what",
        "does", "did", "do", "have", "has", "had", "will", "would", "can", "could", "one"}


def tok(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP}


def coverage(fact: str, turn: str) -> float:
    a, b = tok(fact), tok(turn)
    return len(a & b) / max(1, len(a))


def gram_match(fact: str, turn: str, n: int = 8) -> bool:
    """True when an n-word contiguous window of the fact appears verbatim in the turn.
    A fact shorter than n must appear whole (the window shrinks to the fact)."""
    fw = (fact or "").lower().split()
    tw = " ".join((turn or "").lower().split())
    if not fw:
        return False
    n = min(n, len(fw))
    return any(" ".join(fw[i:i + n]) in tw for i in range(len(fw) - n + 1))


def is_grounded(fact: str, turn: str) -> bool:
    return coverage(fact, turn) >= 0.4 or gram_match(fact, turn)


_SHEET_HEADER = """# retro labeling sheet - {n} rows (seed {seed}, reproducible)
Hand labels only, no model, per the Grok round-3 Q3a protocol WITH the label-definition
fix logged at adoption: the deployed coverage metric is FACT-SIDE (the fraction of the
FACT's content tokens present in the turn), NOT turn-side - the label must validate the
metric that actually ships, or the protocol measures a number no code computes.
  span_present: can the fact's wording be traced to this turn (its own words or a
    trivial paraphrase of them)? y/n
  coverage_accurate: is the printed coverage plausible FOR THIS FACT against this
    turn? y/n
Strata: flagged vs passed x age bins <7d / 7-30d / >30d, fixed seed. The bar-shift
rule (a >= 0.10 Wilson-interval exclusion, confirmed by a second independent sample,
move only to restore the binding lower bound to 0.70) runs on these labels."""


def _stratify(pool: list, n: int, seed: int = 20261018) -> list:
    """Grok round-3 Q3a stratification: flagged rows get at least a third of the
    sheet where they exist (that is where the labels matter most), the rest to the
    passed stratum; within each stratum, round-robin across the available age bins,
    shuffled under a fixed seed so the sample is reproducible for the second draw."""
    import random
    rng = random.Random(seed)
    flagged = [r for r in pool if r["verdict"] != "grounded"]
    passed = [r for r in pool if r["verdict"] == "grounded"]

    def take(rows, k):
        b = {"<7d": [], "7-30d": [], ">30d": []}
        for r in rows:
            b["<7d" if 0 <= r["age_days"] < 7 else ("7-30d" if r["age_days"] <= 30 else ">30d")].append(r)
        for v in b.values():
            rng.shuffle(v)
        out, keys, i = [], [k2 for k2 in ("<7d", "7-30d", ">30d") if b[k2]], 0
        while len(out) < k and any(b[k2] for k2 in keys):
            key = keys[i % len(keys)]
            if b[key]:
                out.append(b[key].pop())
            i += 1
        return out

    n_flag = min(len(flagged), max(n // 3, min(40, len(flagged))))
    return take(flagged, n_flag) + take(passed, n - n_flag)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("STRATA_URL", "http://127.0.0.1:8005"))
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--json", default="", help="write flagged rows as jsonl here (local, review)")
    ap.add_argument("--sample", type=int, default=0,
                    help="Grok round-3 Q3a: also draw a stratified labeling sheet of N rows "
                         "(flagged/passed x age bins <7d/7-30d/>30d, fixed seed, hand labels only)")
    ap.add_argument("--sheet", default="", help="write the labeling sheet here (local, personal text)")
    a = ap.parse_args()
    with urllib.request.urlopen(a.url + "/memory", timeout=15) as r:
        stats = json.load(r)["stats"]
    store_dir = stats.get("dir")
    if not store_dir:
        raise SystemExit("stats.dir missing - is the server up and memory on?")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from serve.memory import attach                       # the ADR-002 attach law
    counts = {"grounded": 0, "unattributed_paraphrase": 0, "no_turn": 0}
    out = open(a.json, "w") if a.json else None
    pool = []                                   # every measured row, for the --sample sheet
    try:
        with attach(store_dir, autocommit=True) as con, con.cursor() as cur:
            cur.execute(
                """select m.id, m.text, m.provenance->>'turn_id', t.user_text, t.assistant_text,
                          m.created_at
                   from memories m
                   left join turns t on t.id = (m.provenance->>'turn_id')::bigint
                   where m.source like 'curation-%%' and m.invalid_at is null
                     and m.provenance ? 'turn_id'
                   order by m.id desc limit %s""", (a.limit,))
            for mid, text, tid, ut, at, created in cur.fetchall():
                if not tid or not ut:
                    # the turn aged out or the id never existed: unmeasurable, and
                    # unmeasurable is its own review flag - never silently "grounded"
                    verdict, turn = "no_turn", ""
                else:
                    turn = (ut or "") + " " + (at or "")
                    verdict = "grounded" if is_grounded(text, turn) else "unattributed_paraphrase"
                counts[verdict] += 1
                if out and verdict != "grounded":
                    out.write(json.dumps({"id": mid, "turn_id": tid, "verdict": verdict,
                                          "coverage": round(coverage(text, turn), 3),
                                          "fact": text[:120]}) + "\n")
                pool.append({"id": mid, "text": text, "turn": turn, "verdict": verdict,
                             "coverage": coverage(text, turn), "ut": ut or "", "at": at or "",
                             "age_days": (__import__("datetime").datetime.now(
                                 __import__("datetime").timezone.utc) - created).days
                             if created else -1})
    finally:
        if out:
            out.close()
    if a.sample:
        draw, seed = _stratify(pool, a.sample), 20261018   # fixed seed: the sheet is reproducible
        with open(a.sheet or f"retro_sheet_{seed}.txt", "w") as sh:
            sh.write(_SHEET_HEADER.format(n=len(draw), seed=seed, sample=a.sample) + "\n")
            for r in sorted(draw, key=lambda r: (r["verdict"], -r["coverage"])):
                sh.write(f"--- row {r['id']} verdict={r['verdict']} coverage={r['coverage']:.3f} "
                         f"age_days={r['age_days']}\n"
                         f"fact: {r['text']}\nuser: {r['ut'][:600]}\nassistant: {r['at'][:600]}\n"
                         f"span_present (does the fact's wording trace to this turn? y/n): ______\n"
                         f"coverage_accurate (is the PRINTED coverage right for this fact? y/n): ______\n\n")
            print(f"sheet: {a.sheet or f'retro_sheet_{seed}.txt'} - {len(draw)} rows "
                  f"(seed {seed}, reproducible)")
    total = sum(counts.values())
    print(f"retro audit: {total} curation rows measured -> "
          f"grounded={counts['grounded']} unattributed_paraphrase={counts['unattributed_paraphrase']} "
          f"no_turn={counts['no_turn']} (review queue, not deletion"
          + (f"; jsonl at {a.json}" if a.json else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
