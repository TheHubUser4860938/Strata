#!/usr/bin/env python3
"""spikes/window/set_ple_venue.py - flip strata-dev.json's --ple-gguf between the stock shard and
the patched D2 copy (the venue switch). Pure JSON edit of the CONFIG; the engine picks it up at
next boot, so pair every change with a dev restart (the window's choreography). Reversible:
--stock restores the original value (from the first-run .stockbak or the built-in default), and
--show prints the current venue without touching anything. Never writes when nothing changes.

WHY --native-dense-gguf ALSO MUST TRAVEL WITH THE FLIP (learned the hard way, 2026-10-11 boot 2,
"native dense: duplicate split shard number" x2, strata-dev.log L1146/L1151 + src/ source read):
with --native given, the engine auto-globs EVERY split sibling beside it (generate.cpp L2420,
native_dense.cpp L227 check; metadata verified: both 00002-original and the copy carry
split.no=1, split.count=2) and L2443-2456 then APPENDS --ple-gguf to the dense shard list when
its PATH STRING is not already in the list - a copy of shard 2 under a new name is a third file
with the same split number, and NativeDense::load refuses. Passing --native-dense-gguf
explicitly (repeatable, generate.cpp L1833) makes native_dense_gguf non-empty, skips that
auto-append, and the dense half loads from the ORIGINAL shards while the n-gram table opens from
the copy (ple_table.open(o.ple_gguf), L3084). The check is stock v0.1.42 code, so the same
pattern is what an M2/D3 boot on the main build needs too."""
from __future__ import annotations
import json, shutil, sys
from pathlib import Path

CFG = Path(__file__).resolve().parents[2] / "strata-dev.json"
sys.path.insert(0, str(CFG.parent))
from strata_control.datafolder import data_dir      # single-root law (2026-10-23): configs spell these paths absolutely

DEFAULT_SHARD2 = str(data_dir(CFG.parent) / "models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf")
NATIVE1 = str(data_dir(CFG.parent) / "models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf")


def current(cfg):
    a = cfg["args"]
    return a[a.index("--ple-gguf") + 1] if "--ple-gguf" in a else "(auto-find: flag absent)"


def dense_shards(cfg):
    a = cfg["args"]
    return [a[i + 1] for i, x in enumerate(a) if x == "--native-dense-gguf"]


def set_dense(cfg, on):
    """on: insert --native-dense-gguf for both ORIGINAL model shards right after --native
    (dense half unchanged, PLE copy stays out of the split set); off: remove all of them."""
    a = cfg["args"]
    while "--native-dense-gguf" in a:
        i = a.index("--native-dense-gguf")
        del a[i:i + 2]
    if on:
        d = [str((CFG.parent / NATIVE1).resolve()), str((CFG.parent / DEFAULT_SHARD2).resolve())]
        i = a.index("--native") + 2          # after the --native VALUE, not between flag and value
        a[i:i] = ["--native-dense-gguf", d[0], "--native-dense-gguf", d[1]]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--show"
    cfg = json.loads(CFG.read_text())
    cur = current(cfg)
    if mode == "--show":
        ds = dense_shards(cfg)
        print(f"--ple-gguf currently: {cur}")
        print(f"--native-dense-gguf: {'ON (' + str(len(ds)) + ' shards)' if ds else 'OFF (engine auto-glob)'}")
        return 0
    if mode == "--copy":
        new = Path(sys.argv[2]).resolve()
        src = Path(cur) if Path(cur).is_absolute() else CFG.parent / cur
        if not new.exists() or not src.exists() or new.stat().st_size != src.stat().st_size:
            print(f"refusing: copy {new} or source {src} missing, or sizes differ", file=sys.stderr)
            return 1
        bak = CFG.with_suffix(".json.stockbak")
        if not bak.exists():
            shutil.copy2(CFG, bak)
            print(f"backup written: {bak}")
        if cur == str(new) and dense_shards(cfg):
            print("already set:", cur); return 0
        cfg["args"][cfg["args"].index("--ple-gguf") + 1] = str(new)
        set_dense(cfg, True)
    elif mode == "--stock":
        bak = CFG.with_suffix(".json.stockbak")
        orig = json.loads(bak.read_text()) if bak.exists() else None
        new = current(orig) if orig else DEFAULT_SHARD2
        if cur == new and not dense_shards(cfg):
            print("already stock:", cur); return 0
        cfg["args"][cfg["args"].index("--ple-gguf") + 1] = new
        set_dense(cfg, False)
    else:
        print("usage: set_ple_venue.py --show | --copy PATH | --stock", file=sys.stderr); return 2
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CFG)
    print(f"--ple-gguf now: {current(json.loads(CFG.read_text()))} (takes effect at next boot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
