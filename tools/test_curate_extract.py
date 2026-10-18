#!/usr/bin/env python3
"""tools/test_curate_extract.py - the ATTR/QUOTE extractor honesty (audit 09 finding 4,
Grok Q3 spec, 2026-10-18). Pure parse + grounding law: no engine, no store, no GPU.

    python3 tools/test_curate_extract.py

The exhibit is the live laundering this change exists to stop: a voting-rights position
harvested from a debate turn and stored as "the user believes X". Every string below is
real or modeled on real; TURN_* is the exhibit shape.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curate_loop import parse_extract_line

TURN_U = ("we replayed the debate clip in class - Maria argued that women should not "
          "have the right to vote, and the room pushed back hard")
TURN_A = "Noted: Maria's position was anti-suffrage; the class disagreed."


class ExtractHonesty(unittest.TestCase):
    NEW = ("FACT: Maria argued against women's suffrage in the class debate || "
           "PROBE: what did Maria argue in the class debate? || GOLD: against women's "
           "suffrage || SCOPE: project || ATTR: attributed || "
           "QUOTE: Maria argued that women should not have the right to vote")

    def test_new_line_parses_with_attribution_and_verbatim_quote(self):
        c = parse_extract_line(self.NEW, TURN_U, TURN_A)
        self.assertIsNotNone(c)
        self.assertEqual(c["attr"], "attributed")
        self.assertTrue(c["quote_ok"], "the quote is a contiguous span of the turn")

    def test_attributed_never_becomes_a_belief(self):
        c = parse_extract_line(self.NEW, TURN_U, TURN_A)
        self.assertNotEqual(c["attr"], "user", "a discussed position is not a held belief")

    def test_user_attr_without_a_matching_quote_is_demoted(self):
        ln = ("FACT: the user believes women should not have the right to vote || "
              "PROBE: what does the user believe about voting? || GOLD: against suffrage || "
              "SCOPE: user || ATTR: user || QUOTE: the user firmly believes in no votes for women")
        c = parse_extract_line(ln, TURN_U, TURN_A)   # the quote is a PARAPHRASE of Maria's words
        self.assertFalse(c["quote_ok"], "the model's rewrite is not a contiguous span")
        self.assertEqual(c["attr"], "unverified", "ATTR=user without grounding loses the claim")

    def test_old_format_still_parses_honestly(self):
        ln = ("FACT: the box runs Strata on one RTX card || PROBE: what hardware runs Strata? || "
              "GOLD: one RTX card || SCOPE: project")
        c = parse_extract_line(ln, "the box runs Strata on one RTX card", "ok")
        self.assertIsNotNone(c, "pre-ATTR lines must not start vanishing from the queue")
        self.assertEqual(c["attr"], "unverified")
        self.assertFalse(c["quote_ok"])

    def test_garbled_attr_falls_to_unverified(self):
        ln = self.NEW.replace("ATTR: attributed", "ATTR: probably")
        c = parse_extract_line(ln, TURN_U, TURN_A)
        self.assertEqual(c["attr"], "unverified")

    def test_idk_gold_still_dies(self):
        ln = ("FACT: the flibber reads torque || PROBE: what does the flibber read? || "
              "GOLD: idk || SCOPE: project || ATTR: user || QUOTE: the flibber reads torque")
        self.assertIsNone(parse_extract_line(ln, "the flibber reads torque", "x"))

    def test_user_attr_with_verbatim_quote_survives(self):
        ln = ("FACT: the owner reboots the box personally || PROBE: who reboots the box? || "
              "GOLD: the owner || SCOPE: user || ATTR: user || "
              "QUOTE: I always reboot it myself")
        c = parse_extract_line(ln, "I always reboot it myself, never you", "ok")
        self.assertTrue(c["quote_ok"])
        self.assertEqual(c["attr"], "user")


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
