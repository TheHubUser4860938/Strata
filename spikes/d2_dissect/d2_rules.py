"""spikes/d2_dissect/d2_rules.py - the shared write-policy rules for the v2 plan builder and its
offline gates (one definition, so build and gate cannot drift).

STOPWORD FORM RULE (measured need, FIRING.md §5): v1 skipped stopword POSITIONS by comparing
exact token ids of the standalone-word encodings. Content positions whose token is a SUBWORD
spelling of the same word (` national`, ` food`, ` city`, ` Keeper`) therefore slipped through
and got written - and the Wendfallen CONTROL question read 8 of those overwritten rows live.
The fix compares the token's TEXT with the byte-BPE space marker stripped, lowercased, against
the stop words themselves (exact word forms: an endswith rule over-matches - `-A` ends in `a`,
`ran` ends in `an`).
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "spikes" / "ple-overlay"))
sys.path.insert(0, str(_ROOT / "spikes" / "window"))

import ple_ngram as ple
from build_d2_plan import STOP

STOP_WORDS = {w.strip().lower() for w in STOP}


def make_stop_form(tok):
    """-> f(token_id) -> True if the token spells a stop word (any subword/inflected form of
    it under the strip-and-compare rule). EOS and the no-token sentinel are NOT stopwords."""
    def stop_form(tid):
        if tid in (ple.PLE_EOS, -1):
            return False
        return tok.decode([tid]).replace("Ġ", "").strip().lower() in STOP_WORDS
    return stop_form


def make_rules(tok):
    """-> (is_stop, is_content). A token is MARKER CONTENT only if it is not a stopword form
    AND spells something with a letter in it: the marker aliases contain bare punctuation ids
    ('-'), and treating those as content wrote gold onto the CONTROL's `Codex-keeper` window
    (measured: the ('-', 'ex') window fired for the archivist in all four control venues),
    i.e. it would have voided the verdict the same way a shared stopword n-gram would."""
    is_stop = make_stop_form(tok)

    def is_content(tid):
        if is_stop(tid):
            return False
        t = tok.decode([tid]).replace("Ġ", "")
        return any(ch.isalpha() for ch in t)
    return is_stop, is_content
