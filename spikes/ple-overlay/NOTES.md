# Spike D0 — PLE n-gram addressing: source study record

Phase 2's second research track (plan §11-7, the PLE delta overlay): before writing ANYTHING into the
model's n-gram table at inference time, we establish exactly how that table is addressed and read.
D0 is source study + CPU probes: no GPU, no engine restart, no model load — it runs while production
serves traffic. The instrument is `ple_ngram.py` (same dir): a line-for-line Python mirror of the
engine's own hash and row I/O, plus the raw-byte row patcher D2/D3 will use.

## What was verified, and how

Every claim below was checked THIS ROUND against the engine source in this tree and, where possible,
by running the mirror against committed ground truth. Nothing here is folklore from the Qwen report.

### 1. The addressing function is pure integer math — exact spec, mirrored and oracle-checked

- Engine source: `src/kernels/ngram.cpp` `ngram_mixed` (L63-70) + `ngram_rows` (L72-98); constants
  `ple_artifact_consts()` (L42-61) transcribed verbatim from the artifact's GGUF metadata (the parity
  test separately checks the two copies of `head_offsets` agree, so the transcription cannot silently drift).
- For one position: `ctx = [token, newest_prev_or_EOS, older_or_EOS]` — the EOS cut (`PLE_EOS_TOKEN_ID
  = 248044`, `include/strata/kernels/ngram.hpp` L37) AND a missing predecessor (`t < 0`; id 0 is a REAL
  token) replace THAT slot and every older one, with the cut evaluated BEFORE the value is stored
  (ngram.cpp L83-87: one token of history too much if you store first). Then per n in {2,3}:
  `mixed = ctx0*m0 XOR ctx1*m1 [XOR ctx2*m2]` (assignment then XOR, every product wraps mod 2^64), and
  for head h: `row = mixed % vocab[h] + offset[h]` — MODULO by an odd per-head vocab, not a mask.
  Heads 0..7 are the bigram n, 8..15 the trigram n (`NGRAM_SIZE=3`, `HEADS_PER_NGRAM=8`, ngram.hpp L33-35).
- **The cross-check that makes this more than self-assertion:** `src/kernels/ple_oracle_vectors.inc`
  commits 6 adversarial hash cases GENERATED from `ref/ngram.py::ngram_rows` — the model reference's own
  function, "validated bit-exact" against `qwen4exp.cpp` L1092-1124 — the same ground truth the engine's
  own `ple_parity.cpp` part A consumes. `ple_ngram.py --oracle` replays every one of those 19 token rows
  × 16 heads through our Python: **0 differ (measured 2026-10-11, torch-free)**. The fixtures deliberately
  target the traps a range check cannot see: window direction (their `prev` is OLDEST-FIRST and reading
  it forwards "still produces indices in range"), EOS placement, NULL≡EOS, token id 0 vs missing, and a
  token whose OWN id is EOS not cutting itself.
- Consequence for the whole overlay idea: **keys are predictable.** For any token sequence we can compute,
  on CPU, in Python, exactly which 16 table rows the engine will read at every position.

### 2. The table's on-disk geometry (this deployment: the sleepyeldrazi Q8_0 shard)

- Tensor `per_layer_token_embd.weight`, shape `[160, 320,001,536]` — ne0=160 is the FAST axis, so a row
  is contiguous and the row index is shape[1] (ngram.cpp L248-249). Our shard 2 is Q8_0:
  `PLE_ROW_BYTES_Q8_0 = (160/32)*34 = 170 B` = 5 blocks of (f16 scale, 32 i8) exactly as GGUF Q8_0
  defines (ngram.hpp L50; the format table entry is kPleFormats L172 — the table type is looked up by
  GGUF type name, so Q8_0 is a first-class supported deployment, not a hack).
- The offset is FALSIFIABLE, not assumed: `PleTable::open` (ngram.cpp L296-312) checks the tensor exactly
  fills the file from `data_start` when it is alone in its shard — and our shard IS alone, so the check
  is live. Measured on our file: 54,400,261,312 B total, `data_start=192`, tensor offset within the data
  section 0, and `192 + 320,001,536 x 170 − file_size = 0` exactly (the fill-difference measured 0 —
  the engine's own boot-time check, passing on this very file today). `data_start` is align32(end-of-KV-
  section) — computed identically by the engine's `GgufFile` (whose comment at ngram.cpp L281 records
  the same 192-byte header for the original shard) and by the repo's `tools/gguf_reader.py` L105-109.
  The mirror's `table_info()` reproduces every one of these checks and refuses to patch anything that
  fails them.
