"""Tests for setup.py's optional Q8_0 PLE n-gram table (the owner's law of 2026-10-23): the pin table,
the family-tree map, and the reuse rule that keeps a box from downloading 54.4 GB twice. No network,
no GPU, nothing installed - the big file is a sparse hole, because a pin is a size as much as a hash.

    python -m unittest tools.test_setup_ple_q8
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import setup  # noqa: E402


class TheQ8Pins(unittest.TestCase):
    def test_every_tree_carries_a_real_pin(self):
        self.assertEqual(sorted(setup.PLE_Q8), ["ista", "unsloth"])
        for tree, (repo, name, size, sha) in setup.PLE_Q8.items():
            self.assertEqual(repo.count("/"), 1, f"{tree}: a Hub repository in owner/repo form")
            self.assertGreater(size, 50 * 1024 ** 3, f"{tree}: a 54 GB-class table, not a shard")
            self.assertEqual(len(sha), 64, f"{tree}: a full sha256")
            self.assertEqual(sha, sha.lower(), f"{tree}: the pin is lowercase hex")
            self.assertTrue(name.endswith(".gguf"), f"{tree}: a GGUF file")

    def test_the_trees_differ_only_in_container_metadata(self):
        # the two trees' files are one trained tensor in two containers: same order of magnitude,
        # different byte counts by design (54,400,261,312 vs 54,400,261,408 - the pin says so)
        sizes = {t: v[2] for t, v in setup.PLE_Q8.items()}
        self.assertNotEqual(sizes["ista"], sizes["unsloth"])
        self.assertLess(abs(sizes["ista"] - sizes["unsloth"]), 1024 ** 2, "96 bytes apart, not a different file")

    def test_the_tree_map_covers_every_family_that_can_take_a_shared_table(self):
        for fam in ("qwen", "coder", "dau", "daureg"):
            self.assertEqual(setup.PLE_Q8_TREE[fam], "ista", f"{fam}: the ista tree's table")
        for fam in ("unsloth", "huihui"):
            self.assertEqual(setup.PLE_Q8_TREE[fam], "unsloth", f"{fam}: the unsloth tree's table")
        for tree in setup.PLE_Q8_TREE.values():
            self.assertIn(tree, setup.PLE_Q8, "no family points at a tree with no pin")

    def test_the_families_with_their_own_table_are_not_mapped(self):
        for fam in ("swift", "gyro", "nvfp4"):     # swift brings a tuned table, gyro carries its own, NVFP4 is FP8
            self.assertIn(fam, setup.FAMILIES, f"{fam}: still a family")
            self.assertNotIn(fam, setup.PLE_Q8_TREE, f"{fam}: no shared Q8 table to fetch")


class TheReuseRule(unittest.TestCase):
    def test_a_done_shard_of_the_pinned_size_is_shared_not_downloaded(self):
        with tempfile.TemporaryDirectory() as t:
            models = Path(t)
            shard = models / "IQ3_S" / "Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
            shard.parent.mkdir()
            with open(shard, "wb") as f:
                f.truncate(setup.PLE_Q8["ista"][2])            # sparse: the size is real, the bytes are holes
            setup.mark(shard)                                  # the box already owns this table, done-marked
            want = models / setup.PLE_Q8["ista"][1]
            with mock.patch.object(setup, "verify_sha256", lambda *a, **k: None), \
                 mock.patch.object(setup, "download", lambda *a, **k: self.fail("it downloaded anyway")), \
                 mock.patch.object(setup, "ok", lambda *a, **k: None):
                got = setup.get_ple_q8(models, "ista")
            self.assertEqual(got, want, "the table takes its pinned name")
            self.assertTrue(want.exists())
            self.assertEqual(want.stat().st_nlink, 2, "a hard link, not a second 54 GB copy")
            self.assertTrue(setup.done(want), "the finish mark moves with the file")

    def test_the_swap_shard_must_be_the_pinned_size(self):
        with tempfile.TemporaryDirectory() as t:
            models = Path(t)
            shard = models / "IQ3_S" / "Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"
            shard.parent.mkdir()
            with open(shard, "wb") as f:
                f.truncate(setup.PLE_Q8["ista"][2] - 4096)     # a size look-alike, one page short
            setup.mark(shard)
            with mock.patch.object(setup, "verify_sha256", lambda *a, **k: None), \
                 mock.patch.object(setup, "download", lambda url, dst: Path(dst).touch()), \
                 mock.patch.object(setup, "hf", lambda repo: "hf://"), \
                 mock.patch.object(setup, "free_gb", lambda p: 999.0), \
                 mock.patch.object(setup, "ok", lambda *a, **k: None):
                setup.get_ple_q8(models, "ista")
            self.assertFalse((models / "IQ3_S" / setup.PLE_Q8["ista"][1]).exists(),
                             "a wrong-size shard is not the table: setup fetched it under its own name")


if __name__ == "__main__":
    unittest.main(verbosity=2)
