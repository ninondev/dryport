# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""assemble-mac-app.sh 新式（2019.3+ 独立 Mac Mono Support）模板的装配：MainMenu.nib 是变体 Contents/Resources 下的单文件，必须进产物，
否则播放器起步就 "Unable to load nib file: MainMenu" 退出（2026-09-14 一款 Unity 游戏 2019.4.17f1）。全部用假模板与假游戏。"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lib/unity-retarget/assemble-mac-app.sh"


class UnityAssembleTests(unittest.TestCase):
    def test_new_layout_copies_the_single_file_mainmenu_nib(self):
        with tempfile.TemporaryDirectory(prefix="sn-unity-asm-") as tmp:
            base = Path(tmp)
            win = base / "win"; (win / "Game_Data/Managed").mkdir(parents=True)
            (win / "Game_Data/Managed/UnityEngine.dll").write_bytes(b"win engine")
            (win / "Game_Data/globalgamemanagers").write_bytes(b"ggm"); (win / "Game_Data/Resources").mkdir(); (win / "Game_Data/Resources/unity default resources").write_bytes(b"win udr")
            (win / "MonoBleedingEdge/etc").mkdir(parents=True); (win / "MonoBleedingEdge/etc/config").write_text("cfg")
            payload = base / "pkg/Payload"; variant = payload / "Variations/macosx64_nondevelopment_mono/UnityPlayer.app"
            (variant / "Contents/MacOS").mkdir(parents=True); (variant / "Contents/Frameworks").mkdir(); (variant / "Contents/Resources").mkdir()
            (variant / "Contents/MacOS/UnityPlayer").write_bytes(b"#!/bin/sh\nexit 0\n"); (variant / "Contents/MacOS/UnityPlayer").chmod(0o755)
            (variant / "Contents/Frameworks/UnityPlayer.dylib").write_bytes(b"engine")
            (variant / "Contents/Resources/MainMenu.nib").write_bytes(b"nib bytes")
            (variant / "Contents/Resources/unity default resources").write_bytes(b"udr")
            (payload / "Frameworks").mkdir(); (payload / "Frameworks/libmonobdwgc-2.0.dylib").write_bytes(b"mono")
            (payload / "MonoBleedingEdge/etc").mkdir(parents=True); (payload / "MonoBleedingEdge/etc/config").write_text("cfg")
            managed = payload / "Variations/mono/Managed"; managed.mkdir(parents=True); (managed / "UnityEngine.dll").write_bytes(b"mac engine")
            overlay = base / "overlay"; overlay.mkdir()
            out = base / "Out.app"
            env = {**os.environ, "SN_RUNTIMES": str(base / "runtimes"), "SN_VENV": str(base / "novenv"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
            r = subprocess.run(["/bin/bash", str(SCRIPT), str(win), "Game_Data", str(variant), str(managed), str(overlay), str(out), "Fixture", "com.example.fixture", "42"],
                               env=env, capture_output=True, text=True, timeout=120)
            self.assertTrue((out / "Contents/Resources/MainMenu.nib").is_file(), r.stdout + r.stderr)
            self.assertEqual((out / "Contents/Resources/MainMenu.nib").read_bytes(), b"nib bytes")
            self.assertTrue((out / "Contents/Frameworks/libmonobdwgc-2.0.dylib").is_file())


if __name__ == "__main__":
    unittest.main()
