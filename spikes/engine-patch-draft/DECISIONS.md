# DECISIONS - what this session's measurements changed about the patch design

1. **D2 needs no C++ at all.** The copy venue (patched shard COPY + `--ple-gguf`, stock-boot
   D3 cross-check) answers the calibration question with zero engine code, proven boot-compatible
   by the measured exact-fill check (fill-difference 0, no runtime content-hash consumer).
   The C++ overlay below is the SHIPPABLE form, but it only gets build time after D2 says yes.
2. **The Q8 block scale must be decoded, not skipped.** A delta stored as raw Q8 int8 codes
   cannot be added to the base row's int8 codes: each 32-value block has its own f16 scale, and
   `dequantize_q8_0` is value = f16(scale) x int8 (grammar re-read this session from the same
   definition `tools/strata_session`'s sibling mirror uses - `spikes/ple-overlay/ple_ngram.py
   q8_dequant`, oracle-checked). A naive byte sum would corrupt every combined row by a ratio
   up to the scale ratio of the two blocks. So the combine happens AFTER dequant, on floats.
3. **Sidecar row bound is 320,001,446, not 320,001,536.** Recomputed from the artifact
   constants this session: head bands chain contiguously (offsets == running sums of vocabs,
   verified True) and the last band ends at 320,001,446; the remaining 90 rows are unreachable
   by ANY n-gram hash (a delta written there loads fine but is read as zeros forever - exactly
   the silent D2/D3 failure mode). The loader must refuse rows at or above the band top.
4. **K8V4 makes grafted KV rows position-free on the K and V side** (recon: `core/layer.hpp:222`
   "K INT8 unrotated (the scores stay exact)"; V rotated-Q4 via Hadamard, no RoPE), so Spike B's
   front-graft does NOT need the HF panel's RoPE re-phase for KV bytes; the position-bound parts
   are the INDEXER pooled keys (`kernels/cuda/qsa.cu:225-242` rotate by block position) and the
   GDN blob (opaque, order-entangled - replay, don't graft). The dense-model re-phase story in
   spikes/mam-dc6 is thus NOT the bottleneck here; the indexer-key story is.
5. **Marker choice is settled by the gate experiment** (spikes/mam-dc6, 5cb497e0): delimiter
   rows carry the winning marker shape (13 paired wins, 0 losses, p=0.00024, biggest in the
   decayed regime); HDR inverts at bank 80 and CTX loses to incremental at scale - the engine
   patch does not need header-text or one-pass-rebuild machinery to be useful.
