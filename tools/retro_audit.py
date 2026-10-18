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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("STRATA_URL", "http://127.0.0.1:8005"))
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--json", default="", help="write flagged rows as jsonl here (local, review)")
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
    try:
        with attach(store_dir, autocommit=True) as con, con.cursor() as cur:
            cur.execute(
                """select m.id, m.text, m.provenance->>'turn_id', t.user_text, t.assistant_text
                   from memories m
                   left join turns t on t.id = (m.provenance->>'turn_id')::bigint
                   where m.source like 'curation-%%' and m.invalid_at is null
                     and m.provenance ? 'turn_id'
                   order by m.id desc limit %s""", (a.limit,))
            for mid, text, tid, ut, at in cur.fetchall():
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
    finally:
        if out:
            out.close()
    total = sum(counts.values())
    print(f"retro audit: {total} curation rows measured -> "
          f"grounded={counts['grounded']} unattributed_paraphrase={counts['unattributed_paraphrase']} "
          f"no_turn={counts['no_turn']} (review queue, not deletion"
          + (f"; jsonl at {a.json}" if a.json else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
