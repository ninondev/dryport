# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public checks accept later repository and manifest configuration."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_installation_boundary as boundary

ROOT = Path(__file__).resolve().parents[1]


class PublicConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-public-config-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "checkout"
        for name in ("README.md", "README.zh.md", "lib/runtimes.json", "tools/release-check.py", ".publicignore"):
            source = ROOT / name
            if source.is_file():
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    def test_configured_repository_address_keeps_clone_checks(self):
        for name in ("README.md", "README.zh.md"):
            path = self.root / name
            path.write_text(path.read_text().replace("OWNER", "fixture-maintainer"))
        with patch.object(boundary, "ROOT", self.root):
            boundary.InstallationBoundaryTests().test_clone_example_is_explicitly_an_unconfigured_template()

    def test_registered_manifest_hash_keeps_installation_warning_checks(self):
        registry = self.root / "lib/runtimes.json"
        data = json.loads(registry.read_text())
        data["manifest_hashes"]["a" * 64] = "synthetic release"
        registry.write_text(json.dumps(data))
        with patch.object(boundary, "ROOT", self.root):
            boundary.InstallationBoundaryTests().test_installation_example_discloses_missing_runtime_preparation()
