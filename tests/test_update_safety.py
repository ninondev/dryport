# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Offline updater regressions: no remote fetch or writes through hostile paths."""
import json
import os
import shlex
import shutil
import sys
import tarfile
from pathlib import Path

import test_self_update as fixtures


class UpdateSafetyTests(fixtures.SelfUpdateTests):
    def test_remote_sources_are_refused_before_network_or_cache_writes(self):
        guard = self.base / "network-guard"
        guard.mkdir()
        attempted = self.base / "network-attempted"
        (guard / "sitecustomize.py").write_text(
            "import urllib.request\nfrom pathlib import Path\n"
            "def blocked(*args, **kwargs):\n"
            f"    Path({str(attempted)!r}).write_text('attempted')\n"
            "    raise RuntimeError('network forbidden in this fixture')\n"
            "urllib.request.OpenerDirector.open = blocked\n")
        python = self.bin / "python3"
        python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        python.chmod(0o755)
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                result = self.run_update("--from", scheme + "://updates.example.invalid", "--check",
                                         PYTHONPATH=str(guard))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("远程更新已暂停", result.stderr)
                self.assertFalse(attempted.exists())
                self.assertFalse(self.runtimes.exists())
                self.assertEqual(self.revision(), "oldrev000")

    def test_archive_path_cannot_escape_release_directory(self):
        path = self.release / "release-manifest.json"
        manifest = json.loads(path.read_text())
        shutil.copyfile(self.release / manifest["app"]["archive"], self.base / "outside.tar.xz")
        manifest["app"]["archive"] = "../outside.tar.xz"
        path.write_text(json.dumps(manifest))
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive", result.stderr)
        self.assertEqual(self.revision(), "oldrev000")

    def test_cache_parent_symlink_is_refused_before_mkdir(self):
        outside = self.base / "outside-cache"
        outside.mkdir()
        self.runtimes.mkdir()
        (self.runtimes / "build").symlink_to(outside)
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.revision(), "oldrev000")

    def test_archive_external_symlink_is_refused_before_app_replacement(self):
        stage = self.base / "hostile-stage"
        self.make_app(stage / "Dryport.app", "newrev111", "new binary")
        exe = stage / "Dryport.app/Contents/MacOS/Dryport"
        original = self.app / "Contents/MacOS/Dryport"
        exe.unlink()
        exe.symlink_to(original)
        archive = next(self.release.glob("*.tar.xz"))
        with tarfile.open(archive, "w:xz") as tar:
            tar.add(stage / "Dryport.app", arcname="Dryport.app")
        path = self.release / "release-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["app"].update(sha256=fixtures.sha256(archive), bytes=archive.stat().st_size)
        path.write_text(json.dumps(manifest))
        result = self.run_update("--from", str(self.release))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("归档", result.stderr)
        self.assertEqual(self.revision(), "oldrev000")
