# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""发布扫描与打包的反例测试，不把个人数据样本写进仓库。"""
import importlib.util
import json
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_check", ROOT / "tools/release-check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_snapshot_uses_the_policy_of_the_repository_being_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".publicignore").write_text(".publicignore\nprivate.txt\nrecords/\n")
            (root / "private.txt").write_text("Private working notes\n")
            (root / "records").mkdir()
            (root / "records/probe.md").write_text("Private probe\n")
            (root / "README.md").write_text("Public source\n")
            entries, issues = release.snapshot(root)
            self.assertEqual(set(entries), {"README.md"})
            self.assertEqual(issues, [])

    def test_private_paths_and_ids_are_flagged_without_values(self):
        samples = [("/Users/" + "private-account/game").encode(),
                   ("7656119" + "1234567890").encode()]
        for sample in samples:
            issues = release.issues_for("note.md", sample)
            self.assertTrue(issues)
            self.assertNotIn(sample.decode(), json.dumps(issues))

    def test_examples_and_public_id_constant_are_allowed(self):
        self.assertEqual(release.issues_for("note.md", b"/Users/example/game 76561197960265728"), [])
        # Wine 前缀里的 drive_c/users/<名> 是 Windows 布局，不是本机主目录；测试夹具用 users/fixture 也放行
        self.assertEqual(release.issues_for("t.py", b"prefix/drive_c/users/crossover/AppData/Local/Temp/steambridge.log"), [])
        self.assertEqual(release.issues_for("t.py", b"state/prefix/drive_c/users/fixture/x"), [])
        self.assertTrue(release.issues_for("t.py", b"/Users/" + b"somebody/Games"))   # 拼起来写，免得这份测试自己被检查器拦

    def test_private_database_and_disguised_binary_are_rejected(self):
        self.assertTrue(release.issues_for("owned-windows.json", b"[]"))
        self.assertTrue(release.issues_for("notes.md", b"MZ\0\0"))
        self.assertTrue(release.issues_for("snapshot.json", b"{}"))
        self.assertTrue(release.issues_for("project/source.py", b"../outside", True))

    def test_archive_reproducible_and_contains_only_checked_bytes(self):
        entries = {"bin/run": (b"#!/bin/sh\nexit 0\n", 0o755), "README.md": (b"source preview\n", 0o644)}
        with tempfile.TemporaryDirectory() as directory:
            one, two = Path(directory) / "a.tar.gz", Path(directory) / "b.tar.gz"
            self.assertEqual(release.archive(one, entries), release.archive(two, entries))
            with self.assertRaises(FileExistsError):
                release.archive(one, entries)
            with tarfile.open(one) as tar:
                self.assertEqual(len(tar.getnames()), 3)
                for member in tar.getmembers():
                    self.assertEqual(member.uid, 0)
                    self.assertEqual(member.uname, "")
                    self.assertEqual(member.mtime, 0)
                    self.assertNotIn(".git", member.name)
                manifest = json.load(tar.extractfile("source-preview/SOURCE-MANIFEST.json"))
                self.assertEqual(set(manifest), set(entries))


if __name__ == "__main__":
    unittest.main()
