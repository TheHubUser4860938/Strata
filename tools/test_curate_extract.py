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
from curate_loop import parse_extract_line, fact_class, _state_pick

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

    def test_assistant_side_quote_never_becomes_a_user_belief(self):
        # Grok round-3 Q1a: the soul speaks, the soul speaks gets journaled, the journaled
        # soul gets re-extracted - and a quote from the ASSISTANT side of the turn is not
        # the user's words no matter how verbatim it is.
        ln = ("FACT: the assistant insists the apparatus is not the text || "
              "PROBE: what does the assistant insist about texts? || GOLD: the apparatus "
              "is not the text || SCOPE: project || ATTR: user || "
              "QUOTE: the apparatus is not the text")
        c = parse_extract_line(ln, "please log my standing rule about quotes",
                               "Understood: the apparatus is not the text.")
        self.assertEqual(c["quote_side"], "assistant", "the span is real but it is the agent's own line")
        self.assertEqual(c["attr"], "unverified", "ATTR=user loses the claim on the side check")
        # the same span under observed is honest - it IS behavior the conversation shows
        ln2 = ln.replace("ATTR: user", "ATTR: observed")
        c2 = parse_extract_line(ln2, "please log my standing rule about quotes",
                                "Understood: the apparatus is not the text.")
        self.assertEqual(c2["attr"], "observed")
        self.assertEqual(c2["quote_side"], "assistant")

    def test_user_attr_with_verbatim_quote_survives(self):
        ln = ("FACT: the owner reboots the box personally || PROBE: who reboots the box? || "
              "GOLD: the owner || SCOPE: user || ATTR: user || "
              "QUOTE: I always reboot it myself")
        c = parse_extract_line(ln, "I always reboot it myself, never you", "ok")
        self.assertTrue(c["quote_ok"])
        self.assertEqual(c["attr"], "user")


class Lanes(unittest.TestCase):
    """The item-4 lanes (2026-10-23): taboo = the user's negative constraints, state = the
    machine's self-reported condition. Grammar, shelves, and the same-key supersede - all pure,
    no engine, no store. Both lanes fit the existing axes: they ride provenance (trust), a
    decimal SUBCLASS of an existing top (class), and a named durability rule - no fifth axis."""

    def line(self, f, s="user", a="user", l="fact", q="the room pushed back hard"):
        return (f"FACT: {f} || PROBE: p? || GOLD: g || SCOPE: {s} || ATTR: {a} || LANE: {l} || "
                f"QUOTE: {q}")

    def test_taboo_lane_parses_and_shelves_a_rule(self):
        c = parse_extract_line(self.line("The user never wants the server restarted unasked",
                                         l="taboo"), TURN_U, TURN_A)
        self.assertEqual(c["lane"], "taboo")
        self.assertEqual(fact_class(c["fact"], c["scope"], c["lane"]), "USR/TABOO")

    def test_ops_taboo_when_the_rule_governs_operations(self):
        self.assertEqual(fact_class("never run a second server for probing", "project", "taboo"),
                         "OPS/TABOO")

    def test_state_lane_carries_a_key_and_shelves_ops_state(self):
        c = parse_extract_line(self.line("engine = 0.1.48 (2026-10-23)", s="project",
                                         a="observed", l="state:engine", q="engine 0.1.48"),
                               TURN_U, TURN_A)
        self.assertEqual((c["lane"], c["state_key"]), ("state", "engine"))
        self.assertEqual(fact_class(c["fact"], c["scope"], c["lane"]), "OPS/STATE")

    def test_pre_lane_line_stays_facts_byte_unchanged(self):
        c = parse_extract_line("FACT: the room pushed back || PROBE: p? || GOLD: g || "
                               "SCOPE: project || ATTR: observed || QUOTE: pushed back",
                               TURN_U, TURN_A)
        self.assertEqual(c["lane"], "fact")
        self.assertEqual(c["state_key"], "")

    def test_garbled_lane_demotes_never_drops(self):
        c = parse_extract_line(self.line("the room pushed back", l="wisdom"), TURN_U, TURN_A)
        self.assertIsNotNone(c)
        self.assertEqual(c["lane"], "fact")

    def test_state_without_a_key_is_an_ordinary_fact(self):
        c = parse_extract_line(self.line("engine = 0.1.48", s="project", a="observed",
                                         l="state", q="engine 0.1.48"), TURN_U, TURN_A)
        self.assertEqual(c["lane"], "fact", "a key that cannot match a future key earns no rule")

    def test_state_pick_supersedes_the_oldest_canonical_pre_batch_row(self):
        rows = [(7, "engine = 0.1.43 (2026-10-16)", 100.0),
                (9, "Engine: 0.1.44", 200.0),
                (11, "engine = 0.1.46", 300.0),                # this batch: guard excludes it
                (13, "the engine, 0.1.40, was still young", 50.0),   # prose: no key, no claim
                (15, "port = 8005", 60.0)]                     # other key
        self.assertEqual(_state_pick("engine", rows, min_created=250.0), 7,
                         "oldest pre-batch canonical row first - the chain heals one hop per look")
        self.assertIsNone(_state_pick("ram", rows, min_created=0.0), "no canonical row, no supersede")


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
