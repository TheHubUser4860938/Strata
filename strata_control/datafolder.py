"""The one data folder (the single-root law of 2026-10-23): where stores, embedders, session dirs,
bench files and model packs live - remembered `--data-dir` first (settings.json "data_dir", the same
file setup reads), else setup's own default: `Strata-data` NEXT TO the install folder.

The symlink era is over and this is why it could end. The dev box kept its data folder in its home
and pointed `install/Strata-data` at it with a symlink, because the code spelled the same folder two
ways: the data folder for the big things (models, packs, mtp) and the install folder for the memory
store, the sessions and the embedder. A normal clone with a sibling data folder had no such bridge
and got two trees. Every tool that reads one of those paths asks here instead, both writers and both
readers name one folder, and the repo folder holds only the program.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_files() -> list[Path]:
    """Where Strata remembers things, in read order (setup.settings_path's own candidates, kept
    here so a tool never invents a second settings file: %APPDATA%\\Strata on Windows,
    $XDG_CONFIG_HOME/strata else ~/.config/strata)."""
    cands = []
    if os.name == "nt":
        cands.append(Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Strata" / "settings.json")
    cands.append(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "strata" / "settings.json")
    return cands


def data_dir(root: Path) -> Path:
    """The data folder for the install at `root`: the remembered one when it exists and is a
    directory, else the default beside the install folder. Never creates anything; a tool run on a
    fresh clone simply gets `../Strata-data`, which is exactly what setup would make."""
    for cfg in settings_files():
        try:
            d = json.loads(cfg.read_text(encoding="utf-8-sig")).get("data_dir")
        except (OSError, ValueError):
            continue
        if d and Path(d).is_dir():
            return Path(d)
    return Path(root).parent / "Strata-data"
