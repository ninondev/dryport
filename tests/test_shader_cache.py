# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""缓存机制回归，使用合成输入与假编译器；不读取游戏、不运行 GPU。"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/unity-retarget"))
import shader_cache
from shader_cache import ShaderCache

RESULT = ("#version 150\nvoid main() {}\n", [{"k": "input", "name": "position", "bind": 0}])


class ShaderCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shader-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.compiler = self.root / "compiler"
        self.compiler.write_bytes(b"compiler-A")
        self.library = self.root / "compiler-library"
        self.library.write_bytes(b"library-A")
        self.env_patch = mock.patch.dict(os.environ, {key: value for key, value in os.environ.items() if not key.startswith("DYLD_")}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.cache = self.new_cache()
        self.calls = 0

    def new_cache(self, **kwargs):
        return ShaderCache(self.root / "cache", dependency_resolver=lambda compiler: [self.library], **kwargs)

    def compile(self):
        self.calls += 1
        return RESULT[0], [dict(line) for line in RESULT[1]]

    def run_cached(self, cache=None, data=b"DXBC-sample", lang="150", flags="20888", function=None):
        return (cache or self.cache).run(data, self.compiler, lang, flags, function or self.compile)

    def test_persistent_hit_preserves_exact_bytes_and_returns_fresh_reflection(self):
        one = self.run_cached()
        one[1][0]["name"] = "caller mutation"
        two = self.run_cached(self.new_cache())
        self.assertEqual(two[0].encode(), RESULT[0].encode())
        self.assertEqual(two[1], RESULT[1])
        self.assertEqual(self.calls, 1)

    def test_compiler_and_dynamic_library_changes_invalidate_even_with_same_mtime(self):
        self.run_cached()
        for path, contents in ((self.compiler, b"compiler-B"), (self.library, b"library-B")):
            stat = path.stat()
            path.write_bytes(contents)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 3)

    def test_compiler_changed_during_work_does_not_store_under_old_key(self):
        def changed_while_compiling():
            self.library.write_bytes(b"library-B")
            return RESULT
        self.assertEqual(self.run_cached(function=changed_while_compiling), RESULT)
        self.assertEqual(list(self.cache.directory.glob("??/*.json")), [])
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 1)

    def test_macho_dependency_symlink_switch_invalidates_identity(self):
        self.compiler.write_bytes(b"\xcf\xfa\xed\xfecompiler")
        self.library.write_bytes(b"\xcf\xfa\xed\xfelibrary-A")
        second = self.root / "library-B"
        second.write_bytes(b"\xcf\xfa\xed\xfelibrary-B")
        link = self.root / "active.dylib"
        link.symlink_to(self.library)

        def otool(command, **kwargs):
            if command[1] == "-l":
                return f"cmd LC_RPATH\ncmdsize 64\npath {self.root} (offset 12)\n" if command[2] == str(self.compiler.resolve()) else ""
            return "binary:\n\t@rpath/active.dylib (compatibility version 1.0.0)\n" if command[2] == str(self.compiler.resolve()) else "binary:\n"

        identity = shader_cache.CompilerIdentity()
        with mock.patch.object(shader_cache.subprocess, "check_output", side_effect=otool):
            before = identity.fingerprint(self.compiler)
            link.unlink()
            link.symlink_to(second)
            after = identity.fingerprint(self.compiler)
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_input_language_flags_and_protocol_are_separate_keys(self):
        self.run_cached()
        self.run_cached(data=b"DXBC-other")
        self.run_cached(lang="metal")
        self.run_cached(flags="a0080")
        with mock.patch.object(shader_cache, "PROTOCOL", shader_cache.PROTOCOL + 1):
            self.run_cached()
        self.assertEqual(self.calls, 5)

    def test_corrupt_json_and_valid_json_with_wrong_digest_recompile(self):
        self.run_cached()
        path, = self.cache.directory.glob("??/*.json")
        for contents in (b"{broken", b"[]"):
            path.write_bytes(contents)
            self.assertEqual(self.run_cached(), RESULT)
        entry = json.loads(path.read_bytes())
        entry["payload"][0] = "corrupted-but-valid-json"
        path.write_text(json.dumps(entry))
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 4)
        self.assertEqual(self.cache.stats()["invalid"], 3)

    def test_wrong_reflection_with_recomputed_digest_is_not_a_hit(self):
        self.run_cached()
        path, = self.cache.directory.glob("??/*.json")
        entry = json.loads(path.read_bytes())
        entry["payload"][1][0]["bind"] = "not-an-integer"
        entry["sha256"] = hashlib.sha256(shader_cache._json(entry["payload"])).hexdigest()
        path.write_text(json.dumps(entry))
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 2)

    def test_parallel_identical_requests_compile_once(self):
        count_lock = threading.Lock()
        entered = threading.Event()
        release = threading.Event()

        def slow_compile():
            with count_lock:
                self.calls += 1
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release compiler")
            return RESULT

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(self.run_cached, function=slow_compile) for _ in range(8)]
            self.assertTrue(entered.wait(5))
            deadline = time.monotonic() + 5
            while self.cache.stats().get("waits", 0) < 7 and time.monotonic() < deadline:
                time.sleep(0.005)
            release.set()
            self.assertEqual([future.result(5) for future in futures], [RESULT] * 8)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.cache.stats()["waits"], 7)

    def test_failure_and_exception_are_retried(self):
        attempts = iter([(None, "translate failed"), RuntimeError("compiler crashed"), RESULT])

        def flaky():
            self.calls += 1
            result = next(attempts)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(self.run_cached(function=flaky), (None, "translate failed"))
        with self.assertRaisesRegex(RuntimeError, "compiler crashed"):
            self.run_cached(function=flaky)
        self.assertEqual(self.run_cached(function=flaky), RESULT)
        self.assertEqual(self.run_cached(function=flaky), RESULT)
        self.assertEqual(self.calls, 3)

    def test_empty_or_malformed_success_is_not_cached(self):
        for bad in (("", []), ("source", [{"k": "input", "name": "x"}]), ("source", "not reflection")):
            callback = mock.Mock(return_value=bad)
            self.run_cached(function=callback)
            self.run_cached(function=callback)
            self.assertEqual(callback.call_count, 2)
        self.assertEqual(list(self.cache.directory.glob("??/*.json")), [])

    def test_disk_write_failure_does_not_change_compilation_result(self):
        (self.root / "cache").write_text("a file blocks the directory")
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.cache.stats()["write_errors"], 2)

    def test_disable_and_unknown_compiler_identity_bypass(self):
        disabled = self.new_cache(enabled=False)
        self.assertEqual(self.run_cached(disabled), RESULT)
        self.assertEqual(self.run_cached(disabled), RESULT)
        with mock.patch.dict(os.environ, {"DYLD_LIBRARY_PATH": "/unresolved"}):
            self.assertEqual(self.run_cached(), RESULT)
        unknown = ShaderCache(self.root / "unknown", dependency_resolver=mock.Mock(side_effect=OSError("missing library")))
        self.assertEqual(self.run_cached(unknown), RESULT)
        self.assertEqual(self.run_cached(unknown), RESULT)
        self.assertEqual(unknown.identity._resolve.call_count, 1)
        self.assertEqual(self.calls, 5)
        self.assertFalse((self.root / "cache").exists())

    def test_missing_compiler_preserves_uncached_behavior(self):
        self.assertEqual(self.cache.run(b"DXBC", self.root / "missing", "150", "0", self.compile), RESULT)
        self.assertEqual(self.calls, 1)

    def test_atomic_write_failure_leaves_no_partial_entry(self):
        with mock.patch.object(shader_cache.os, "replace", side_effect=OSError("interrupted write")):
            self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(list(self.cache.directory.glob("??/*")), [])
        self.assertEqual(self.run_cached(), RESULT)
        self.assertEqual(self.calls, 2)

    def test_budget_pruning_never_changes_a_result(self):
        cache = self.new_cache(max_bytes=512)
        for i in range(64):
            self.assertEqual(self.run_cached(cache, data=str(i).encode()), RESULT)
        files = list(cache.directory.glob("??/*.json"))
        self.assertLessEqual(sum(path.stat().st_size for path in files), 512)
        self.assertGreater(cache.stats()["evictions"], 0)


if __name__ == "__main__":
    unittest.main()
