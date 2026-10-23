"""`strata_control/datafolder.py` is the ONE data-folder resolver (the single-root law of 2026-10-23):
the remembered `--data-dir` from settings.json wins when it exists, and the default is `Strata-data`
BESIDE the install folder. Both config writers and every tool ask here, which is what stops a fresh
clone from landing two trees - the split the dev box used to hide behind a `repo/Strata-data` symlink.

    python -m unittest tests.terminal.test_datafolder        (also runs under: python -m pytest tests/terminal)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from strata_control.datafolder import data_dir, settings_files    # noqa: E402


class TheOneResolver(unittest.TestCase):
    """Every case gets its own config home: settings_files() honors XDG_CONFIG_HOME, so the real
    ~/.config/strata/settings.json is never read or written here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        self.install = self.home / "Projects" / "Strata-Mnemosyne"
        self.install.mkdir(parents=True)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._env
        self.tmp.cleanup()

    def remember(self, path: Path) -> None:
        cfg = self.home / "config" / "strata"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps({"data_dir": str(path)}), encoding="utf-8")

    def test_the_default_lands_beside_the_install_not_inside_it(self):
        self.assertEqual(data_dir(self.install), self.home / "Projects" / "Strata-data",
                         "AI_SETUP's 'a Strata-data folder next to the Strata folder' IS this line of code")
        self.assertFalse((self.install / "Strata-data").exists(),
                         "the install folder holds only the program (the symlink era ended 2026-10-23)")

    def test_a_remembered_folder_wins_over_the_default(self):
        vault = self.home / "vault" / "Strata-data"
        vault.mkdir(parents=True)
        self.remember(vault)
        self.assertEqual(data_dir(self.install), vault)

    def test_a_remembered_folder_that_is_gone_does_not_win(self):
        self.remember(self.home / "deleted" / "Strata-data")        # named in settings, never created
        self.assertEqual(data_dir(self.install), self.home / "Projects" / "Strata-data",
                         "a remembered path that is not a directory is not a data folder (the is_dir guard)")

    def test_the_resolver_reads_the_file_setup_writes(self):
        self.assertTrue(any(p.parent.name == "strata" and p.name == "settings.json" for p in settings_files()),
                        "one settings file, no invented second one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
