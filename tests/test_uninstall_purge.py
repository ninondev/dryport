# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""彻底卸载只删本工具自己创建的东西：下载目录看 .dryport-appid，归档看 app 里的 steam_appid.txt，
存档状态目录默认不删；软链、标记不符、受保护根一律跳过。全部在临时目录里演，不碰真实 SN_GAMES。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-purge-")
        base = Path(self.tmp.name)
        self.games = base / "games"; self.games.mkdir()
        self.home = base / "home"; self.home.mkdir()          # 假 SN_HOME（受保护根）
        self.runtimes = base / "runtimes"; self.runtimes.mkdir()
        self.steam = base / "steam"; self.steam.mkdir()
        self.webkit = base / "webkit"; self.webkit.mkdir()
        self.env = dict(os.environ, SN_GAMES=str(self.games), SN_HOME=str(ROOT), SN_RUNTIMES=str(self.runtimes),
                        STEAM=str(self.steam), SN_WEBKIT_STATE=str(self.webkit))
        # 下载目录（带标记）、另一个没标记的目录、归档容器、Ren'Py 状态、WebKit 状态
        (self.games / "game_dl").mkdir(); (self.games / "game_dl/.dryport-appid").write_text("4242\n")
        (self.games / "game_dl/data.bin").write_bytes(b"x" * 4096)
        (self.games / "not_ours").mkdir(); (self.games / "not_ours/file").write_bytes(b"y" * 4096)
        arc = self.games / ".uninstalled" / "4242-abc"; (arc / "Game.app/Contents/Resources").mkdir(parents=True)
        (arc / "Game.app/Contents/Resources/steam_appid.txt").write_text("4242")
        (arc / "Game.app/Contents/Resources/payload").write_bytes(b"z" * 8192)
        other = self.games / ".uninstalled" / "4242-bad"; (other / "Game.app/Contents/Resources").mkdir(parents=True)
        (other / "Game.app/Contents/Resources/steam_appid.txt").write_text("9999")   # 容器名像，内容不是
        ws = self.games / ".wine-state" / "4242"; (ws / "prefix/drive_c/users/example/Documents").mkdir(parents=True)
        (ws / "steam_appid.txt").write_text("4242"); (ws / "prefix/drive_c/users/example/Documents/save.dat").write_bytes(b"wine save")
        rs = self.games / ".renpy-state" / "4242"; (rs / "saves").mkdir(parents=True)
        (rs / "steam_appid.txt").write_text("4242"); (rs / "saves/1-1-LT1.save").write_bytes(b"s" * 1024)
        wk = self.webkit / "4242"; wk.mkdir()
        (wk / ".dryport-webkit.json").write_text(json.dumps({"kind": "dryport-webkit-state", "appid": "4242"}))
        (wk / "data").write_bytes(b"w" * 1024)
        self.installed = {"appid": "4242", "folder": str(self.games / "game_dl"), "app": ""}

    def tearDown(self):
        self.tmp.cleanup()

    def run_module(self, code):
        script = ("import sys, json; sys.path.insert(0, %r); import uninstall_purge as up\n" % str(ROOT / "lib")) + code
        return subprocess.run([sys.executable, "-B", "-c", script], env=self.env, capture_output=True, text=True, timeout=60)

    def test_plan_lists_only_our_things_and_saves_default_off(self):
        r = self.run_module("print(json.dumps(up.plan('4242', %r)))" % self.installed)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        kinds = sorted((i["kind"], i["default"]) for i in p["items"])
        self.assertEqual(kinds, [("archive", True), ("download", True), ("saves", False), ("saves", False), ("saves", False)])   # Ren'Py、Wine、WebKit 三处状态目录都是默认不删的存档行
        self.assertTrue(all(i["bytes"] > 0 for i in p["items"]))
        self.assertEqual([s["kind"] for s in p["skipped"]], ["archive"])            # 4242-bad 被跳过
        self.assertNotIn(str(self.games / "not_ours"), json.dumps(p))

    def test_apply_deletes_defaults_keeps_saves_and_refuses_bad_marker(self):
        r = self.run_module("print(json.dumps(up.apply('4242', saves=False, installed=%r)))" % self.installed)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.splitlines()[-1])
        self.assertTrue(res["ok"])
        self.assertFalse((self.games / "game_dl").exists())
        self.assertFalse((self.games / ".uninstalled/4242-abc").exists())
        self.assertTrue((self.games / ".uninstalled/4242-bad").exists())            # 内容不符，未动
        self.assertTrue((self.games / ".renpy-state/4242/saves/1-1-LT1.save").exists())   # 默认不删存档
        self.assertTrue((self.games / ".wine-state/4242/prefix/drive_c/users/example/Documents/save.dat").exists())   # Wine prefix 同样默认保留
        self.assertTrue((self.webkit / "4242/data").exists())
        self.assertTrue((self.games / "not_ours/file").exists())
        self.assertEqual(sorted(d["kind"] for d in res["deleted"]), ["archive", "download"])

    def test_apply_with_saves_deletes_state_dirs(self):
        r = self.run_module("print(json.dumps(up.apply('4242', saves=True, installed=%r)))" % self.installed)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.games / ".renpy-state/4242").exists())
        self.assertFalse((self.games / ".wine-state/4242").exists())
        self.assertFalse((self.webkit / "4242").exists())

    def test_symlink_and_foreign_paths_are_skipped(self):
        link = self.games / "linked_dl"; link.symlink_to(self.games / "game_dl")
        outside = Path(self.tmp.name) / "outside"; outside.mkdir(); (outside / ".dryport-appid").write_text("4242")
        for folder in (link, outside, self.runtimes):
            r = self.run_module("print(json.dumps(up.plan('4242', {'folder': %r, 'app': ''})))" % str(folder))
            p = json.loads(r.stdout)
            self.assertFalse(any(i["kind"] == "download" for i in p["items"]), folder)
            self.assertTrue(any(s["kind"] == "download" for s in p["skipped"]), folder)
        self.assertTrue((self.games / "game_dl/data.bin").exists())

    def test_cli_plan_and_apply_gate(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lib/uninstall_purge.py"), "plan", "4242"], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)   # 真实 scan_installed 在空 SN_GAMES 里查不到记录，只列归档与状态目录
        self.assertIn("归档的 app", r.stdout)
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lib/uninstall_purge.py"), "apply", "4242"], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)               # 没有 --yes 不删
        self.assertTrue((self.games / ".uninstalled/4242-abc").exists())


if __name__ == "__main__":
    unittest.main()
