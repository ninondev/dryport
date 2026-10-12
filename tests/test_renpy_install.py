# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""真实 CLI 接线与归档回归；Steam、运行时下载及 app 构建均为临时边界替身。

固定 profile 的实际检查器使用自有小包摘要；游戏脚本从不执行。
不复制 CLI 函数，不把替身产物当原厂签名或真实安装验证。
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/dryport"
APPID = "2118420"
GAME_FILES = {
    "gundog.py": b'raise RuntimeError("fixture payload must never execute")\n',
    "renpy/__init__.py": b'raise RuntimeError("fixture engine must never import")\n',
}
# 自有两文件和 renpy 目录的预先固定清单摘要，不放宽生产 profile。
FIXTURE_SHA256 = "e60f5f8f59df7f7f6ce8a4f42f21e3d7029ebf95e20c6d8c0b13dfd1413ba993"


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[key] = ["link", os.readlink(path)]
        elif path.is_file():
            result[key] = ["file", hashlib.sha256(path.read_bytes()).hexdigest()]
        else:
            result[key] = ["directory"]
    return result


class RenpyInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-renpy-install-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / "toolchain"
        self.lib = self.home / "lib"
        self.lib.mkdir(parents=True)
        # 与已有卸载回归相同：复制当前完整模块，测试执行其真实函数。
        for name in ("paths.sh", "paths.py", "repack-gate.sh", "api.py", "route_tiers.py", "renpy-wrap.sh",
                     "renpy-profile.py", "renpy-build.py", "renpy-runtime.py",
                     "runtime_archive.py", "love-runtime.py", "nwjs-build.py", "love-inspect.py",
                     "uninstall_purge.py"):
            shutil.copy2(ROOT / "lib" / name, self.lib / name)
        self.games = self.base / "games with spaces"
        self.game = self.games / "Own Fixture"
        self.game.mkdir(parents=True)
        for name, body in GAME_FILES.items():
            path = self.game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        (self.game / ".dryport-appid").write_text(APPID)
        self.app = self.games / "Own Fixture.app"
        self.runtime = self.base / "shared runtime"
        self.runtime.mkdir()
        (self.runtime / "anticheat-db.json").write_text("[]")
        (self.runtime / "runtime-canary").write_bytes(b"original shared runtime")
        self.steam = self.base / "isolated Steam"
        self.steam.mkdir()
        self.calls = self.base / "calls.jsonl"
        self.commands = self.base / "commands"
        self.commands.mkdir()
        self.env = {
            "PATH": str(self.commands) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.base), "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1", "SN_HOME": str(self.home), "SN_GAMES": str(self.games),
            "SN_RUNTIMES": str(self.runtime), "STEAM": str(self.steam), "SN_FORCE_REPACK": "0",
            "SN_TEST_CALLS": str(self.calls), "SN_TEST_LIB": str(self.lib),
            "SN_TEST_PROFILE_SHA256": FIXTURE_SHA256,
        }
        self.write_boundaries()

    def script(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o755)

    def write_boundaries(self):
        common = ("import json,os,sys\nfrom pathlib import Path\n"
                  "def record(kind, args):\n"
                  "    with open(os.environ['SN_TEST_CALLS'],'a') as f:\n"
                  "        f.write(json.dumps({'kind':kind,'args':args})+'\\n')\n")
        self.script(self.commands / "python3", common + f"REAL = {sys.executable!r}\n" + '''
args = sys.argv[1:]
name = Path(args[0]).name if args else ""
if name == "renpy-profile.py":
    # 仅换自有 fixture 的预期摘要；执行实际 profile/inspect/main。
    import importlib,runpy
    sys.path.insert(0, os.environ["SN_TEST_LIB"])
    importlib.import_module("renpy-build").PAYLOAD_SHA256 = os.environ["SN_TEST_PROFILE_SHA256"]
    record("profile", args[1:])
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
elif name == "renpy-build.py":
    record("build", args[1:])
    if os.environ.get("SN_TEST_BUILD_FAIL"):
        raise SystemExit(23)
    output = Path(args[3])
    output.mkdir(exist_ok=False)
    (output / "Contents/MacOS").mkdir(parents=True)
    (output / "Contents/Resources").mkdir()
    (output / "Contents/MacOS/launcher").write_bytes(b"owned build fixture; never executed")
    (output / "Contents/Resources/steam_appid.txt").write_text("2118420\\n")
    print(json.dumps({"app":str(output), "fixture":True}))
else:
    os.execv(REAL, [REAL, *args])
''')
        self.script(self.lib / "risk.py", common + '''
appid = int(sys.argv[-1])
code = int(os.environ.get("SN_TEST_RISK_CODE", "0"))
verdict = {0:"可原生改包", 1:"只能原样跑(Wine)", 2:"一律不碰", 3:"风险待核实"}[code]
record("risk", [appid, code])
print(json.dumps(dict(protocol=1,appid=appid,code=code,name="own fixture",verdict=verdict,why=["isolated fixture"])))
raise SystemExit(code)
''')
        self.script(self.home / "bin/install.sh", common + '''
record("runtime", sys.argv[1:])
raise SystemExit(int(os.environ.get("SN_TEST_RUNTIME_RC", "0")))
''')
        self.script(self.commands / "node", common + '''
name = Path(sys.argv[1]).name
if name == "steam-download.mjs" and sys.argv[2] == "--verify-delivery":
    record("delivery", sys.argv[2:])
    print(json.dumps(dict(protocol=1,appid=sys.argv[3],complete=True,scope="base_game")))
elif name == "steam-add.mjs":
    record("add", sys.argv[2:])
    if os.environ.get("SN_TEST_ADD_FAIL"):
        print("fixture: official AddShortcut unavailable", file=sys.stderr)
        raise SystemExit(69)
    print(123456)
elif name == "steam-shortcut-remove.mjs":
    record("remove", sys.argv[2:])
    if sys.argv[1] != str(Path(os.environ["SN_TEST_LIB"]) / name) or len(sys.argv) != 3:
        raise SystemExit(94)
    if os.environ.get("SN_TEST_REMOVE_NO_ENDPOINT"):
        print("fixture: Steam endpoint unavailable", file=sys.stderr)
        raise SystemExit(69)
else:
    record("unexpected_node", sys.argv[1:])
    raise SystemExit(91)
''')
        self.script(self.commands / "curl", common + 'record("cdp", sys.argv[1:])\n')
        self.script(self.commands / "codesign", common + 'record("sign", sys.argv[1:])\n')
        self.script(self.lib / "steam-shortcut-mirror.py", common + '''
if sys.argv[1] == "--find":
    raise SystemExit(1)
elif sys.argv[1] == "--active":
    print(123)
elif sys.argv[1] == "--remove":
    record("legacy_remove", sys.argv[2:])
    raise SystemExit(95)
else:
    raise SystemExit(92)
''')
        self.script(self.lib / "steam-shortcut-check.py", common + '''
record("shortcut_check", sys.argv[1:])
if sys.argv[1] == "prepare":
    receipt = Path(os.environ["SN_TEST_CALLS"]).with_name("shortcut-backup.json")
    receipt.write_text(json.dumps({"uid":123}))
    print(receipt)
elif sys.argv[1] != "verify":
    raise SystemExit(93)
''')

    def invoke(self, *arguments, **environment):
        return subprocess.run([str(CLI), *arguments], env={**self.env, **environment},
                              cwd=self.base, capture_output=True, text=True, timeout=20)

    def records(self, kind):
        records = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return [record["args"] for record in records if record["kind"] == kind]

    def test_fixed_profile_installs_through_runtime_builder_and_official_add_boundary(self):
        original = inventory(self.game)
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.records("runtime"), [["--runtime-only", "--with-renpy"]])
        self.assertEqual(self.records("build"), [[str(self.game), "Own Fixture", str(self.app)]])
        self.assertEqual(self.records("add"), [["Own Fixture", str(self.app)]])
        self.assertEqual((self.app / "Contents/Resources/steam_appid.txt").read_text(), APPID)
        self.assertEqual(inventory(self.game), original)
        checks = self.records("shortcut_check")
        self.assertEqual([args[0] for args in checks], ["prepare", "verify"])
        self.assertIn("═══ 完成 ═══", result.stdout)

    def test_no_add_stops_after_packaging_and_reports_the_product_path(self):
        """--no-add：四步照走、产物签好，但不碰快捷方式；产物路径写进调用方给的 WRAP_OUT_FILE。自动验证（smoke）与不想入库的人用。"""
        outfile = self.base / "wrap-out.txt"
        result = self.invoke("install", APPID, str(self.game), "--no-add", WRAP_OUT_FILE=str(outfile))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.records("build"), [[str(self.game), "Own Fixture", str(self.app)]])
        self.assertEqual(self.records("add"), [])
        self.assertEqual(self.records("shortcut_check"), [])
        self.assertEqual((self.app / "Contents/Resources/steam_appid.txt").read_text(), APPID)
        self.assertEqual(outfile.read_text().strip(), str(self.app))
        self.assertIn("跳过入库", result.stdout)
        self.assertIn(f"产物: {self.app}", result.stdout)
        self.assertNotIn("═══ 完成 ═══", result.stdout)
        unknown = self.invoke("install", APPID, str(self.game), "--bogus")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertEqual(self.records("build"), [[str(self.game), "Own Fixture", str(self.app)]])

    def test_each_selected_route_uses_runtime_only_and_propagates_runtime_failure(self):
        fixtures = {
            "unity": {"UnityPlayer.dll": b"2022.3.0f1 (0123456789ab)\n",
                      "Own_Data/Managed/Assembly-CSharp.dll": b"own fixture; never loaded"},
            "nwjs": {"package.json": b'{"name":"own-fixture","main":"index.html"}',
                     "index.html": b"<p>own fixture; never opened</p>"},
            "love": {"main.lua": b'error("fixture must never execute")\n',
                     "conf.lua": b'function love.conf(t) t.version="11.5" t.identity="own-fixture" end\n'},
            "renpy": GAME_FILES,
        }
        for route, files in fixtures.items():
            with self.subTest(route=route):
                game = self.games / ("Scope " + route)
                game.mkdir()
                for name, body in files.items():
                    path = game / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                original = inventory(game)
                self.calls.write_text("")
                # 故意在真实第 4 步的外部 runtime 边界返回 27，不能继续构建或入库。
                result = self.invoke("install", APPID, str(game), SN_TEST_RUNTIME_RC="27")
                self.assertEqual(result.returncode, 27, result.stdout + result.stderr)
                self.assertEqual(self.records("runtime"), [["--runtime-only", "--with-" + route]])
                self.assertEqual(self.records("delivery"), [["--verify-delivery", APPID, str(game)]])
                self.assertEqual(self.records("build"), [])
                self.assertEqual(self.records("add"), [])
                self.assertFalse((self.games / (game.name + ".app")).exists())
                self.assertEqual(inventory(game), original)
                self.assertNotIn("═══ 完成 ═══", result.stdout)

    def test_first_risk_database_preparation_keeps_global_scope(self):
        (self.runtime / "anticheat-db.json").unlink()
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.records("runtime"), [[], ["--runtime-only", "--with-renpy"]])
        self.assertTrue(self.records("risk"))
        self.assertEqual(len(self.records("add")), 1)

    def test_risk_refusal_stops_before_delivery_runtime_build_or_add(self):
        original = inventory(self.game)
        result = self.invoke("install", APPID, str(self.game), SN_TEST_RISK_CODE="2")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assert_not_assembled_or_registered()
        self.assertEqual(self.records("delivery"), [])
        self.assertEqual(inventory(self.game), original)

    def test_love_version_refusal_stops_before_runtime_build_or_add(self):
        game = self.games / "Unsupported LOVE"
        game.mkdir()
        (game / "main.lua").write_bytes(b'error("fixture must never execute")\n')
        (game / "conf.lua").write_bytes(b'function love.conf(t) t.version="10.2" t.identity="own-fixture" end\n')
        original = inventory(game)
        result = self.invoke("install", APPID, str(game))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_not_assembled_or_registered()
        self.assertIn("11.5", result.stderr)
        self.assertEqual(inventory(game), original)

    def test_runtime_and_build_success_do_not_hide_official_add_failure(self):
        state = self.games / ".renpy-state" / APPID / "saves"
        state.mkdir(parents=True)
        save = state / "1-1.save"
        save.write_bytes(b"own existing save; never decoded")
        save_mtime = save.stat().st_mtime_ns
        preserved = [inventory(path) for path in (self.game, self.runtime, state)]

        result = self.invoke("install", APPID, str(self.game), SN_TEST_ADD_FAIL="1")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.records("runtime"), [["--runtime-only", "--with-renpy"]])
        self.assertEqual(len(self.records("build")), 1)
        self.assertEqual(self.records("add"), [["Own Fixture", str(self.app)]])
        self.assertEqual([args[0] for args in self.records("shortcut_check")], ["prepare"])
        self.assertIn("official AddShortcut unavailable", result.stderr)
        self.assertNotIn("═══ 完成 ═══", result.stdout)
        self.assertTrue(self.app.is_dir())
        self.assertEqual((self.app / "Contents/MacOS/launcher").read_bytes(), b"owned build fixture; never executed")
        self.assertEqual([inventory(path) for path in (self.game, self.runtime, state)], preserved)
        self.assertEqual(save.stat().st_mtime_ns, save_mtime)

    def assert_not_assembled_or_registered(self):
        for kind in ("runtime", "build", "add"):
            self.assertEqual(self.records(kind), [], kind)
        self.assertFalse(self.app.exists())

    def test_other_appid_cannot_use_matching_payload_as_its_installation(self):
        original = inventory(self.game)
        result = self.invoke("install", "42", str(self.game))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_not_assembled_or_registered()
        self.assertEqual(inventory(self.game), original)
        self.assertIn("尚无已验证", result.stderr)

    def test_modified_payload_remains_candidate_without_runtime_build_or_add(self):
        with (self.game / "gundog.py").open("ab") as stream:
            stream.write(b"# changed after the fixed profile\n")
        original = inventory(self.game)
        result = self.invoke("install", APPID, str(self.game))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_not_assembled_or_registered()
        self.assertEqual(inventory(self.game), original)
        self.assertNotIn("早期剧情存读档已验证", result.stdout)

    def test_build_failure_never_adds_or_reports_completed_installation(self):
        original = inventory(self.game)
        result = self.invoke("install", APPID, str(self.game), SN_TEST_BUILD_FAIL="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.records("runtime"), [["--runtime-only", "--with-renpy"]])
        self.assertEqual(len(self.records("build")), 1)
        self.assertEqual(self.records("add"), [])
        self.assertEqual(self.records("shortcut_check"), [])
        self.assertFalse(self.app.exists())
        self.assertEqual(inventory(self.game), original)
        self.assertNotIn("═══ 完成 ═══", result.stdout)

    def test_direct_wrapper_rejects_foreign_registration_identity(self):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "lib/renpy-wrap.sh"), str(self.game),
             "Own Fixture", str(self.app), "42"],
            env=self.env, cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_not_assembled_or_registered()
        self.assertIn("尚无已验证", result.stderr)

    def test_direct_wrapper_cannot_force_past_anticheat_refusal(self):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "lib/renpy-wrap.sh"), str(self.game),
             "Own Fixture", str(self.app), APPID],
            env={**self.env, "SN_TEST_RISK_CODE": "2", "SN_FORCE_REPACK": "1"},
            cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assert_not_assembled_or_registered()

    def test_archive_moves_app_aside_and_preserves_external_saves_runtime_download(self):
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.games / ".renpy-state" / APPID
        (state / "saves").mkdir(parents=True)
        (state / ".dryport-renpy.json").write_text(json.dumps({
            "protocol": 1, "kind": "dryport-renpy", "appid": APPID}))
        (state / "saves/persistent").write_bytes(b"own persistent canary; never decoded")
        (state / "saves/1-1.save").write_bytes(b"own saved story canary; never decoded")
        preserved = {"state": inventory(state), "download": inventory(self.game),
                     "runtime": inventory(self.runtime), "app": inventory(self.app)}
        self.calls.write_text("")

        preview = self.invoke("archive", APPID)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertTrue(self.app.is_dir())
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertEqual(self.records("remove"), [])

        result = self.invoke("archive", APPID, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.app.exists())
        archived = list(self.games.glob(".uninstalled/2118420-*/Own Fixture.app"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(inventory(archived[0]), preserved["app"])
        self.assertEqual(inventory(state), preserved["state"])
        self.assertEqual(inventory(self.game), preserved["download"])
        self.assertEqual(inventory(self.runtime), preserved["runtime"])
        self.assertEqual(self.records("remove"), [[str(self.app)]])
        self.assertEqual(self.records("legacy_remove"), [])
        self.assertNotIn(APPID, json.loads((self.games / "installed.json").read_text()))

    def test_uninstall_is_irreversible_deletes_only_ours_and_keeps_state_unless_asked(self):
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.game / ".dryport-appid").write_text(APPID + "\n")   # 本工具的下载标记；没有它下载目录不会被删
        state = self.games / ".renpy-state" / APPID
        (state / "saves").mkdir(parents=True)
        (state / "steam_appid.txt").write_text(APPID)
        (state / "saves/1-1.save").write_bytes(b"own saved story canary; never decoded")
        foreign = self.games / "someone_else"; foreign.mkdir(); (foreign / "keep").write_bytes(b"not ours")

        preview = self.invoke("uninstall", APPID)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("不可逆", preview.stdout)
        self.assertEqual(self.records("remove"), [])
        self.assertTrue(self.app.is_dir()); self.assertTrue(self.game.is_dir())

        plan = self.invoke("uninstall", APPID, "--plan-json")
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        kinds = sorted((i["kind"], i["default"]) for i in json.loads(plan.stdout)["items"])
        self.assertEqual(kinds, [("app", True), ("download", True), ("saves", False)])

        result = self.invoke("uninstall", APPID, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.app.exists())
        self.assertEqual(list(self.games.glob(".uninstalled/2118420-*")), [])     # 归档后随即删除
        self.assertFalse(self.game.exists())                                        # 我们的下载目录
        self.assertTrue((state / "saves/1-1.save").exists())                        # 存档默认保留
        self.assertTrue((foreign / "keep").exists())
        self.assertEqual(self.records("remove"), [[str(self.app)]])                 # 快捷方式仍走官方接口
        self.assertIn("实际释放", result.stdout)

        again = self.invoke("uninstall", APPID, "--saves", "--yes")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertFalse(state.exists())

    def test_restore_puts_the_archived_app_back_and_readds_it_through_the_official_path(self):
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = inventory(self.app)
        result = self.invoke("archive", APPID, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.app.exists())
        self.calls.write_text("")

        preview = self.invoke("restore", APPID)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("恢复", preview.stdout)
        self.assertFalse(self.app.exists())
        self.assertEqual(self.records("add"), [])

        result = self.invoke("restore", APPID, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(inventory(self.app), before)                                # 原样放回
        self.assertEqual(list((self.games / ".uninstalled").iterdir()), [])          # 空容器随手收掉
        self.assertEqual(self.records("add"), [["Own Fixture", str(self.app)]])     # 重新入库仍走官方接口
        self.assertEqual(self.records("remove"), [])
        again = self.invoke("archive", APPID, "--yes")                               # 恢复后就是装着的
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.records("remove"), [[str(self.app)]])

        nothing = self.invoke("restore", "999", "--yes")
        self.assertNotEqual(nothing.returncode, 0)
        self.assertIn("没有归档", nothing.stderr)

    def test_archive_without_steam_endpoint_keeps_app_state_and_registration(self):
        result = self.invoke("install", APPID, str(self.game))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.games / ".renpy-state" / APPID / "saves"
        state.mkdir(parents=True)
        save = state / "1-1.save"
        save.write_bytes(b"own existing save; never decoded")
        save_mtime = save.stat().st_mtime_ns
        registry = self.games / "installed.json"
        registry_before = registry.read_bytes() if registry.exists() else None
        roots = (self.app, self.game, self.runtime, state)
        before = [inventory(path) for path in roots]
        self.calls.write_text("")

        result = self.invoke("archive", APPID, "--yes", SN_TEST_REMOVE_NO_ENDPOINT="1")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture: Steam endpoint unavailable", result.stdout + result.stderr)
        self.assertEqual(self.records("remove"), [[str(self.app)]])
        self.assertEqual(self.records("legacy_remove"), [])
        self.assertEqual([inventory(path) for path in roots], before)
        self.assertEqual(save.stat().st_mtime_ns, save_mtime)
        self.assertEqual(registry.read_bytes() if registry.exists() else None, registry_before)
        self.assertFalse((self.games / ".uninstalled").exists())
        self.assertNotIn("已完成卸载", result.stdout)


if __name__ == "__main__":
    unittest.main()
