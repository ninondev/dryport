# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""独立 app 的升级与回退：只按清单核过 SHA256 与提交号才换 app，旧版保留可回退；全部用临时目录与假 codesign。"""
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/self-update.sh"


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-self-update-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.bin = self.base / "fakebin"; self.bin.mkdir()
        (self.bin / "codesign").write_text('#!/bin/bash\nexit "${SN_TEST_SIGN_FAIL:-0}"\n'); (self.bin / "codesign").chmod(0o755)
        self.runtimes = self.base / "runtimes"
        self.games = self.base / "games"; self.games.mkdir()
        self.app = self.games / "Dryport.app"
        self.make_app(self.app, "oldrev000", "old binary")
        self.release = self.base / "release"; self.release.mkdir()
        self.publish("newrev111", "new binary")
        self.env = {**os.environ, "PATH": str(self.bin) + ":/usr/bin:/bin", "SN_HOME": str(ROOT),
                    "SN_RUNTIMES": str(self.runtimes), "SN_GAMES": str(self.games)}

    def make_app(self, path, revision, payload, version="0.1.0"):
        (path / "Contents/MacOS").mkdir(parents=True)
        (path / "Contents/MacOS/Dryport").write_text("#!/bin/bash\necho " + payload + "\n")
        (path / "Contents/MacOS/Dryport").chmod(0o755)
        (path / "Contents/Resources/toolchain").mkdir(parents=True)
        (path / "Contents/Resources/toolchain/SOURCE-REVISION.txt").write_text(revision + "\n")
        (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))

    def publish(self, revision, payload, version="0.2.0"):
        self.published = getattr(self, "published", 0) + 1
        stage = self.base / f"stage-{revision}-{self.published}"
        self.make_app(stage / "Dryport.app", revision, payload, version)
        archive = self.release / f"Dryport-{version}.app.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            tar.add(stage / "Dryport.app", arcname="Dryport.app")
        (self.release / "release-manifest.json").write_text(json.dumps({
            "manifest_version": 1, "version": version, "revision": revision,
            "app": {"archive": archive.name, "bundle": "Dryport.app", "sha256": sha256(archive), "bytes": archive.stat().st_size}}))
        return archive

    def run_update(self, *args, **extra):
        return subprocess.run(["bash", str(SCRIPT), "--app", str(self.app), *args], env={**self.env, **extra},
                              capture_output=True, text=True, timeout=60)

    def revision(self):
        return (self.app / "Contents/Resources/toolchain/SOURCE-REVISION.txt").read_text().strip()

    def test_check_reports_update_then_up_to_date_after_install(self):
        r = self.run_update("--from", str(self.release), "--check")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr); self.assertIn("有新版本", r.stdout)
        r = self.run_update("--from", str(self.release))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.revision(), "newrev111")

        self.assertIn("new binary", (self.app / "Contents/MacOS/Dryport").read_text())
        previous = list(self.games.glob(".dryport-previous.*/Dryport.app"))
        self.assertEqual(len(previous), 1)
        self.assertIn("old binary", (previous[0] / "Contents/MacOS/Dryport").read_text())
        self.assertEqual(list(self.games.glob(".dryport-update.*")), [])
        r = self.run_update("--from", str(self.release), "--check")
        self.assertEqual(r.returncode, 0); self.assertIn("已是最新", r.stdout)
        r = self.run_update("--from", str(self.release))
        self.assertNotEqual(r.returncode, 0); self.assertIn("not newer", r.stderr)
        self.assertEqual(self.revision(), "newrev111")

    def test_runtime_root_symlink_is_resolved_before_cache_paths(self):
        real = self.base / "runtime storage"
        real.mkdir()
        alias = self.base / "runtime alias"
        alias.symlink_to(real, target_is_directory=True)
        result = self.run_update("--from", str(self.release), SN_RUNTIMES=str(alias))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.revision(), "newrev111")
        self.assertTrue(list((real / "build/updates").glob("*.tar.xz")))
        self.assertTrue(alias.is_symlink())

    def test_tampered_archive_or_bad_signature_leaves_app_untouched(self):
        archive = next(self.release.glob("*.tar.xz"))
        data = bytearray(archive.read_bytes()); data[-1] ^= 0xFF; archive.write_bytes(bytes(data))
        r = self.run_update("--from", str(self.release))
        self.assertNotEqual(r.returncode, 0); self.assertIn("SHA256 不符", r.stdout + r.stderr)
        self.assertEqual(self.revision(), "oldrev000")
        self.assertTrue(list((self.runtimes / "build/updates").glob("*.bad")))
        self.publish("newrev111", "new binary")
        r = self.run_update("--from", str(self.release), SN_TEST_SIGN_FAIL="3")
        self.assertNotEqual(r.returncode, 0); self.assertIn("签名核对不过", r.stderr)
        self.assertEqual(self.revision(), "oldrev000")
        self.assertEqual(list(self.games.glob(".dryport-update.*")), [])
        self.assertEqual(list(self.games.glob(".dryport-previous.*")), [])

    def test_runtime_root_line_break_cannot_select_a_different_directory(self):
        self.runtimes.mkdir()
        requested = self.base / (self.runtimes.name + "\n")
        requested.mkdir()
        result = self.run_update("--from", str(self.release), SN_RUNTIMES=str(requested))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.revision(), "oldrev000")
        self.assertEqual(list(self.runtimes.iterdir()), [])
        self.assertEqual(list(requested.iterdir()), [])

    def test_revision_mismatch_inside_archive_is_refused(self):
        manifest = json.loads((self.release / "release-manifest.json").read_text())
        manifest["revision"] = "someotherrev"; (self.release / "release-manifest.json").write_text(json.dumps(manifest))
        r = self.run_update("--from", str(self.release))
        self.assertNotEqual(r.returncode, 0); self.assertIn("提交号与清单不符", r.stderr)
        self.assertEqual(self.revision(), "oldrev000")

    def test_rollback_restores_the_previous_app(self):
        self.assertEqual(self.run_update("--from", str(self.release)).returncode, 0)
        self.assertEqual(self.revision(), "newrev111")
        r = self.run_update("--rollback")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.revision(), "oldrev000")
        self.assertTrue(list(self.games.glob(".dryport-rolledback.*/Dryport.app")))
        self.assertEqual(list(self.games.glob(".dryport-previous.*")), [])
        r = self.run_update("--rollback")
        self.assertNotEqual(r.returncode, 0); self.assertIn("没有可回退", r.stderr)

    def test_missing_source_and_dev_build_are_explained(self):
        r = self.run_update()
        self.assertNotEqual(r.returncode, 0); self.assertIn("--from", r.stderr)
        dev = self.games / "Dev.app"; (dev / "Contents/MacOS").mkdir(parents=True)
        r = subprocess.run(["bash", str(SCRIPT), "--app", str(dev), "--from", str(self.release), "--check"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("installed version", r.stderr)
        self.assertIn("已装 dev", r.stderr)


if __name__ == "__main__":
    unittest.main()
