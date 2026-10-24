#!/usr/bin/env python3
"""Offline tests for tools/m4_catalog.py - no server, no GPU: a fixture store and its plans.

The rules under test (Phase 1, 2026-10-24, the owner's option 3):
  * the label is what the rows SHARE: a token in 5 of 6 rows outranks a token in 1 of 6,
    however loud the lone row is (rank = rows-containing, then frequency, then alphabetical);
  * determinism: the same store plans the same labels twice (the sweep must not churn);
  * target law: a shelf below --min earns no label; the top-24 view is the only view;
  * maintenance law: a stale label surfaces as MOVED; after the move lands, the next plan
    says NOTHING (no supersession growth for a quiet tick); a shelf outside the view keeps
    its old catalog row (the store never erases).
Run: python3 tools/test_m4_catalog.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from m4_catalog import keywords, plan  # noqa: E402

AI_ROWS = [  # five rows share gpu+vram; one row shouts about a zeppelin
    "The RTX card has 24 GB vram and a gpu boost clock.",
    "Under load the gpu fans spin and vram climbs to 22 GB.",
    "A second gpu would share the vram budget poorly.",
    "Gpu drivers pinned; the vram floor is untouched.",
    "Measuring gpu clocks needs a warm box, the vram reads quiet.",
    "The zeppelin of the century was gpu-less, said one loud row.",
]
KL_ROWS = [  # a three-row drawer: below the min, never labeled
    "Klornish is a fictional metropolis.",
    "Klornish has towers by the river.",
    "Klornish appears in the user's fiction.",
]


def make_store(dirpath: str) -> int:
    """The m2 pattern: a REAL fixture store (MemoryService starts a cluster in the tmp dir and
    its close stops it); the stale AI label is a MEM/CAT row in the user scope, as the product
    writes it. Returns the stale row's id for the move-then-same leg."""
    from serve.memory import MemoryService
    m = MemoryService(dirpath)
    for text in AI_ROWS:
        m.remember(text, scope="default", source="fixture", klass="AI")
    for text in KL_ROWS:
        m.remember(text, scope="default", source="fixture", klass="EVAL")
    m.remember("MEM - postgres, store, rows", scope="user", source="fixture",
               klass="MEM/CAT", guard=False)   # a shelf outside the view: keep, never erase
    stale = m.remember("AI - vram, clocks, drivers, zeppelin, boost, budget", scope="user",
                       source="fixture", klass="MEM/CAT", guard=False)
    m.close()
    return stale["id"]


def main() -> int:
    # unit: the keyword rank reads the share and ignores the shout
    label = keywords(AI_ROWS)
    assert label.startswith("gpu, vram"), f"shared tokens must lead the label: {label}"
    assert "zeppelin" not in label, "one loud row must not set the subject"
    assert label == keywords(AI_ROWS), "labels are deterministic"

    with tempfile.TemporaryDirectory() as td:
        store = f"{td}/store"
        Path(store).mkdir()
        make_store(store)
        p = plan(store, min_rows=5, top=24)
        targeted = {c for c, _n in p["targets"]}
        assert "AI" in targeted, "the six-row drawer is in the view"
        assert "EVAL" not in targeted, "a three-row drawer earns no label"
        assert not p["new"], "the fixture has no unlabeled drawer in the view"
        moved = {c: (old, new) for _i, c, old, new in p["moved"]}
        assert set(moved) == {"AI"}, f"only the stale label moves: {moved}"
        assert moved["AI"][1].startswith("gpu, vram"), moved["AI"]
        assert "MEM" in p["kept"], "a shelf outside the view keeps its catalog row"
        p2 = plan(store, min_rows=5, top=24)
        assert p == p2, "two passes on one store plan identically"

        # the move lands the way the tool applies it (remember fresh + invalidate by=), and the
        # NEXT plan says nothing: a quiet tick grows no chains. Direct store verbs here because
        # the offline test owns no HTTP server; apply() speaks the same two ops over the API.
        from serve.memory import MemoryService
        m = MemoryService(store)
        fresh = m.remember(f"AI - {moved['AI'][1]}", scope="user", source="fixture",
                           klass="MEM/CAT", guard=False)
        m.invalidate(p["moved"][0][0], fresh["id"])   # positional like the server: (memory_id, by_id)
        m.close()
        p3 = plan(store, min_rows=5, top=24)
        assert not p3["moved"] and not p3["new"] and p3["same"] >= 1, \
            f"after the move a quiet pass must plan nothing: {p3}"
        assert len(p3["kept"]) == 1 and "MEM" in p3["kept"]
    print("test_m4_catalog: label rank, determinism, target law, move-then-same - all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
