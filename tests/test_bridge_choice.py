# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""桥是逐游戏的选择。默认不接（不铺 overlay、不设 override、不起 helper）；
`dryport bridge enable/disable/status` 读写选择；enable 在确认前先讲清四件事。
启动级证据用假 Wine 跑真 launcher：记录 helper 是否被起、Wine 进程拿到什么环境。
选择文件不得经符号链接读写（写要原子落位、读按未开启处理并提示一次），
测试自己写进 shell 脚本的路径一律 shlex.quote，且临时目录名带空格来证明引号不是摆设。"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "macOS 实际签名与独占发布")
class BridgeChoiceLaunchTests(unittest.TestCase):
    def setUp(self):
        # 目录名带空格：写进 shell 脚本的路径若未引号，这里立刻暴露
        temporary = tempfile.TemporaryDirectory(prefix="sn bridge choice ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.lib = self.base / "toolchain/lib"
        self.lib.mkdir(parents=True)
        for name in ("paths.sh", "paths.py", "repack-gate.sh", "runtime_archive.py", "steam_drm.py"):
            shutil.copyfile(ROOT / "lib" / name, self.lib / name)
        receipt = dict(protocol=1, appid=42, code=0, name="fixture", verdict="可原生改包", why=["fixture"])
        (self.lib / "risk.py").write_text("print(" + repr(json.dumps(receipt)) + ")\n")
        self.games = self.base / "games"
        self.games.mkdir()
        self.game = self.base / "original"
        self.game.mkdir()
        (self.game / ".dryport-appid").write_text("42")
        self.exe = "Game.exe"
        (self.game / self.exe).write_bytes(b"original Windows fixture, never executed")
        (self.game / "steam_api64.dll").write_bytes(b"game's own steam_api64")
        self.runtime = self.base / "runtimes/wine-cx/bin"
        self.runtime.mkdir(parents=True)
        self.recorder = self.base / "helper-started"
        self.envfile = self.base / "wine-env.txt"
        # 假 wine：注册类调用直接过；跑游戏时把环境落盘，证明启动器给 Wine 传了/没传桥变量
        (self.runtime / "wine").write_text(
            "#!/bin/bash\ncase \"${1:-}\" in wineboot|reg|regedit) exit 0;; esac\n"
            f"/usr/bin/env > {shlex.quote(str(self.envfile))}\nexit 0\n")
        (self.runtime / "wine").chmod(0o755)
        (self.runtime / "wineserver").write_text("#!/bin/sh\nexit 0\n")
        (self.runtime / "wineserver").chmod(0o755)
        # 桥运行时：DLL 带 builtin 标记；helper 记录自己被启动过，再打印就绪行
        bridge = self.base / "runtimes/steambridge"
        bridge.mkdir(parents=True)
        dll = bytearray(b"MZ" + b"\0" * 0x4e)
        dll[0x40:0x50] = b"Wine builtin DLL"
        (bridge / "steam_api64.dll").write_bytes(bytes(dll))
        (bridge / "steambridge-helper").write_text(
            f"#!/bin/sh\nprintf 'started\\n' >> {shlex.quote(str(self.recorder))}\n"
            f"echo 'listening on 127.0.0.1:0'\nsleep 30\n")
        (bridge / "steambridge-helper").chmod(0o755)
        self.env = {**os.environ, "SN_HOME": str(self.lib.parent), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "runtimes"), "STEAM_APPID": "42", "SN_FORCE_REPACK": "0"}
        self.launch_env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "SN_WINE_NO_NOTIFY": "1"}

    def wrap(self, name="Chosen"):
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, name],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.games / f"{name}.app"

    def launch(self, app, extra=None):
        env = dict(self.launch_env)
        if extra:
            env.update(extra)
        return subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher")], env=env,
                              capture_output=True, text=True, timeout=60)

    def wine_env(self):
        text = self.envfile.read_text()
        table = {}
        for line in text.splitlines():
            key, _, value = line.partition("=")
            table.setdefault(key, value)
        return table

    def test_launch_without_the_choice_starts_no_helper_no_overlay_no_override(self):
        app = self.wrap()
        result = self.launch(app)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.recorder.exists(), "没有选择时不能起 helper")
        self.assertFalse((self.games / ".wine-state/42/overlay").exists(), "没有选择时不能铺 overlay")
        table = self.wine_env()
        self.assertNotIn("STEAMBRIDGE_PORT", table)
        self.assertNotIn("STEAMBRIDGE_TOKEN", table)
        self.assertEqual(table.get("WINEDLLOVERRIDES"), "mscoree,mshtml=d", "没有选择时不能设桥的 override")

    def test_enabled_choice_or_env_turns_the_bridge_on_at_launch(self):
        app = self.wrap()
        (self.games / ".wine-state/42").mkdir(parents=True, exist_ok=True)
        (self.games / ".wine-state/42/bridge-choice").write_text("enabled\n")
        result = self.launch(app)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.recorder.exists(), "选择开启后 helper 应被启动")
        table = self.wine_env()
        self.assertTrue(table.get("STEAMBRIDGE_PORT", "").isdigit())
        self.assertEqual(len(table.get("STEAMBRIDGE_TOKEN", "")), 64)
        self.assertTrue(table.get("WINEDLLOVERRIDES", "").startswith("steam_api,steam_api64=b;"))
        self.assertTrue((self.games / ".wine-state/42/overlay/x86_64-windows/steam_api64.dll").is_file())

    def test_sn_wine_bridge_zero_beats_the_choice_and_one_beats_no_choice(self):
        app = self.wrap()
        (self.games / ".wine-state/42").mkdir(parents=True, exist_ok=True)
        (self.games / ".wine-state/42/bridge-choice").write_text("enabled\n")
        result = self.launch(app, {"SN_WINE_BRIDGE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.recorder.exists(), "SN_WINE_BRIDGE=0 必须压过开启的选择")
        shutil.rmtree(self.games / ".wine-state/42")
        result = self.launch(app, {"SN_WINE_BRIDGE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.recorder.exists(), "SN_WINE_BRIDGE=1 是显式手工开关")

    def test_symbolic_link_choice_is_treated_as_off_with_one_note(self):
        app = self.wrap()
        (self.games / ".wine-state/42").mkdir(parents=True, exist_ok=True)
        (self.games / ".wine-state/42/bridge-choice").symlink_to(self.base / "elsewhere")
        result = self.launch(app)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.recorder.exists(), "符号链接的选择必须按未开启处理，不能起 helper")
        self.assertFalse((self.games / ".wine-state/42/overlay").exists(), "按未开启处理就不能铺 overlay")
        self.assertEqual(result.stderr.count("按未开启处理"), 1, "要在 stderr 提示一次，且只一次")

    def test_disabled_choice_is_not_enabled_and_check_reports_the_decision(self):
        app = self.wrap()
        (self.games / ".wine-state/42").mkdir(parents=True, exist_ok=True)
        (self.games / ".wine-state/42/bridge-choice").write_text("disabled\n")
        check = subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"],
                               env=self.launch_env, capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        state = json.loads(check.stdout)
        self.assertFalse(state["bridge_wanted"])
        (self.games / ".wine-state/42/bridge-choice").write_text("enabled\n")
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"],
                                          env=self.launch_env, capture_output=True, text=True, timeout=30).stdout)
        self.assertTrue(state["bridge_wanted"])
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"],
                                          env={**self.launch_env, "SN_WINE_BRIDGE": "0"}, capture_output=True,
                                          text=True, timeout=30).stdout)
        self.assertFalse(state["bridge_wanted"])
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"],
                                          env={**self.launch_env, "SN_WINE_BRIDGE": "1"}, capture_output=True,
                                          text=True, timeout=30).stdout)
        self.assertTrue(state["bridge_wanted"])


