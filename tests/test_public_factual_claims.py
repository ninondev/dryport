# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public route and evidence statements must stay within the implemented scope."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FactualDocTests(unittest.TestCase):
    def test_historical_hardware_class_is_preserved(self):
        import json
        entries = json.loads((ROOT / "docs/compatibility.json").read_text())["entries"]
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(entry.get("hardware"), "Apple M5, 16 GB")
        for name in ("docs/compatibility.md", "docs/compatibility.zh.md",
                     "docs/release-notes-preview.md", "docs/release-notes-preview.zh.md"):
            self.assertIn("Apple M5, 16 GB", (ROOT / name).read_text())

    def test_app_signing_and_first_downloaded_launch_are_described_accurately(self):
        cases = (("docs/release-notes-preview.md", "ad-hoc signing", "Downloaded-app first launch remains unverified"),
                 ("docs/release-notes-preview.zh.md", "ad-hoc 签名", "下载所得 app 的首次启动尚未验证"))
        for name, signing, warning in cases:
            text = (ROOT / name).read_text()
            self.assertIn(signing, text)
            self.assertIn(warning, text)
            self.assertNotIn("未签名 app", text)
            self.assertNotIn("The app is not signed", text)

    def test_route_scope_names_pinned_versions_and_fallback_limits(self):
        for name in ("README.md", "README.zh.md", "docs/how-it-works.md", "docs/how-it-works.zh.md"):
            text = (ROOT / name).read_text()
            for term in ("Gun-Dog", "11.5", "WebKit", "Rosetta", "2020.1", "Wine Staging", "D3D11"):
                with self.subTest(file=name, term=term):
                    self.assertIn(term, text)
        self.assertIn("Unsupported Ren'Py versions are refused", (ROOT / "README.md").read_text())
        self.assertIn("不支持的 Ren'Py 版本会被拒绝", (ROOT / "README.zh.md").read_text())

    def test_runtime_patches_and_licence_gaps_are_disclosed(self):
        for name in ("README.md", "README.zh.md", "docs/how-it-works.md", "docs/how-it-works.zh.md"):
            text = (ROOT / name).read_text()
            self.assertNotIn("does not modify them", text)
            self.assertNotIn("不修改它们", text)
            self.assertIn("patch" if "zh" not in name else "补丁", text)
        self.assertIn("Legacy archives may lack licence texts", (ROOT / "README.md").read_text())
        self.assertIn("旧归档可能缺许可证原文", (ROOT / "README.zh.md").read_text())

    def test_historical_results_are_qualified_and_not_a_complete_inventory(self):
        cases = (("README.md", "original records are unpublished", "Every other tested game"),
                 ("README.zh.md", "原始记录没有公开", "其余每款测过的游戏"),
                 ("docs/release-notes-preview.md", "original records are unpublished", "Everything below comes"),
                 ("docs/release-notes-preview.zh.md", "原始记录未公开", "以下全部来自"))
        for name, required, old in cases:
            text = (ROOT / name).read_text()
            self.assertIn(required, text)
            self.assertNotIn(old, text)
