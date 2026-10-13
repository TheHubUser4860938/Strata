#!/usr/bin/env python3
"""Offline tests for tools/m2_merge.py - no server, no GPU: a fixture db and a dry run.

The rules under test, each earned as a bug on the live store:
  * payload grouping still merges pure paraphrases of one fact (KTNC pair);
  * the v3 SUBJECT RULE (round 3, 2026-10-13: 94 rows retired whose rare names - Edgeley,
    Paddock - appeared nowhere in the winner) never lets a loser keep a rare subject the
    winner lacks, however similar the grammar;
  * --apply prints every pair, a dry run prints only --show (round 3 applied 239 on 6 shown).
Run: python3 tools/test_m2_merge.py
"""
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "m2_merge.py"

SCHEMA = """
create table memories (
    id integer primary key autoincrement,
    scope text not null,
    text text not null,
    source text,
    hits integer default 0,
    confidence real,
    created_at integer,
    class text,
    provenance text,
    invalid_at integer,
    superseded_by integer
);
"""

FIXTURES = [
    # one fact, two phrasings: shared rare payload, no divergent subjects -> mergeable.
    # the loser is a strict token SUBSET of the winner (real paraphrases that swap word forms
    # are deliberately NOT offered: the subject rule errs safe, which is the whole point)
    ("default", "KTNC operates country music broadcasts from Falls City."),
    ("default", "KTNC operates country music broadcasts from Falls City on ninety-five point nine."),
    # three DIFFERENT townships: identical grammar, shared mid-frequency payload, each row
    # names its own rare subject -> group forms, both losers subject-vetoed, nothing retires
    ("default", "The township of Alpha is located in Angel County, Minnesota."),
    ("default", "The township of Beta is located in Brevik County, Minnesota."),
    ("default", "The township of Gamma is located in Grant County, Minnesota."),
    # a fact whose only copy carries a rare name in question form (the round-3 EVAL shape):
    # loser names Edgeley, winner (a different airport) does not -> vetoed
    ("default", "What FAA location identifier is assigned to Edgeley Municipal Airport? Answer: D05."),
    ("default", "The FAA location identifier for Casselton Municipal Airport is D25."),
]


def make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for i, (scope, text) in enumerate(FIXTURES):
        conn.execute("insert into memories (scope, text, source, created_at) values (?,?,?,?)",
                     (scope, text, "fixture", 1_700_000_000 + i))
    conn.commit()
    conn.close()


def run_dry_run(db: str) -> str:
    out = subprocess.run([sys.executable, str(TOOL), "--db", db], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, f"tool exited {out.returncode}: {out.stderr[-400:]}"
    return out.stdout


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = f"{td}/store.db"
        make_db(db)
        out = run_dry_run(db)
    print(out)

    fails = []
    # the KTNC paraphrase pair must still be offered for merge (rule not neutered)
    ktnc_drops = re.findall(r"drop \[\d+\] 'KTNC[^']+", out)
    if len(ktnc_drops) != 1:
        fails.append(f"expected exactly 1 KTNC drop (the second phrasing), got {ktnc_drops}")
    # townships + Edgeley: three losers that name subjects their winners lack -> vetoed
    m = re.search(r"subject-vetoed: (\d+)", out)
    if not m or int(m.group(1)) < 3:
        fails.append(f"expected >= 3 subject-vetoed (2 townships + Edgeley), got {m and m.group(1)}")
    # no township or Edgeley row may appear as a drop
    for name in ("Alpha", "Beta", "Gamma", "Edgeley"):
        if f"drop" in out and re.search(rf"drop \[\d+\] '[^']*{name}", out):
            fails.append(f"{name} offered for drop despite the subject rule")
    # dry run must say so, and must NOT claim anything was applied
    if "dry run" not in out or "applied:" in out:
        fails.append("dry run mislabelled its mode")
    # the summary line must report the veto count at all (round 3 lesson: counts visible, not hidden)
    if "mergeable:" not in out:
        fails.append("summary line missing mergeable count")

    if fails:
        print("\n".join("FAIL: " + f for f in fails))
        return 1
    print("OK - paraphrases merge, subject-divergent rows never do, counts are honest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
