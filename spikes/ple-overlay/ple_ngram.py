"""spikes/ple-overlay/ple_ngram.py - Python reference for the PLE n-gram table: the hash, the row I/O,
and the patcher. Spike D0/D2/D3 (the PLE delta overlay) writes and validates against this file; it is a
line-for-line mirror of src/kernels/ngram.cpp (Strata MIT, (c) its authors) so that what WE compute and
what the ENGINE computes are the same function, by construction and by cross-check, not by hope.

What the engine guarantees we mirror (src/kernels/ngram.cpp L63-98, verified by C++ parity tests
src/kernels/ple_parity.cpp and src/ngram/ple_reader_test.cpp against the artifact's own outputs):

  * rows16 per token: ctx = [own_id, newest_prev_or_EOScut, older...]; the EOS cut (id 248044) AND any
    missing predecessor (t < 0) replace THAT slot and every older one with the EOS id itself;
  * per n in {2, 3}: mixed = (ctx0*m0) XOR (ctx1*m1) [XOR ctx2*m2], uint64 wraps;
  * head h (0..7 bigram, 8..15 trigram): row = mixed % vocab[h] + offset[h]  - MODULO, not mask;
  * table [160 values x 320,001,536 rows], head h's row occupies OUT[h*160, h*160+160) of the 2560-wide
    gathered embedding (HEAD-SLOWEST), and one file row is PLE_ROW_BYTES_Q8_0 = 170 bytes: 5 blocks of
    (f16 scale, 32 int8) exactly as GGUF Q8_0 defines it (ngram.hpp L50, kPleFormats L172);
  * a row index >= n_rows would read as zeros (ple_reader issue() L438) - and every legal row is < the
    last offset+vocab = 320,001,536 exactly, so out-of-range only happens if the hash or consts drift.

Run modes (this file is a library; the CLI is --selftest + two probe commands):
  python ple_ngram.py --selftest                     # property + known-value checks, no model needed
  python ple_ngram.py --oracle                       # the engine's OWN oracle vectors (see below)
  python ple_ngram.py --table PATH --rows "id,id,.." # rows + row byte offsets for a token sequence
  python ple_ngram.py --table PATH --dequant ROW     # the row's 160 floats, decoded
  python ple_ngram.py --table PATH --apply PLAN.json --out COPY.gguf   # copy + pwrite (the patcher)
The pwrite plan itself is built via the make_plan(src16, dst16, provenance) library function (D2's
marker-writer will call it; there is no --clone CLI mode - plan JSON is written programmatically).

The --oracle mode (added D0, 2026-10-11): src/kernels/ple_oracle_vectors.inc carries 6 hash cases
(tokens/prev/rows) GENERATED from ref/ngram.py::ngram_rows - the model reference's own function,
"validated bit-exact" against qwen4exp.cpp L1092-1124 - the SAME ground truth src/kernels/ple_parity.cpp
part A consumes. The fixtures are adversarial on purpose (window direction, EOS cut placement, token id 0
vs missing), so a mirror that agrees with them is agreeing with the official semantics, not just with ours:
measured 2026-10-11, ALL 19 fixture token rows x 16 heads match bit-exactly (0 differ). --selftest runs the
oracle check automatically whenever that .inc file is present in this checkout.

Mechanism research (c) 2026 Mustafa Akbas is NOT implicated here: this file mirrors STRATA's own C++
(D0 source study, 2026-10-11). MIT like Strata; part of the memory-core fork.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

# ---- constants transcribed from ngram.cpp L47-59 (the artifact's own, "docs/gguf-dump-shard1.txt") ----
MULT = (23703573157769, 20109073645365, 8052911324071)
VOCAB = (20000003, 20000023, 20000033, 20000047, 20000059, 20000063, 20000069, 20000077,
         20000081, 20000093, 20000107, 20000147, 20000153, 20000159, 20000161, 20000171)
OFFSET = (0, 20000003, 40000026, 60000059, 80000106, 100000165, 120000228, 140000297,
          160000374, 180000455, 200000548, 220000655, 240000802, 260000955, 280001114, 300001275)
N_ROWS = 320_001_536
PLE_EOS = 248_044          # PLE_EOS_TOKEN_ID, ngram.hpp L37
ROW_BYTES = 170            # PLE_ROW_BYTES_Q8_0, ngram.hpp L50: 5 x (2 B f16 scale + 32 x i8)
HEAD_DIM = 160
TABLE_TENSOR = "per_layer_token_embd.weight"

_U64 = (1 << 64) - 1


def ngram_rows16(tok: int, prev2: int, prev1: int) -> tuple[int, ...]:
    """One token -> 16 table rows. EXACT mirror of ngram.cpp ngram_rows() for one position:
    prev2/prev1 are the two predecessors OLDEST FIRST, -1 where there is none."""
    ctx = [tok]
    cut = False
    for t in (prev1, prev2):                      # s = 1 reads the NEWEST predecessor first
        if cut or t < 0 or t == PLE_EOS:          # the cut is evaluated BEFORE storing, ngram.cpp L83-87
            cut = True
            ctx.append(PLE_EOS)
        else:
            ctx.append(t)
    mixed2 = (ctx[0] * MULT[0] & _U64) ^ (ctx[1] * MULT[1] & _U64)
    mixed3 = mixed2 ^ (ctx[2] * MULT[2] & _U64)
    rows = []
    for h in range(16):
        mixed = mixed2 if h < 8 else mixed3       # heads 0..7 bigram, 8..15 trigram
        rows.append(mixed % VOCAB[h] + OFFSET[h])
    return tuple(rows)


def rows_for_sequence(ids: list[int]) -> list[tuple[int, ...]]:
    """All positions of a token sequence, each with its real predecessor window (the first tokens see
    -1, which the cut turns into the EOS id - exactly what the engine's ss.ple_prev seeds do)."""
    out = []
    for i, t in enumerate(ids):
        out.append(ngram_rows16(t, ids[i - 2] if i >= 2 else -1, ids[i - 1] if i >= 1 else -1))
    return out


# ---- GGUF header parse via the repo's OWN reader (tools/gguf_reader.py; the gguf-py package cannot open
# these files - they use Q2_0/type-42 encodings newer than the library, and GGUF v3 metadata types are
# u8-first, which bit our first hand parser). The reader's data_start honors the file's general.alignment. ----

def table_info(path: str) -> dict:
    """Find the table tensor's byte home the same way PleTable::open does, and reproduce the checks the
    engine performs: 160-fast shape, Q8_0 rows, and (single-tensor shard) the table must exactly fill
    the file from its offset (the `alone` arm, ngram.cpp L298-311)."""
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from gguf_reader import GGUFFile
    g = GGUFFile(Path(path))
    mine = [t for t in g.tensors if t.name == TABLE_TENSOR]
    if len(mine) != 1:
        raise SystemExit(f"{path}: {TABLE_TENSOR} found {len(mine)} times")
    t = mine[0]
    if t.shape[0] != HEAD_DIM:
        raise SystemExit(f"{path}: {TABLE_TENSOR} shape[0] = {t.shape[0]}, expected {HEAD_DIM}")
    if t.type_name != "Q8_0":
        raise SystemExit(f"{path}: tensor type {t.type_name} - this patcher only writes Q8_0 rows (170 B)")
    info = {"path": path, "n_rows": t.shape[1], "row_bytes": t.expected_bytes() // t.shape[1],
            "tensor_type": t.type_name, "data_start": g.data_start, "tensor_offset": t.offset,
            "file_size": os.path.getsize(path), "n_tensors": len(g.tensors), "version": g.version,
            "alignment": g.alignment}
    if info["row_bytes"] != ROW_BYTES:
        raise SystemExit(f"{path}: row_bytes {info['row_bytes']} != {ROW_BYTES}")
    if info["n_rows"] != N_ROWS:
        raise SystemExit(f"{path}: {info['n_rows']} rows != {N_ROWS} - the engine's compiled consts would "
                         "index a different table; refuse to patch")
    if len(g.tensors) == 1 and (t.offset != 0 or g.data_start + t.expected_bytes() != info["file_size"]):
        raise SystemExit(f"{path}: the alone-check the engine performs would fail on this file")
    return info


def f_stat(path):
    import os
    return os.path.getsize(path)


def row_file_pos(info: dict, row: int) -> int:
    return info["data_start"] + info["tensor_offset"] + row * ROW_BYTES


def read_row_bytes(path: str, info: dict, row: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(row_file_pos(info, row))
        b = f.read(ROW_BYTES)
    if len(b) != ROW_BYTES:
        raise SystemExit(f"row {row}: short read")
    return b


def q8_dequant(block: bytes) -> list[float]:
    """5 x 34 bytes -> 160 floats (dequantize_q8_0: value = f16(scale) * int8)."""
    out = []
    for b in range(HEAD_DIM // 32):
        d, = struct.unpack("<e", block[b * 34: b * 34 + 2])
        out += [d * q for q in struct.unpack("<32b", block[b * 34 + 2: b * 34 + 34])]
    return out


# ---- the patcher: clone rows by RAW BYTES (same Q8 grid, so no re-quantization error at all) ----

def make_plan(table: str, pairs: list[tuple[list[int], list[int], list[int]]]) -> dict:
    """`pairs` = [(dst_ids, dst_prev2, dst_prev1), ...] parallel to src rows? No - a plan is simpler:
    caller hands (src_rows16, dst_rows16) 16-slot pairs directly. Returns the JSON-serializable plan
    the engine-independent pwrite will apply: (dst_row, src_row) plus the token provenance."""
    info = table_info(table)
    writes = []
    for src16, dst16, prov in pairs:
        for h in range(16):
            writes.append({"head": h, "src_row": src16[h], "dst_row": dst16[h],
                           "pos": row_file_pos(info, dst16[h]), "provenance": prov})
    return {"table": table, "row_bytes": ROW_BYTES, "n_rows": info["n_rows"], "writes": writes}


def apply_plan(plan: dict, out_path: str) -> None:
    """Copy the whole shard, then pwrite each (src_row -> dst_row) 170-byte block. Same-size output,
    so the engine's `alone` invariant still holds after the patch."""
    import os, shutil
    out = Path(out_path)
    if not out.exists() or out.stat().st_size != f_stat(plan["table"]):
        print(f"copying {plan['table']} -> {out} ...", flush=True)
        shutil.copyfile(plan["table"], out)
    info = table_info(plan["table"])
    base = info["data_start"] + info["tensor_offset"]
    with open(plan["table"], "rb") as src, open(out, "r+b") as dst:
        for w in plan["writes"]:
            src.seek(base + w["src_row"] * ROW_BYTES)
            blob = src.read(ROW_BYTES)
            dst.seek(base + w["dst_row"] * ROW_BYTES)
            dst.write(blob)
    print(f"patched {len(plan['writes'])} rows into {out}")


# ---- the engine's own oracle vectors: 6 hash cases generated from ref/ngram.py::ngram_rows ----------

ORACLE_INC = Path(__file__).resolve().parents[2] / "src" / "kernels" / "ple_oracle_vectors.inc"


def oracle_cases(path=ORACLE_INC):
    """Parse caseN_{tokens,prev,rows} out of the committed .inc. prev is OLDEST FIRST, two entries per
    token; rows is 16 per token. Returns [(name, tokens, prev, rows), ...] in file order."""
    import re
    txt = Path(path).read_text()
    txt = "\n".join(l.split("//")[0] for l in txt.splitlines())   # arrays carry inline comments (case2_rows)
    got = {}
    for kind in ("tokens", "prev", "rows"):
        for m in re.finditer(rf"inline const \w+ case(\d+)_{kind}\[\d+\]\s*=\s*{{([^}}]*)}}", txt):
            got[(int(m.group(1)), kind)] = [int(x) for x in m.group(2).split(",") if x.strip()]
    names = {int(ci): nm for nm, ci in
             re.findall(r'\{"([^"]+)", \d+, case(\d+)_tokens', txt)}   # from the kHashCases table
    cases = []
    for ci in sorted({c for c, _ in got}):
        assert (ci, "tokens") in got and (ci, "prev") in got and (ci, "rows") in got, f"case{ci} incomplete"
        toks, prev, rows = got[(ci, "tokens")], got[(ci, "prev")], got[(ci, "rows")]
        assert len(prev) == 2 * len(toks) and len(rows) == 16 * len(toks), f"case{ci}: array sizes inconsistent"
        cases.append((names.get(ci, f"case{ci}"), toks, prev, rows))
    return cases


def oracle_check(cases) -> tuple[int, int]:
    """Compare OUR ngram_rows16 to every oracle row. Returns (checked, mismatches) - position-level
    (one row per token), not per-head, so a failure line names the case and token, not 16 repeats."""
    fails = 0
    for name, toks, prev, rows in cases:
        for i, tok in enumerate(toks):
            mine = ngram_rows16(tok, prev[2 * i], prev[2 * i + 1])   # prev is OLDEST FIRST
            want = tuple(rows[16 * i:16 * i + 16])
            if mine != want:
                fails += 1
                h = next(h for h in range(16) if mine[h] != want[h])
                print(f"FAIL oracle '{name}' tok#{i} id={tok}: head {h}: ours {mine[h]} != oracle {want[h]}")
    return sum(len(c[1]) for c in cases), fails


# ---- selftest: the properties the engine asserts, checked against OUR implementation ----

def selftest() -> int:
    fails = 0

    def check(cond, msg):
        nonlocal fails
        if not cond:
            fails += 1
            print("FAIL:", msg)

    # range and structure
    for tok in (0, 1, 42, 100000, 248044, 248068):
        for a in (-1, 0, 248044, 55):
            for b in (-1, 7):
                rows = ngram_rows16(tok, b, a)
                for h, r in enumerate(rows):
                    check(OFFSET[h] <= r < OFFSET[h] + VOCAB[h], f"head {h} row {r} out of band for {tok},{b},{a}")
    # the head bands must be a DISJOINT CHAIN, and they must FIT the table. Measured fact (D0): the
    # chain ends at 320,001,446, which is 90 rows SHORT of the file's 320,001,536 - the tail 90 rows
    # are unreachable by ANY n-gram hash under the artifact's consts (dead space, never a trigger).
    for h in range(15):
        check(OFFSET[h] + VOCAB[h] == OFFSET[h + 1], f"band chain break at head {h}")
    top = OFFSET[15] + VOCAB[15]
    check(top <= N_ROWS, "bands must fit the table")
    check(N_ROWS - top == 90, f"tail gap changed: {N_ROWS - top} rows unreachable (was 90 at transcription)")
    # EOS cut: a predecessor EOS is stored AS the EOS id and cuts every older slot too (ngram.cpp L82-87)
    r1 = ngram_rows16(7, 999, PLE_EOS)
    r2 = ngram_rows16(7, 999, -1)                  # missing predecessor -> same EOS stand-in
    r3 = ngram_rows16(7, PLE_EOS, PLE_EOS)
    check(r1 == r2 == r3, "missing == EOS == EOS-cut-older must converge")
    check(r1 != ngram_rows16(7, 999, 42), "a real predecessor must matter")
    # the cut propagates to the OLDER slot once the newer is EOS (ctx[2] must be EOS, not 999)
    check(ngram_rows16(7, 999, PLE_EOS) == ngram_rows16(7, PLE_EOS, PLE_EOS), "forward propagation of the cut")
    # the token's OWN eos does not cut its own context (only predecessors are examined)
    check(ngram_rows16(PLE_EOS, 999, 42) != ngram_rows16(PLE_EOS, 999, PLE_EOS), "own EOS is not self-cut")
    # XOR structure, not sum: an equal-pair must cancel to zero in one term and not the other
    mixed2 = (5 * MULT[0]) % _U64 ^ (5 * MULT[1]) % _U64
    check(mixed2 != (5 * MULT[0] + 5 * MULT[1]) % _U64, "XOR must differ from SUM on real multipliers")
    # modulo-vs-mask: the vocabs are odd; a mask would agree only by luck
    check(all(v % 2 == 1 for v in VOCAB), "head vocabs are odd (prime-shaped) - mask semantics differ")
    # stability across runs (pure function)
    check(ngram_rows16(123, 4, 5) == ngram_rows16(123, 4, 5), "determinism")
    # the engine's own oracle vectors (ref/ngram.py ground truth) - the strongest check here: it is
    # NOT our semantics against itself. Skipped only when the .inc is absent from the checkout.
    if ORACLE_INC.exists():
        checked, ofail = oracle_check(oracle_cases())
        check(ofail == 0, f"oracle hash cases: {ofail}/{checked} token rows differ")
        print(f"oracle vectors: {checked} token rows vs ref/ngram.py ground truth, {ofail} differ")
    else:
        print(f"note: {ORACLE_INC} not found - oracle hash cases skipped")
    # q8 codec round trip against a synthetic block
    blk = (struct.pack("<e", 0.25) + bytes([0] * 16 + [127, -128 if False else 127] * 0 + [127] + [0] * 31))[:34]
    vals = q8_dequant(blk * 5)
    check(abs(vals[0]) < 1e-9 and abs(vals[16] - 0.25 * 127) < 1e-3, "Q8 scale*x8 basic math")
    print("selftest:", "ALL PASS" if fails == 0 else f"{fails} FAILED")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--oracle", action="store_true",
                    help="run the ref/ngram.py oracle-vector check and exit")
    ap.add_argument("--table", default="Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf")
    ap.add_argument("--rows", help="comma token ids: print each token's 16 rows + byte homes")
    ap.add_argument("--dequant", help="row number: print its 160 floats")
    ap.add_argument("--apply", help="plan JSON to apply")
    ap.add_argument("--out", help="output table copy to write")
    a = ap.parse_args()
    if a.oracle:
        checked, ofail = oracle_check(oracle_cases())
        print(f"oracle: {checked} token rows checked vs ref/ngram.py vectors, {ofail} differ")
        sys.exit(1 if ofail else 0)
    if a.selftest:
        sys.exit(selftest())
    if a.dequant is not None:
        info = table_info(a.table)
        print(json.dumps(q8_dequant(read_row_bytes(a.table, info, int(a.dequant)))))
        return
    if a.rows:
        ids = [int(x) for x in a.rows.split(",")]
        info = table_info(a.table)
        for i, (tok, r16) in enumerate(zip(ids, rows_for_sequence(ids))):
            print(json.dumps({"i": i, "token": tok, "rows": list(r16),
                              "bytes": [row_file_pos(info, r) for r in r16]}))
        return
    if a.apply:
        apply_plan(json.load(open(a.apply)), a.out)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
