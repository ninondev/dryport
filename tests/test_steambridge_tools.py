# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""离线机制回归：合成 PE、隔离构建和假 helper，不接触 Steam 或真实游戏。"""
import contextlib
import importlib.util
import io
import json
import os
import plistlib
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "lib" / "steambridge"
sys.path.insert(0, str(BRIDGE))
from pe_exports import InvalidPE, read_exports


TERMINAL_REASON = "Requires a controlling terminal; /dev/tty is unavailable"


def controlling_terminal_available():
    fd = None
    try:
        fd = os.open("/dev/tty", os.O_RDONLY | getattr(os, "O_NOCTTY", 0))
        return os.isatty(fd)
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def synthetic_dll(path):
    """最小 PE32+ 布局，名字表故意不按字母排序。"""
    data = bytearray(1536)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0, 0, 0, 0xF0, 0x2000)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 60, 0x200)
    struct.pack_into("<I", data, 0x98 + 108, 1)
    struct.pack_into("<II", data, 0x98 + 112, 0x1000, 0x200)
    struct.pack_into("<8sIIIIIIHHI", data, 0x188, b".edata\0\0", 0x400, 0x1000, 0x400, 0x200, 0, 0, 0, 0, 0)
    struct.pack_into("<IIHHIIIIIII", data, 0x200, 0, 0, 0, 0, 0, 1, 2, 2, 0x1040, 0x1060, 0x1080)
    struct.pack_into("<II", data, 0x240, 0x2000, 0x2010)
    struct.pack_into("<II", data, 0x260, 0x1100, 0x1120)
    struct.pack_into("<HH", data, 0x280, 0, 1)
    for offset, name in ((0x300, b"SteamAPI_Zulu\0"), (0x320, b"SteamAPI_Alpha\0")):
        data[offset:offset + len(name)] = name
    path.write_bytes(data)
    return data


