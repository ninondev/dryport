# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""原厂运行时校验不得把缺文件、写坏文件或并发覆盖判成就绪。"""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import runtime_archive as runtime


class RuntimeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sn-runtime-archive-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def archive(self, extra=()):
        path = self.base / "runtime.zip"
        with zipfile.ZipFile(path, "w") as out:
            executable = zipfile.ZipInfo("love.app/Contents/MacOS/love")
            executable.external_attr = (stat.S_IFREG | 0o755) << 16
            out.writestr(executable, b"immutable runtime")
            link = zipfile.ZipInfo("love.app/runtime")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            out.writestr(link, b"Contents/MacOS/love")
            for name, value in extra:
                out.writestr(name, value)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_roundtrip_preserves_original_bytes_links_and_executable(self):
        archive, sha = self.archive()
        manifest = runtime.archive_manifest(archive, sha, "love.app")
        output = self.base / "expanded"
        runtime.extract_verified(archive, output, manifest)
        self.assertEqual(runtime.verify_tree(output, manifest, "love.app"), output / "love.app")
        self.assertEqual(os.readlink(output / "love.app/runtime"), "Contents/MacOS/love")
        self.assertTrue(os.access(output / "love.app/Contents/MacOS/love", os.X_OK))

    def test_hash_mismatch_and_archive_symlink_are_rejected(self):
        archive, sha = self.archive()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            runtime.archive_manifest(archive, "0" * 64, "love.app")
        alias = self.base / "alias.zip"
        alias.symlink_to(archive)
        with self.assertRaisesRegex(ValueError, "软链"):
            runtime.archive_manifest(alias, sha, "love.app")
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), sha)

    def test_unsafe_paths_and_link_parents_never_reach_extraction(self):
        for name in ("../outside", "/outside", "love.app/../outside", "love.app/./alias", "love.app/runtime/child", "love.app/RUNTIME"):
            with self.subTest(name=name):
                archive, sha = self.archive([(name, b"bad")])
                with self.assertRaises(ValueError):
                    runtime.archive_manifest(archive, sha, "love.app")

    def test_mutation_missing_extra_and_permissions_fail_readback(self):
        archive, sha = self.archive()
        manifest = runtime.archive_manifest(archive, sha, "love.app")
        for case in ("changed", "missing", "extra", "mode", "link"):
            with self.subTest(case=case):
                output = self.base / case
                runtime.extract_verified(archive, output, manifest)
                executable = output / "love.app/Contents/MacOS/love"
                if case == "changed": executable.write_bytes(b"changed runtime")
                elif case == "missing": executable.unlink()
                elif case == "extra": (output / "love.app/addition").write_text("extra")
                elif case == "mode": executable.chmod(0o644)
                else:
                    (output / "love.app/runtime").unlink()
                    (output / "love.app/runtime").symlink_to("/tmp")
                with self.assertRaises((ValueError, OSError)):
                    runtime.verify_tree(output, manifest, "love.app")

    def test_existing_extract_destination_is_never_merged(self):
        archive, sha = self.archive()
        manifest = runtime.archive_manifest(archive, sha, "love.app")
        output = self.base / "existing"
        output.mkdir()
        (output / "mine").write_text("keep")
        with self.assertRaises(FileExistsError):
            runtime.extract_verified(archive, output, manifest)
        self.assertEqual([p.name for p in output.iterdir()], ["mine"])

    @unittest.skipUnless(platform.system() == "Darwin", "macOS 原子发布")
    def test_publish_does_not_replace_even_empty_concurrent_destination(self):
        source = self.base / "stage"
        target = self.base / "target"
        source.mkdir()
        target.mkdir()
        (source / "ours").write_text("prepared")
        with self.assertRaises(FileExistsError):
            runtime.publish(source, target)
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual((source / "ours").read_text(), "prepared")

    def test_existing_invalid_version_is_preserved_without_network(self):
        root = self.base / "runtimes"
        target = root / "love/11.5"
        target.mkdir(parents=True)
        marker = target / "mine"
        marker.write_text("keep")
        with mock.patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("network")):
            with self.assertRaises(ValueError):
                runtime.install_love(root)
        self.assertEqual(list(target.iterdir()), [marker])
        self.assertEqual(marker.read_text(), "keep")

    def test_download_corruption_preserves_evidence_without_publishing(self):
        root = self.base / "runtimes"
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b"corrupt archive")
        with mock.patch.object(runtime.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                runtime.install_love(root)
        self.assertFalse((root / "love/11.5").exists())
        archives = list((root / "love").glob(".love-download-*/*.zip"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), b"corrupt archive")

    def test_download_does_not_follow_retargeted_root_or_love_parent_alias(self):
        for kind in ('root', 'love-parent'):
            with self.subTest(kind=kind):
                base = self.base / kind
                base.mkdir()
                actual, redirected = base / 'actual', base / 'redirected'
                actual.mkdir()
                redirected.mkdir()
                if kind == 'root':
                    root = alias = base / 'root-alias'
                    alias.symlink_to(actual)
                    stage_parent, redirected_parent = actual / 'love', redirected / 'love'
                else:
                    root = base / 'root'
                    root.mkdir()
                    alias = root / 'love'
                    alias.symlink_to(actual)
                    stage_parent, redirected_parent = actual, redirected
                writes = []

                def response(*args, **kwargs):
                    stage = next(stage_parent.glob('.love-download-*'))
                    # 模拟联网等待期间用户调整了原始路径的父软链。
                    alias.unlink()
                    alias.symlink_to(redirected)
                    other = redirected_parent / stage.name
                    other.mkdir(parents=True)
                    writes.append(other / runtime.LOVE_ARCHIVE)
                    return io.BytesIO(b'corrupt fixture; no network')

                opener = mock.Mock()
                opener.open.side_effect = response
                with mock.patch.object(runtime.urllib.request, 'build_opener', return_value=opener):
                    with self.assertRaises(ValueError):
                        runtime.install_love(root)
                self.assertFalse(writes[0].exists(), '下载重新解释父软链并写到另一位置')
                archives = list(stage_parent.glob('.love-download-*/*.zip'))
                self.assertEqual(len(archives), 1)
                self.assertEqual(archives[0].read_bytes(), b'corrupt fixture; no network')

    def node_tarball(self, top="node-v22.11.0-darwin-arm64", extra=()):
        import tarfile
        path = self.base / "node.tar.xz"
        with tarfile.open(path, "w:xz") as tar:
            for rel in ("bin", "lib", "share"):
                info = tarfile.TarInfo(f"{top}/{rel}"); info.type = tarfile.DIRTYPE; info.mode = 0o755; tar.add if False else tar.addfile(info)
            tar.add("/usr/bin/true", arcname=f"{top}/bin/node")          # 真 Mach-O，file 报告含 arm64，任何参数都退出 0
            for rel, data in extra:
                info = tarfile.TarInfo(f"{top}/{rel}"); info.size = len(data); info.mode = 0o644; tar.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo(f"{top}/bin/npm"); info.type = tarfile.SYMTYPE; info.linkname = "../lib/node_modules/npm/bin/npm-cli.js"; tar.addfile(info)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_node_install_verifies_sha_extracts_safely_and_is_idempotent(self):
        archive, sha = self.node_tarball()
        root = self.base / "runtimes"
        opener = mock.Mock(); opener.open.return_value = archive.open("rb")
        with mock.patch.object(runtime.urllib.request, "build_opener", return_value=opener), mock.patch.object(runtime, "NODE_SHA256", sha):
            node = runtime.install_node(root)
        self.assertEqual(node.resolve(), (root / "node/22.11.0/bin/node").resolve())   # 安装函数返回的是 resolve 过的路径
        self.assertTrue(node.is_file() and os.access(node, os.X_OK))
        self.assertTrue((root / "node/22.11.0/bin/npm").is_symlink())
        self.assertEqual(list((root / "node").glob(".node-download-*")), [])
        self.assertEqual(runtime.check_node(root / "node/22.11.0").resolve(), node.resolve())
        with mock.patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("不该再下载")):
            self.assertEqual(runtime.install_node(root).resolve(), node.resolve())                 # 已有版本不重下

    def test_node_install_rejects_bad_sha_and_escaping_members(self):
        archive, sha = self.node_tarball()
        root = self.base / "runtimes"
        opener = mock.Mock(); opener.open.return_value = archive.open("rb")
        with mock.patch.object(runtime.urllib.request, "build_opener", return_value=opener), mock.patch.object(runtime, "NODE_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                runtime.install_node(root)
        self.assertFalse((root / "node/22.11.0").exists())
        self.assertEqual(len(list((root / "node").glob(".node-download-*/*.tar.xz"))), 1)   # 现场保留
        bad, sha_bad = self.node_tarball(extra=(("../escape.txt", b"x"),))
        opener = mock.Mock(); opener.open.return_value = bad.open("rb")
        with mock.patch.object(runtime.urllib.request, "build_opener", return_value=opener), mock.patch.object(runtime, "NODE_SHA256", sha_bad):
            with self.assertRaisesRegex(ValueError, "越界"):
                runtime.install_node(self.base / "runtimes2")
        self.assertFalse((self.base / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
