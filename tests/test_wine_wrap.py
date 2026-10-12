# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Wine 打包不能删除同名 app，不能把游戏名称或 exe 路径执行成 shell。"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "macOS 实际签名与独占发布")
class WineWrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-wine-package-")
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
        self.exe = 'literal$(touch INJECTED)&".exe'
        (self.game / self.exe).write_bytes(b"original Windows fixture, never executed")
        self.runtime = self.base / "runtimes/wine-cx/bin"
        self.runtime.mkdir(parents=True)
        (self.runtime / "wine").write_text("#!/bin/sh\nexit 99\n")
        (self.runtime / "wine").chmod(0o755)
        self.env = {**os.environ, "SN_HOME": str(self.lib.parent), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "runtimes"), "STEAM_APPID": "42", "SN_FORCE_REPACK": "0"}

    def wrap(self, name="literal & <game>", exe=None):
        return subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game),
                               self.exe if exe is None else exe, name], env=self.env,
                              capture_output=True, text=True, timeout=20)

    def test_literals_remain_data_and_signed_payload_preserves_game_and_runtime(self):
        before = (self.runtime / "wine").read_bytes()
        result = self.wrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "literal & <game>.app"
        metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        self.assertEqual(metadata["CFBundleName"], "literal & <game>")
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual(config["exe"], "./" + self.exe)
        self.assertNotIn(self.exe, (app / "Contents/MacOS/launcher").read_text())
        self.assertEqual((app / "Contents/Resources/game" / self.exe).read_bytes(), (self.game / self.exe).read_bytes())
        self.assertEqual((self.runtime / "wine").read_bytes(), before)
        self.assertEqual((app / "Contents/Resources/steam_appid.txt").read_text(), "42")
        self.assertFalse((self.game / "INJECTED").exists())
        self.assertEqual(list(self.games.glob(".wine-stage.*")), [], "成功发布后不能留空的 stage 目录")
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True, capture_output=True)

    def test_existing_app_or_dangling_symlink_is_preserved(self):
        target = self.games / "fixture.app"
        target.mkdir()
        (target / "progress").write_text("saved game")
        result = self.wrap("fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / "progress").read_text(), "saved game")
        alias = self.games / "alias.app"
        alias.symlink_to(self.base / "missing")
        result = self.wrap("alias")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(alias.is_symlink())
        self.assertFalse(list(self.games.glob(".wine-stage.*")))

    def test_invalid_display_or_escaped_entry_creates_no_app(self):
        for name, exe in (("../escaped", self.exe), ("normal", "../outside.exe"), ("normal", "/bin/sh")):
            with self.subTest(name=name, exe=exe):
                result = self.wrap(name, exe)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(self.games.iterdir()), [])

    def test_invalid_bundle_signature_keeps_stage_without_publishing(self):
        outside = self.base / "outside"
        outside.write_text("preserve")
        (self.game / "external-link").symlink_to(outside)
        result = self.wrap("invalid-bundle")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.games / "invalid-bundle.app").exists())
        self.assertTrue(list(self.games.glob(".wine-stage.*")))
        self.assertEqual(outside.read_text(), "preserve")

    def bridge_runtime(self, marker=True):
        bridge = self.base / "runtimes/steambridge"
        bridge.mkdir(parents=True, exist_ok=True)
        dll = bytearray(b"MZ" + b"\0" * 0x4e)
        if marker:
            dll[0x40:0x50] = b"Wine builtin DLL"
        (bridge / "steam_api64.dll").write_bytes(bytes(dll))
        (bridge / "steambridge-helper").write_text("#!/bin/sh\nexit 0\n"); (bridge / "steambridge-helper").chmod(0o755)
        return bridge

    def test_launch_arguments_and_workdir_are_recorded_and_precede_engine_windowing(self):
        (self.game / "Bin").mkdir(); (self.game / "Bin/data.conf").write_text("x")
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Args Fixture"],
                                env={**self.env, "SN_WINE_ENGINE": "unreal", "SN_WINE_LAUNCH_ARGS": '["-conf", "data conf", "-noconsole"]', "SN_WINE_WORKDIR": "Bin"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "Args Fixture.app"
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual(config["launch_args"], ["-conf", "data conf", "-noconsole"]); self.assertEqual(config["workdir"], "Bin")
        check = subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        state = json.loads(check.stdout)
        self.assertEqual(state["launch_args"], ["-conf", "data conf", "-noconsole"])
        self.assertEqual(state["windowed_args"], ["-windowed", "-ResX=1280", "-ResY=720"]); self.assertEqual(state["workdir"], "Bin")
        # 手工调用：SN_WINE_ARGS 一串按 Windows 写法切；工作目录越界或不存在拒绝
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Args Text"],
                                env={**self.env, "SN_WINE_ARGS": '-x "two words" -y'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.games / "Args Text.app/Contents/Resources/wine-launch.json").read_text())
        self.assertEqual(config["launch_args"], ["-x", "two words", "-y"]); self.assertEqual(config["workdir"], "")
        for bad in ("../outside", "Nope"):
            result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Bad " + bad],
                                    env={**self.env, "SN_WINE_WORKDIR": bad}, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0); self.assertFalse((self.games / f"Bad {bad}.app").exists())

    def test_dosbox_gets_a_windowed_conf_after_the_steam_arguments(self):
        from test_steam_drm import build_pe
        (self.game / "DOSBox.exe").write_bytes(build_pe(0x14C, [".text"]))
        (self.game / "rogue.conf").write_text("[sdl]\nfullscreen=true\n")
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "DOSBox.exe", "Dos Fixture"],
                                env={**self.env, "SN_WINE_LAUNCH_ARGS": '["-conf", "rogue.conf", "-noconsole"]'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "Dos Fixture.app"
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual(config["launch_args"], ["-conf", "rogue.conf", "-noconsole"]); self.assertEqual(config["windowed_args"], ["-conf", "sn-windowed.conf"])
        self.assertEqual((app / "Contents/Resources/game/sn-windowed.conf").read_text(), "[sdl]\nfullscreen=false\n")
        self.assertFalse((self.game / "sn-windowed.conf").exists())   # 下载目录不动

    def test_launcher_config_records_engine_windowing_and_bridge_and_check_mode_prepares_overlay(self):
        (self.game / "Engine/Binaries/ThirdParty/Steamworks").mkdir(parents=True)
        (self.game / "Engine/Binaries/ThirdParty/Steamworks/steam_api64.dll").write_bytes(b"game's own steam_api64")
        bridge = self.bridge_runtime()
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Unreal Fixture"],
                                env={**self.env, "SN_WINE_ENGINE": "unreal"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "Unreal Fixture.app"
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual(config["engine"], "unreal"); self.assertEqual(config["appid"], "42"); self.assertEqual(config["name"], "Unreal Fixture")
        self.assertEqual(config["windowed_args"], ["-windowed", "-ResX=1280", "-ResY=720"])
        self.assertTrue(config["bridge"]); self.assertEqual(config["bridge_dir"], str(bridge))
        self.assertNotIn("bridge_in_place", config)
        cloned = app / "Contents/Resources/game/Engine/Binaries/ThirdParty/Steamworks/steam_api64.dll"
        self.assertEqual(cloned.read_bytes(), b"game's own steam_api64")                                   # 克隆里保持游戏原件，桥只走 overlay
        self.assertFalse(cloned.with_suffix(".dll.orig").exists())
        self.assertEqual((self.game / "Engine/Binaries/ThirdParty/Steamworks/steam_api64.dll").read_bytes(), b"game's own steam_api64")   # 下载目录不动
        launcher = app / "Contents/MacOS/launcher"
        check = subprocess.run(["/bin/bash", str(launcher), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        state = json.loads(check.stdout)
        self.assertEqual(state["windowed_args"], ["-windowed", "-ResX=1280", "-ResY=720"])
        self.assertTrue(state["bridge"]); self.assertTrue(state["overlay_ready"]); self.assertTrue(state["helper_present"])
        self.assertEqual(state["state"], str(self.games / ".wine-state/42"))
        overlay = self.games / ".wine-state/42/overlay/x86_64-windows/steam_api64.dll"   # 状态在 app 外，app 本体不被写
        self.assertEqual(overlay.read_bytes(), (bridge / "steam_api64.dll").read_bytes())
        self.assertEqual((self.games / ".wine-state/42/steam_appid.txt").read_text(), "42")
        self.assertFalse((app / "Contents/Resources/overlay").exists())
        self.assertFalse((self.games / ".wine-state/42/prefix").exists())      # 自检不起 Wine
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True, capture_output=True)
        # 桥 DLL 换了版本：下次自检重新铺
        (bridge / "steam_api64.dll").write_bytes((bridge / "steam_api64.dll").read_bytes() + b"v2")
        subprocess.run(["/bin/bash", str(launcher), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(overlay.read_bytes(), (bridge / "steam_api64.dll").read_bytes())

    def test_games_without_steam_api_or_with_an_unmarked_bridge_get_no_overlay(self):
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Plain Fixture"],
                                env={**self.env, "SN_WINE_ENGINE": "unity-il2cpp"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "Plain Fixture.app"
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertFalse(config["bridge"]); self.assertEqual(config["windowed_args"][:2], ["-screen-fullscreen", "0"])
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30).stdout)
        self.assertFalse(state["bridge"]); self.assertFalse(state["overlay_ready"])
        self.assertFalse((self.games / ".wine-state/42/overlay").exists())
        # 带 steam_api64 的游戏但桥 DLL 没有 builtin 标记：不铺 overlay
        (self.game / "steam_api64.dll").write_bytes(b"game dll")
        self.bridge_runtime(marker=False)
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Unmarked Fixture"],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "Unmarked Fixture.app"
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30).stdout)
        self.assertTrue(state["bridge"]); self.assertFalse(state["overlay_ready"]); self.assertEqual(state["engine"], "other"); self.assertEqual(state["windowed_args"], [])
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertNotIn("bridge_in_place", config)
        self.assertEqual((app / "Contents/Resources/game/steam_api64.dll").read_bytes(), b"game dll")

    def test_launcher_handles_sigterm_while_the_game_runs(self):
        import signal, time
        # 假 wine：wineboot/reg 立刻退出；跑游戏 exe 时 exec sleep，pid 就是 sleep 自己
        (self.runtime / "wine").write_text('#!/bin/bash\ncase "${1:-}" in wineboot|reg|regedit) exit 0;; esac\nexec sleep 30\n')
        (self.runtime / "wineserver").write_text("#!/bin/sh\nexit 0\n"); (self.runtime / "wineserver").chmod(0o755)
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), self.exe, "Signal Fixture"],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.games / "Signal Fixture.app/Contents/MacOS/launcher"
        proc = subprocess.Popen(["/bin/bash", str(launcher)], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base), "SN_WINE_NO_NOTIFY": "1"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            game_pid = None
            for _ in range(100):
                out = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
                if out:
                    game_pid = int(out[0]); break
                time.sleep(0.05)
            self.assertIsNotNone(game_pid, "游戏子进程没起来")
            time.sleep(0.3)
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 143)
            self.assertNotEqual(subprocess.run(["kill", "-0", str(game_pid)], capture_output=True).returncode, 0, "游戏进程应已被收掉")
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_first_prefix_is_saved_as_template_and_the_next_app_clones_it(self):
        """首次建 prefix 走 wineboot 并存成模板（等 wineserver 落盘注册表之后），第二个 app 的 prefix 直接 APFS 克隆，不再 wineboot；
        SN_WINE_PREFIX_TEMPLATE=0 走老路。假 wine：wineboot 造出 prefix 骨架，游戏 exe 直接退出。"""
        (self.runtime / "wine").write_text(
            '#!/bin/bash\ncase "${1:-}" in\n'
            '  wineboot) mkdir -p "$WINEPREFIX/drive_c/windows/system32" "$WINEPREFIX/dosdevices"; echo booted >> "$WINEPREFIX/BOOTS"; exit 0;;\n'
            '  reg|regedit) exit 0;;\nesac\nexit 0\n')
        # wineserver -w 之后注册表才落盘：假 wineserver 在 -w 时补上 system.reg
        (self.runtime / "wineserver").write_text('#!/bin/sh\n[ "$1" = -w ] && [ -d "$WINEPREFIX" ] && touch "$WINEPREFIX/system.reg"\nexit 0\n')
        (self.runtime / "wineserver").chmod(0o755)
        wine_root = self.base / "runtimes/wine-cx"
        (wine_root / "lib/wine/x86_64-windows").mkdir(parents=True); (wine_root / "lib/wine/i386-windows").mkdir(parents=True)
        (wine_root / "lib/wine/x86_64-windows/winemetal.dll").write_bytes(b"64"); (wine_root / "lib/wine/i386-windows/winemetal.dll").write_bytes(b"32")
        # 两个 app 同一 appid 也行：状态目录按 appid 分，第二个用 STATE 不同的名字即可；这里用假 appid 43 要改风险回执，简单起见共用 42，
        # 第二个 app 的 prefix 目录由 SN_GAMES/.wine-state/<appid> 决定，所以第二次启动前把第一个 prefix 挪走模拟「另一款游戏首启」。
        for name in ("Tpl One", "Tpl Two"):
            r = self.wrap(name)
            self.assertEqual(r.returncode, 0, r.stderr)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "SN_WINE_NO_NOTIFY": "1"}
        first = subprocess.run(["/bin/bash", str(self.games / "Tpl One.app/Contents/MacOS/launcher")], env=env, capture_output=True, text=True, timeout=30)
        self.assertIn("模板已存", first.stderr, first.stderr)
        templates = list((self.base / "runtimes/wine-prefix-template").glob("*/prefix/system.reg"))
        self.assertEqual(len(templates), 1, "首启后应有且只有一个模板")
        state = self.games / ".wine-state/42"
        self.assertTrue((state / "prefix/BOOTS").exists())
        shutil.rmtree(state)     # 模拟另一款游戏首启
        second = subprocess.run(["/bin/bash", str(self.games / "Tpl Two.app/Contents/MacOS/launcher")], env=env, capture_output=True, text=True, timeout=30)
        self.assertIn("从模板克隆", second.stderr, second.stderr)
        self.assertTrue((state / "prefix/system.reg").exists())
        self.assertEqual((state / "prefix/BOOTS").read_text(), "booted\n", "克隆来的 prefix 不该再 wineboot")
        # 关掉模板：走老路
        shutil.rmtree(state)
        third = subprocess.run(["/bin/bash", str(self.games / "Tpl Two.app/Contents/MacOS/launcher")], env={**env, "SN_WINE_PREFIX_TEMPLATE": "0"},
                               capture_output=True, text=True, timeout=30)
        self.assertNotIn("从模板克隆", third.stderr)
        self.assertEqual((state / "prefix/BOOTS").read_text(), "booted\n")

    def test_32bit_main_exe_selects_the_32bit_bridge(self):
        import struct
        # 最小 PE32：MZ 头 → e_lfanew=0x40 → "PE\0\0" + 机器 0x14C；同目录带 32 位 steam_api.dll
        pe = bytearray(0x60); pe[:2] = b"MZ"; struct.pack_into("<I", pe, 0x3C, 0x40); pe[0x40:0x44] = b"PE\0\0"; struct.pack_into("<H", pe, 0x44, 0x14C)
        (self.game / "Game32.exe").write_bytes(bytes(pe))
        (self.game / "steam_api.dll").write_bytes(b"game's 32-bit steam_api")
        bridge = self.bridge_runtime()
        dll32 = bytearray(b"MZ" + b"\0" * 0x4e); dll32[0x40:0x50] = b"Wine builtin DLL"; (bridge / "steam_api.dll").write_bytes(bytes(dll32))
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Game32.exe", "ThirtyTwo"],
                                env={**self.env, "SN_WINE_ENGINE": "unity-il2cpp"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.games / "ThirtyTwo.app"
        config = json.loads((app / "Contents/Resources/wine-launch.json").read_text())
        self.assertEqual((config["arch"], config["bridge_dll"], config["bridge"]), ("i386", "steam_api.dll", True))
        self.assertEqual((app / "Contents/Resources/game/steam_api.dll").read_bytes(), b"game's 32-bit steam_api")   # 克隆保持原件
        self.assertFalse((app / "Contents/Resources/game/steam_api.dll.orig").exists())
        state = json.loads(subprocess.run(["/bin/bash", str(app / "Contents/MacOS/launcher"), "--sn-check"], env={"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, capture_output=True, text=True, timeout=30).stdout)
        self.assertEqual((state["arch"], state["bridge_dll"]), ("i386", "steam_api.dll")); self.assertTrue(state["overlay_ready"])
        self.assertEqual((self.games / ".wine-state/42/overlay/i386-windows/steam_api.dll").read_bytes(), bytes(dll32))
        self.assertFalse((self.games / ".wine-state/42/overlay/x86_64-windows").exists())
        # 32 位一律默认接桥（2026-09-14 两轮对照：GameMaker 四款、未识别引擎 12 款，见 wine-wrap.sh 注释）；SN_WINE_BRIDGE=0 关掉
        for engine, name in (("gamemaker", "ThirtyTwoGM"), ("unknown", "ThirtyTwoOther")):
            result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Game32.exe", name],
                                    env={**self.env, "SN_WINE_ENGINE": engine}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("不接桥", result.stderr)
            config = json.loads((self.games / f"{name}.app/Contents/Resources/wine-launch.json").read_text())
            self.assertEqual((config["arch"], config["bridge"]), ("i386", True))
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Game32.exe", "ThirtyTwoOff"],
                                env={**self.env, "SN_WINE_ENGINE": "unknown", "SN_WINE_BRIDGE": "0"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("不接桥", result.stderr)
        self.assertFalse(json.loads((self.games / "ThirtyTwoOff.app/Contents/Resources/wine-launch.json").read_text())["bridge"])
        # 64 位 exe 仍选 steam_api64.dll
        pe64 = bytearray(pe); struct.pack_into("<H", pe64, 0x44, 0x8664); (self.game / "Game64.exe").write_bytes(bytes(pe64))
        (self.game / "steam_api64.dll").write_bytes(b"game's 64-bit steam_api")
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Game64.exe", "SixtyFour"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.games / "SixtyFour.app/Contents/Resources/wine-launch.json").read_text())
        self.assertEqual((config["arch"], config["bridge_dll"]), ("x86_64", "steam_api64.dll"))

    def test_launcher_registers_new_builtin_dlls_into_an_existing_prefix(self):
        import struct, time
        # 旧 prefix 建好后运行时目录多了内建 DLL（2026-09-13 后装 32 位 DXMT）：加载器要 system32/syswow64 里有同名副本，否则导入报 not found
        wine_root = self.runtime.parent
        for sub in ("x86_64-windows", "i386-windows"): (wine_root / "lib/wine" / sub).mkdir(parents=True)
        (wine_root / "lib/wine/x86_64-windows/old.dll").write_bytes(b"old64"); (wine_root / "lib/wine/i386-windows/old.dll").write_bytes(b"old32")
        (wine_root / "lib/wine/x86_64-windows/winemetal.dll").write_bytes(b"new64"); (wine_root / "lib/wine/i386-windows/winemetal.dll").write_bytes(b"new32")
        (wine_root / "lib/wine/i386-windows/wineboot.exe").write_bytes(b"not a dll")
        def pe(machine):   # 最小 PE：登记检查按文件的机器类型分位数
            data = bytearray(0x48); data[:2] = b"MZ"; struct.pack_into("<I", data, 0x3C, 0x40); data[0x40:0x44] = b"PE\0\0"; struct.pack_into("<H", data, 0x44, machine); return bytes(data)
        (wine_root / "lib/wine/i386-windows/wbemprox.dll").write_bytes(b"lives in wbem")     # wineboot 登记在子目录
        (wine_root / "lib/wine/i386-windows/mscorwks.dll").write_bytes(b"never registered")  # wineboot 有意不登记
        (wine_root / "lib/wine/i386-windows/twain_32.dll").write_bytes(pe(0x14C))            # wineboot 登记在 windows 根目录
        (wine_root / "lib/wine/x86_64-windows/twain_32.dll").write_bytes(pe(0x8664))
        result = self.wrap("Sync Fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.games / "Sync Fixture.app/Contents/MacOS/launcher"
        prefix = self.games / ".wine-state/42/prefix"
        for sub in ("system32", "syswow64"): (prefix / "drive_c/windows" / sub).mkdir(parents=True)
        (prefix / "drive_c/windows/system32/old.dll").write_bytes(b"prefix copy of old64")
        (prefix / "drive_c/windows/syswow64/wbem").mkdir(); (prefix / "drive_c/windows/syswow64/wbem/WbemProx.dll").write_bytes(pe(0x14C))   # 登记在子目录
        (prefix / "drive_c/windows/twain_32.dll").write_bytes(pe(0x14C))                                                                    # 32 位登记在 windows 根目录，64 位没登记
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base)}
        check = subprocess.run(["/bin/bash", str(launcher), "--sn-check"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual((prefix / "drive_c/windows/system32/winemetal.dll").read_bytes(), b"new64")
        self.assertEqual((prefix / "drive_c/windows/syswow64/winemetal.dll").read_bytes(), b"new32")
        self.assertEqual((prefix / "drive_c/windows/syswow64/old.dll").read_bytes(), b"old32")
        self.assertEqual((prefix / "drive_c/windows/system32/old.dll").read_bytes(), b"prefix copy of old64", "已有副本不覆盖")
        self.assertFalse((prefix / "drive_c/windows/syswow64/wineboot.exe").exists(), "只登记 .dll")
        self.assertFalse((prefix / "drive_c/windows/syswow64/wbemprox.dll").exists(), "子目录里已登记（大小写不同）的名字不重复放")
        self.assertFalse((prefix / "drive_c/windows/syswow64/mscorwks.dll").exists(), "wineboot 有意不登记的名字不放")
        self.assertFalse((prefix / "drive_c/windows/syswow64/twain_32.dll").exists(), "32 位已在 windows 根目录登记，不重复放")
        self.assertEqual((prefix / "drive_c/windows/system32/twain_32.dll").read_bytes(), pe(0x8664), "64 位没登记过，要放")
        self.assertIn("4 个内建 DLL", check.stderr)
        self.assertTrue((prefix / ".sn-builtins-stamp").exists())
        check = subprocess.run(["/bin/bash", str(launcher), "--sn-check"], env=env, capture_output=True, text=True, timeout=30)
        self.assertNotIn("内建 DLL", check.stderr, "运行时没变就不再扫")
        time.sleep(1.1)
        (wine_root / "lib/wine/i386-windows/d3d10core.dll").write_bytes(b"new32b")
        check = subprocess.run(["/bin/bash", str(launcher), "--sn-check"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual((prefix / "drive_c/windows/syswow64/d3d10core.dll").read_bytes(), b"new32b")
        self.assertIn("1 个内建 DLL", check.stderr)
        self.assertFalse((self.games / ".wine-state/42/prefix/drive_c/windows/syswow64/old64.dll").exists())

    def test_steam_drm_wrapped_exe_is_refused_even_with_old_override(self):
        import struct
        # PE32 + 节表 [.text, .bind]：.bind 是 Steamworks DRM 包装器的标志段（a wrapped executable 2026-09-13）
        def pe(sections):
            data = bytearray(0x40 + 24 + 40 * len(sections)); data[:2] = b"MZ"; struct.pack_into("<I", data, 0x3C, 0x40)
            data[0x40:0x44] = b"PE\0\0"; struct.pack_into("<HHIIIHH", data, 0x44, 0x14C, len(sections), 0, 0, 0, 0, 0)
            for i, name in enumerate(sections):
                data[0x58 + 40 * i:0x58 + 40 * i + 8] = name.encode().ljust(8, b"\0")
            return bytes(data)
        (self.game / "Wrapped.exe").write_bytes(pe([".text", ".rdata", ".bind"]))
        (self.game / "steam_api.dll").write_bytes(b"game's 32-bit steam_api")
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Wrapped.exe", "Wrapped"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Steamworks DRM", result.stderr); self.assertIn(".bind", result.stderr)
        self.assertFalse((self.games / "Wrapped.app").exists())
        self.assertEqual(list(self.games.glob(".wine-stage.*")), [], "拒绝时不能留 stage 目录")
        # 没有 .bind 的同构 exe 照常打包
        (self.game / "Plain.exe").write_bytes(pe([".text", ".rdata", ".reloc"]))
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Plain.exe", "Plain"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DRM", result.stderr)
        self.assertFalse(json.loads((self.games / "Plain.app/Contents/Resources/wine-launch.json").read_text())["steam_drm"])
        # 旧覆盖开关也必须拒绝；普通 EXE 的成功断言仍在上面。
        result = subprocess.run(["/bin/bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "Wrapped.exe", "Forced"], env={**self.env, "SN_WINE_ALLOW_DRM": "1"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Steamworks DRM", result.stderr)
        self.assertFalse((self.games / "Forced.app").exists())
        self.assertEqual(list(self.games.glob(".wine-stage.*")), [])

    def test_drm_inspector_failure_stops_before_any_packaging(self):
        (self.lib / "steam_drm.py").write_text("raise RuntimeError('fixture inspection failure')\n")
        before = (self.game / self.exe).read_bytes()
        result = self.wrap("Inspection Failure")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("could not inspect", result.stderr)
        self.assertNotIn("带 Steamworks DRM", result.stderr)
        self.assertFalse((self.games / "Inspection Failure.app").exists())
        self.assertEqual(list(self.games.glob(".wine-stage.*")), [])
        self.assertEqual((self.game / self.exe).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