class PETest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="steambridge-pe-")
        self.addCleanup(self.temp.cleanup)
        self.dll = Path(self.temp.name) / "owned game.dll"
        self.data = synthetic_dll(self.dll)

    def test_extract_sorted_names_and_json_without_notices(self):
        expected = ["SteamAPI_Alpha", "SteamAPI_Zulu"]
        self.assertEqual(read_exports(self.dll), expected)
        result = subprocess.run([str(ROOT / "tools/dump-exports.sh"), str(self.dll), "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(result.stderr, "")

    def test_reject_truncated_directory(self):
        self.dll.write_bytes(self.data[:0x210])
        with self.assertRaises(InvalidPE):
            read_exports(self.dll)

    def test_reject_unbacked_rva(self):
        struct.pack_into("<I", self.data, 0x260, 0x9000)
        self.dll.write_bytes(self.data)
        with self.assertRaises(InvalidPE):
            read_exports(self.dll)

    def test_reject_invalid_ordinal(self):
        struct.pack_into("<H", self.data, 0x280, 9)
        self.dll.write_bytes(self.data)
        with self.assertRaises(InvalidPE):
            read_exports(self.dll)

    def test_reject_wrong_architecture_and_empty_input(self):
        struct.pack_into("<H", self.data, 0x84, 0x14C)
        self.dll.write_bytes(self.data)
        with self.assertRaises(InvalidPE):
            read_exports(self.dll)
        self.dll.write_bytes(b"")
        result = subprocess.run([str(ROOT / "tools/dump-exports.sh"), str(self.dll)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("文件为空", result.stderr)


class BuildAndInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="steambridge-tools-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.runtime = self.base / "runtime with spaces"
        self.out = self.runtime / "steambridge"
        self.out.mkdir(parents=True)
        self.env = dict(os.environ, SN_RUNTIMES=str(self.runtime), SN_HOME=str(ROOT))

    def run_script(self, script, *args):
        env = self.env.copy()
        if script.name == "install-into-game.sh":
            # 安装机制测试隔离风险数据；风险政策本身由 test_risk_gate 覆盖。
            home = self.base / "source-fixture"
            lib = home / "lib"
            lib.mkdir(parents=True, exist_ok=True)
            (lib / "repack-gate.sh").write_bytes((ROOT / "lib/repack-gate.sh").read_bytes())
            (lib / "risk.py").write_text('import json,sys\nprint(json.dumps(dict(protocol=1,appid=int(sys.argv[2]),code=0,name="fixture",verdict="可原生改包",why=["fixture"])))\n')
            env["SN_HOME"] = str(home)
        return subprocess.run(["bash", str(script), *map(str, args)], env=env, cwd=self.base, capture_output=True, text=True, timeout=15)

    def test_generator_default_output_is_outside_source(self):
        result = subprocess.run([sys.executable, "-B", str(BRIDGE / "gen.py")], env=self.env, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "generated/stubs_gen.c").is_file())
        self.assertTrue((self.out / "generated/steam_api64.def").is_file())

    def test_forbidden_interface_fails_before_generation(self):
        spec = importlib.util.spec_from_file_location("steambridge_generator", BRIDGE / "gen.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        gen.IMPLEMENTED.append("SteamAPI_ISteamUser_GetAuthSessionTicket")
        target = self.base / "must-not-exist"
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(io.StringIO()):
            gen.main(["--output-dir", str(target)])
        self.assertIn("never-implement", str(exc.exception))
        self.assertFalse(target.exists())

    def test_compile_failure_preserves_all_previous_artifacts(self):
        names = ("steam_api64.dll", "steambridge-helper", "sbtest.exe")
        for name in names:
            (self.out / name).write_text("previous:" + name)
        fakebin = self.base / "fakebin"
        fakebin.mkdir()
        for command in ("x86_64-w64-mingw32-gcc", "cc", "otool"):
            path = fakebin / command
            path.write_text("#!/bin/sh\nexit 91\n")
            path.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env["PATH"]
        result = self.run_script(BRIDGE / "build.sh")
        self.assertEqual(result.returncode, 91, result.stderr)
        for name in names:
            self.assertEqual((self.out / name).read_text(), "previous:" + name)
        self.assertFalse((self.out / ".build-lock").exists())
        self.assertEqual(list(self.out.glob(".build.*")), [])

    def fake_game_and_helper(self, helper):
        games = self.base / "generated games"
        self.env["SN_GAMES"] = str(games)
        game = games / "game with spaces.app/Contents/Resources/game"
        game.mkdir(parents=True)
        (game.parent.parent / "Info.plist").write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier="local.winewrap.fixture", CFBundleExecutable="launcher")))
        (game.parent / "wine-launch.json").write_text(json.dumps(dict(appid="123")))
        dll = game / "steam_api64.dll"
        dll.write_bytes(b"original game dll")
        (self.out / "steam_api64.dll").write_bytes(b"test bridge dll")
        executable = self.out / "steambridge-helper"
        executable.write_text(helper)
        executable.chmod(0o755)
        return game, dll

    @unittest.skipUnless(controlling_terminal_available(), TERMINAL_REASON)
    def test_failed_helper_leaves_game_and_backup_untouched(self):
        game, dll = self.fake_game_and_helper("#!/bin/sh\necho test-startup-failure >&2\nexit 2\n")
        result = self.run_script(BRIDGE / "install-into-game.sh", game, 123)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dll.read_bytes(), b"original game dll")
        self.assertFalse(Path(str(dll) + ".orig").exists())
        self.assertIn("test-startup-failure", result.stderr)
        self.assertNotIn("已安装", result.stdout)

    @unittest.skipUnless(controlling_terminal_available(), TERMINAL_REASON)
    def test_install_uses_runtime_and_preserves_original(self):
        helper = f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(self.base / "pid")!r}).write_text(str(os.getpid()))\nprint("[helper] listening on 127.0.0.1:0", flush=True)\ntime.sleep(30)\n'
        game, dll = self.fake_game_and_helper(helper)
        try:
            result = self.run_script(BRIDGE / "install-into-game.sh", game, 123)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(dll.read_bytes(), b"test bridge dll")
            self.assertEqual(Path(str(dll) + ".orig").read_bytes(), b"original game dll")
        finally:
            pidfile = self.base / "pid"
            if pidfile.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), signal.SIGTERM)

    def test_missing_or_multiple_dlls_do_not_install(self):
        game, dll = self.fake_game_and_helper("#!/bin/sh\nexit 2\n")
        sub = game / "second plugin"
        sub.mkdir()
        (sub / "steam_api64.dll").write_bytes(b"another dll")
        result = self.run_script(BRIDGE / "install-into-game.sh", game, 123)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dll.read_bytes(), b"original game dll")
        self.assertIn("2 份", result.stderr)


if __name__ == "__main__":
    unittest.main()
