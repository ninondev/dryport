# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Unconfigured public installation examples must not claim first-install readiness."""
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallationBoundaryTests(unittest.TestCase):
    def test_clone_example_is_explicitly_an_unconfigured_template(self):
        for name, warning in (("README.md", "OWNER is an unconfigured placeholder"),
                              ("README.zh.md", "OWNER 是未配置的占位符")):
            text = (ROOT / name).read_text()
            clone = re.search(r"git clone https://github.com/([A-Za-z0-9-]+)/dryport", text)
            self.assertIsNotNone(clone, "A clone example must use a repository address")
            if clone.group(1) == "OWNER":
                self.assertIn(warning, text)
                self.assertLess(text.index(warning), clone.start())

    def test_installation_example_discloses_missing_runtime_preparation(self):
        for name, warning in (("README.md", "A complete first installation on a clean Mac is unverified"),
                              ("README.zh.md", "干净 Mac 的完整首次安装尚未验证")):
            text = (ROOT / name).read_text()
            self.assertIn(warning, text)
            self.assertIn("mkxp-z", text)
        registry = json.loads((ROOT / "lib/runtimes.json").read_text())
        hashes = registry["manifest_hashes"]
        self.assertIsInstance(hashes, dict)
        for digest in hashes:
            self.assertRegex(digest, r"^[a-fA-F0-9]{64}$")
