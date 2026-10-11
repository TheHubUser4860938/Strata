#!/usr/bin/env python3
"""tools/strata_session.py - dissect, verify, and graft Strata session (slot-save) files.

The byte grammar and the file hash are READ OFF the engine (src/core/conversation_file.cpp,
memory-core branch, v0.1.42 fork base; line refs quoted inline) - NOT guessed:
  file = [64 B header][payload][u64 payload_hash][8 B "STRSEND\\x01"]   (kHeader=64, kTrailer=16, L47/L1082)
  header = magic "STRSESS\\x01" | u32 version=1 | u32 header_size=64 | u64 model | u64 config
           | u64 payload_len | u64[2] reserved | u64 header_hash = session_hash64(header[0:56], 0)
  payload = i64[18] geometry | i64 layer_lo | i64 layer_hi | u64 cvec
            | checkpoint(live) | u64 n_ckpt | checkpoint * n
            | u64 n_kv | for each: i64[7] (format cells heads head_dim page_size pooled_rows idx_dim)
                              + 5 x (u64 byte_len + raw bytes)     # parts order: k v k_scale v_scale pooled
  checkpoint = u64 n_ids + i32[n_ids] | u64 n_imgs + n_imgs x (i64 start, u64 hash)
               | 5 x (u64 n + n bytes) in order gdn ple tails dead block_pos | u64 used
  payload_hash = session_hash64(payload_bytes, seed 0)      (L1074-1083: the hasher starts fresh
             at put_payload; the header is written unhashed)
  session_hash64 = the engine's SessionHasher (L820-854), ported below line-for-line.

WHY it exists: Spike B1 ("a conversation saved as a session file, restored, then recalled in a
FRESH conversation that never re-sends the source") treats the session file as the cartridge
container (plan 11-6). This tool proves by bytes what the container holds, and builds grafted
files: cartridge rows + live.ids spliced, prior sections untouched. Every operation is verified
by re-computing the header hash and the payload hash exactly like the engine does - so the
question "would the engine accept this file?" is answered before the engine ever sees it.

Self-test (`--selftest`, no GPU, no engine): reconstructs the engine's OWN golden test fixture
(sample() from src/core/conversation_file_test.cpp L58-99) and checks BOTH committed constants:
size 17,878,535 bytes and session_hash64-of-whole-file = 0x70f812b350360d09. A grammar slip, a
field-order slip, or a hasher port slip each shows as a mismatch. Run it; do not trust this file
until it passes on your machine.

Commands:
  --selftest                                     # the golden check above (exit 0 = pass)
  --inspect  FILE                                # full section table (sizes, offsets, identity)
  --verify   FILE                                # header hash + payload hash + marker recompute
  --graft    FILE --out NEW --cartridge CAR [--gdn-zero-past T]
             # B1 experiment file: payload's live.ids gets the cartridge ids PREPENDED
             # (file-side analog of the head-splice the engine-side graft needs), the KV
             # sections are COPIED VERBATIM (v1: the cartridge contributes ids+metadata; the
             # row-rewrite path is --replace-kv, below), hashes are re-signed.
  --replace-kv FILE --out NEW --cartridge-car JSON  # v2: raw-block replacement inside one QSA
             # layer's parts (JSON: {layer, part, src_file, src_offset, dst_offset, length});
             # extent metadata in the kv header must already match - this writes bytes, not
             # geometry, and re-signs. The window's B2 experiment.

Cartridge file format (built by the probes; JSON): {"ids": [int32...], "note": str,
  "source_text": str}  - ids are the token ids of the fact text (server-side tokenization
  belongs to the engine; the probes take them from /tokenize, which IS the engine's own).

MIT, like Strata; fork tooling for the memory-core experiments.
"""
from __future__ import annotations
import argparse, json, struct, sys
from pathlib import Path

MAGIC = b"STRSESS\x01"
KEND = b"STRSEND\x01"
P1, P2, P3, P4, P5 = (0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9,
                      0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5)
M64 = (1 << 64) - 1


