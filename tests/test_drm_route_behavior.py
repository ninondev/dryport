# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""各路线对 Steamworks DRM 包装（PE 节 .bind）的处理如实入档——Wine 路线打包前拒绝（退出非 0、不产 app），
原生路线不运行这个 exe、只提示并继续（继续的证据：走到下一步，rgss3 是找 mkxp-z 运行时）。行为本次运行不变，这里钉住现状。"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def drm_exe():
    """PE32+ 带节表 [.text, .bind]：.bind 是 Steamworks DRM 包装器的标志段。"""
    sections = [".text", ".bind"]
    data = bytearray(0x40 + 24 + 40 * len(sections))
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x44, 0x8664, len(sections), 0, 0, 0, 0, 0)
    for i, name in enumerate(sections):
        data[0x58 + 40 * i:0x58 + 40 * i + 8] = name.encode().ljust(8, b"\0")
    return bytes(data)


@unittest.skipUnless(sys.platform == "darwin", "跑真 bin/dryport")
class DrmRouteBehaviorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-drm-route-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "toolchain"
        shutil.copytree(ROOT / "bin", self.home / "bin")
        shutil.copytree(ROOT / "lib", self.home / "lib")
        receipt = dict(protocol=1, appid=42, code=0, name="fixture", verdict="可原生改包", why=["fixture"])
        (self.home / "lib/risk.py").write_text("print(" + repr(json.dumps(receipt)) + ")\n")
        (self.home / "lib/fetch-icon.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.home / "lib/fetch-icon.sh").chmod(0o755)
        self.games = self.base / "games"
        self.games.mkdir()
        runtime = self.base / "runtimes/wine-cx/bin"
        runtime.mkdir(parents=True)
        (runtime / "wine").write_text("#!/bin/sh\nexit 99\n")
        (runtime / "wine").chmod(0o755)
        self.env = {**os.environ, "SN_HOME": str(self.home), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "runtimes"), "STEAM": str(self.base / "steam"),
                    "SN_FORCE_REPACK": "0", "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": "/usr/bin:/bin:" + os.environ.get("PATH", "")}

    def cli(self, *args, extra=None):
        env = dict(self.env)
        if extra:
            env.update(extra)
        return subprocess.run([str(self.home / "bin/dryport"), "wrap", *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def notice_printed_for_every_route(self, result):
        self.assertIn("注意", result.stdout)
        self.assertIn("Steamworks DRM 包装", result.stdout)
        self.assertIn("原生路线不运行这个 exe", result.stdout)

    def test_wine_route_refuses_before_packaging(self):
        game = self.games / "DrmWine"
        game.mkdir()
        (game / ".dryport-appid").write_text("42")
        (game / "GameAssembly.dll").write_bytes(b"il2cpp marker for unity-il2cpp detection")
        (game / "Game.exe").write_bytes(drm_exe())
        result = self.cli(str(game), "DrmWine", extra={"SN_WINE_EXE": "Game.exe"})
        self.notice_printed_for_every_route(result)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wine 路线不打包", result.stderr)
        self.assertIn("不绕过 DRM", result.stderr)
        self.assertFalse((self.games / "DrmWine.app").exists(), "拒绝时不能产出 app")

    def test_native_route_prints_the_notice_and_continues(self):
        game = self.games / "DrmNative"
        game.mkdir()
        (game / ".dryport-appid").write_text("42")
        (game / "Game.ini").write_text("[RPG]\nLibrary=RGSS3R\n")
        (game / "Game.exe").write_bytes(drm_exe())
        result = self.cli(str(game), "DrmNative")
        self.notice_printed_for_every_route(result)
        self.assertNotIn("Wine 路线不打包", result.stderr, "原生路线不能因 DRM 拒绝")
        self.assertIn("找不到 mkxp-z", result.stderr, "继续的证据：DRM 之后走到了找运行时这一步")
        self.assertFalse((self.games / "DrmNative.app").exists())

    def test_failed_inspection_refuses_without_claiming_drm(self):
        game = self.games / "UnknownInspection"
        game.mkdir()
        (game / ".dryport-appid").write_text("42")
        (game / "GameAssembly.dll").write_bytes(b"il2cpp marker")
        original = drm_exe()[:0x50]
        (game / "Game.exe").write_bytes(original)
        result = self.cli(str(game), "UnknownInspection", extra={"SN_WINE_EXE": "Game.exe"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not inspect", result.stderr.lower())
        self.assertNotIn("主程序带 Steamworks DRM", result.stderr)
        self.assertFalse((self.games / "UnknownInspection.app").exists())
        self.assertEqual((game / "Game.exe").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
