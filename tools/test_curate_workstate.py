#!/usr/bin/env python3
"""tools/test_curate_workstate.py - the journal-not-store filter, standalone.

The corpus this filter is cut from is the 2026-10-17 shelving audit: the store carried ~100
active rows of WORK-STATE - row counters ("store 381"), commit snapshots ("remote head at
commit 0f172e63"), session-local narration ("during this session"), in-progress plans ("plans
to implement the router") - every one true when journaled and a lie within the day. Three of
them were extracted from that audit's OWN mid-flight words and retired the same hour. Every
string below is real, from one side of the line or the other.

    python3 tools/test_curate_workstate.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curate_loop import WORK_STATE

SKIP = [  # real work-state rows the store carried and the cleanup retired
    "The store currently contains 2,655 active rows.",
    "The project state was pushed as commit 24e0c909 with a status of 80 + 4 + 8.",
    "The user restarted their dev server during this session.",
    "The assistant is building a query-shelf router.",
    "The user plans to implement the router after rebooting.",
    "The current system remote head at commit 0f172e63.",
    "The venue re-record is queued behind the RAM upgrade.",
    "On 2026-10-16, the assistant was building out a system where the map file is a journal.",
    "The store's memory system currently contains 2,801 memories and 58 pending turns.",
    "The project involves building and coding in other components before running an arm c test.",
    "The dev server uses a store named 'memory-eval' containing 2,801 rows.",
    "The store at this moment holds 2,550 active vectors.",
]

KEEP = [  # real durable facts - the filter must not eat these
    "The user performs all server reboots personally; the assistant signals ready or HOLD.",
    "Decided 2026-10-16: PostgreSQL is the base of Strata Mnemosyne (ADR-002).",
    "The project includes a module named curate_loop.py.",
    "Family-based admissions have accounted for roughly 60-65 percent of all legal permanent "
    "residents since the 1980s.",
    "The user lives at 8057 S 500 W Claypool, Indiana.",
    "On 2026-10-16 the dev build was updated to Strata 0.1.44.1.",
    "The user's name is Eugene and the assistant persona is named Clio.",
    "The memory system is ON by default in this fork; opt-out is memory.enabled=false.",
    "journal_turn now updates the newest same-user_text row per scope.",
    "The 128 GB RAM upgrade arrives around 2026-10-22.",
]


class WorkStateFilter(unittest.TestCase):
    def test_work_state_is_refused(self):
        for s in SKIP:
            self.assertTrue(WORK_STATE.search(s), f"work-state stored anyway: {s!r}")

    def test_durable_facts_pass(self):
        for s in KEEP:
            self.assertFalse(WORK_STATE.search(s), f"durable fact refused: {s!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
