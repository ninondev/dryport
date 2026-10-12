# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""`dryport rewrap <appid>`：旧 app 原样搬进 .uninstalled，从下载目录同名同路径重新生成；失败时放回。全部用临时 SN_HOME / SN_GAMES / SN_RUNTIMES。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from test_steam_drm import build_pe

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "macOS 实际签名与独占发布")
class RewrapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-rewrap-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "toolchain"
        shutil.copytree(ROOT / "bin", self.home / "bin"); shutil.copytree(ROOT / "lib", self.home / "lib")
        receipt = dict(protocol=1, appid=42, code=0, name="fixture", verdict="可原生改包", why=["fixture"])
        (self.home / "lib/risk.py").write_text("print(" + repr(json.dumps(receipt)) + ")\n")
        (self.home / "lib/fetch-icon.sh").write_text("#!/bin/sh\nexit 0\n"); (self.home / "lib/fetch-icon.sh").chmod(0o755)
        self.games = self.base / "games"; self.games.mkdir()
        self.folder = self.games / "Fixture Download"; self.folder.mkdir()
        (self.folder / ".dryport-appid").write_text("42")
        self.original_exe = build_pe(0x8664, [".text"])
        (self.folder / "Fixture.exe").write_bytes(self.original_exe)
        runtime = self.base / "runtimes/wine-cx/bin"; runtime.mkdir(parents=True)
        (runtime / "wine").write_text("#!/bin/sh\nexit 99\n"); (runtime / "wine").chmod(0o755)
        self.env = {**os.environ, "SN_HOME": str(self.home), "SN_GAMES": str(self.games), "SN_RUNTIMES": str(self.base / "runtimes"),
                    "STEAM": str(self.base / "steam"), "SN_FORCE_REPACK": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        # 一个旧 app：标记指向 42，壳是旧内容
        self.app = self.games / "Fixture Name.app"
        (self.app / "Contents/MacOS").mkdir(parents=True); (self.app / "Contents/Resources").mkdir(parents=True)
        (self.app / "Contents/Resources/steam_appid.txt").write_text("42")
        (self.app / "Contents/MacOS/launcher").write_text("#!/bin/bash\n# old launcher\n")

    def cli(self, *args):
        return subprocess.run([str(self.home / "bin/dryport"), *args], env=self.env, capture_output=True, text=True, timeout=120)

    def test_rewrap_keeps_name_and_path_and_parks_the_old_app(self):
        r = self.cli("rewrap", "42", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.app.is_dir(), "新 app 必须在原路径")
        launcher = (self.app / "Contents/MacOS/launcher").read_text()
        self.assertNotIn("old launcher", launcher); self.assertIn("sync_builtins", launcher)
        config = json.loads((self.app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual((config["name"], config["appid"], config["exe"]), ("Fixture Name", "42", "./Fixture.exe"))
        parked = list((self.games / ".uninstalled").glob("42-rewrap-*/Fixture Name.app"))
        self.assertEqual(len(parked), 1, "旧 app 原样搬进 .uninstalled")
        self.assertIn("old launcher", (parked[0] / "Contents/MacOS/launcher").read_text())
        self.assertIn("已重打包", r.stdout)
        self.assertEqual((self.folder / "Fixture.exe").read_bytes(), self.original_exe, "下载目录不动")

    def test_failed_rewrap_puts_the_old_app_back(self):
        shutil.rmtree(self.base / "runtimes/wine-cx")           # 没有 Wine 运行时：打包必失败
        r = self.cli("rewrap", "42", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.app.is_dir(), "失败后旧 app 要放回原处")
        self.assertIn("old launcher", (self.app / "Contents/MacOS/launcher").read_text())
        self.assertEqual(list((self.games / ".uninstalled").glob("42-rewrap-*")), [], "放回后不留空的备份目录")
        self.assertIn("旧 app 在", r.stderr)

    def test_rewrap_refuses_without_app_or_folder_and_previews_without_yes(self):
        r = self.cli("rewrap", "43")
        self.assertNotEqual(r.returncode, 0); self.assertIn("没有装着的 app", r.stderr)
        r = self.cli("rewrap", "42")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("old launcher", (self.app / "Contents/MacOS/launcher").read_text(), "没有 --yes 只预演，不动 app")
        self.assertFalse((self.games / ".uninstalled").exists())


if __name__ == "__main__":
    unittest.main()
