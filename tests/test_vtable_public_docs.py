# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public ABI metadata docs describe original records as unpublished."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VtableDocumentationTests(unittest.TestCase):
    def test_provenance_does_not_cite_a_missing_private_record(self):
        text = (ROOT / "lib/steambridge/vtables/README.md").read_text()
        self.assertNotIn("steambridge-状态.md", text)
        self.assertIn("原始交叉验证记录未公开", text)
        for command in ("tools/steam-vtable-map.py", "tools/steam-flat-argsizes.py --tables-out"):
            self.assertIn(command, text)