@unittest.skipUnless(sys.platform == "darwin", "跑真 bin/dryport")
class BridgeCommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-bridge-cmd-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "toolchain"
        shutil.copytree(ROOT / "bin", self.home / "bin")
        shutil.copytree(ROOT / "lib", self.home / "lib")
        self.games = self.base / "games"
        self.games.mkdir()
        self.state = self.games / ".wine-state/42"
        self.state.mkdir(parents=True)
        self.app = self.games / "Fixture Name.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/Resources").mkdir(parents=True)
        (self.app / "Contents/Resources/steam_appid.txt").write_text("42")
        (self.app / "Contents/Resources/wine-launch.json").write_text(json.dumps({
            "wine": "/nonexistent/wine", "exe": "./Game.exe", "toolchain": str(self.home), "name": "Fixture Name",
            "appid": "42", "engine": "other", "windowed_args": [], "launch_args": [], "workdir": "",
            "bridge": True, "bridge_dir": "/nonexistent/bridge", "state": str(self.state),
            "arch": "x86_64", "bridge_dll": "steam_api64.dll", "steam_drm": False}))
        self.env = {**os.environ, "SN_HOME": str(self.home), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "runtimes"), "STEAM": str(self.base / "steam"),
                    "SN_FORCE_REPACK": "0", "PYTHONDONTWRITEBYTECODE": "1"}

    def cli(self, *args):
        return subprocess.run([str(self.home / "bin/dryport"), "bridge", *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_enable_previews_the_four_facts_before_confirmation_and_writes_with_yes(self):
        result = self.cli("enable", "42")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        facts = ["身份、统计与成就", "写入这个 Steam 账号", "多人游戏、会话票据、物品相关接口永不转发", "账号安全性尚未证实"]
        preview_at = result.stdout.index("预演")
        for fact in facts:
            self.assertIn(fact, result.stdout)
            self.assertLess(result.stdout.index(fact), preview_at, f"事实「{fact}」必须印在确认之前")
        self.assertFalse((self.state / "bridge-choice").exists(), "没有 --yes 不能写选择")
        result = self.cli("enable", "42", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.state / "bridge-choice").read_text(), "enabled\n")
        self.assertIn("已开启", result.stdout)
        for fact in facts:      # 落盘路径同样先讲事实
            self.assertLess(result.stdout.index(fact), result.stdout.index("已开启"))

    def test_disable_writes_without_confirmation_and_status_reports_both(self):
        result = self.cli("enable", "42", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.cli("disable", "42")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.state / "bridge-choice").read_text(), "disabled\n")
        result = self.cli("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("42", result.stdout); self.assertIn("已关闭", result.stdout)
        result = self.cli("status", "42")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Fixture Name", result.stdout)

    def test_status_default_and_choice_file_for_uninstalled_appid(self):
        result = self.cli("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("未选择", result.stdout)
        result = self.cli("enable", "999", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.games / ".wine-state/999/bridge-choice").read_text(), "enabled\n")
        self.assertIn("没有装着的 app", result.stdout)
        result = self.cli("status")
        self.assertIn("999", result.stdout)

    def test_usage_and_bad_arguments(self):
        for args in (("bogus",), ("enable",), ("enable", "abc"), ("enable", "42", "--nope")):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(list(self.games.glob(".wine-state/*/bridge-choice")))

    def test_enable_and_disable_refuse_a_symbolic_or_foreign_choice_and_write_atomically(self):
        elsewhere = self.base / "elsewhere-choice"
        elsewhere.write_text("elsewhere\n")
        (self.state / "bridge-choice").symlink_to(elsewhere)
        r = self.cli("enable", "42", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("符号链接", r.stdout + r.stderr)
        self.assertTrue((self.state / "bridge-choice").is_symlink(), "符号链接原样保留，不写穿")
        self.assertEqual(elsewhere.read_text(), "elsewhere\n", "目标文件一个字节都不能动")
        r = self.cli("disable", "42")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.state / "bridge-choice").is_symlink())
        (self.state / "bridge-choice").unlink()
        (self.state / "bridge-choice").mkdir()          # 目录占位：同样拒绝
        r = self.cli("enable", "42", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.state / "bridge-choice").is_dir())
        (self.state / "bridge-choice").rmdir()
        r = self.cli("enable", "42", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.state / "bridge-choice").read_text(), "enabled\n")
        self.assertEqual(list(self.state.glob(".bridge-choice.*")), [], "原子落位后不留临时文件")

    def test_status_treats_a_symbolic_link_choice_as_off_with_one_note(self):
        elsewhere = self.base / "elsewhere-choice"
        elsewhere.write_text("enabled\n")
        (self.state / "bridge-choice").symlink_to(elsewhere)
        r = self.cli("status", "42")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("未选择", r.stdout, "符号链接的选择必须按未开启报，不能顺着链接读出 enabled")
        self.assertNotIn("已开启", r.stdout)
        self.assertEqual(r.stderr.count("不是普通文件"), 1, "stderr 提示一次，且只一次")


if __name__ == "__main__":
    unittest.main()
