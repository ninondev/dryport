# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
import importlib.util
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("renpy_inspect", Path(__file__).resolve().parents[1] / "lib/renpy-inspect.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
VERSION = b"branch='fix'\nnightly=False\nofficial=True\nversion='8.1.3.23091805'\nversion_name='test'\n"


class RenpyInspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.game, self.sdk = self.base / "game", self.base / "sdk"
        for root in (self.game, self.sdk):
            (root / "renpy").mkdir(parents=True)
            (root / "renpy/vc_version.py").write_bytes(VERSION)
            (root / "renpy/core.py").write_bytes(b"value = 1\n\n")
            (root / "game").mkdir()
        (self.sdk / "renpy.py").write_bytes(b"print('launcher')\n")
        (self.game / "mygame.py").write_bytes(b"print('launcher')\n")

    def test_full_source_difference_is_visible_despite_equal_version(self):
        (self.game / "renpy/core.py").write_bytes(b"value = 2\n\n")
        result = M.inspect(self.game, self.sdk)
        self.assertTrue(result["version_fields_equal"])
        self.assertFalse(result["engine_sources"]["all_bytes_equal"])
        self.assertFalse(result["engine_sources"]["differences"][0]["ast_equal"])

    def test_whitespace_is_still_a_byte_difference(self):
        (self.game / "renpy/core.py").write_bytes(b"value = 1\n   \n")
        result = M.inspect(self.game, self.sdk)
        self.assertEqual(result["engine_sources"]["bytes_equal_count"], 1)
        self.assertTrue(result["engine_sources"]["differences"][0]["ast_equal"])
        self.assertTrue(result["root_python_vs_sdk_launcher"][0]["bytes_equal"])

    def test_missing_and_extra_sources_are_not_discarded(self):
        (self.game / "renpy/extra.py").write_bytes(b"x = 1")
        (self.sdk / "renpy/sdk_only.py").write_bytes(b"x = 1")
        result = M.inspect(self.game, self.sdk)["engine_sources"]
        self.assertEqual(result["missing_from_sdk"], ["renpy/extra.py"])
        self.assertEqual(result["missing_from_game"], ["renpy/sdk_only.py"])
        self.assertFalse(result["all_bytes_equal"])

    def test_version_is_never_executed_and_ambiguity_is_rejected(self):
        marker = self.base / "executed"
        malicious = VERSION + f"open({str(marker)!r}, 'w').write('oops')\n".encode()
        for data in (malicious, VERSION + b"version='8.1.3.23091805'\n",
                     VERSION.replace(b"False", b"0"), VERSION.replace(b"8.1.3.23091805", b"8.1.3"),
                     VERSION + b"\n" * M.MAX_METADATA):
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                M.parse_version(data)
        self.assertFalse(marker.exists())

    def test_rpa_and_native_dependencies_remain_explicit(self):
        (self.game / "game/scripts.rpa").write_bytes(b"do not unpickle me")
        (self.game / "game/plugin.pyd").write_bytes(b"native")
        result = M.inspect(self.game, self.sdk)
        self.assertEqual(result["uninspected_archives"], ["game/scripts.rpa"])
        self.assertEqual(result["game_native_files"], ["game/plugin.pyd"])
        self.assertNotIn("playable", result)

    def test_links_and_oversize_files_are_refused(self):
        (self.game / "renpy/link.py").symlink_to(self.sdk / "renpy/core.py")
        with self.assertRaises(ValueError):
            M.inspect(self.game, self.sdk)
        with self.assertRaises(ValueError):
            M.read_regular(self.sdk / "renpy/core.py", 1)

    def test_common_script_change_does_not_disappear_in_python_count(self):
        for root in (self.game, self.sdk):
            (root / "renpy/common").mkdir()
            (root / "renpy/common/00steam.rpy").write_bytes(b"init python:\n    # log()\n")
            (root / "renpy/common/helpers.rpym").write_bytes(b"label helper:\n    return\n")
        (self.game / "renpy/common/00steam.rpy").write_bytes(b"init python:\n    log()\n")
        result = M.inspect(self.game, self.sdk)
        self.assertEqual(result["engine_sources"]["game_count"], 2)
        self.assertTrue(result["engine_sources"]["all_bytes_equal"])
        files = result["engine_files"]
        self.assertEqual(files["game_count"], 4)
        self.assertFalse(files["all_hashes_equal"])
        self.assertEqual([d["path"] for d in files["differences"]], ["renpy/common/00steam.rpy"])
        self.assertEqual(files["differences"][0]["kind"], "renpy_source")
        self.assertNotIn("ast_equal", files["differences"][0])

    def test_binary_compiled_and_archive_files_are_hashed_without_decoding(self):
        for name in ("compiled.rpyc", "module.rpymc", "archive.rpa", "plugin.so.1", "unknown.dat"):
            (self.game / "renpy" / name).write_bytes(b"\x80\x04\x00game")
            (self.sdk / "renpy" / name).write_bytes(b"\x80\x04\x00sdk")
        result = M.inspect(self.game, self.sdk)
        differences = {d["path"]: d for d in result["engine_files"]["differences"]}
        self.assertEqual(differences["renpy/compiled.rpyc"]["kind"], "compiled")
        self.assertEqual(differences["renpy/module.rpymc"]["kind"], "compiled")
        self.assertEqual(differences["renpy/archive.rpa"]["kind"], "archive")
        self.assertEqual(differences["renpy/plugin.so.1"]["kind"], "native")
        self.assertEqual(differences["renpy/unknown.dat"]["kind"], "other")
        self.assertTrue(all("ast_equal" not in d for d in differences.values()))
        self.assertFalse(result["engine_files"]["semantic_compatibility_checked"])

    def test_missing_non_python_files_are_reported(self):
        (self.game / "renpy/extra.rpymc").write_bytes(b"game")
        (self.sdk / "renpy/sdk-only.dat").write_bytes(b"sdk")
        result = M.inspect(self.game, self.sdk)["engine_files"]
        self.assertEqual(result["missing_from_sdk"], ["renpy/extra.rpymc"])
        self.assertEqual(result["missing_from_game"], ["renpy/sdk-only.dat"])
        self.assertFalse(result["all_hashes_equal"])

    def test_non_python_file_larger_than_source_limit_is_streamed(self):
        content = b"large binary\x00" * 100_000
        for root in (self.game, self.sdk):
            (root / "renpy/large.rpyc").write_bytes(content)
        with mock.patch.object(M, "MAX_SOURCE", 1024):
            result = M.inspect(self.game, self.sdk)
        info = result["engine_files"]["game_inventory"]["renpy/large.rpyc"]
        self.assertEqual(info["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(info["size"], len(content))
        self.assertTrue(result["engine_files"]["all_hashes_equal"])

    def test_binary_reads_are_bounded_and_do_not_retain_payload(self):
        path = self.base / "large.rpyc"
        content = b"binary" * 100_000
        path.write_bytes(content)
        fdopen = M.os.fdopen
        read_sizes = []
        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, size=-1):
                read_sizes.append(size)
                return self.stream.read(size)
        with mock.patch.object(M, "READ_CHUNK", 4096), mock.patch.object(
                M.os, "fdopen", side_effect=lambda *a, **kw: ObservedStream(fdopen(*a, **kw))):
            info, retained = M.fingerprint_regular(path, len(content))
        self.assertIsNone(retained)
        self.assertEqual(info["sha256"], hashlib.sha256(content).hexdigest())
        self.assertGreater(len(read_sizes), 1)
        self.assertTrue(all(0 < size <= 4096 for size in read_sizes))

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        path = self.base / "fifo"
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            M.read_regular(path, 1024)

    def test_total_file_budget_and_late_tree_change_fail(self):
        with mock.patch.object(M, "MAX_ENGINE_TOTAL", 1):
            with self.assertRaises(ValueError):
                M.inspect(self.game, self.sdk)
        original = M.fingerprint_regular
        changed = False
        def add_after_read(path, *args, **kwargs):
            nonlocal changed
            result = original(path, *args, **kwargs)
            if not changed and Path(path).name == "vc_version.py":
                changed = True
                (self.game / "renpy/late.dat").write_bytes(b"late")
            return result
        with mock.patch.object(M, "fingerprint_regular", side_effect=add_after_read):
            with self.assertRaises(ValueError):
                M.inspect(self.game, self.sdk)

    def test_optional_python_ast_does_not_affect_hash_comparison(self):
        (self.game / "renpy/core.py").write_bytes(b"value=1\n")
        result = M.inspect(self.game, self.sdk, compare_ast=False)
        self.assertNotIn("ast_equal", result["engine_sources"]["differences"][0])
        self.assertFalse(result["engine_sources"]["all_bytes_equal"])


if __name__ == "__main__":
    unittest.main()
