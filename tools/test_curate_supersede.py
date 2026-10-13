#!/usr/bin/env python3
"""Offline unit tests for the supersede target picker - the 2026-10-12 Westhope->Pruetz->Arthur
cascade post-mortem. No HTTP, no server: _pick_target and _probe_shape are pure functions.

    python3 tools/test_curate_supersede.py
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from curate_loop import _pick_target, _probe_shape

AIR = "The FAA location identifier for {} Municipal Airport is {}."


def airport_rows():
    """The live shape from the evening of 2026-10-12: 211 (Pruetz "was 5K9", the true active row,
    0 hits), 221 (Arthur 1A2), 337 (Westhope assigned D64), 1625 (the sweep's fresh Westhope row,
    9 hits) - the old connective match pointed a Pruetz correction at 1625 because faa/location/
    identifier/municipal sat at df 3-6 and looked rare."""
    return [
        (211, AIR.format("Pruetz", "5K9").replace(" is ", " was "), 0, 100.0),
        (221, AIR.format("Arthur", "1A2"), 1, 100.0),
        (337, "Westhope Municipal Airport is assigned the FAA location identifier D64.", 2, 100.0),
        (1625, AIR.format("Westhope", "D64"), 9, 100.0),
    ] + [(900 + i, f"an unrelated radio station row number {i} about a minnesota township county",
          1, 100.0) for i in range(6)]


class SupersedeTarget(unittest.TestCase):
    def test_subject_rule_anchors_the_match(self):
        # the cascade case: a Pruetz correction must target 211 (shares the unique token
        # "pruetz"), never 1625 (shares only corpus connectives) - even though 1625 has 9 hits
        old, shared, _ = _pick_target(AIR.format("Pruetz", "5K9"), airport_rows())
        self.assertEqual(old, 211)
        self.assertGreaterEqual(shared, 2)

    def test_two_rows_on_one_subject_stay_conservative(self):
        # westhope sits in 337 AND 1625 (df 2): no unique-subject anchor, so the picker returns
        # None and the fact stores fresh - the merge review (a human) sorts duplicates; the
        # write path and the read path never guess
        old, _, _ = _pick_target(AIR.format("Westhope", "D64"), airport_rows())
        self.assertIsNone(old)

    def test_siblings_are_not_targets(self):
        sib = [(1626, "The FAA identifier for Pruetz Municipal Airport is 5K9.", 12, 105.0)]
        # unprotected, the fresh sibling lifts df(pruetz) to 2 and the anchor dies (the old bug
        # did worse: it TARGETED the sibling); with min_created past it, 211 is the target again
        self.assertIsNone(_pick_target(AIR.format("Pruetz", "5K9"), airport_rows() + sib)[0])
        self.assertEqual(_pick_target(AIR.format("Pruetz", "5K9"),
                                      airport_rows() + sib, min_created=101.0)[0], 211)

    def test_true_contradiction_still_targets(self):
        rows = [(7, "Radio KCVN is licensed to Cozad, Nebraska.", 3, 10.0),
                (8, "An unrelated township county quiz row about Minnesota.", 1, 10.0)]
        old, _, _ = _pick_target("Radio KCVN is licensed to Frisco, Texas.", rows)
        self.assertEqual(old, 7, "a different value under the same subject IS a correction")

    def test_equal_two_way_match_is_ambiguous(self):
        rows = [(7, "The zeta probe reports alpha uniqueone.", 1, 10.0),
                (8, "The zeta probe reports beta uniquetwo.", 1, 10.0)]
        old, shared, amb = _pick_target("The zeta probe reports uniqueone and uniquetwo.", rows)
        self.assertIsNone(old, "two rows match equally: ambiguous, store as a new fact")
        self.assertEqual(sorted(amb), [7, 8])
        self.assertGreaterEqual(shared, 2)

    def test_one_shared_rare_word_is_a_coincidence(self):
        rows = [(7, "The pruetz municipal airport broadcasts on 1180.", 5, 10.0)]
        # pruetz is unique (anchor ok) but the intersection is 1 token and does not dominate the
        # smaller payload: "broadcasts" retired an unrelated fact once, in review - never again
        old, _, _ = _pick_target("The pruetz township census recorded 411 people.", rows)
        self.assertIsNone(old)


class ProbeShape(unittest.TestCase):
    def test_quiz_format_is_probe(self):
        self.assertTrue(_probe_shape("What is the FAA identifier for Westhope?", "D64."))
        self.assertTrue(_probe_shape("Which county?", "Itasca County."))

    def test_prose_answers_and_statements_are_not(self):
        self.assertFalse(_probe_shape(
            "What is the identifier?",
            "It is D64, assigned by the FAA to the small municipal airport in North Dakota near "
            "the Canadian border, and the runway is changing."))
        self.assertFalse(_probe_shape("My middle name is Eugene", "Noted and stored."))
        self.assertFalse(_probe_shape("", ""))


if __name__ == "__main__":
    unittest.main()
