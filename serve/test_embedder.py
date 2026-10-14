"""serve/test_embedder.py - the bge-small pins and a semantic smoke, offline-capable.

The pin test needs no model (a fake dir with wrong SHA256SUMS must raise before anything
loads - a silently swapped embedder silently changes every ranking in the store). The real-
model smoke SKIPS cleanly when the files are absent, so CI without the 133 MB download still
passes; on the dev box (downloaded 2026-10-14, pinned in SHA256SUMS) it asserts the exact
behavior the temporal diagnostic demanded: a paraphrased query ranks its decision note over
unrelated facts with ZERO lexical overlap.
"""
import tempfile
import unittest
from pathlib import Path

from serve.embedder import Embedder, _verify_pinned

MODEL_DIR = Path(__file__).resolve().parent.parent / "Strata-data/embeddings/bge-small-en-v1.5"


class Pins(unittest.TestCase):
    def test_wrong_sha256_raises_before_any_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "model.onnx").write_bytes(b"not really a model")
            (p / "tokenizer.json").write_text("{}")
            (p / "SHA256SUMS").write_text("0" * 64 + "  model.onnx\n" + "1" * 64 + "  tokenizer.json\n")
            with self.assertRaises(ValueError) as cm:
                Embedder(p)
            self.assertIn("pin mismatch", str(cm.exception))

    def test_no_pin_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:      # verify passes; the ONNX load is what fails next
            _verify_pinned(Path(d))                   # (no exception about pins from a pinless dir)


@unittest.skipUnless((MODEL_DIR / "model.onnx").exists() and (MODEL_DIR / "SHA256SUMS").exists(),
                     "bge-small files not downloaded on this box")
class RealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.emb = Embedder(MODEL_DIR)

    def test_semantic_ranking_and_norms(self):
        docs = self.emb.embed_docs([
            "The user decided to batch the date-filter parameter implementation into Ladder A",
            "The township of Darnen is located in Stevens County, Minnesota",
            "NVIDIA GeForce RTX 4090 with 24 GB VRAM drives this machine"])
        # the query shares NO rare word with the decision note - pure semantic reach
        q = self.emb.embed_query("which plan was chosen for folding in the hygiene fix?")
        cos = docs @ q
        self.assertGreater(cos[0], cos[1])
        self.assertGreater(cos[0], cos[2])
        self.assertGreater(cos[0], 0.3)
        import numpy as np
        self.assertTrue(np.allclose(np.linalg.norm(docs, axis=1), 1.0, atol=1e-4))
        self.assertEqual(docs.shape, (3, 384))

    def test_query_prefix_changes_the_vector(self):
        import numpy as np
        a = self.emb.embed_query("cascade fix")
        b = self.emb._encode(["cascade fix"])[0]       # unprefixed
        self.assertGreater(float(np.linalg.norm(a - b)), 0.05,
                           "bge asymmetry (query prefix) must be live or ranking degrades quietly")


if __name__ == "__main__":
    unittest.main(verbosity=2)
