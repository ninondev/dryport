# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""检查失败必须在下载、打包前停止；全部使用临时目录，不触碰 Steam。"""
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import risk


def app(*categories, oslist="windows"):
    return {"common": {"name": "fixture", "oslist": oslist,
                       "category": {f"category_{c}": "1" for c in categories}}}


class RiskDataTests(unittest.TestCase):
    def test_truncated_cache_never_returns_partial_single_player_evidence(self):
        # 完整节点须同时给出 Windows 与单机证据，截断测试仍要求保留严格的允许断言。
        node = b"\x00common\x00\x01oslist\x00windows\x00\x00category\x00\x01category_2\x001\x00"
        header = struct.pack("<II", 0x07564427, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "appinfo.vdf"
            def write(body, suffix=b"\0\0\0\0", size=None):
                record = bytes(40) + body
                path.write_bytes(header + struct.pack("<II", 42, len(record) if size is None else size) + record + suffix)
            write(node)
            self.assertNotIn(42, risk.load_appinfo(path))
            write(node + b"\x08\x08\x08")
            self.assertEqual(risk.assess(42, risk.load_appinfo(path)[42], {})["verdict"], risk.REPACK)
            write(node, size=10000)
            with self.assertRaises(ValueError):
                risk.load_appinfo(path)
            write(node + b"\x08\x08\x08", suffix=b"")
            with self.assertRaises(ValueError):
                risk.load_appinfo(path)
        with self.assertRaises(ValueError):
            risk.parse_bvdf_strtbl(b"\x00" + struct.pack("<I", 9), ["common"])
        with self.assertRaises(ValueError):
            risk.parse_bvdf_strtbl(b"\x00" + struct.pack("<I", 0), ["common"])

    def test_missing_metadata_does_not_mean_safe(self):
        for data in (None, {}, {"common": {}}, app()):
            self.assertEqual(risk.assess(42, data, {})["verdict"], risk.UNKNOWN)

    def test_single_player_evidence(self):
        self.assertEqual(risk.assess(42, app(2), {})["verdict"], risk.REPACK)
        self.assertEqual(risk.assess(42, app(2, 1), {})["verdict"], risk.WINE_ONLY)
        self.assertEqual(risk.assess(42, app(2, 8), {})["verdict"], risk.REFUSE)
        for categories in ((2,), (2, 1)):
            self.assertEqual(risk.assess(42, app(*categories, oslist="windows,macos"), {})["verdict"], risk.UNKNOWN)

    def test_windows_platform_must_be_explicit_before_single_player_or_online_routes(self):
        invalid = (None, False, 1, [], ["windows"], {"windows": True}, "", "  ", ",", "linux",
                   "win", "notwindows", "windowsphone", "macosx")
        for platforms in invalid:
            for categories in ((2,), (2, 1)):
                with self.subTest(platforms=platforms, categories=categories):
                    result = risk.assess(42, app(*categories, oslist=platforms), {})
                    self.assertEqual(result["verdict"], risk.UNKNOWN)
                    self.assertEqual(risk.EXIT_CODES[result["verdict"]], 3)
                    self.assertTrue(any("Windows" in reason for reason in result["why"]))
        missing = app(2)
        del missing["common"]["oslist"]
        self.assertEqual(risk.assess(42, missing, {})["verdict"], risk.UNKNOWN)   # 没有内容包：连「省略即 Windows」都不适用

    def test_platforms_derive_from_launch_depot_or_steam_default(self):
        content = {"depots": {"43": {"manifests": {"public": {"gid": "1"}}}}}
        game = {"common": {"name": "old", "type": "Game", "category": {"category_2": "1"}}, **content}
        self.assertEqual(risk.app_platforms(game), ({"windows"}, "default"))
        r = risk.assess(42, game, {})
        self.assertEqual(r["verdict"], risk.REPACK); self.assertEqual(r["platform_evidence"], "default")
        self.assertTrue(any("按 Steam 惯例" in w for w in r["why"]))
        tool = {"common": {"name": "t", "type": "Tool", "category": {"category_2": "1"}}, **content}
        self.assertEqual(risk.app_platforms(tool), (set(), "none"))
        launch = {**game, "config": {"launch": {"0": {"executable": "a.exe", "config": {"oslist": "windows"}}}}}
        self.assertEqual(risk.app_platforms(launch), ({"windows"}, "launch"))
        depot = {"common": game["common"], "depots": {"43": {"manifests": {"public": {"gid": "1"}}, "config": {"oslist": "macos,windows"}}}}
        self.assertEqual(risk.app_platforms(depot), ({"macos", "windows"}, "depot"))
        self.assertEqual(risk.assess(42, depot, {})["verdict"], risk.UNKNOWN)   # 有 macOS 版就不走 Windows 入口
        explicit = {**game, "common": {**game["common"], "oslist": "linux"}}
        self.assertEqual(risk.app_platforms(explicit), ({"linux"}, "oslist"))
        self.assertEqual(risk.assess(42, explicit, {})["verdict"], risk.UNKNOWN)
        for junk in (None, 1, [], ["windows"], "", " , "):
            self.assertEqual(risk.app_platforms({**game, "common": {**game["common"], "oslist": junk}}), ({"windows"}, "default"))
        # Windows + Linux 仍可走当前 Windows 入口；大小写与分隔空白不改变明确平台证据。
        for platforms in ("windows", "WINDOWS", " windows , linux "):
            self.assertEqual(risk.assess(42, app(2, oslist=platforms), {})["verdict"], risk.REPACK)
            self.assertEqual(risk.assess(42, app(2, 1, oslist=platforms), {})["verdict"], risk.WINE_ONLY)

    def test_mac_platform_retains_official_release_guidance(self):
        for platforms in ("macos", "windows,macos", " WINDOWS , MaCoS "):
            for categories in ((2,), (2, 1)):
                with self.subTest(platforms=platforms, categories=categories):
                    result = risk.assess(42, app(*categories, oslist=platforms), {})
                    self.assertEqual(result["verdict"], risk.UNKNOWN)
                    self.assertTrue(any("Steam 已提供 macOS" in reason for reason in result["why"]))

    def test_missing_or_unsupported_platform_never_downgrades_anticheat_refusal(self):
        for platforms in (None, [], "", "linux", "windows", "windows,macos"):
            with self.subTest(platforms=platforms):
                vac = risk.assess(42, app(2, 8, oslist=platforms), {})
                external = risk.assess(42, app(2, oslist=platforms),
                                       {42: {"status": "Supported", "anticheats": ["fixture"]}})
                for result in (vac, external):
                    self.assertEqual(result["verdict"], risk.REFUSE)
                    self.assertEqual(risk.EXIT_CODES[result["verdict"]], 2)

    def test_disabled_or_malformed_category_not_positive_evidence(self):
        for category in ({"category_2": "0"}, {"category_x": "1"}, []):
            self.assertEqual(risk.assess(42, {"common": {"category": category}}, {})["verdict"], risk.UNKNOWN)

    def test_every_anticheat_status_refused(self):
        for status in ("Supported", "Running", "Planned", "Broken", "Denied", "new-status"):
            ac = {42: {"status": status, "anticheats": ["fixture"]}}
            self.assertEqual(risk.assess(42, app(2), ac)["verdict"], risk.REFUSE)

    def test_missing_empty_or_corrupt_database_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "db.json"
            with self.assertRaises(OSError):
                risk.load_ac(p)
            for content in ("[]", "{}", "broken", '[{}]', '[{"anticheats": []}]'):
                p.write_text(content)
                with self.assertRaises(ValueError):
                    risk.load_ac(p)
            p.write_text(json.dumps([{"anticheats": ["fixture"], "storeIds": {"steam": "42"}}]))
            self.assertIn(42, risk.load_ac(p))

    def test_paths_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "SN_RUNTIMES": str(Path(d) / "rt"), "STEAM": str(Path(d) / "steam")}
            result = subprocess.run([sys.executable, str(ROOT / "lib/risk.py"), "--gate", "42"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 3)
            self.assertIn(str(Path(d) / "steam"), result.stderr)


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        lib = self.lib = self.base / "toolchain/lib"
        lib.mkdir(parents=True)
        for name in ("paths.sh", "paths.py", "repack-gate.sh"):
            (lib / name).write_bytes((ROOT / "lib" / name).read_bytes())
        self.game = self.base / "game"
        self.game.mkdir()
        (self.game / ".dryport-appid").write_text("42")
        (self.base / "rt").mkdir()
        (self.base / "rt/anticheat-db.json").write_text("[]")  # 这里隔离入口闸，风险结论由下面的假 checker 提供。
        self.env = {**os.environ, "SN_HOME": str(self.base / "toolchain"), "SN_GAMES": str(self.base / "games"),
                    "SN_RUNTIMES": str(self.base / "rt"), "STEAM": str(self.base / "steam"), "SN_FORCE_REPACK": "0"}
        self.env.pop("STEAM_APPID", None)
        self.env.pop("SN_LIB", None)

    def run_entry(self, code, command, force=False):
        verdict = {0: risk.REPACK, 1: risk.WINE_ONLY, 2: risk.REFUSE, 3: risk.UNKNOWN}.get(code, risk.UNKNOWN)
        receipt = json.dumps(dict(protocol=1, appid=42, code=code, name="fixture", verdict=verdict, why=["fixture"]))
        (self.lib / "risk.py").write_text(f"print({receipt!r})\nraise SystemExit({code})\n")
        env = {**self.env, "SN_FORCE_REPACK": "1" if force else "0"}
        return subprocess.run(command, env=env, capture_output=True, text=True)

    def test_installer_stops_before_download_for_every_failed_check(self):
        for code in (1, 2, 3, 7, 127):
            result = self.run_entry(code, [str(ROOT / "bin/dryport"), "install", "42", "fixture"])
            self.assertNotEqual(result.returncode, 0)
            if code in (1, 2, 3):
                self.assertIn("fixture", result.stdout + result.stderr)
            self.assertNotIn("[2/5]", result.stdout)
            self.assertFalse((self.base / "games").exists())

    def test_wrap_stops_before_dispatch_for_unknown_codes_even_when_forced(self):
        for code in (2, 3, 7, 127):
            result = self.run_entry(code, [str(ROOT / "bin/dryport"), "wrap", str(self.game)], force=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("尚未实现", result.stderr)

    def test_wine_entry_has_same_guard(self):
        result = self.run_entry(2, ["bash", str(ROOT / "lib/wine-wrap.sh"), str(self.game), "game.exe", "fixture"], force=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.base / "games").exists())

    def test_native_and_bridge_direct_entries_have_guard(self):
        commands = [
            ["bash", str(ROOT / "lib" / f"{engine}-wrap.sh"), str(self.game), "fixture", str(self.base / "output.app"), "42"]
            for engine in ("nwjs", "godot", "krkr")
        ]
        commands.append(["bash", str(ROOT / "lib/steambridge/install-into-game.sh"), str(self.game), "42"])
        for command in commands:
            result = self.run_entry(2, command, force=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse((self.base / "output.app").exists())

    def test_missing_appid_refused(self):
        (self.game / ".dryport-appid").unlink()
        result = self.run_entry(0, [str(ROOT / "bin/dryport"), "wrap", str(self.game)])
        self.assertEqual(result.returncode, 3)

    def test_import_failure_is_never_an_online_override(self):
        (self.lib / "risk.py").write_text("import missing_dependency_for_test\n")
        env = {**self.env, "SN_FORCE_REPACK": "1"}
        result = subprocess.run([str(ROOT / "bin/dryport"), "wrap", str(self.game)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn("没有返回完整判定", result.stderr)

    def test_explicit_online_override_still_requires_identity(self):
        for force, expected in ((False, 1), (True, 0)):
            command = ["bash", "-c", '. "$SN_HOME/lib/paths.sh"; . "$SN_LIB/repack-gate.sh"; require_repack_permission "$1"', "probe", str(self.game)]
            result = self.run_entry(1, command, force)
            self.assertEqual(result.returncode, expected)

    def test_optimized_python_cannot_remove_receipt_validation(self):
        base = dict(protocol=1, appid=42, code=0, name="fixture", verdict=risk.REPACK, why=["fixture"])
        cases = [({**base, "appid": 999}, 0), ({**base, "protocol": 99}, 0),
                 ({**base, "why": []}, 0), (base, 1)]
        command = ["bash", "-c", '. "$SN_HOME/lib/paths.sh"; . "$SN_LIB/repack-gate.sh"; require_repack_permission "$1"',
                   "probe", str(self.game)]
        for receipt, exit_code in cases:
            with self.subTest(receipt=receipt, exit_code=exit_code):
                payload = json.dumps(receipt)
                (self.lib / "risk.py").write_text(f"print({payload!r})\nraise SystemExit({exit_code})\n")
                result = subprocess.run(command, env={**self.env, "PYTHONOPTIMIZE": "1", "SN_FORCE_REPACK": "1"},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 3, result.stderr)

    def test_every_registry_scan_failure_stops_uninstall_before_deletion(self):
        packed = self.base / "games/packed.app"
        downloaded = self.base / "games/downloaded"
        for path in (packed, downloaded):
            path.mkdir(parents=True)
            (path / "keep.txt").write_text("must survive a failed scan")
        marker = packed / "Contents/MacOS/steam_appid.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("42")
        for failed_field in ("app", "folder", "name"):
            with self.subTest(field=failed_field):
                entry = {"42": dict(app=str(packed), folder=str(downloaded), name="fixture")}
                (self.lib / "api.py").write_text(
                    "import sys\n"
                    "def scan_installed():\n"
                    f"    if sys.argv[2] == {failed_field!r}: raise OSError('fixture scan failure')\n"
                    f"    return {entry!r}\n")
                result = subprocess.run([str(ROOT / "bin/dryport"), "uninstall", "42", "--yes"],
                                        env=self.env, cwd=self.base, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("fixture scan failure", result.stderr)
                self.assertNotIn("═══ 卸载", result.stdout)
                self.assertTrue((packed / "keep.txt").exists())
                self.assertTrue((downloaded / "keep.txt").exists())

    def test_null_registry_paths_never_become_relative_deletion_targets(self):
        for name in ("api.py", "route_tiers.py", "paths.py", "uninstall_purge.py"):
            (self.lib / name).write_bytes((ROOT / "lib" / name).read_bytes())
        games = self.base / "games"
        games.mkdir()
        (games / "installed.json").write_text(json.dumps({"42": {"app": None, "folder": None, "name": "fixture"}}))
        unrelated = self.base / "None"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("not a game installation")
        result = subprocess.run([str(ROOT / "bin/dryport"), "uninstall", "42", "--yes"],
                                env=self.env, cwd=self.base, capture_output=True, text=True)
        self.assertTrue((unrelated / "keep.txt").exists(), result.stdout)
        self.assertNotIn("已删 None", result.stdout)


class UninstallArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-uninstall-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.lib = self.base / "toolchain/lib"
        self.lib.mkdir(parents=True)
        for name in ("paths.sh", "paths.py", "repack-gate.sh", "api.py", "route_tiers.py", "uninstall_purge.py"):
            (self.lib / name).write_bytes((ROOT / "lib" / name).read_bytes())
        self.calls = self.base / "shortcut-calls.txt"
        commands = self.base / "commands"
        commands.mkdir()
        node = commands / "node"
        node.write_text(
            f"#!{sys.executable}\n"
            "import json,os,sys\nfrom pathlib import Path\n"
            f"expected = {str(self.lib / 'steam-shortcut-remove.mjs')!r}\n"
            "if len(sys.argv) != 3 or sys.argv[1] != expected: raise SystemExit(91)\n"
            f"Path({str(self.calls)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "if os.environ.get('SN_TEST_REMOVE_NO_ENDPOINT'):\n"
            "    print('fixture: Steam endpoint unavailable', file=sys.stderr)\n"
            "    raise SystemExit(69)\n"
            "if os.environ.get('SN_TEST_TAMPER_MARKER'):\n"
            "    (Path(sys.argv[2])/'Contents/MacOS/steam_appid.txt').write_text('99')\n"
            "raise SystemExit(int(os.environ.get('SN_TEST_SHORTCUT_EXIT','0')))\n")
        node.chmod(0o755)
        (self.lib / "steam-shortcut-mirror.py").write_text(
            "raise RuntimeError('legacy mirror removal must not be called')\n")
        self.games = self.base / "games"
        self.games.mkdir()
        self.app = self.games / "fixture.app"
        self.download = self.games / "downloaded"
        self.download.mkdir()
        (self.download / ".dryport-appid").write_text("42")
        (self.download / "game.dat").write_bytes(b"original game bytes")
        self.env = {**os.environ, "SN_HOME": str(self.base / "toolchain"), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "rt"), "STEAM": str(self.base / "steam"),
                    "PATH": str(commands) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}
        self.make_app()

    def make_app(self):
        marker = self.app / "Contents/MacOS/steam_appid.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("42")
        save = self.app / "Contents/Game/Save01.dat"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"save inside app")
        (self.games / "installed.json").write_text(json.dumps({"42": {
            "name": "fixture", "app": str(self.app), "folder": str(self.download)}}))

    def run_cli(self, verb, *args, **env):
        return subprocess.run([str(ROOT / "bin/dryport"), verb, "42", *args],
                              env={**self.env, **env}, cwd=self.base, capture_output=True, text=True, timeout=20)

    def run_archive(self, *args, **env):   # 归档：可逆
        return self.run_cli("archive", *args, **env)

    def run_uninstall(self, *args, **env): # 卸载：不可逆
        return self.run_cli("uninstall", *args, **env)

    def test_dry_run_moves_nothing(self):
        for verb in ("archive", "uninstall"):
            with self.subTest(verb=verb):
                result = self.run_cli(verb)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((self.app / "Contents/Game/Save01.dat").exists())
                self.assertTrue((self.download / "game.dat").exists())
                self.assertFalse((self.games / ".uninstalled").exists())
                self.assertFalse(self.calls.exists())
        self.assertIn("不可逆", result.stdout)

    def test_uninstall_deletes_only_marked_download_and_archives_and_reports_freed_space(self):
        unmarked = self.games / "unmarked"
        unmarked.mkdir()
        (unmarked / "keep.txt").write_text("someone else's directory")
        result = self.run_uninstall("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.calls.read_text()),
                         [str(self.lib / "steam-shortcut-remove.mjs"), str(self.app)])   # 快捷方式仍走官方接口
        self.assertFalse(self.app.exists())
        self.assertFalse(self.download.exists())
        self.assertEqual(list(self.games.glob(".uninstalled/*")), [])
        self.assertTrue((unmarked / "keep.txt").exists())
        self.assertIn("实际释放", result.stdout)
        self.assertIn("卸载完成", result.stdout)

    def test_uninstall_refuses_a_download_directory_without_our_marker(self):
        (self.download / ".dryport-appid").unlink()
        result = self.run_uninstall("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.download / "game.dat").read_bytes(), b"original game bytes")
        self.assertIn("[不动]", result.stdout)                      # 账单阶段就说明不动它
        self.assertIn("没有本工具的 .dryport-appid 标记", result.stdout)
        self.assertNotIn("已删除：" + str(self.download), result.stdout)
        self.assertFalse(self.app.exists())
        self.assertEqual(list(self.games.glob(".uninstalled/*")), [])

    def test_uninstall_stops_before_deleting_when_shortcut_removal_fails(self):
        result = self.run_uninstall("--yes", SN_TEST_SHORTCUT_EXIT="7")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.app / "Contents/Game/Save01.dat").exists())
        self.assertEqual((self.download / "game.dat").read_bytes(), b"original game bytes")
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertNotIn("已删除", result.stdout)

    def test_archive_and_the_old_keep_download_flag_both_archive_reversibly(self):
        for verb, args in (("archive", ("--yes",)), ("uninstall", ("--yes", "--keep-download"))):
            if not self.app.exists():
                self.make_app()
            result = self.run_cli(verb, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.app.exists())
            self.assertEqual((self.download / "game.dat").read_bytes(), b"original game bytes")
            self.assertEqual(json.loads(self.calls.read_text()),
                             [str(self.lib / "steam-shortcut-remove.mjs"), str(self.app)])
        saves = list(self.games.glob(".uninstalled/42-*/fixture.app/Contents/Game/Save01.dat"))
        self.assertEqual(len(saves), 2)
        self.assertTrue(all(path.read_bytes() == b"save inside app" for path in saves))

    def test_shortcut_failure_preserves_app_and_download(self):
        result = self.run_archive("--yes", SN_TEST_SHORTCUT_EXIT="7")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.app.exists())
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertTrue((self.download / "game.dat").exists())
        self.assertNotIn("已完成卸载", result.stdout)

    def test_missing_steam_endpoint_does_not_archive_or_forget_installation(self):
        save = self.app / "Contents/Game/Save01.dat"
        before = save.read_bytes(), save.stat().st_mtime_ns
        registry = self.games / "installed.json"
        registered = registry.read_bytes()

        result = self.run_archive("--yes", SN_TEST_REMOVE_NO_ENDPOINT="1")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture: Steam endpoint unavailable", result.stdout + result.stderr)
        self.assertEqual(json.loads(self.calls.read_text()),
                         [str(self.lib / "steam-shortcut-remove.mjs"), str(self.app)])
        self.assertTrue(self.app.is_dir())
        self.assertEqual((save.read_bytes(), save.stat().st_mtime_ns), before)
        self.assertEqual((self.download / "game.dat").read_bytes(), b"original game bytes")
        self.assertEqual(registry.read_bytes(), registered)
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertNotIn("已完成卸载", result.stdout)

    def test_resource_marker_takes_precedence_over_legacy_marker(self):
        (self.app / "Contents/MacOS/steam_appid.txt").write_text("99")
        marker = self.app / "Contents/Resources/steam_appid.txt"
        marker.parent.mkdir()
        marker.write_text("42")
        result = self.run_archive("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        saves = list(self.games.glob(".uninstalled/42-*/fixture.app/Contents/Game/Save01.dat"))
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0].read_bytes(), b"save inside app")

    def test_invalid_resource_marker_cannot_fall_back_to_legacy_marker(self):
        marker = self.app / "Contents/Resources/steam_appid.txt"
        marker.parent.mkdir()
        for value in ("99", ""):
            with self.subTest(value=value):
                marker.write_text(value)
                result = self.run_archive("--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(self.app.exists())
                self.assertFalse(self.calls.exists())
        marker.unlink()
        marker.symlink_to(self.base / "missing-marker")
        result = self.run_archive("--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_changed_marker_after_shortcut_processing_stops_archive(self):
        result = self.run_archive("--yes", SN_TEST_TAMPER_MARKER="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.calls.exists())
        self.assertTrue((self.app / "Contents/Game/Save01.dat").exists())
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertIn("归档失败", result.stderr)
        self.assertNotIn("已完成卸载", result.stdout)

    def test_registry_update_failure_reports_retained_archive(self):
        path = self.lib / "api.py"
        path.write_text("import sys\nif len(sys.argv)>1 and sys.argv[1]=='forget': raise SystemExit(7)\n" + path.read_text())
        result = self.run_archive("--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.app.exists())
        saves = list(self.games.glob(".uninstalled/42-*/fixture.app/Contents/Game/Save01.dat"))
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0].read_bytes(), b"save inside app")
        self.assertTrue((self.download / "game.dat").exists())
        self.assertIn("安装记录更新失败", result.stderr)
        self.assertNotIn("已完成卸载", result.stdout)

    def test_mismatched_or_missing_marker_cannot_authorize_archive(self):
        marker = self.app / "Contents/MacOS/steam_appid.txt"
        for text in ("99", None):
            if text is None:
                marker.unlink()
            else:
                marker.write_text(text)
            result = self.run_archive("--yes")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(self.app.exists())
            self.assertFalse(self.calls.exists())

    def test_archive_symlink_is_rejected_before_shortcut_changes(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.games / ".uninstalled").symlink_to(outside, target_is_directory=True)
        result = self.run_archive("--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.app.exists())
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_registry_targets_cannot_move_other_directories(self):
        registry = self.games / "installed.json"
        marker = self.app / "Contents/MacOS/steam_appid.txt"
        marker.unlink()
        relative = self.base / "relative.app"
        relative.mkdir()
        for target in ("relative.app", 7, str(self.games), str(self.base / "toolchain"), str(self.base)):
            registry.write_text(json.dumps({"42": {"app": target, "folder": str(self.download)}}))
            result = self.run_archive("--yes")
            self.assertNotEqual(result.returncode, 0, (target, result.stdout))
            self.assertTrue(relative.exists())
            self.assertTrue(self.app.exists())
            self.assertFalse(self.calls.exists())

    def test_symlink_app_is_rejected(self):
        moved = self.base / "external.app"
        self.app.rename(moved)
        self.app.symlink_to(moved, target_is_directory=True)
        result = self.run_archive("--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.app.is_symlink())
        self.assertTrue((moved / "Contents/Game/Save01.dat").exists())
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
