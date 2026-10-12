# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""A source preview cannot imply unreviewed runtime binary distribution is cleared."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceScopeTests(unittest.TestCase):
    def test_release_documents_do_not_include_private_decision_process(self):
        for name in ("docs/LEGAL.md", "docs/release-notes-preview.md", "docs/release-notes-preview.zh.md"):
            text = (ROOT / name).read_text()
            self.assertNotIn("ta" + "sks/", text)
            self.assertNotIn("owner", text.lower())

    def test_public_name_is_explicitly_pending(self):
        self.assertIn("The public name is still being chosen", (ROOT / "docs/release-notes-preview.md").read_text())
        self.assertIn("公开名称还在选择", (ROOT / "docs/release-notes-preview.zh.md").read_text())

    def test_preview_scope_and_runtime_distribution_hold_are_explicit(self):
        text = (ROOT / "docs/LEGAL.md").read_text()
        self.assertIn("Preview release scope: source only", text)
        self.assertIn("Affected runtime bundles must not be published", text)
        self.assertIn("exact corresponding source", text)
        self.assertIn("complete dependency notices", text)
        self.assertIn("has not obtained a general Valve or Unity authorization", text)

    def test_legacy_archives_are_not_promised_complete_licence_texts(self):
        text = (ROOT / "docs/LEGAL.md").read_text()
        self.assertIn("Legacy archives may lack licence texts", text)
        self.assertIn("does not establish legal clearance", text)
