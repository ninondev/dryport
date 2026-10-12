# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Private operation scripts and save recipes stay out of both source exports."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_export", ROOT / "tools/release-check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ExportBoundaryTests(unittest.TestCase):
    def test_source_export_excludes_one_off_scripts_and_private_recipes(self):
        if (ROOT / ".publicignore").is_file():
            files, prefixes = release.exclusions(ROOT)
            self.assertIn("tools/一次性/", prefixes)
            self.assertIn("tools/ingame-save-games.json", files)
        entries, _ = release.snapshot(ROOT)
        self.assertFalse(any(name.startswith("tools/一次性/") for name in entries))
        self.assertNotIn("tools/ingame-save-games.json", entries)
        if not (ROOT / ".publicignore").exists():
            self.assertFalse((ROOT / "tools/一次性").exists())
            self.assertFalse((ROOT / "tools/ingame-save-games.json").exists())

    def test_internal_export_list_matches_source_exclusions(self):
        ignore = ROOT / ".publicignore"
        if not ignore.is_file():
            self.skipTest("Internal export configuration is absent from the public tree")
        lines = ignore.read_text().splitlines()
        self.assertIn("tools/一次性/", lines)
        self.assertIn("tools/ingame-save-games.json", lines)
