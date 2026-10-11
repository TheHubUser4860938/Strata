# Engine patch draft (window-time build candidates) - design + code sketches

STATUS: sketches, not applied diffs. Line anchors were verified against the source in-tree
(memory-core @ 9134098f) during design review on 2026-10-11; the actual diffs are generated
against the build tree in the coordinated window (and this whole directory is moot for D2,
which runs on the zero-C++ COPY venue - see ../ple-overlay/NOTES.md section 5 and
../../window/README.md). Everything below is env-gated by design: with no env var set, every
touched path is byte-identical to the release behavior (fork rule), and nothing here is on
any hot path when off.

Contents:
- delta_overlay_sketch.cpp  - PLE delta sidecar at the one decode chokepoint (D2-C venue,
                              the shippable form of PLE writes)
- kvprobe_sketch.md         - Spike B's dump/graft surface: where QSA KV lives, what session
                              files already do, and the two C++ hooks K1/K2 would add
- DECISIONS.md              - what this session's measurements changed about the patch design
