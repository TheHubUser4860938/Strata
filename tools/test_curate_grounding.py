#!/usr/bin/env python3
"""Offline unit tests for the curation extractor's date grounding - the cheapest item on the tier-3
backlog and the one that attacks the worst measured category (temporal reasoning, 0.465).

The change: the extract prompt now carries the turn's own observation date and tells the model to write
ABSOLUTE dates into facts. It costs no extra ask and no retrieval compute - it lengthens one prompt - and
it exists because a rendered memory line is `[n] (scope) text` with no date in it at all, so a fact that
does not name its own day cannot be ordered by the reader even when it is present.

The control arm matters more than the change: `--no-date` must reproduce the pre-grounding prompt
BYTE-FOR-BYTE, or the A/B measures a rewrite plus a hope instead of one variable. That is what the pinned
SHA below is for - and it is not decorative: the first cut of this edit silently dropped "no numbering, "
(14 characters) while looking identical, which is exactly how a control stops being a control.

    python3 tools/test_curate_grounding.py
"""
import hashlib
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from curate_loop import (EXTRACT_PROMPT, EXTRACT_PROMPT_UNDATED, extract_prompt, observed_on)

# the shipped prompt every fact now in the store was written with (git HEAD before the grounding)
OLD_PROMPT_SHA = "e7c8fa6b64114ee04e36946de02927136eb56471925732dec6ccbd49833e7f93"

TURN = {"user": "we shipped the fix yesterday and closed the Carcassonne ticket",
        "assistant": "noted - I'll remember that", "created_at": 1760486400.0}


class ControlArmStaysByteIdentical(unittest.TestCase):
    def test_undated_prompt_is_the_shipped_bytes(self):
        self.assertEqual(hashlib.sha256(EXTRACT_PROMPT_UNDATED.encode()).hexdigest(), OLD_PROMPT_SHA)

    def test_undated_call_carries_no_date_paragraph(self):
        p = extract_prompt(TURN, 3, dated=False)
        self.assertNotIn("DATES:", p)
        self.assertNotIn("{observed}", p)          # formatted, not left dangling
        self.assertIn("No other lines, no numbering, no commentary.", p)

    def test_control_is_the_same_length_as_the_prompt_on_record(self):
        self.assertEqual(len(EXTRACT_PROMPT_UNDATED), 608)


class DatedPrompt(unittest.TestCase):
    def test_the_turn_date_reaches_the_model(self):
        want = observed_on(TURN["created_at"])
        self.assertIn(want, extract_prompt(TURN, 3))
        self.assertGreaterEqual(time.strptime(want.split()[0], "%Y-%m-%d").tm_year, 2020)
        self.assertIn(" ", want)                    # date AND weekday: "Friday" needs the weekday

    def test_it_asks_for_absolute_dates_and_forbids_guessing(self):
        p = extract_prompt(TURN, 3)
        for needle in ("YYYY-MM-DD", "yesterday", "last week", "leave it out"):
            self.assertIn(needle, p)

    def test_the_grounding_costs_one_paragraph_not_a_second_ask(self):
        # measured 2026-10-15: +525 chars (the DATES paragraph). The bound is a tripwire, not a
        # claim: a future edit that turns this into a multi-turn context dump changes the ask's
        # cost profile and should fail here. RE-BASED 2026-10-18: the ATTR/QUOTE paragraph
        # (audit 09 finding 4 / Grok Q3) adds a SECOND grounding paragraph, measured +520 on
        # top; the tripwire moved 900 -> 1300 - one paragraph's worth of headroom for the next
        # law, still no license for a context dump.
        delta = len(extract_prompt(TURN, 3)) - len(extract_prompt(TURN, 3, dated=False))
        self.assertGreater(delta, 0)
        # 1300 budget = the DATES paragraph's cost guard (name says it: grounding must stay one
        # paragraph). Raised to 1900 for the LANES paragraph (item 4, 2026-10-23: the taboo +
        # machine-state lanes the practitioners named twice) - still nowhere near a second ask,
        # which would cost another call's latency per turn, not another byte-count.
        self.assertLess(delta, 1900)

    def test_both_arms_see_the_same_conversation(self):
        dated, ctl = extract_prompt(TURN, 3), extract_prompt(TURN, 3, dated=False)
        tail = "CONVERSATION:"
        self.assertEqual(dated[dated.index(tail):], ctl[ctl.index(tail):])

    def test_a_brace_in_user_text_cannot_break_the_template(self):
        t = dict(TURN, user='he said: if (x) { return "last week"; }')
        self.assertIn("{ return", extract_prompt(t, 3))


class MissingStamp(unittest.TestCase):
    def test_no_stamp_says_unknown_rather_than_inventing_a_day(self):
        for bad in (None, "", "not-a-time", 0):
            self.assertIn("unknown", observed_on(bad))
        self.assertNotIn("DATES: this conversation happened on .",
                         extract_prompt({"user": "u", "assistant": "a"}, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
