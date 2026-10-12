# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""发布目录打包：app 包、源码快照与清单的 SHA256 必须与文件一致；用假 swift/codesign，运行时包跳过。
app 归档用 xz 压缩——只有真正要打包归档的测试在缺 xz 时跳过（
不许整类跳过，脏树拒绝这类不需要 xz 的检查必须永远运行），XzSkipScopeTests 守着这个范围。"""
import hashlib
import importlib.util
import json
import io
import shutil
import tarfile
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XZ = shutil.which("xz")


spec_pack = importlib.util.spec_from_file_location("release_pack", ROOT / "tools/release-pack.py")
release_pack = importlib.util.module_from_spec(spec_pack)
spec_pack.loader.exec_module(release_pack)
spec_export = importlib.util.spec_from_file_location("pack_export", ROOT / "tools/release-check.py")
export = importlib.util.module_from_spec(spec_export)
spec_export.loader.exec_module(export)


class ReleasePackTests(unittest.TestCase):
    def assert_policy_exclusions(self, listing, prefix):
        with self.subTest(export_policy=prefix):
            if not (ROOT / ".publicignore").is_file():
                self.skipTest("Private export policy is absent from the public source")
            files, prefixes = export.exclusions(ROOT)
            for banned in sorted(files) + list(prefixes):
                self.assertNotIn(prefix + banned, listing, banned)

    def test_dirty_tree_is_refused_before_any_packing(self):
        # 不需要 xz（也不需要 swift/codesign）：脏树在产出任何东西之前就被拒绝
        with tempfile.TemporaryDirectory(prefix="sn-release-pack-dirty-") as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()

            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "fixture")
            (repo / "marker").write_text("one\n")
            git("add", "marker")
            git("commit", "-qm", "fixture")
            (repo / "marker").write_text("two\n")          # 未提交改动
            out = base / "out"
            r = subprocess.run([sys.executable, "-B", str(ROOT / "tools/release-pack.py"), "--out", str(out),
                                "--version", "0.1.dirty", "--skip-runtimes"],
                               env={**os.environ, "SN_HOME": str(repo)}, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("未提交改动", r.stdout + r.stderr)
            self.assertEqual(list(out.iterdir()), [], "拒绝时不能产出任何文件")

    @unittest.skipUnless(XZ, "app 归档需要 xz；干净 runner 镜像没有就只跳过这一个测试")
    def test_manifest_matches_the_files_it_lists(self):
        with tempfile.TemporaryDirectory(prefix="sn-release-pack-") as tmp:
            base = Path(tmp)
            fake = base / "fakebin"; fake.mkdir()
            for name, script in {"swift": '#!/bin/bash\ncase "$*" in *--show-bin-path*) echo "$SN_TEST_BIN";; esac\n',
                                 "codesign": "#!/bin/bash\nexit 0\n", "Dryport": "#!/bin/bash\nexit 0\n"}.items():
                (fake / name).write_text(script); (fake / name).chmod(0o755)
            out = base / "release"
            env = {**os.environ, "SN_HOME": str(ROOT), "SN_TEST_BIN": str(fake), "SN_GAMES": str(base / "games"),
                   "PATH": str(fake) + ":/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
            r = subprocess.run([sys.executable, "-B", str(ROOT / "tools/release-pack.py"), "--out", str(out),
                                "--version", "0.2.0", "--skip-runtimes", "--allow-dirty"], env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            manifest = json.loads((out / "release-manifest.json").read_text())
            self.assertEqual(manifest["version"], "0.2.0")
            self.assertIn("dirty", manifest)
            self.assertEqual(manifest["revision"], subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
            app = out / manifest["app"]["archive"]
            self.assertTrue(app.is_file())
            with tarfile.open(app) as package:
                info = plistlib.loads(package.extractfile("Dryport.app/Contents/Info.plist").read())
            self.assertEqual(info["CFBundleShortVersionString"], manifest["version"])
            self.assertEqual(manifest["app"]["sha256"], hashlib.sha256(app.read_bytes()).hexdigest())
            listing = subprocess.run(["tar", "-tf", str(app)], capture_output=True, text=True).stdout
            self.assertIn("Dryport.app/Contents/Resources/toolchain/lib/api.py", listing)
            self.assertIn("Dryport.app/Contents/Resources/toolchain/SOURCE-REVISION.txt", listing)
            self.assert_policy_exclusions(listing, "Dryport.app/Contents/Resources/toolchain/")
            src = out / manifest["source"]["archive"]
            self.assertTrue(src.is_file())
            self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(src.read_bytes()).hexdigest())
            src_listing = subprocess.run(["tar", "-tf", str(src)], capture_output=True, text=True).stdout
            self.assert_policy_exclusions(src_listing, "source-preview/")
            self.assertNotIn("runtimes", manifest)
            for name, entry in manifest["files"].items():
                self.assertEqual(entry["sha256"], hashlib.sha256((out / name).read_bytes()).hexdigest(), name)
            self.assertEqual(set(manifest["files"]), {app.name, src.name, "THIRD-PARTY-NOTICES.md"})
            notices = (out / "THIRD-PARTY-NOTICES.md").read_text()
            self.assertIn("GPL-3.0-only", notices)
            self.assertNotIn("## ", notices)             # --skip-runtimes：只剩本项目这一行
            self.assertEqual(list(out.glob(".release-app.*")), [])


class AppLicenseGateTests(unittest.TestCase):
    """独立 app 必须自带 LICENSE 与 docs/LEGAL.md；缺失时停下并点名缺的文件。"""

    def test_app_with_license_files_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Dryport.app"
            for rel in release_pack.APP_LICENSE_FILES:
                path = app / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("legal text\n")
            self.assertTrue(release_pack.assert_app_license_files(app))

    def test_missing_files_are_named_and_existing_ones_not(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Dryport.app"
            (app / "Contents/Resources/toolchain").mkdir(parents=True)
            (app / "Contents/Resources/toolchain/LICENSE").write_text("legal text\n")
            with self.assertRaises(SystemExit) as caught:
                release_pack.assert_app_license_files(app)
            message = str(caught.exception)
            self.assertIn("docs/LEGAL.md", message)
            self.assertNotIn("toolchain/LICENSE", message)


class ThirdPartyNoticesTests(unittest.TestCase):
    """THIRD-PARTY-NOTICES.md 渲染：每个运行时一节署名与原文位置；没有运行时（--skip-runtimes）只剩本项目一行。"""

    def manifest(self):
        return {"runtimes": [
            {"name": "toy", "version": "1.0", "license": "MIT", "source": "fixture",
             "notice": ["toy: (c) 2026 fixture authors. MIT."],
             "license_members": ["third-party-licenses/toy/toy__build__LICENSE.txt", "third-party-licenses/toy/NOTICE.txt"]},
            {"name": "wine-cx", "version": "crossover-26.3.0", "license": "LGPL-2.1-or-later", "source": "crossover"},
        ]}

    def test_renders_intro_and_one_section_per_runtime(self):
        md = release_pack.render_third_party_notices(self.manifest())
        self.assertIn("MIT", md)
        self.assertIn("third-party-licenses/<名字>/", md)           # 导语说明原文在归档与安装后的位置
        self.assertIn("## toy 1.0", md)
        self.assertIn("- 许可证：MIT", md)
        self.assertIn("- 来源：fixture", md)
        self.assertIn("- toy: (c) 2026 fixture authors. MIT.", md)
        self.assertIn("`third-party-licenses/toy/NOTICE.txt`", md)
        self.assertIn("## wine-cx crossover-26.3.0", md)            # 没有 notice/license_members 也要成节
        self.assertTrue(md.endswith("\n"))

    def test_no_runtimes_leaves_only_the_project_line(self):
        md = release_pack.render_third_party_notices({"runtimes": []})
        self.assertIn("GPL-3.0-only", md)
        self.assertNotIn("## ", md)
        self.assertNotIn("toy", md)


class LicenseSkipGateTests(unittest.TestCase):
    """发布目录里因许可证原文跳过的运行时必须拦下；没构建之类的原因照旧放行。"""

    def test_license_skips_are_collected_other_reasons_pass(self):
        skipped = [{"name": "toy", "why": "缺许可证原文：toy/build/LICENSE.txt"},
                   {"name": "bare", "why": "清单没登记许可证原文"},
                   {"name": "absent", "why": "缺主程序 absent/thing.app"}]
        bad = release_pack.license_skip_failures({"skipped": skipped})
        self.assertEqual([s["name"] for s in bad], ["toy", "bare"])

    def test_no_skips_passes(self):
        self.assertEqual(release_pack.license_skip_failures({"skipped": []}), [])
        self.assertEqual(release_pack.license_skip_failures({}), [])


class XzSkipScopeTests(unittest.TestCase):
    """xz 缺失时只有真正需要 xz 的测试跳过；其余检查必须照跑。"""

    def test_only_the_xz_dependent_test_skips_without_xz(self):
        from unittest import mock

        def without_xz(name, mode=os.F_OK | os.X_OK):
            return None if name == "xz" else shutil.which(name, mode)

        with mock.patch("shutil.which", without_xz):
            spec = importlib.util.spec_from_file_location("release_pack_tests_without_xz",
                                                          ROOT / "tests/test_release_pack.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        cases = {case._testMethodName for case in unittest.TestLoader().loadTestsFromTestCase(module.ReleasePackTests)}
        self.assertIn("test_dirty_tree_is_refused_before_any_packing", cases)
        self.assertIn("test_manifest_matches_the_files_it_lists", cases)
        cls = module.ReleasePackTests   # 方法级 skip 的标记在函数对象上，不在实例上
        self.assertTrue(getattr(cls.test_manifest_matches_the_files_it_lists, "__unittest_skip__", False),
                        "打包归档的测试在缺 xz 时要带原因跳过")
        self.assertTrue(getattr(cls.test_manifest_matches_the_files_it_lists, "__unittest_skip_why__", ""))
        self.assertFalse(getattr(cls.test_dirty_tree_is_refused_before_any_packing, "__unittest_skip__", False),
                         "不需要 xz 的检查（脏树拒绝）必须照跑，不能被整类跳过带走")


if __name__ == "__main__":
    unittest.main()


class ValveBinaryGateTests(unittest.TestCase):
    """发布包里凡是叫 steam_api* 的必须是本项目编的桥。

    2026-09-13 为了读 32 位 ABI，本机一次性放了 5 份 Valve 的 steam_api.dll。仓库靠
    .gitignore 的 `*.dll` 挡住了，发布包这一侧此前没有闸：运行时包本来就该带 DLL（我们自己的），
    按扩展名一刀切用不了，只能按内容认——桥编出来的 PE 在 0x40 处有 `Wine builtin DLL`，
    Valve 的原件是普通 DOS stub。
    """

    def archive(self, directory, name, entries):
        path = Path(directory) / name
        with tarfile.open(path, "w:gz") as tar:
            for member_name, payload in entries.items():
                info = tarfile.TarInfo(member_name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        return path

    def pe(self, marker: bytes) -> bytes:
        return b"MZ" + b"\0" * 0x3e + marker.ljust(16, b"\0") + b"\0" * 0x40

    def test_our_bridge_dll_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.archive(directory, "runtime.tar.gz",
                         {"steambridge/steam_api.dll": self.pe(b"Wine builtin DLL"),
                          "steambridge/steam_api64.dll": self.pe(b"Wine builtin DLL")})
            self.assertTrue(release_pack.assert_no_valve_binaries(Path(directory)))

    def test_valve_original_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.archive(directory, "runtime.tar.gz",
                         {"steambridge/steam_api.dll": self.pe(b"\x0e\x1f\xba\x0e\x00\xb4\x09\xcd")})
            with self.assertRaises(SystemExit) as caught:
                release_pack.assert_no_valve_binaries(Path(directory))
            self.assertIn("steam_api.dll", str(caught.exception))

    def test_valve_macos_dylib_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.archive(directory, "runtime.tar.gz", {"x/libsteam_api.dylib": b"\xcf\xfa\xed\xfe" + b"\0" * 80})
            with self.assertRaises(SystemExit) as caught:
                release_pack.assert_no_valve_binaries(Path(directory))
            self.assertIn("libsteam_api.dylib", str(caught.exception))

    def test_unrelated_dlls_are_not_touched(self):
        with tempfile.TemporaryDirectory() as directory:
            self.archive(directory, "wine.tar.gz", {"wine-cx/lib/wine/x86_64-windows/msi.dll": self.pe(b"whatever")})
            self.assertTrue(release_pack.assert_no_valve_binaries(Path(directory)))
