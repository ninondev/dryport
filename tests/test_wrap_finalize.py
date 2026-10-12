# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""用真实 macOS ad-hoc 签名检查打包收尾；只创建和修改临时 app。"""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/dryport"
CODESIGN = "/usr/bin/codesign"


@unittest.skipUnless(sys.platform == "darwin" and Path(CODESIGN).exists(),
                     "需要 macOS 的真实 codesign")
class WrapFinalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = subprocess.run(["/usr/bin/xcrun", "--find", "clang"],
                                  capture_output=True, text=True)
        if compiler.returncode:
            raise unittest.SkipTest("需要 Xcode Command Line Tools 的 clang")
        cls.fixture_dir = tempfile.TemporaryDirectory(prefix="sn-sign-template-")
        cls.addClassCleanup(cls.fixture_dir.cleanup)
        base = Path(cls.fixture_dir.name)
        cls.template = base / "Fixture.app"
        macos = cls.template / "Contents/MacOS"
        macos.mkdir(parents=True)
        resources = cls.template / "Contents/Resources"
        resources.mkdir()
        (resources / "asset.txt").write_text("original fixture asset")
        (cls.template / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "Fixture",
            "CFBundleIdentifier": "org.dryport.finalize-fixture",
            "CFBundleName": "Fixture",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
        }))
        source = base / "fixture.c"
        source.write_text("int main(void) { return 0; }\n")
        subprocess.run(["/usr/bin/xcrun", "clang", str(source), "-o", str(macos / "Fixture")],
                       check=True, capture_output=True, timeout=30)
        entitlements = base / "entitlements.plist"
        cls.entitlements = {"com.apple.security.cs.allow-jit": True}
        entitlements.write_bytes(plistlib.dumps(cls.entitlements))
        subprocess.run([CODESIGN, "--force", "--sign", "-", "--options", "runtime",
                        "--entitlements", str(entitlements), str(cls.template)],
                       check=True, capture_output=True, timeout=10)
        subprocess.run([CODESIGN, "--verify", "--strict", str(cls.template)],
                       check=True, capture_output=True, timeout=10)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sn-wrap-finalize-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.games = self.base / "games with spaces"
        self.games.mkdir()
        self.app = self.games / "临时游戏 Fixture.app"
        shutil.copytree(self.template, self.app)
        self.report = self.base / "wrap-output.txt"
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "runtimes"),
                    "STEAM": str(self.base / "isolated Steam"),
                    "STEAM_APPID": "42", "WRAP_OUT_FILE": str(self.report)}

    def invoke(self, function="wrap_done", *args):
        # 未知命令只打印帮助；source 后直接调用真实函数，避免复制函数实现。
        return subprocess.run([
            "/bin/bash", "-c",
            '. "$1" __test_wrap_finalize__ >/dev/null; shift; "$@"',
            str(CLI), str(CLI), function, *(str(arg) for arg in (args or (self.app,)))],
            env=self.env, capture_output=True, text=True, timeout=15)

    def assert_valid_signature(self):
        result = subprocess.run([CODESIGN, "--verify", "--strict", str(self.app)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def invalidate_during_verify(self):
        # 模拟签名完成与回读之间的文件变动；签名和验签仍交给系统 codesign。
        shim = self.base / "shim"
        shim.mkdir()
        command = shim / "codesign"
        command.write_text('''#!/bin/bash
if [ "$1" = --verify ]; then
  for arg; do target="$arg"; done
  printf '%s' 'changed after signing' > "$target/Contents/Resources/asset.txt"
fi
exec /usr/bin/codesign "$@"
''')
        command.chmod(0o755)
        self.env["PATH"] = str(shim) + os.pathsep + self.env.get("PATH", "/usr/bin:/bin")

    def test_final_marker_is_in_resources_and_signature_remains_valid(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app / "Contents/Resources/steam_appid.txt").read_text(), "42")
        self.assertFalse((self.app / "Contents/MacOS/steam_appid.txt").exists())
        self.assertEqual(self.report.read_text(), str(self.app))
        self.assert_valid_signature()
        metadata = subprocess.run([CODESIGN, "-d", "--entitlements", ":-", str(self.app)],
                                  check=True, capture_output=True, timeout=10)
        self.assertEqual(plistlib.loads(metadata.stdout), self.entitlements)
        flags = subprocess.run([CODESIGN, "-dv", str(self.app)], check=True,
                               capture_output=True, text=True, timeout=10)
        self.assertIn("runtime", flags.stderr)

    def test_repeated_finalize_resigns_updated_marker(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.env["STEAM_APPID"] = "43"
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.app / "Contents/Resources/steam_appid.txt").read_text(), "43")
        self.assert_valid_signature()

    def test_signature_failure_does_not_create_success_report(self):
        (self.app / "Contents/MacOS/unsigned.txt").write_text("not executable code")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("最终签名失败", result.stderr)
        self.assertFalse(self.report.exists())

    def test_verification_failure_preserves_existing_report(self):
        self.report.write_text("previous successful result")
        self.invalidate_during_verify()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("最终签名验证失败", result.stderr)
        self.assertEqual(self.report.read_text(), "previous successful result")
        verification = subprocess.run([CODESIGN, "--verify", str(self.app)],
                                      capture_output=True, timeout=10)
        self.assertNotEqual(verification.returncode, 0)

    def test_marker_write_failure_does_not_create_success_report(self):
        (self.app / "Contents/Resources/steam_appid.txt").mkdir()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.report.exists())

    def test_report_write_failure_returns_failure(self):
        self.report.mkdir()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.report.is_dir())
        self.assert_valid_signature()

    def run_script_route(self):
        producer = self.base / "fixture-wrap.sh"
        producer.write_text('''#!/bin/bash
exec /usr/bin/ditto "$1" "$3"
''')
        producer.chmod(0o755)
        return self.invoke("wrap_via_script", producer, self.app, "route fixture", "42", "fixture")

    def test_script_route_propagates_signature_failure_without_success_text(self):
        (self.app / "Contents/MacOS/unsigned.txt").write_text("not executable code")
        result = self.run_script_route()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("最终签名失败", result.stderr)
        self.assertNotIn("已生成:", result.stdout)
        self.assertFalse(self.report.exists())

    def test_script_route_propagates_verification_failure_without_success_text(self):
        self.invalidate_during_verify()
        result = self.run_script_route()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("最终签名验证失败", result.stderr)
        self.assertNotIn("已生成:", result.stdout)
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
