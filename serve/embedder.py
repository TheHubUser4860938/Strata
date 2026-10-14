"""CPU-only semantic embedder for the memory store (PHASE 4).

The store's lexical recall (FTS5) stays the candidate generator; cosine similarity over
these vectors RERANKS the candidates - the semantic layer that answers "what did the user
decide about the cascade" when the stored note says "batched the date-filter parameter into
Ladder A". The temporal diagnostic (spikes/lme/TEMPORAL_DIAG.md) is the failure study this
exists to fix: multi-evidence questions rank only the lexically-closest event's row on top.

Model: BAAI/bge-small-en-v1.5 (MIT), ONNX Runtime, CPUExecutionProvider only - this never
touches the GPU, which is the engine's. 33M params, 384 dims, 512-token max; a short note
embeds in ~10-20 ms on a modern CPU with the thread cap below. Opt-in by construction: with
no "embedder" object in the config's memory block this module is never imported and the
store behaves byte-identically to before.

The files are pinned by SHA-256 (SHA256SUMS beside them, written at download 2026-10-14):
model.onnx 828e1496... , tokenizer.json d241a60d... . A mismatch raises, it does not warn -
a silently swapped embedder silently changes every ranking in the store.

bge retrieval recipe (from the model card, verified against its config): queries carry the
prefix "Represent this sentence for searching relevant passages: ", documents are embedded
unprefixed, mean pooling over last_hidden_state masked by attention_mask, L2 normalize, dot
product = cosine.
"""
from __future__ import annotations

import hashlib
import numpy as np
from pathlib import Path

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DIMS = 384


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_pinned(model_dir: Path) -> None:
    sums = model_dir / "SHA256SUMS"
    if not sums.exists():
        return                                   # no pin file: nothing to verify against
    for line in sums.read_text(encoding="utf-8").splitlines():
        want, _, name = line.partition("  ")
        f = model_dir / name.strip()
        if not want or not f.exists():
            continue
        got = _sha256(f)
        if got != want.strip():
            raise ValueError(
                f"embedder pin mismatch for {name}: {got[:12]}... != {want.strip()[:12]}... - "
                "a swapped model silently changes every ranking in the store")


class Embedder:
    """Load bge-small ONNX from model_dir and embed text on CPU. threads caps the ORT
    intra-op parallelism so curation backfills never starve the engine's host work."""

    def __init__(self, model_dir: str | Path, threads: int = 4, max_seq: int = 512):
        import onnxruntime as ort                  # lazy: opt-in feature, not a base import
        from tokenizers import Tokenizer           # ditto
        d = Path(model_dir)
        if not d.is_absolute() and not (d / "model.onnx").exists():
            # repo-root relative, the same convention memory.dir follows - a config written with a
            # relative path then works whatever the server's cwd happens to be.
            d = Path(__file__).resolve().parent.parent / d
        _verify_pinned(d)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(d / "model.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_seq)
        self.tok.enable_padding(pad_id=0, pad_token="[PAD]")
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self.max_seq = max_seq
        self.model_name = d.name                    # stats() names what is actually loaded

    def _encode(self, texts: list[str]) -> np.ndarray:
        enc = self.tok.encode_batch(texts)
        ids = np.fromiter((t for e in enc for t in e.ids), np.int64,
                          count=sum(len(e.ids) for e in enc)).reshape(len(enc), -1)
        mask = np.fromiter((t for e in enc for t in e.attention_mask), np.int64,
                           count=ids.size).reshape(ids.shape)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self.sess.run(["last_hidden_state"], feed)[0]        # (n, seq, 384)
        m = mask[..., None].astype(np.float32)
        vecs = (out * m).sum(1) / np.clip(m.sum(1), 1e-9, None)    # masked mean pooling
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return (vecs / np.clip(norms, 1e-9, None)).astype(np.float32)   # L2: dot = cosine

    def embed_query(self, query: str) -> np.ndarray:
        return self._encode([QUERY_PREFIX + (query or "")])[0]

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        """Documents embed unprefixed (bge recipe). Chunked to bound peak memory on
        backfills; order is preserved."""
        if not texts:
            return np.zeros((0, DIMS), np.float32)
        return np.vstack([self._encode(chunk) for chunk in _chunks(texts, 64)])


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]
