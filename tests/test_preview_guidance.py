# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Preview instructions agree with local update and runtime boundaries."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PreviewGuidanceTests(unittest.TestCase):
    def test_path_layer_preparation_references_exist(self):
        comments = "\n".join(line for line in (ROOT / "lib/paths.sh").read_text().splitlines()
                             if line.startswith("#"))
        references = re.findall(r"(?:tools|docs)/[A-Za-z0-9._/-]+", comments)
        self.assertIn("docs/usage.md", references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue((ROOT / reference).is_file(), reference)

    def test_usage_discloses_source_only_and_actual_manual_build_gaps(self):
        text = (ROOT / "docs/usage.md").read_text()
        self.assertIn("source-only preview", text)
        self.assertIn("Affected runtime bundles remain withheld", text)
        self.assertIn("no automated source-build installer for mkxp-z or KiriKiri", text)
        self.assertIn("tools/build-wine.sh --check", text)
        self.assertIn("--from <local folder>", text)
        self.assertNotIn("release dir or URL", text)
        chinese = (ROOT / "docs/usage.zh.md").read_text()
        self.assertIn("源码预览", chinese)
        self.assertIn("没有自动源码构建安装器", chinese)
        self.assertIn("--from <本地目录>", chinese)
        self.assertNotIn("发布目录或 URL", chinese)

    def test_security_policy_explains_possession_and_ad_hoc_signing(self):
        text = (ROOT / "SECURITY.md").read_text()
        self.assertIn("token possession", text)
        self.assertIn("does not establish process identity", text)
        self.assertIn("ad-hoc signing", text)
        self.assertIn("not notarized", text)
        self.assertNotIn("program other than the launched game", text)
        self.assertNotIn("without signed releases", text)

    def test_bridge_comment_describes_token_possession_without_identity_claim(self):
        text = (ROOT / "lib/steambridge/bridge.c").read_text()
        self.assertIn("Token possession does not identify the calling process", text)
        self.assertNotIn("another local program cannot reach", text)
