# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""构建器的替换/回滚边界；使用假编译器和临时 app，不触碰用户产物。"""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AppBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-app-build-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.games = self.root / "games"
        self.games.mkdir()
        self.target = self.games / "Dryport.app"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, script in {
            "swift": '#!/bin/bash\ncase "$*" in *--show-bin-path*) echo "$SN_TEST_BIN";; esac\n',
            "codesign": '#!/bin/bash\nexit "${SN_TEST_SIGN_FAIL:-0}"\n',
            "Dryport": '#!/bin/bash\nexit 0\n',
        }.items():
            path = self.bin / name
            path.write_text(script)
            path.chmod(0o755)
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_GAMES": str(self.games),
                    "SN_TEST_BIN": str(self.bin), "PATH": str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin"}
        self.env.pop("SN_APP", None)

    def old_app(self, bundle_id="dev.ninon.dryport"):
        contents = self.target / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
        (self.target / "keep.txt").write_text("old app stays recoverable")

    def run_build(self, cwd=None, **extra):
        return subprocess.run(["bash", str(ROOT / "app/build.sh")], env={**self.env, **extra},
                              cwd=cwd, capture_output=True, text=True)

    def test_unknown_app_is_untouched(self):
        self.old_app("org.example.other-app")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.target / "keep.txt").exists())
        self.assertFalse(list(self.games.glob(".dryport-*")))

    def test_symlink_target_is_untouched(self):
        other = self.root / "other"
        other.mkdir()
        self.target.symlink_to(other, target_is_directory=True)
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.target.is_symlink())
        self.assertTrue(other.exists())

    def test_signing_failure_preserves_old_app_without_partial_target(self):
        self.old_app()
        result = self.run_build(SN_TEST_SIGN_FAIL="7")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.target / "keep.txt").exists())
        self.assertFalse(list(self.games.glob(".dryport-*")))

    def test_success_keeps_recoverable_backup(self):
        self.old_app()
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "Contents/MacOS/Dryport").exists())
        backups = list(self.games.glob(".dryport-backup.*/Dryport.app/keep.txt"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old app stays recoverable")
        self.assertFalse(list(self.games.glob(".dryport-build.*")))

    def test_persisted_paths_preserve_nondefault_roots_and_special_characters(self):
        games = self.root / '游戏库 & "目录"'
        runtimes = self.root / "运行时's cache"
        steam = self.root / "Steam profile"
        output = self.root / "separate output/Dryport.app"
        result = self.run_build(SN_APP=str(output), SN_GAMES=str(games),
                                SN_RUNTIMES=str(runtimes), STEAM=str(steam))
        self.assertEqual(result.returncode, 0, result.stderr)
        with (output / "Contents/Resources/toolchain-paths.plist").open("rb") as stream:
            paths = plistlib.load(stream)
        self.assertEqual(paths, {"SN_HOME": str(ROOT), "SN_GAMES": str(games),
                                 "SN_RUNTIMES": str(runtimes), "STEAM": str(steam)})
        self.assertFalse(games.exists())
        self.assertFalse(runtimes.exists())
        self.assertFalse(steam.exists())

    def test_relative_roots_use_the_build_invocation_directory(self):
        result = self.run_build(cwd=self.root, SN_APP="output/Dryport.app", SN_GAMES="games relative",
                                SN_RUNTIMES="./runtime relative", STEAM="steam relative")
        self.assertEqual(result.returncode, 0, result.stderr)
        with (self.root / "output/Dryport.app/Contents/Resources/toolchain-paths.plist").open("rb") as stream:
            paths = plistlib.load(stream)
        self.assertEqual(Path(paths["SN_GAMES"]).resolve(), (self.root / "games relative").resolve())
        self.assertEqual(Path(paths["SN_RUNTIMES"]).resolve(), (self.root / "runtime relative").resolve())
        self.assertEqual(Path(paths["STEAM"]).resolve(), (self.root / "steam relative").resolve())

    def test_explicit_release_version_is_written_to_the_bundle(self):
        result = self.run_build(SN_APP_VERSION="0.2.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        info = plistlib.loads((self.target / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleShortVersionString"], "0.2.3")


if __name__ == "__main__":
    unittest.main()

    def test_standalone_bundle_carries_the_tracked_toolchain_with_a_bundle_relative_home(self):
        output = self.root / "standalone/Dryport.app"
        result = subprocess.run(["bash", str(ROOT / "app/build.sh"), "--standalone"],
                                env={**self.env, "SN_APP": str(output)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        toolchain = output / "Contents/Resources/toolchain"
        self.assertTrue((toolchain / "bin/dryport").is_file())
        self.assertTrue((toolchain / "lib/api.py").is_file())
        self.assertTrue((toolchain / "lib/paths.sh").is_file())
        self.assertTrue((toolchain / "tools/runtime-bundle.py").is_file())
        self.assertTrue((toolchain / "LICENSE").is_file())
        self.assertTrue((toolchain / "docs/LEGAL.md").is_file())
        self.assertTrue((toolchain / "SOURCE-REVISION.txt").read_text().strip())
        self.assertFalse(list(toolchain.rglob("__pycache__")))
        self.assertFalse((toolchain / "lib/unity-retarget/hlslcc-cli").exists())   # 本机编出来的二进制不带
        self.assertFalse((toolchain / "tools/一次性").exists())
        with (output / "Contents/Resources/toolchain-paths.plist").open("rb") as stream:
            paths = plistlib.load(stream)
        self.assertEqual(paths["SN_HOME"], "@bundle/Contents/Resources/toolchain")
        # 包内工具链不设 SN_HOME 也能自己定位：paths.py 从文件位置推 SN_HOME
        probe = subprocess.run(["python3", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.SN_HOME)", str(toolchain / "lib")],
                               env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)}, capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), str(toolchain))

    def test_unknown_flag_is_rejected(self):
        result = subprocess.run(["bash", str(ROOT / "app/build.sh"), "--bogus"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.target.exists())
