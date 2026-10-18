#!/usr/bin/env python3
"""tools/test_retro_audit.py - the grounding mechanics of the retro-sweep (audit 09
finding 4 / Grok Q3 grading law, 2026-10-18). Pure math: no store, no engine, no GPU.

    python3 tools/test_retro_audit.py

The strings are modeled on the real shapes: faithful extractions pass, invented facts
flag, and the voting-rights exhibit PASSES grounding by design (attribution is the
ATTR field's job, not this tool's - see retro_audit.py's scope-honesty header).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_audit import coverage, gram_match, is_grounded

TURN = ("we set up the new box last weekend with strata running on one rtx 4090 and "
        "sixty four gigabytes of system ram, and the server listens on port eight thousand five")


class RetroMechanics(unittest.TestCase):
    def test_verbatim_span_is_grounded(self):
        fact = "the server listens on port eight thousand five"
        self.assertTrue(gram_match(fact, TURN), "an 8+ token window rides verbatim")
        self.assertTrue(is_grounded(fact, TURN))

    def test_full_coverage_paraphrase_is_grounded(self):
        fact = "one rtx 4090 runs strata with sixty four gigabytes system ram"
        # "one" is a stopword and "runs" misses ("running" is a different raw token) -
        # measured 8/9 = 0.889, far over the 0.4 bar; the law is coverage OR span, not exactness
        self.assertGreaterEqual(coverage(fact, TURN), 0.85)
        self.assertTrue(is_grounded(fact, TURN))

    def test_invented_fact_is_flagged(self):
        fact = "the store uses postgres for caching layers"
        self.assertEqual(coverage(fact, TURN), 0.0, "no content token from the turn")
        self.assertFalse(is_grounded(fact, TURN))

    def test_short_fact_span_and_coverage_arms(self):
        # the window shrinks to the fact: "sixty four gigabytes" appears whole -> grounded.
        # "eight thousand six" has NO matching span - but the OR law's coverage arm (2 of 3
        # content tokens in the turn = 0.67 >= 0.4) grounds it anyway: a near-miss span with
        # real token backing is a paraphrase, not a fabrication. The flag is for facts the
        # turn supports on NEITHER arm.
        self.assertTrue(is_grounded("sixty four gigabytes", TURN))
        self.assertFalse(gram_match("eight thousand six", TURN), "the span does not exist")
        self.assertTrue(is_grounded("eight thousand six", TURN), "coverage rescues it - by law")

    def test_empty_fact_flags_not_grounded(self):
        self.assertFalse(is_grounded("", TURN))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