- Read path: `PleReader::issue` (`src/ngram/ple_reader.cpp` L422+) gathers the 16 rows per position,
  coalescing page-aligned jobs, and a row index >= n_rows reads as ZEROS (L438-440). Gathered head h
  lands in `OUT[h*160, (h+1)*160)` of the 2560-wide input to the PLE block — head-slowest; verified in
  ngram.cpp (L499/503/612, `out2560 + h * PLE_HEAD_DIM`) and named as a checked property by
  `ple_parity.cpp` L568.
- Measured probes on our real table (mirror `--rows/--dequant`, this machine): rows for tokens
  [11, 220, 69922, 248044, 321] land in-band for all 16 heads, e.g. token 11 → head 0 row 13,555,483
  at byte 2,304,432,302; row 42 dequantizes to sane in-distribution values (max |x| 0.021, per-block
  f16 scales 1.38e-4 / 8.17e-5 / 1.26e-4 / 1.65e-4 / 1.20e-4), and the LAST row of the table
  (320,001,535) dequantizes to all zeros, consistent with never-written tail space.

### 3. Facts about the row space that D2/D3 design must respect

- **90 rows are unreachable dead space.** The head bands chain disjointly from 0 to
  `offset[15]+vocab[15] = 320,001,446`; the table holds 320,001,536 rows, so the last 90 can never be
  triggered by ANY n-gram hash under the artifact's constants. (The mirror selftest pins this number:
  if the consts ever drift, the gap changes and the check fails.)
- **The last row is all-zero** (measured dequant probe): consistent with out-of-range reads zeroing —
  that tail was likely never written. So "all-zero row" is a known-safe no-op content in this table.
- **Collisions are structural, not accidental**: each head band holds ~20M rows shared by a 2^64 key
  space — millions of n-grams map to the same row by design, and the trained readout learned with that
  noise. A delta write to one row therefore NEVER affects only "our" fact; it perturbs every key that
  hashes there. That is what the plan's key-check + overflow-chain requirement (§11-7) is for, and it is
  why the file-swap cross-check venue (a patched COPY of the shard, stock engine) matters as the final
  proof that an effect lives in the table rather than in our plumbing.
- **Raw-byte row clones need no requantization**: two rows on the same Q8_0 grid are byte-identical when
  copied, so a clone carries zero added quantization error (the patcher writes 170-byte blocks only).

### 4. Where the table is CONSUMED (what a written row must survive)

The gathered 2560-wide vector feeds a LEARNED readout module — `tools/iq_pack.py` names its tensors:
`blk.1.ple_key.weight` (BF16), `blk.1.ple_value.weight` (BF16), `blk.1.ple_conv1d.weight` (F16),
`ple_norm_key/query/conv.weight` (F32). The module is trained; a synthetic row whose values are out of
the activation distribution the readout saw will do unpredictable things. D0 cannot test that from the
read side — this is the plan's named "weakest link" (§11-7) and exactly what **D2 is the cheap
make-or-break probe for**. What D0 DOES establish: the keys are predictable, the file geometry is
reproducible, the patcher is exact, and row CLONES (not invented values) are in-distribution by
construction — they are bytes the trainer wrote.

## Run

```sh
python3 spikes/ple-overlay/ple_ngram.py --oracle      # vs ref/ngram.py vectors: 19 rows, 0 differ
python3 spikes/ple-overlay/ple_ngram.py --selftest    # properties + oracle; prints "ALL PASS"
python3 spikes/ple-overlay/ple_ngram.py --rows "11,220" [--table SHARD2]   # key computation
```

No torch or transformers needed (stdlib + the repo's `tools/gguf_reader.py` for header parsing).

## What D0 does NOT prove

- Not that writing changes BEHAVIOR (that is D2 on the live engine, in the coordinated window).
- Not the value-calibration question (§4 above).
- Not how the PLE block's output is combined per layer beyond the readout tensors named here — the
  injection arithmetic lives in `ple.cu`/`ple.hpp` (part C of `ple_parity.cpp` cross-checks it against
  a captured ggml graph); we read that test's claims, we did not re-run the GPU parity binary (needs
  the build window).
- The engine's live PLE on/off plumbing (`ple_prefetch_enable`, Direct/Mmap modes) was noted, not probed.

Licensing: this file mirrors STRATA (MIT © Niko1221 and contributors) — no AkbasCore mechanism material
is implicated in the addressing spec.
