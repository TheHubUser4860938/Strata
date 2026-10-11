// spikes/engine-patch-draft/delta_overlay_sketch.cpp - STRATA_PLE_OVERLAY: the shippable form of
// PLE writes (D2-C venue). Build-time form = real diffs generated against the tree in the window;
// this file keeps the design reviewable. Env-gated end to end: unset => every line below is dead.
//
// WHY HERE: recon (see README.md) proved every gathered row - Direct read, mmap pointer, PleReader
// row-cache hit, gather_batch fast/slow, prefetch - funnels through ONE function,
// `PleTable::Impl::decode(row_bytes, out160)` (src/kernels/ngram.cpp L224; call sites
// L455/L463/L499/L531/L543). One chokepoint = one seam.
//
// KEY SEMANTICS (DECISIONS.md 2+3): Q8_0 rows are blocks of (f16 scale, 32 int8); deltas MUST be
// combined AFTER dequantization (float add), never at the int8 level, and sidecar rows at or above
// 320,001,446 (the real band top = offset[15]+vocab[15], recomputed 2026-10-11; the table's
// 320,001,536 rows end in 90 unreachable dead rows) are loader-refused, not silently unreadable.

// ---- sidecar format v1 (produced by spikes/window/emit_delta_sidecar.py in the window) ----
// magic[8] "STRPLED1" | version:u32 | row_dim:u32 (=160) | n_rows_delta:u64 | rows then are
// n_rows_delta x { u32 row; i8 row_dim }  - i8 deltas of Q8 CODES are NOT used; instead:
// n_rows_delta x { u32 row; f32 row_dim }  - float deltas in DEQUANTIZED units, added per element.
// Header hash (session_hash64-style, same hasher as tools/strata_session.py) in the 8 bytes after
// n_rows_delta; loader mmaps PROT_READ, binary-searches by row (immutable, lock-free: issue()
// holds a mutex and gather_batch runs on prefill futures - a mutable map would serialize the hot
// path, an immutable mmap cannot).

// ---- the combine, in prose (this is the whole engine-side change, ~30 lines real diff) ----
// bool PleTable::Impl::decode(const uint8_t* row, uint32_t row_index, float* out160) const {
//     fmt->dequant(row, scale, out160);                       // existing behavior, unchanged
//     if (g_ple_overlay && delta) {                           // env: STRATA_PLE_OVERLAY=<path>
//         const float* d = delta->find(row_index);            // nullptr = no delta for this row
//         if (d) for (int j = 0; j < PLE_HEAD_DIM; ++j) out160[j] += d[j];   // float combine
//     }
// }
//   * row index available at all five call sites today (impl_->rows[h], rows[i], row) - recon
//     confirmed; the diff threads one extra u32 into decode and its 5 callers, nothing else;
//   * key-check semantics live at WRITE time (tools compute which keys land where + the overflow
//     chain, plan 11-7), so the engine side stays a dumb float add - deliberate: hot-path budget
//     is microseconds (ngram.cpp records 2.10-2.61 ms/token for the whole 16-row gather; the
//     delta add is ~160 FADDs, noise), and every collision already lives in this table by
//     design (20M rows/head vs 2^64 keys - NOTES.md 3);
//   * clone-mode (D2 v1) needs NO separate engine path: clone = delta of (dequant(src)-
//     dequant(dst)), one f32 rounding, emitted by the sidecar tool - the COPY venue (zero C++)
//     does exact byte clones for D2 proper, this overlay is for iteration speed later;
//   * overflow chain in the sidecar: rows sorted; `find` = lower_bound + probe forward up to
//     K=8 slots (bounded, lock-free) OR the writer co-locates the chain across the 16 heads of
//     one key (head band index differs => separate rows; chain is per-row, keep v1 single-slot
//     + K-probe, document).

// ---- PleTable::open additions (same file, same env gate) ----
// after the existing exact-fill check (ngram.cpp L296-312, untouched):
//   if (const char* p = std::getenv("STRATA_PLE_OVERLAY")) {
//       impl_->delta = std::make_unique<DeltaTable>();
//       if (!impl_->delta->load(p, err)) { err = "PLE overlay: " + err; close(); return false; }
//   }
// house style for env reads: read-once statics like STRATA_KV_ROT / STRATA_PCIE_FRAC_DEFAULT
// (recon verified the convention); delivery to the engine process = config "env" block
// (serve/server.py L2497) => zero serve changes.
