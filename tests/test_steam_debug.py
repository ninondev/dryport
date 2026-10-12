# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Steam 本机连接开关：status 只读；enable 只在 Steam 客户端包存在时创建一个空文件，别的东西占着路径就拒绝；
disable 经确认只删除本工具创建的那个空文件并提示重启 Steam，别的东西一概不动。全部用临时 STEAM。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SteamDebugSwitchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-steam-debug-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.steam = self.base / "Steam"
        self.bundle = self.steam / "Steam.AppBundle/Steam/Contents/MacOS"
        self.flag = self.bundle / ".cef-enable-remote-debugging"
        self.env = {**os.environ, "STEAM": str(self.steam), "SN_HOME": str(ROOT), "SN_GAMES": str(self.base / "games"),
                    "SN_RUNTIMES": str(self.base / "runtimes"), "PYTHONDONTWRITEBYTECODE": "1"}

    def api(self, *args):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lib/api.py"), "steam-debug", *args], env=self.env, capture_output=True, text=True, timeout=30)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def cli(self, *args):
        return subprocess.run([str(ROOT / "bin/dryport"), "steam-debug", *args], env=self.env, capture_output=True, text=True, timeout=30)

    def test_status_is_read_only_and_enable_needs_the_client_bundle(self):
        rc, d = self.api("status")
        self.assertEqual(rc, 0); self.assertFalse(d["bundle_present"]); self.assertFalse(d["flag_exists"])
        self.assertFalse(self.steam.exists())
        rc, d = self.api("enable")
        self.assertEqual(rc, 1); self.assertEqual(d["event"], "error"); self.assertFalse(self.steam.exists())

    def test_enable_creates_exactly_one_empty_file_and_is_idempotent(self):
        self.bundle.mkdir(parents=True)
        (self.bundle / "steam_osx").write_bytes(b"not touched")
        before = sorted(p.name for p in self.bundle.iterdir())
        rc, d = self.api("enable")
        self.assertEqual(rc, 0, d); self.assertTrue(d["created"]); self.assertTrue(d["flag_exists"])
        self.assertEqual(d["restart_needed"], not d["endpoint_ok"])   # 端点探测的是本机真 Steam，不假定它开没开
        self.assertTrue(self.flag.is_file()); self.assertEqual(self.flag.stat().st_size, 0)
        self.assertEqual(sorted(p.name for p in self.bundle.iterdir()), sorted(before + [".cef-enable-remote-debugging"]))
        self.assertEqual((self.bundle / "steam_osx").read_bytes(), b"not touched")
        rc, d = self.api("enable")
        self.assertEqual(rc, 0); self.assertFalse(d["created"]); self.assertTrue(d["flag_exists"])

    def test_occupied_path_is_refused(self):
        self.bundle.mkdir(parents=True)
        self.flag.mkdir()
        rc, d = self.api("enable")
        self.assertEqual(rc, 1); self.assertIn("占用", d["msg"]); self.assertTrue(self.flag.is_dir())
        self.flag.rmdir(); self.flag.symlink_to(self.base / "elsewhere")
        rc, d = self.api("enable")
        self.assertEqual(rc, 1); self.assertTrue(self.flag.is_symlink()); self.assertFalse((self.base / "elsewhere").exists())

    def test_cli_previews_without_yes_and_creates_with_yes(self):
        self.bundle.mkdir(parents=True)
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("开关文件：无", r.stdout); self.assertFalse(self.flag.exists())
        r = self.cli("--enable")                       # 非终端、没 --yes：只预演
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("预演", r.stdout); self.assertFalse(self.flag.exists())
        r = self.cli("--enable", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("已创建", r.stdout); self.assertTrue(self.flag.is_file())
        self.assertIn("重新打开 Steam", r.stdout)

    def test_disable_removes_only_the_empty_flag_and_says_restart(self):
        self.bundle.mkdir(parents=True)
        (self.bundle / "steam_osx").write_bytes(b"not touched")
        rc, d = self.api("disable")                     # 文件本来就没有：无事发生
        self.assertEqual(rc, 0, d); self.assertFalse(d["removed"]); self.assertFalse(d["flag_exists"])
        rc, d = self.api("enable")
        self.assertEqual(rc, 0, d); self.assertTrue(d["flag_exists"])
        rc, d = self.api("disable")
        self.assertEqual(rc, 0, d); self.assertTrue(d["removed"]); self.assertFalse(d["flag_exists"])
        self.assertTrue(d["restart_needed"], "删了开关后要重启 Steam 才关端点")
        self.assertEqual((self.bundle / "steam_osx").read_bytes(), b"not touched", "别的东西一概不动")
        self.assertEqual(sorted(p.name for p in self.bundle.iterdir()), ["steam_osx"])

    def test_disable_refuses_occupied_or_foreign_flag(self):
        self.bundle.mkdir(parents=True)
        (self.bundle / "steam_osx").write_bytes(b"not touched")
        self.flag.write_bytes(b"not ours")             # 非空：不是本工具建的空文件
        rc, d = self.api("disable")
        self.assertEqual(rc, 1); self.assertIn("不是空", d["msg"]); self.assertEqual(self.flag.read_bytes(), b"not ours")
        self.flag.unlink(); self.flag.symlink_to(self.base / "elsewhere")
        rc, d = self.api("disable")
        self.assertEqual(rc, 1); self.assertTrue(self.flag.is_symlink()); self.assertFalse((self.base / "elsewhere").exists())

    def test_cli_disable_previews_then_removes_and_status_reports(self):
        self.bundle.mkdir(parents=True)
        self.flag.write_bytes(b"")
        r = self.cli("--disable")                       # 非终端、没 --yes：只预演
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("预演", r.stdout); self.assertTrue(self.flag.exists())
        self.assertIn("任何本机程序", r.stdout, "删除前要把端点风险讲明白")
        r = self.cli("--disable", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.flag.exists())
        self.assertIn("重新打开 Steam", r.stdout)
        r = self.cli("--status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("开关文件：无", r.stdout)


if __name__ == "__main__":
    unittest.main()