def _rotl(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & M64


def _round1(acc: int, w: int) -> int:
    return (_rotl((acc + w * P2) & M64, 31) * P1) & M64


class SessionHasher:
    """Line-for-line port of SessionHasher (conversation_file.cpp L820-854)."""
    def __init__(self, seed: int = 0):
        self.lane = [(seed + P1 + P2) & M64, (seed + P2) & M64, seed & M64, (seed - P1) & M64]
        self.pending = b""
        self.total = 0

    def block(self, p: bytes) -> None:
        for i in range(4):
            w = struct.unpack_from("<Q", p, 8 * i)[0]
            self.lane[i] = _round1(self.lane[i], w)

    def update(self, data: bytes) -> None:
        self.total = (self.total + len(data)) & M64
        buf = self.pending + data
        n = (len(buf) // 32) * 32
        for off in range(0, n, 32):
            self.block(buf[off:off + 32])
        self.pending = buf[n:]

    def digest(self) -> int:
        l0, l1, l2, l3 = self.lane
        h = (_rotl(l0, 1) + _rotl(l1, 7) + _rotl(l2, 12) + _rotl(l3, 18)) & M64
        for l in self.lane:
            h = ((h ^ _round1(0, l)) * P1 + P4) & M64
        h = (h + self.total) & M64
        p, i, n = self.pending, 0, len(self.pending)
        while i + 8 <= n:
            h = (_rotl(h ^ _round1(0, struct.unpack_from("<Q", p, i)[0]), 27) * P1 + P4) & M64
            i += 8
        while i < n:
            h = (_rotl((h ^ (p[i] * P5)) & M64, 11) * P1) & M64
            i += 1
        h ^= h >> 33; h = (h * P2) & M64
        h ^= h >> 29; h = (h * P3) & M64
        h ^= h >> 32
        return h


def session_hash64(data: bytes, seed: int = 0) -> int:
    hs = SessionHasher(seed)
    hs.update(data)
    return hs.digest()


def header_bytes(model: int, config: int, payload_len: int) -> bytes:
    h = MAGIC + struct.pack("<IIQQQQQ", 1, 64, model, config, payload_len, 0, 0)
    h += struct.pack("<Q", session_hash64(h[:56], 0))
    return h  # 64 B


# ---------------- parsing ----------------

def parse_session(path: str, want_payload: bool = False) -> dict:
    """Section table without loading payload bodies (want_payload=True also returns live.bytes
    for the tiny synthetic files; real files stream by offsets)."""
    data = Path(path).read_bytes()
    if len(data) < 64 + 16:
        raise ValueError("file shorter than header + trailer")
    if data[:8] != MAGIC:
        raise ValueError(f"bad magic {data[:8]!r}")
    version, hsize, model, config, payload_len, r0, r1 = struct.unpack_from("<IIQQQQQ", data, 8)
    (hh,) = struct.unpack_from("<Q", data, 56)
    if hh != session_hash64(data[:56], 0):
        raise ValueError("header hash mismatch - not an intact Strata session file")
    if version != 1 or hsize != 64:
        raise ValueError(f"version {version}, header_size {hsize}: this reader speaks v1/64")
    ph_at = 64 + payload_len
    (ph,) = struct.unpack_from("<Q", data, ph_at)
    if data[ph_at + 8:ph_at + 16] != KEND:
        raise ValueError("missing STRSEND end marker")
    if ph != session_hash64(data[64:64 + payload_len], 0):   # the engine hashes the PAYLOAD only
        raise ValueError("payload hash mismatch - corrupted or hand-edited payload")
    out = {"model": model, "config": config, "payload_len": payload_len,
           "file_size": len(data), "offsets": {}, "kv": []}
    pos, base = 64, 64
    def take(fmt):
        nonlocal pos
        n = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, data, pos)
        pos += n
        return v
    geo = take("<18q")
    out["geometry"] = list(geo)
    layer_lo, layer_hi, cvec = take("<qqQ")
    out["layer_lo"], out["layer_hi"], out["cvec"] = layer_lo, layer_hi, cvec

    def checkpoint(name):
        nonlocal pos
        (n_ids,) = take("<Q")
        ids_at = pos
        pos += 4 * n_ids
        (n_img,) = take("<Q")
        img_at = pos
        pos += 16 * n_img
        entry = {"name": name, "ids": (ids_at, n_ids), "imgs": (img_at, n_img)}
        for nm in ("gdn", "ple", "tails", "dead", "block_pos"):
            (nb,) = take("<Q")
            entry[nm] = (pos, nb)
            pos += nb
        (used,) = take("<Q")
        entry["used"] = used
        if want_payload:
            entry["ids_list"] = list(struct.unpack_from(f"<{n_ids}i", data, ids_at))
        out["checkpoints"].append(entry)
    out["checkpoints"] = []
    checkpoint("live")
    (n_ckpt,) = take("<Q")
    for ci in range(n_ckpt):
        checkpoint(f"ckpt{ci}")
    (n_kv,) = take("<Q")
    for ki in range(n_kv):
        fmt_, cells, heads, head_dim, page_size, pooled_rows, idx_dim = take("<7q")
        parts = {}
        for nm in ("k", "v", "k_scale", "v_scale", "pooled"):
            (nb,) = take("<Q")
            parts[nm] = (pos, nb)
            pos += nb
        out["kv"].append({"i": ki, "format": fmt_, "cells": cells, "heads": heads,
                          "head_dim": head_dim, "page_size": page_size,
                          "pooled_rows": pooled_rows, "idx_dim": idx_dim, "parts": parts})
    out["payload_end"] = pos
    return out


# ---------------- building / rewriting ----------------

class Writer:
    def __init__(self):
        self.b = bytearray()
    def u64(self, v): self.b += struct.pack("<Q", v)
    def i64(self, v): self.b += struct.pack("<q", v)
    def raw(self, p): self.b += p
    def blob(self, p: bytes):
        self.u64(len(p)); self.b += p
    def ids(self, lst):
        self.u64(len(lst)); self.b += struct.pack(f"<{len(lst)}i", *lst)


def _checkpoint_bytes(c: dict) -> bytes:
    w = Writer()
    w.ids(c["ids_list"])
    w.u64(len(c.get("imgs", [])))
    for st, hh in c.get("imgs", []):
        w.i64(st); w.u64(hh)
    for nm in ("gdn", "ple", "tails", "dead", "block_pos"):
        w.blob(c.get(nm, b""))
    w.u64(c.get("used", 0))
    return bytes(w.b)


def build_file(model: int, config: int, geometry, layer_lo: int, layer_hi: int, cvec: int,
               live: dict, ckpts: list, kvs: list) -> bytes:
    """kvs: [{format,cells,heads,head_dim,page_size,pooled_rows,idx_dim,
             k,v,k_scale,v_scale,pooled (bytes)}]"""
    w = Writer()
    w.raw(struct.pack("<18q", *geometry))
    w.i64(layer_lo); w.i64(layer_hi); w.u64(cvec)
    w.raw(_checkpoint_bytes(live))
    w.u64(len(ckpts))
    for c in ckpts:
        w.raw(_checkpoint_bytes(c))
    w.u64(len(kvs))
    for kv in kvs:
        w.raw(struct.pack("<7q", kv["format"], kv["cells"], kv["heads"], kv["head_dim"],
                          kv["page_size"], kv["pooled_rows"], kv["idx_dim"]))
        for nm in ("k", "v", "k_scale", "v_scale", "pooled"):
            w.blob(kv.get(nm, b""))
    payload = bytes(w.b)
    whole = header_bytes(model, config, len(payload)) + payload
    # the engine's stored payload hash covers the PAYLOAD ONLY (SessionHasher starts fresh at
    # put_payload, conversation_file.cpp L1074-1083); the golden whole-file hash (kGoldenHash) is
    # the TEST's own recompute over every byte of the finished file - different thing, both here.
    return whole + struct.pack("<Q", session_hash64(payload, 0)) + KEND


# ---------------- selftest: the engine's own golden fixture ----------------

def _bytes_of(n: int, seed: int) -> bytes:
    return bytes((i * 7 + seed) & 0xFF for i in range(n))


def _pattern(n: int, seed: int) -> bytes:
    return bytes((i * 131 + seed) & 0xFF for i in range(n))


def _cp(tokens: int, seed: int) -> dict:
    return {"ids_list": [1000 + i for i in range(tokens)],
            "imgs": [(3, 0x1234567890ABCDEF ^ seed)],
            "gdn": _bytes_of(4099, seed), "ple": _bytes_of(77, seed + 1),
            "tails": _bytes_of(301, seed + 2), "dead": _bytes_of(64, seed + 3),
            "block_pos": _bytes_of(8, seed + 4), "used": 42 + seed}


def selftest() -> int:
    GOLDEN_SIZE, GOLDEN_HASH = 17878535, 0x70F812B350360D09
    kvs = []
    for layer in range(3):
        kvs.append({"format": 2 + layer, "cells": 1024, "heads": 2, "head_dim": 256,
                    "page_size": 64, "pooled_rows": 0 if layer == 2 else 9, "idx_dim": 128,
                    "k": _pattern((17 << 20) + 5 if layer == 0 else 4096, layer & 0xFF),
                    "v": _pattern(4096 + layer, (layer + 10) & 0xFF),
                    "k_scale": _pattern(0 if layer == 1 else 512, (layer + 20) & 0xFF),
                    "v_scale": _pattern(512, (layer + 30) & 0xFF),
                    "pooled": _pattern(0 if layer == 2 else 9 * 128 * 4, (layer + 40) & 0xFF)})
    f = build_file(0x1111222233334444, 0x5555666677778888, [100 + i for i in range(18)],
                   0, 48, 0, _cp(1000, 1), [_cp(10, 2), _cp(500, 3)], kvs)
    fails = 0
    if len(f) != GOLDEN_SIZE:
        print(f"FAIL size: got {len(f)}, golden {GOLDEN_SIZE}"); fails += 1
    got = session_hash64(f, 0)
    if got != GOLDEN_HASH:
        print(f"FAIL hash: got {got:#018x}, golden {GOLDEN_HASH:#018x}"); fails += 1
    # round trip: parse what we built, re-check structure
    p = parse_session_bytes(f)
    if p["payload_len"] + 80 != len(f) or p["kv"][0]["cells"] != 1024 or p["kv"][2]["pooled_rows"] != 0:
        print("FAIL parse round trip"); fails += 1
    print("selftest:", "ALL PASS (engine golden fixture reproduced)" if fails == 0 else f"{fails} FAILED")
    return fails


def parse_session_bytes(data: bytes) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sess", delete=True) as t:
        t.write(data); t.flush()
        return parse_session(t.name, want_payload=True)


# ---------------- grafting ----------------

def graft(path: str, out_path: str, cartridge_path: str) -> int:
    """v1 cartridge splice (B1 experiment): rebuild the file with the cartridge's ids PREPENDED
    to live.ids, byte-identical state arrays, byte-identical KV sections. Re-signs everything.
    (The window's live engine graft additionally needs the KV ROWS + indexer keys + GDN built by
    prefill - this file-side splice proves the CONTAINER path: same bytes the engine will write
    and accept.)"""
    car = json.loads(Path(cartridge_path).read_text())
    car_ids = [int(x) for x in car["ids"]]
    for x in car_ids:
        if not -(1 << 31) <= x < (1 << 31):
            raise ValueError("cartridge ids must be signed int32 range")
    info = parse_session(path, want_payload=True)
    src = Path(path).read_bytes()

    def entry_dict(e):
        at, n = e["imgs"]
        d = {"ids_list": e.get("ids_list", []),
             "imgs": [tuple(struct.unpack_from("<qQ", src, at + 16 * i)) for i in range(n)],
             "used": e["used"]}
        for nm in ("gdn", "ple", "tails", "dead", "block_pos"):
            a, nb = e[nm]
            d[nm] = src[a:a + nb]
        return d

    live = entry_dict(info["checkpoints"][0])
    n_before = info["checkpoints"][0]["ids"][1]
    live["ids_list"] = car_ids + live["ids_list"]
    ckpts = [entry_dict(e) for e in info["checkpoints"][1:]]
    kvs = []
    for kv in info["kv"]:
        e = {k: v for k, v in kv.items() if k != "parts"}
        for nm, (at, nb) in kv["parts"].items():
            e[nm] = src[at:at + nb]
        kvs.append(e)
    f = build_file(info["model"], info["config"], info["geometry"], info["layer_lo"],
                   info["layer_hi"], info["cvec"], live, ckpts, kvs)
    Path(out_path).write_bytes(f)
    again = parse_session(out_path, want_payload=True)
    ok = again["payload_len"] + 80 == len(f)
    ok = ok and again["checkpoints"][0]["ids_list"][:len(car_ids)] == car_ids
    print(f"graft: live ids {n_before} -> {len(live['ids_list'])} (spliced +{len(car_ids)}), "
          f"{len(f)} bytes, re-signed, re-parsed clean + ids confirmed: {ok}")
    return 0 if ok else 1


def replace_kv(path: str, out_path: str, ops_path: str) -> int:
    """Raw byte surgery inside KV part buffers (B2: grafted rows): ops JSON =
    [{"layer": i, "part": "k"|"v"|"k_scale"|"v_scale"|"pooled", "src": FILE, "src_offset": N,
      "dst_offset": N, "length": N}] - src defaults to the session file itself (a copy).
    Byte lengths per part must not change (extent untouched); re-signs header + payload hash.
    Geometry correctness (row layout, page order) is the CALLER's burden - the kv header's
    cells/heads/head_dim/page_size plus the pool layout in qsa.hpp give the row math."""
    ops = json.loads(Path(ops_path).read_text())
    info = parse_session(path)
    buf = bytearray(Path(path).read_bytes())
    for op in ops:
        kv = info["kv"][op["layer"]]
        at, nb = kv["parts"][op["part"]]
        s = Path(op.get("src", path)).read_bytes()
        so, do, ln = op["src_offset"], op["dst_offset"], op["length"]
        if do + ln > nb or so + ln > len(s):
            raise ValueError(f"op out of range: {op}")
        buf[at + do: at + do + ln] = s[so: so + ln]
    pl = info["payload_len"]
    buf[56:64] = struct.pack("<Q", session_hash64(bytes(buf[:56]), 0))
    buf[64 + pl: 72 + pl] = struct.pack("<Q", session_hash64(bytes(buf[64:64 + pl]), 0))
    Path(out_path).write_bytes(bytes(buf))
    parse_session(out_path)  # raises unless the re-signed file parses clean
    print(f"replace-kv: {len(ops)} ops applied, payload re-signed, re-parse clean")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--inspect", metavar="FILE")
    ap.add_argument("--verify", metavar="FILE")
    ap.add_argument("--graft", metavar="FILE")
    ap.add_argument("--replace-kv", nargs=2, metavar=("FILE", "OPS.json"))
    ap.add_argument("--out")
    ap.add_argument("--cartridge")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.replace_kv:
        sys.exit(replace_kv(a.replace_kv[0], a.out or a.replace_kv[0] + ".kv", a.replace_kv[1]))
    if a.verify:
        info = parse_session(a.verify)
        print(f"OK {a.verify}: {info['file_size']} B, payload {info['payload_len']}, "
              f"kv sections {len(info['kv'])}, identity model={info['model']:#x} config={info['config']:#x}")
        sys.exit(0)
    if a.inspect:
        info = parse_session(a.inspect, want_payload=True)
        print(json.dumps(info, indent=1))
        sys.exit(0)
    if a.graft:
        sys.exit(graft(a.graft, a.out or a.graft + ".grafted", a.cartridge or
                       sys.exit("--graft needs --cartridge")))
    ap.print_help()


if __name__ == "__main__":
    main()
