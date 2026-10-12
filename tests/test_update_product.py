# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Local updates require a newer numeric version; remote previews explain their limit."""
import json
import plistlib
import tarfile
from pathlib import Path
import unittest

import test_self_update as fixtures


class UpdateProductTests(unittest.TestCase):
    setUp = fixtures.SelfUpdateTests.setUp
    make_app = fixtures.SelfUpdateTests.make_app
    publish = fixtures.SelfUpdateTests.publish
    run_update = fixtures.SelfUpdateTests.run_update
    revision = fixtures.SelfUpdateTests.revision

    def test_equal_and_older_versions_refuse_before_cache_or_app_changes(self):
        original = {p.relative_to(self.app).as_posix(): p.read_bytes()
                    for p in self.app.rglob("*") if p.is_file()}
        for version in ("0.0.9", "0.1.0", "0.1.test"):
            with self.subTest(version=version):
                self.publish("different-revision", "replacement", version)
                result = self.run_update("--from", str(self.release))
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("version", (result.stdout + result.stderr).lower())
                self.assertFalse(self.runtimes.exists())
                after = {p.relative_to(self.app).as_posix(): p.read_bytes()
                         for p in self.app.rglob("*") if p.is_file()}
                self.assertEqual(after, original)

    def test_unknown_installed_version_refuses_replacement(self):
        (self.app / "Contents/Info.plist").unlink()
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed version", result.stderr.lower())
        self.assertEqual(self.revision(), "oldrev000")
        self.assertFalse(self.runtimes.exists())

    def test_numeric_components_compare_numerically_and_equal_zero_padding_refuses(self):
        info = self.app / "Contents/Info.plist"
        info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.9.0"}))
        self.publish("numeric-upgrade", "replacement", "0.10.0")
        result = self.run_update("--from", str(self.release))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.publish("equal-padded", "replacement", "0.10")
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not newer", result.stderr)
        self.assertEqual(self.revision(), "numeric-upgrade")

    def test_archive_version_must_equal_its_manifest(self):
        stage = self.base / "mismatch/Dryport.app"
        self.make_app(stage, "newrev111", "replacement", "0.3.0")
        manifest_path = self.release / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        archive = self.release / manifest["app"]["archive"]
        with tarfile.open(archive, "w:xz") as package:
            package.add(stage, arcname="Dryport.app")
        manifest["app"].update(sha256=fixtures.sha256(archive), bytes=archive.stat().st_size)
        manifest_path.write_text(json.dumps(manifest))
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version mismatch", result.stderr.lower())
        self.assertEqual(self.revision(), "oldrev000")

    def test_remote_preview_has_an_explicit_plain_message_and_exit_code(self):
        result = self.run_update("--from", "https://updates.example.invalid", "--check")
        self.assertEqual(result.returncode, 20)
        self.assertIn("remote updates are off in this preview; update from a local folder", result.stderr)
        self.assertFalse(self.runtimes.exists())
