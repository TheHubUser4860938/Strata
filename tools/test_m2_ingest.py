#!/usr/bin/env python3
"""tools/test_m2_ingest.py - the fragment gate, standalone, no GPU no network.

The corpus this gate is cut from is the 2026-10-17 manual cleanup: the SDK-guide ingest of
2026-10-12 stored 1,755 rows and the cleanup retired ~150 of them as shards - citations cut
at "U.S." and "Stat.", code collapsed to bare statements, answers to questions the store never
sees. Every string below is a real shape from that store, on one side or the other of the line.

    python3 tools/test_m2_ingest.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2_ingest import doc_title, fragment, reunite, whole_doc

REJECT = [  # real shards the store carried until the cleanup retired them
    "The Immigration Act of 1924 (43 Stat.",
    "from mcp.server.fastmcp import FastMCP",
    "Resource-server surface.",
    "Straightforward and proven.",
    "Constitutional and practical.",
    "Fully aligned with current policy.",
    "print(message.root.params.data)",
    "ListToolsResult) rather than unwrapped values (e.g.",
    'server = Server("my-server", "1.0")',
    "import warnings from mcp import MCPDeprecationWarning",
    "the platform locale (cp1252 on Windows).",
    "§ 214.2(f)(10) and any related provisions.",
    "544.3s Duration 2 Rounds 4 Queries 14 URLs Analyzed",
    "What section do we push first?",
    "Q7: Preferences for natural-born citizens in key roles?",
    "They continue to work",
    "Client-credentials extension.",
    "Extracted parameter values containing ..",
    "Unions of multiple primitives (e.g.",
    "It codifies Roosevelt",
    "Two edge cases to check",
    "Discovery and registration helpers.",
    "cursor parameter removed from ClientSession list methods",
    "a DirectDispatcher for in-process embedding).",
    "Note that extra never worked at runtime in v1",
]

KEEP = [  # real facts from the same two documents - the gate must not eat these
    "Family-based admissions have accounted for roughly 60-65 percent of all legal permanent "
    "residents since the 1980s.",
    "Pew shows a record 14 million in 2023.",
    "Legal immigration levels roughly tripled in the decades that followed.",
    "Current officeholders finish their terms.",
    "The rebuilt bridge reopened in 1981 with wider lanes and a reinforced pier.",
    "Annual certifications and harsh clawbacks prevent abuse.",
    "ARSA supercharges the existing near-shoring trend.",
    "Single-mother households in particular surged dramatically.",
    "ARSA Titles V and VI do not impose private belief.",
    "A high-trust society requires a shared moral framework.",
    "The user's Odysseus system uses BGE large embeddings for fast recall.",
    "Title IV of the act bans individual security trading by officeholders.",
]


class FragmentGate(unittest.TestCase):
    def test_real_shards_are_rejected(self):
        for s in REJECT:
            self.assertTrue(fragment(s, "sentence"), f"shard stored anyway: {s!r}")

    def test_real_facts_are_kept(self):
        for s in KEEP:
            self.assertFalse(fragment(s, "sentence"), f"fact rejected: {s!r}")

    def test_abbreviation_splits_reunite(self):
        got = reunite(["The Immigration and Nationality Act (8 U.S.", "C. 1101 governs visas."])
        self.assertEqual(len(got), 1, "the citation must survive as one unit")
        self.assertIn("U.S.", got[0])

    def test_reunite_does_not_glue_normal_sentences(self):
        got = reunite(["Rania saw a 210-foot whale.", "What percentage of it is remora?"])
        self.assertEqual(len(got), 2, "a real sentence boundary stays a boundary")


class WholeDoc(unittest.TestCase):
    """The 2026-10-17 redesign: one document, one memory. The file is copied, the store keeps
    a pointer - title, size, content hash, path, topics - and nothing is split."""

    def _doc(self, d, name, body):
        import pathlib
        src = pathlib.Path(d, name)
        src.write_text(body, encoding="utf-8")
        return src

    def test_pointer_carries_title_hash_path_and_topics(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            src = self._doc(d, "guide.md", "# Zephyr Migration Guide\n\n"
                            + "The zephyr client handles streamable transports and ziggurat auth flows. " * 30)
            info = whole_doc(str(src), str(pathlib.Path(d, "Documents")))
            self.assertIn("Zephyr Migration Guide", info["pointer"])
            self.assertIn("sha256", info["pointer"])
            self.assertIn(str(pathlib.Path(d, "Documents")), info["pointer"])
            self.assertIn("read ", info["pointer"], "the pointer tells the model to read the file")
            self.assertTrue(info["dest"].endswith(".md"))
            self.assertLess(len(info["pointer"]), 4000, "the pointer is one fact, store-capped")

    def test_content_named_and_idempotent(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            a = self._doc(d, "a.md", "# Same\n\nbody text here. " * 20)
            b = self._doc(d, "b.md", "# Same\n\nbody text here. " * 20)
            ia, ib = whole_doc(str(a), d), whole_doc(str(b), d)
            self.assertEqual(ia["dest"], ib["dest"], "identical content files to the same name")
            c = self._doc(d, "c.md", "# Same\n\nchanged body. " * 20)
            self.assertNotEqual(whole_doc(str(c), d)["dest"], ia["dest"],
                                "changed content refiles honestly - the sha is the identity")

    def test_title_falls_back_to_first_line_then_filename(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(doc_title("x.md", "# Head\n\ntext"), "Head")
            self.assertEqual(doc_title("x.md", "No heading here.\n\nmore"), "No heading here.")
            self.assertEqual(doc_title(str(Path(d) / "plainfile.md"), ""), "plainfile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
