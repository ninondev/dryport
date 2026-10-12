# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Wine 兜底的主程序 exe 选择：按布局选，排除运行库与崩溃处理器，选不准要说明是猜的。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import wine_exe


def make(root, *files):
    for rel, size in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


class WineExeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-wine-exe-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()

    def test_unreal_prefers_the_root_bootstrap_over_shipping(self):
        g = self.base / "Fixture"; make(g, ("FixtureGame.exe", 100), ("FixtureGame/Binaries/Win64/Fixture-Win64-Shipping.exe", 5000),
                                        ("Engine/Binaries/ThirdParty/Steamworks/Steamv147/Win64/steam_api64.dll", 10),
                                        ("Engine/Extras/Redist/en-us/UE4PrereqSetup_x64.exe", 9000))
        exe, keep, reason = wine_exe.pick(g)
        self.assertEqual(exe.as_posix(), "FixtureGame.exe"); self.assertIn("引导", reason)
        make(g / "nohoot", ("Game/Binaries/Win64/Game-Win64-Shipping.exe", 10))
        exe, _, reason = wine_exe.pick(g / "nohoot")
        self.assertEqual(exe.as_posix(), "Game/Binaries/Win64/Game-Win64-Shipping.exe"); self.assertIn("Shipping", reason)

    def test_unity_picks_the_exe_beside_the_data_directory_and_skips_crash_handler(self):
        g = self.base / "Unbound"; make(g, ("A Space for the Unbound.exe", 600), ("UnityCrashHandler64.exe", 9000),
                                        ("A Space for the Unbound_Data/globalgamemanagers", 1))
        exe, keep, reason = wine_exe.pick(g)
        self.assertEqual(exe.as_posix(), "A Space for the Unbound.exe"); self.assertIn("Unity", reason)
        self.assertNotIn(Path("UnityCrashHandler64.exe"), keep)

    def test_single_root_exe_and_guessing_are_labelled(self):
        g = self.base / "Single"; make(g, ("game.exe", 10), ("_CommonRedist/vcredist/vc_redist.x64.exe", 999), ("dxsetup.exe", 999))
        exe, keep, reason = wine_exe.pick(g)
        self.assertEqual(exe.as_posix(), "game.exe"); self.assertEqual(reason, "根目录唯一的 exe")
        g = self.base / "GuessFixture"; make(g, ("FixtureGame.exe", 100), ("Editor.exe", 500), ("Data/tool.exe", 5))
        exe, keep, reason = wine_exe.pick(g)
        self.assertEqual(exe.as_posix(), "FixtureGame.exe"); self.assertIn("猜的", reason)
        g = self.base / "empty"; g.mkdir()
        self.assertEqual(wine_exe.pick(g)[0], None)

    def test_steam_launch_config_wins_and_resolves_windows_paths(self):
        g = self.base / "ScummFixture"; make(g, ("scummvm/scummvm.exe", 100), ("unins000.exe", 900), ("run.bat", 1), ("fixture-target.ini", 1))
        (g / "run.bat").write_text('".\\scummvm\\scummvm.exe" --fullscreen fixture-target\r\nexit\r\n')
        apps = {42: {"config": {"launch": {"0": {"executable": "run.bat", "type": "default", "config": {"oslist": "windows"}}}}}}
        r = wine_exe.choose(g, 42, apps=apps)
        self.assertEqual((r["exe"], r["args"], r["workdir"], r["source"]), ("scummvm/scummvm.exe", ["--fullscreen", "fixture-target"], "", "steam"))
        self.assertIn("run.bat", r["reason"])
        # 大小写、反斜杠、子目录、工作目录、参数引号；mac/linux 入口、测试分支、option 入口都跳过
        g = self.base / "DosFixture"; make(g, ("Bin/DOSBox.exe", 100), ("Bin/fixture.conf", 1), ("Launcher.exe", 50))
        apps = {1: {"config": {"launch": {
            "0": {"executable": "Game.app", "config": {"oslist": "macos"}},
            "1": {"executable": "Launcher.exe", "type": "option1", "config": {"oslist": "windows"}},
            "2": {"executable": "bin\\dosbox.EXE", "arguments": '-conf "fixture.conf" -noconsole', "workingdir": "BIN", "config": {"oslist": "windows"}},
            "3": {"executable": "Launcher.exe", "config": {"oslist": "windows", "betakey": "beta"}}}}}}
        r = wine_exe.choose(g, "1", apps=apps)
        self.assertEqual((r["exe"], r["args"], r["workdir"]), ("Bin/DOSBox.exe", ["-conf", "fixture.conf", "-noconsole"], "Bin"))
        # 配置对不上文件（或没有配置、appid 空）就回到启发式
        apps = {2: {"config": {"launch": {"0": {"executable": "Missing.exe", "config": {"oslist": "windows"}}}}}}
        r = wine_exe.choose(g, 2, apps=apps)
        self.assertEqual((r["exe"], r["source"]), ("Launcher.exe", "heuristic"))
        self.assertEqual(wine_exe.choose(g, None, apps=apps)["source"], "heuristic")
        # 越界与绝对路径不认
        self.assertIsNone(wine_exe.resolve_rel(g, "..\\other\\x.exe")); self.assertIsNone(wine_exe.resolve_rel(g, "C:\\x.exe"))
        self.assertEqual(wine_exe.split_windows_args('a "b c" d'), ["a", "b c", "d"]); self.assertEqual(wine_exe.split_windows_args(""), [])

    def test_cli_prints_relative_path_or_fails(self):
        g = self.base / "cli"; make(g, ("run.exe", 1))
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lib/wine_exe.py"), str(g), "--json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0); self.assertEqual(json.loads(r.stdout)["exe"], "run.exe")
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lib/wine_exe.py"), str(self.base / "missing")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
