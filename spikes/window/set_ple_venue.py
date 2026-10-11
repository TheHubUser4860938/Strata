#!/usr/bin/env python3
"""spikes/window/set_ple_venue.py - flip strata-dev.json's --ple-gguf between the stock shard and
the patched D2 copy (the venue switch). Pure JSON edit of the CONFIG; the engine picks it up at
next boot, so pair every change with a dev restart (the window's choreography). Reversible:
--stock restores the original value (from the first-run .stockbak or the built-in default), and
--show prints the current venue without touching anything. Never writes when nothing changes."""
from __future__ import annotations
import json, shutil, sys
from pathlib import Path

CFG = Path(__file__).resolve().parents[2] / "strata-dev.json"
DEFAULT_SHARD2 = ("Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S"
                  "-00002-of-00002.gguf")


def current(cfg):
    a = cfg["args"]
    return a[a.index("--ple-gguf") + 1] if "--ple-gguf" in a else "(auto-find: flag absent)"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--show"
    cfg = json.loads(CFG.read_text())
    cur = current(cfg)
    if mode == "--show":
        print(f"--ple-gguf currently: {cur}"); return 0
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
        if cur == str(new):
            print("already set:", cur); return 0
        cfg["args"][cfg["args"].index("--ple-gguf") + 1] = str(new)
    elif mode == "--stock":
        bak = CFG.with_suffix(".json.stockbak")
        orig = json.loads(bak.read_text()) if bak.exists() else None
        new = current(orig) if orig else DEFAULT_SHARD2
        if cur == new:
            print("already stock:", cur); return 0
        cfg["args"][cfg["args"].index("--ple-gguf") + 1] = new
    else:
        print("usage: set_ple_venue.py --show | --copy PATH | --stock", file=sys.stderr); return 2
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CFG)
    print(f"--ple-gguf now: {current(json.loads(CFG.read_text()))} (takes effect at next boot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
