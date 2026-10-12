# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""固定原厂最小依赖的选择、验证和发布边界，不启动游戏。"""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
R = importlib.import_module("renpy-runtime")


class RenpyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sn-renpy-runtime-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.output = self.base / "result"
        self.sdk_files = {"LICENSE.txt": b"original notices", "lib/python3.9/encodings/__init__.pyc": b"stdlib",
                          "lib/python3.9/vendor/helper.pyc": b"keep entire selected tree",
                          "lib/python3.9/sitecustomize.pyc": b"official paths",
                          "renpy/__init__.py": b"game owns engine", "common/00start.rpy": b"game owns common",
                          "lib/py3-windows-x86_64/python.exe": b"not selected", "launcher/editor": b"not selected"}
        self.sdk_files.update({"lib/py3-mac-universal/" + name: b"original universal " + name.encode()
                               for name in R.SDK_BINARIES})
        self.steam_files = {R.STEAM_LIBRARY: b"original Steam universal library",
                            "lib/py3-linux-x86_64/libsteam_api.so": b"not selected"}
        self.sdk = self.archive("sdk.zip", {R.SDK_TOP + "/" + key: value for key, value in self.sdk_files.items()})
        self.steam = self.archive("steam.zip", self.steam_files)
        for name, path in (("SDK_SHA256", self.sdk), ("STEAM_SHA256", self.steam)):
            patch = mock.patch.object(R, name, R.sha(path))
            patch.start()
            self.addCleanup(patch.stop)
        self.native = mock.patch.object(R, "validate_native", return_value={"game_started": False})
        self.native_mock = self.native.start()
        self.addCleanup(self.native.stop)
        for name, value in (("SN_RUNTIMES", self.base / "runtimes"),
                            ("SDK_BYTES", self.sdk.stat().st_size),
                            ("STEAM_BYTES", self.steam.stat().st_size)):
            patch = mock.patch.object(R, name, value, create=True)
            patch.start()
            self.addCleanup(patch.stop)

    def archive(self, name, items):
        target = self.base / name
        with zipfile.ZipFile(target, "w") as archive:
            for key, value in items.items():
                info = zipfile.ZipInfo(key)
                info.external_attr = (stat.S_IFREG | (0o755 if "/py3-mac-universal/" in key else 0o644)) << 16
                archive.writestr(info, value)
        return target

    def prepare(self):
        return R.prepare(self.sdk, self.steam, self.output)

    def test_selected_complete_trees_and_original_steam_bytes_only(self):
        before = {p: R.sha(p) for p in (self.sdk, self.steam)}
        result = self.prepare()
        runtime = self.output / "Runtime"
        expected = {key: value for key, value in self.sdk_files.items()
                    if any(key.startswith(path + "/") for path in R.SELECTED_ROOTS)}
        expected[R.STEAM_LIBRARY] = self.steam_files[R.STEAM_LIBRARY]
        actual = {p.relative_to(runtime).as_posix(): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)
        self.assertEqual(result["payload"]["files"], len(expected))
        self.assertEqual(result["payload"]["bytes"], sum(map(len, expected.values())))
        self.assertEqual((self.output / "LICENSE.txt").read_bytes(), self.sdk_files["LICENSE.txt"])
        self.assertEqual({p: R.sha(p) for p in before}, before)
        self.assertEqual(R.check(self.output)["runtime"], str(runtime))
        self.assertFalse((runtime / "steam_appid.txt").exists())
        self.assertFalse(result["game_started"])

    def test_existing_valid_output_reused_without_execution_or_mutation(self):
        self.prepare()
        before = {p: (p.stat().st_mtime_ns, R.sha(p)) for p in self.output.rglob("*") if p.is_file()}
        self.native_mock.side_effect = AssertionError("must not execute reused runtime")
        self.prepare()
        self.assertEqual({p: (p.stat().st_mtime_ns, R.sha(p)) for p in before}, before)

    def test_existing_unrelated_directory_and_symlink_preserved(self):
        self.output.mkdir()
        owned = self.output / "mine"
        owned.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(owned.read_bytes(), b"keep")
        self.output = self.base / "alias"
        self.output.symlink_to(owned.parent)
        with self.assertRaisesRegex(ValueError, "软链"):
            self.prepare()
        self.assertTrue(self.output.is_symlink())
        self.native_mock.assert_not_called()

    def test_wrong_archive_hash_never_publishes(self):
        self.sdk.write_bytes(b"different archive")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.native_mock.assert_not_called()

    def test_unsafe_archive_entries_rejected_before_execution(self):
        for name in (R.SDK_TOP + "/../escape", R.SDK_TOP + "/lib/python3.9/SITECUSTOMIZE.pyc"):
            with self.subTest(name=name):
                with zipfile.ZipFile(self.sdk, "a") as package:
                    package.writestr(name, b"unsafe")
                with mock.patch.object(R, "SDK_SHA256", R.sha(self.sdk)), mock.patch.object(R, "SDK_BYTES", self.sdk.stat().st_size):
                    with self.assertRaises(ValueError):
                        self.prepare()
                self.assertFalse(self.output.exists())
        self.native_mock.assert_not_called()

    def test_selected_symlinks_rejected_even_if_archive_link_is_bounded(self):
        with zipfile.ZipFile(self.sdk, "a") as package:
            link = zipfile.ZipInfo(R.SDK_TOP + "/lib/python3.9/alias")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(link, "sitecustomize.pyc")
        with mock.patch.object(R, "SDK_SHA256", R.sha(self.sdk)), mock.patch.object(R, "SDK_BYTES", self.sdk.stat().st_size):
            with self.assertRaisesRegex(ValueError, "链接"):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_missing_extra_mutated_and_execute_bit_changes_fail_check(self):
        self.prepare()
        binary = self.output / "Runtime/lib/py3-mac-universal/pythonw"
        original, mode = binary.read_bytes(), binary.stat().st_mode & 0o777
        for kind in ("mutation", "missing", "mode", "extra"):
            with self.subTest(kind=kind):
                if kind == "mutation": binary.write_bytes(b"corrupt")
                elif kind == "missing": binary.unlink()
                elif kind == "mode": binary.chmod(0o644)
                else: (binary.parent / "new-dylib").write_bytes(b"extra")
                with self.assertRaises(ValueError):
                    R.check(self.output)
                with mock.patch("urllib.request.build_opener", side_effect=AssertionError("bad runtime must stop")):
                    with self.assertRaises(ValueError):
                        R.install(self.output)
                if kind == "extra": (binary.parent / "new-dylib").unlink()
                binary.write_bytes(original)
                binary.chmod(mode)

    def test_forged_receipt_does_not_make_changed_payload_ready(self):
        self.prepare()
        path = self.output / "runtime.json"
        original = path.read_text()
        for field, value in (("manifest", {}), ("version", "8.1.4"), ("protocol", True), ("sources", {})):
            data = json.loads(original)
            data[field] = value
            path.write_text(json.dumps(data))
            with self.subTest(field=field), self.assertRaises(ValueError):
                R.check(self.output, self.sdk, self.steam)
        data = json.loads(original)
        data["sources"]["sdk"]["sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "来源"):
            R.check(self.output)

    def test_native_failure_preserves_stage_and_never_publishes(self):
        self.native_mock.side_effect = ValueError("signature or stdlib failed")
        with self.assertRaisesRegex(ValueError, "signature or stdlib failed"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.assertEqual(len(list(self.base.glob(".renpy-prepare-*/package/Runtime"))), 1)

    def test_native_check_cannot_silently_modify_runtime(self):
        def mutate(runtime, state):
            (runtime / "lib/python3.9/new.pyc").write_bytes(b"should not appear")
            return {}
        self.native_mock.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "ZIP 外"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_archive_changed_during_validation_never_publishes(self):
        def mutate(runtime, state):
            self.sdk.write_bytes(b"changed")
            return {}
        self.native_mock.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "归档在准备期间变化"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_parent_same_inode_relocated_into_protected_root_refused(self):
        parent, protected = self.base / "outputs", self.base / "Steam"
        parent.mkdir()
        protected.mkdir()
        self.output = parent / "result"
        def relocate(runtime, state):
            parent.rename(protected / "relocated")
            parent.symlink_to(protected / "relocated")
            return {}
        self.native_mock.side_effect = relocate
        with mock.patch.object(R, "STEAM", protected):
            with self.assertRaisesRegex(ValueError, "规范路径或身份变化"):
                self.prepare()
        self.assertFalse((protected / "relocated/result").exists())

    def test_concurrent_existing_output_never_overwritten(self):
        def concurrent(runtime, state):
            self.output.mkdir()
            (self.output / "mine").write_bytes(b"keep")
            return {}
        self.native_mock.side_effect = concurrent
        with self.assertRaisesRegex(ValueError, "输出已存在"):
            self.prepare()
        self.assertEqual((self.output / "mine").read_bytes(), b"keep")

    def test_forbidden_output_stops_before_mkdir(self):
        self.output = ROOT / "should-never-exist-renpy-test"
        with self.assertRaisesRegex(ValueError, "输出不能"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def cache_path(self, kind):
        return R.SN_RUNTIMES / "build/renpy" / R.VERSION / getattr(R, kind + "_NAME")

    def seed_cache(self):
        for kind, source in (("SDK", self.sdk), ("STEAM", self.steam)):
            target = self.cache_path(kind)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

    def package_bytes(self):
        return {p.relative_to(self.output).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.output.rglob("*") if p.is_file()}

    def test_missing_original_archives_use_fixed_cache_without_rewriting_package(self):
        self.prepare()
        self.seed_cache()
        before = self.package_bytes()
        self.sdk.unlink()
        self.steam.unlink()
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("check must be offline")):
            self.assertEqual(R.check(self.output)["runtime"], str(self.output / "Runtime"))
        self.assertEqual(self.package_bytes(), before)

    def test_existing_bad_original_does_not_hide_behind_valid_cache(self):
        self.prepare()
        self.seed_cache()
        for kind in ("corrupt", "symlink"):
            with self.subTest(kind=kind):
                if self.sdk.exists():
                    self.sdk.unlink()
                if kind == "corrupt":
                    self.sdk.write_bytes(b"corrupt original")
                else:
                    self.sdk.symlink_to(self.cache_path("SDK"))
                with self.assertRaises(ValueError):
                    R.check(self.output)
                with mock.patch("urllib.request.build_opener", side_effect=AssertionError("bad source must stop")):
                    with self.assertRaises(ValueError):
                        R.install(self.output)

    def test_missing_archive_cache_is_read_only_failure(self):
        self.prepare()
        self.sdk.unlink()
        before = self.package_bytes()
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("check must be offline")):
            with self.assertRaises(ValueError):
                R.check(self.output)
        self.assertFalse(R.SN_RUNTIMES.exists())
        self.assertEqual(self.package_bytes(), before)

    def test_install_preserves_existing_package_and_retains_original_archives(self):
        self.prepare()
        before = self.package_bytes()
        originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.sdk, self.steam)}
        self.native_mock.side_effect = AssertionError("existing runtime must not execute")
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("existing ZIP must be reused")):
            R.install(self.output)
        self.assertEqual(self.package_bytes(), before)
        for kind, source in (("SDK", self.sdk), ("STEAM", self.steam)):
            self.assertEqual(self.cache_path(kind).read_bytes(), originals[source][0])
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), originals[source])
        self.sdk.unlink()
        self.steam.unlink()
        self.assertEqual(R.check(self.output)["runtime"], str(self.output / "Runtime"))

    def test_install_from_fixed_cache_does_not_download(self):
        self.seed_cache()
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("valid cache must be reused")):
            R.install(self.output)
        self.assertEqual(self.native_mock.call_count, 1)
        receipt = json.loads((self.output / "runtime.json").read_text())
        for kind in ("SDK", "STEAM"):
            self.assertEqual(receipt["sources"][kind.lower()]["path"], str(self.cache_path(kind)))

    def test_cache_fallback_keeps_receipt_and_runtime_checks_strict(self):
        self.prepare()
        self.seed_cache()
        self.sdk.unlink()
        self.steam.unlink()
        receipt_path = self.output / "runtime.json"
        original = receipt_path.read_bytes()
        for field, value in (("url", "https://example.invalid/sdk.zip"), ("sha256", "0" * 64),
                             ("bytes", self.cache_path("SDK").stat().st_size + 1)):
            data = json.loads(original)
            data["sources"]["sdk"][field] = value
            receipt_path.write_text(json.dumps(data))
            with self.subTest(field=field), self.assertRaises(ValueError):
                R.check(self.output)
        receipt_path.write_bytes(original)
        (self.output / "Runtime/lib/py3-mac-universal/pythonw").write_bytes(b"mutated runtime")
        with self.assertRaises(ValueError):
            R.check(self.output)

    def test_install_downloads_only_two_fixed_official_assets_and_reuses_them(self):
        payloads = {R.RELEASE_URL + R.SDK_NAME: self.sdk.read_bytes(),
                    R.RELEASE_URL + R.STEAM_NAME: self.steam.read_bytes()}
        requests = []
        def open_archive(request, *, timeout):
            requests.append(request.full_url)
            self.assertEqual(timeout, 15)
            return io.BytesIO(payloads[request.full_url])
        opener = mock.Mock()
        opener.open.side_effect = open_archive
        with mock.patch("urllib.request.build_opener", return_value=opener) as factory:
            result = R.install(self.output)
        self.assertEqual(requests, list(payloads))
        self.assertTrue(all(isinstance(call.args[0], R.ARCHIVE.HTTPSOnly) for call in factory.call_args_list))
        self.assertFalse(result["game_started"])
        before = self.package_bytes()
        self.native_mock.side_effect = AssertionError("reuse must not execute runtime")
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("reuse must not download")):
            R.install(self.output)
        self.assertEqual(self.package_bytes(), before)

    def test_short_oversize_bad_hash_and_transport_failures_never_publish(self):
        original = self.sdk.read_bytes()
        for kind, response in (("short", original[:-1]), ("oversize", original + b"extra"),
                               ("digest", bytes([original[0] ^ 1]) + original[1:]),
                               ("transport", OSError("network fixture failure"))):
            with self.subTest(kind=kind), mock.patch.object(R, "SN_RUNTIMES", self.base / kind):
                opener = mock.Mock()
                if isinstance(response, Exception):
                    opener.open.side_effect = response
                else:
                    opener.open.return_value = io.BytesIO(response)
                with mock.patch("urllib.request.build_opener", return_value=opener):
                    with self.assertRaisesRegex(ValueError, "现场保留"):
                        R.install(self.output)
                self.assertFalse(self.cache_path("SDK").exists())
                self.assertFalse(self.output.exists())
                self.assertEqual(len(list(self.cache_path("SDK").parent.glob(".renpy-download-*"))), 1)
        self.native_mock.assert_not_called()

    def test_archive_deadline_stops_before_cache_or_runtime_publish(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(self.sdk.read_bytes())
        with mock.patch("urllib.request.build_opener", return_value=opener), \
                mock.patch.object(R.time, "monotonic", side_effect=(0, 181)):
            with self.assertRaisesRegex(ValueError, "180 秒"):
                R.install(self.output)
        self.assertFalse(self.cache_path("SDK").exists())
        self.assertFalse(self.output.exists())

    def test_cache_conflicts_and_foreign_runtime_are_preserved_without_network(self):
        target = self.cache_path("SDK")
        target.parent.mkdir(parents=True)
        for kind in ("corrupt", "directory", "symlink", "dangling"):
            with self.subTest(kind=kind):
                if kind == "corrupt": target.write_bytes(b"keep cache")
                elif kind == "directory": target.mkdir()
                elif kind == "symlink": target.symlink_to(self.sdk)
                else: target.symlink_to(self.base / "does-not-exist")
                with mock.patch("urllib.request.build_opener", side_effect=AssertionError("must not download")):
                    with self.assertRaises(ValueError):
                        R.install(self.output)
                self.assertTrue(os.path.lexists(target))
                self.assertFalse(self.output.exists())
                if kind == "directory": target.rmdir()
                else: target.unlink()
        self.output.mkdir()
        mine = self.output / "mine"
        mine.write_bytes(b"foreign package")
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("must not download")):
            with self.assertRaises(ValueError):
                R.install(self.output)
        self.assertEqual(mine.read_bytes(), b"foreign package")

    def test_cache_parent_relocation_is_rejected_before_publish(self):
        protected = self.base / "Steam"
        protected.mkdir()
        original = self.sdk.read_bytes()
        parent = self.cache_path("SDK").parent
        relocated = protected / "relocated-cache"
        def move_parent():
            parent.rename(relocated)
            parent.symlink_to(relocated)
        class Response(io.BytesIO):
            moved = False
            def read(response, size=-1):
                if not response.moved:
                    move_parent()
                    response.moved = True
                return super().read(size)
        opener = mock.Mock()
        opener.open.return_value = Response(original)
        with mock.patch.object(R, "STEAM", protected), mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "规范路径变化"):
                R.install(self.output)
        self.assertFalse((relocated / R.SDK_NAME).exists())
        self.assertFalse(self.output.exists())

    def test_concurrent_cache_target_is_never_overwritten(self):
        publish = R.ARCHIVE.publish
        def competing_publish(source, destination):
            destination.write_bytes(b"concurrent cache")
            publish(source, destination)
        with mock.patch.object(R.ARCHIVE, "publish", side_effect=competing_publish):
            with self.assertRaisesRegex(ValueError, "未覆盖"):
                R.cache_archive("sdk", source=self.sdk, package=self.output)
        self.assertEqual(self.cache_path("SDK").read_bytes(), b"concurrent cache")
        self.assertFalse(self.output.exists())

    def test_cache_inside_protected_root_is_refused_before_mkdir(self):
        for protected in ("SN_HOME", "STEAM"):
            forbidden = self.base / protected
            with self.subTest(protected=protected), mock.patch.object(R, protected, forbidden), \
                    mock.patch.object(R, "SN_RUNTIMES", forbidden / "runtime-cache"):
                with self.assertRaisesRegex(ValueError, "缓存不能进入"):
                    R.cache_archive("sdk", source=self.sdk, package=self.output)
                self.assertFalse(forbidden.exists())

    def test_cli_install_failure_has_nonzero_status_and_no_success_json(self):
        from contextlib import redirect_stdout, redirect_stderr
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(R, "install", side_effect=ValueError("fixture refused")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = R.main(["install", "--output", str(self.output)])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("fixture refused", stderr.getvalue())


class OriginTests(unittest.TestCase):
    def test_valid_origin_and_loaded_dependency_escape(self):
        runtime, script = Path("/owned/Runtime"), Path("/owned/check/stdlib.py")
        data = {"architecture": "arm64", "python_version": [3, 9, 10],
                "executable": str(runtime / "lib/py3-mac-universal/pythonw"), "renpy_absent": True,
                "path": [str(script.parent), str(runtime / "lib/python3.9"), str(runtime / "lib/py3-mac-arm64")],
                "stdlib": {name: None for name in ("ctypes", "encodings", "hashlib", "json", "math", "pickle", "ssl", "zlib")},
                "loaded_images": [str(runtime / "lib/py3-mac-universal/librenpython.dylib"), "/usr/lib/libSystem.B.dylib"]}
        R.validate_origin(data, runtime, script)
        for field, value in (("loaded_images", ["/old-sdk/librenpython.dylib"]),
                             ("path", [str(script.parent), "/old-sdk/lib/python3.9"]),
                             ("renpy_absent", False), ("architecture", "x86_64")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                R.validate_origin({**data, field: value}, runtime, script)


class OwnedProcessTests(unittest.TestCase):
    def test_timeout_and_receipt_failure_reap_only_own_child(self):
        with tempfile.TemporaryDirectory(prefix="sn-renpy-owned-") as temporary:
            logs = Path(temporary)
            result = R.run_owned([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                                 logs, "timeout", timeout=0.08)
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["returncode"], -signal.SIGKILL)
            with self.assertRaises(ProcessLookupError):
                os.kill(result["pid"], 0)
            real_popen = R.subprocess.Popen
            children = []
            def capture(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            with mock.patch.object(R.subprocess, "Popen", side_effect=capture), mock.patch.object(R, "write_json", side_effect=OSError("receipt")):
                with self.assertRaisesRegex(OSError, "receipt"):
                    R.run_owned([sys.executable, "-I", "-S", "-c", "print('done')"], logs, "receipt")
            self.assertEqual(children[0].returncode, 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(children[0].pid, 0)


if __name__ == "__main__":
    unittest.main()
