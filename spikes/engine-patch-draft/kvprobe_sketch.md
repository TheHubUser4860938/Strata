# STRATA_KVPROBE (Spike B) - dump/graft the 12 QSA layers' KV as cartridges

Env-gated (`STRATA_KVPROBE_DIR=<dir>`, read-once static per house style); unset => the serving
path is the untouched release path. This is the SMALLER of the two experiments' engine surface,
because most of it already exists (that is the finding):

## What the engine already gives us, verified this session

- **A full conversation snapshot format + writer/reader**: `SAVE/RESTORE <path>` on the engine
  stdin protocol (`generate.cpp` ~L9524-9534), file = 64-B header (`STRSESS\x01`, identity binds
  model/config/geometry by `session_hash64`) + payload (live.ids, GDN blob, PLE rows, tails,
  dead, block_pos, used + per-KV-layer sections: header `format/cells/heads/head_dim/page_size/
  pooled_rows/idx_dim` + 5 part buffers k, v, k_scale, v_scale, pooled) + 8-B payload hash +
  `STRSEND\x01` (`src/core/conversation_file.cpp`, header/trailer grammar read off the source).
  `tools/strata_session.py` parses, verifies, GRAFTS (ids splice) and byte-surgery-patches
  (`--replace-kv`) these files with correct re-signing — proven against the engine's own golden
  fixture (byte-identical rebuild + re-verified hashes; engine-side reader accepted nothing we
  touched, but every write we make re-computes both hashes exactly like the engine does).
- **K8V4 positions**: K lives as INT8 **unrotated** (`core/layer.hpp` L222: "the scores stay
  exact"), V as Hadamard-rotated Q4_0 — NEITHER is RoPE'd, so front-grafting KV rows does NOT
  need per-token re-phasing of K/V (the dense-7B MAM story's re-phase step largely dissolves on
  this architecture — the surprise finding of Spike B recon).
- **What DOES carry position**: the indexer pooled keys — rotated by the BLOCK's first cell
  position (`kernels/cuda/qsa.cu` L225-242, mrope) — and the GDN recurrent blob (order-entangled,
  opaque; `session.cpp` zero-fills it for a fresh conversation, restore copies it verbatim).

## The two small hooks (draft; real diffs in-window if B1 shows the need)

1. **Dump**: after a successful prefill, if `STRATA_KVPROBE_DIR` set, write one cartridge file
   per prompt = the QSA-layer pools for the LIVE EXTENT ONLY (cells = n_kv rows, 12 QSA layers +
   draft) + live.ids + block_pos — reuse `conversation_kv_save`'s existing pools()/transfer
   machinery (`src/core/conversation_snapshot.cpp` ~L147-181, the same code the SAVE command
   uses; the hook is a subset-selection, not new IO).
2. **Graft-at-load**: extend the RESTORE path to accept a cartridge as a PREFIX: prepend rows to
   the 5 parts per layer (`conversation_kv_restore` ~L204-227 — the ONE write point into pools),
   head-splice `live.ids` (extent = ids.size(), so ids MUST be spliced too — the file-side splice
   `tools/strata_session.py --graft` already does exactly this for the ids+metadata; row-level
   splice needs the pool math: page_size 4, rows-per-cell 2 heads x (k: 816 B k8v4 hybrid,
   v_scale...), block_pos +1 per spliced block, pooled indexer rows re-rotated at the shifted
   block positions (the ONE math hook: mrope phase by delta — a few lines against the existing
   rope/indexer tables at load time, NOT in the hot path).
3. GDN policy for grafted prefixes: v1 = replay (restore the GDN blob that was saved WITH the
   cartridge rows, so ids and state agree by construction — the session-file round trip already
   guarantees this); mid-stream grafting without replay is B3, not scheduled.

## Why this order

B1 (spikes/window/probe_driver.py --phase b1) tests the CONTAINER claim first — save, restore,
recall in a fresh conversation, prefix-cache counters tell reuse from re-prefill — with ZERO of
the above hooks (plain SAVE/RESTORE + tools/strata_session.py --graft ids-splice). Only if B1
lands does the 1+2 hook set get built (its build fits the same window: g++-compilable subset
already proven standalone for conversation_file.cpp; nvcc parts = one make target, engine down).
