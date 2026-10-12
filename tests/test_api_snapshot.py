# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""API 浏览必须只读，第一次运行与下载未打包时也不能报安装成功。"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import api
import library_discovery as discovery
import uninstall_purge


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-api-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.games = self.root / "games"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in (("GAMES", self.games), ("REGISTRY", self.games / "installed.json"),
                           ("RUNTIMES", self.root / "runtimes"), ("STEAM", self.root / "steam")):
            self.stack.enter_context(patch.object(api, key, value))
        # snapshot/state 也读归档与冒烟目录；不 patch 就会扫到真实 $SN_GAMES
        self.stack.enter_context(patch.object(api, "SMOKE_DIR", self.games / ".smoke"))
        self.stack.enter_context(patch.object(uninstall_purge, "SN_GAMES", self.games))
        self.running = self.stack.enter_context(patch.object(api, "steam_running", return_value=False))
        self.stack.enter_context(patch.dict(api.os.environ))
        for key in ("NWJS_APP", "NWJS_ROOT", "KRKR_APP", "SN_VENV"):
            api.os.environ.pop(key, None)

    def write(self, name, data):
        self.games.mkdir(exist_ok=True)
        (self.games / name).write_text(json.dumps(data))

    def capture(self, call, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            call(*args)
        return json.loads(output.getvalue())

    def make_routes(self):
        self.write("route-all.json", [dict(appid="101", name="推测名", route="wine"),
                                       dict(appid="102", name="待下载", route="godot")])
        self.write("final-routes.json", [dict(appid="101", name="确证名", route="mkxp"),
                                          dict(appid="103", name="第二遍独有", route="refuse")])

    def test_fresh_snapshot_does_not_create_games_directory(self):
        result = self.capture(api.cmd_snapshot)
        self.assertEqual(result["library"], {"total": 0, "rows": []})
        self.assertEqual(result["state"]["installed"], 0)
        self.assertFalse(self.games.exists())
        self.running.assert_called_once()

    def test_manifest_precedence_and_manifest_only_rows_survive(self):
        self.make_routes()
        rows = {r["appid"]: r for r in api.all_routes()}
        self.assertEqual(set(rows), {"101", "102", "103"})
        self.assertEqual((rows["101"]["name"], rows["101"]["source"]), ("确证名", "manifest"))
        self.assertEqual(rows["103"]["route"], "refuse")

    def test_download_is_not_installed_and_existing_app_is_discovered(self):
        self.make_routes()
        folder = self.games / "download"
        folder.mkdir()
        (folder / ".dryport-appid").write_text("101")
        first = self.capture(api.cmd_snapshot)
        row = next(r for r in first["library"]["rows"] if r["appid"] == "101")
        self.assertFalse(row["installed"])
        self.assertEqual(row["folder"], str(folder))
        app = self.games / "fixture.app/Contents/MacOS"
        app.mkdir(parents=True)
        (app / "steam_appid.txt").write_text("101")
        second = self.capture(api.cmd_snapshot)
        self.assertTrue(second["library"]["rows"][0]["installed"])
        self.assertEqual(second["state"]["installed"], 1)
        self.assertFalse((self.games / "installed.json").exists())

    def test_stale_registry_does_not_claim_success_or_rewrite_user_file(self):
        self.make_routes()
        self.write("installed.json", {"101": {"app": str(self.games / "missing.app")}})
        path = self.games / "installed.json"
        before, modified = path.read_bytes(), path.stat().st_mtime_ns
        result = self.capture(api.cmd_snapshot)
        self.assertEqual(result["state"]["installed"], 0)
        self.assertFalse(any(r["installed"] for r in result["library"]["rows"]))
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (before, modified))

    def test_state_library_and_snapshot_share_one_contract(self):
        self.make_routes()
        result = self.capture(api.cmd_snapshot)
        # 库行携带路线分层（route-tiers.json 未列出的路线是 null）：101=mkxp、102=godot。
        # 验证期限过后，全部路线实验，101 不再是 open。
        self.assertEqual({r["appid"]: r["tier"] for r in result["library"]["rows"]},
                         {"101": "experimental", "102": "experimental", "103": None})
        self.assertEqual(result["library"], self.capture(api.library, {}))
        state = self.capture(api.cmd_state)
        for key in ("route_counts", "library_size", "installed", "confirmed", "guessed"):
            self.assertEqual(result["state"][key], state[key])
        self.assertFalse((self.games / "installed.json").exists())

    def test_snapshot_loads_route_tables_once(self):
        self.make_routes()
        with patch.object(api, "all_routes", wraps=api.all_routes) as routes:
            self.capture(api.cmd_snapshot)
            routes.assert_called_once()

    def publish_catalog(self, appid="201", shared=False):
        account = {"id": "123", "account_id": "123", "steamid64": str(discovery.STEAM_ID_BASE + 123)}
        snapshot = {"catalog": {"protocol": 1, "account": account, "excluded": {"borrowed": 8}},
                    "route-all": [dict(appid=appid, name="当前自有", route="nwjs")],
                    "final-routes": [], "owned-windows": [dict(appid=appid, dl=12345)]}
        if shared:
            snapshot["catalog"].update(protocol=2, windows_owned_count=1, windows_shared_count=1,
                                       metadata_missing=3, warnings=["还有 3 条资料待补齐"])
            snapshot["route-all"].append(dict(appid="301", name="共享候选", route="nwjs"))
            snapshot["windows-library"] = [dict(appid=appid, name="当前自有", dl=12345, ownership="owned", license_source="self"),
                                            dict(appid="301", name="共享候选", dl=67890, ownership="borrowed", license_source="family_shared")]
        with patch.object(discovery, "read_client", return_value={"protocol": 1, "account": account}), \
                patch.object(discovery, "current_account_id", return_value="123"):
            return discovery.publish(snapshot, self.games)

    def test_shared_games_use_the_same_library_and_download_metadata(self):
        self.publish_catalog(shared=True)
        with patch.object(discovery, "current_account_id", return_value="123"):
            result = self.capture(api.cmd_snapshot)
        rows = {row["appid"]: row for row in result["library"]["rows"]}
        self.assertEqual(set(rows), {"201", "301"})
        self.assertEqual(rows["301"]["download_bytes"], 67890)
        self.assertEqual(rows["301"]["route"], rows["201"]["route"])
        self.assertEqual(result["state"]["catalog_shared_games"], 1)
        self.assertEqual(result["state"]["metadata_missing"], 3)
        self.assertTrue(result["state"]["catalog_warnings"])

    def test_shared_official_and_missing_metadata_games_stay_visible(self):
        published = self.publish_catalog(shared=True)
        rows = json.loads((published / "route-all.json").read_text())
        rows.extend([dict(appid="401", name="官方 macOS", route="steam", ownership="borrowed"),
                     dict(appid="402", name="资料尚缺", route="unknown", ownership="borrowed")])
        (published / "route-all.json").write_text(json.dumps(rows))
        meta = json.loads((published / "catalog.json").read_text())
        meta.update(games_owned_count=1, games_shared_count=3)
        (published / "catalog.json").write_text(json.dumps(meta))
        with patch.object(discovery, "current_account_id", return_value="123"):
            result = self.capture(api.cmd_snapshot)
        rows = {row["appid"]: row for row in result["library"]["rows"]}
        self.assertEqual(set(rows), {"201", "301", "401", "402"})
        self.assertEqual(rows["401"]["ownership"], "borrowed")
        self.assertFalse(rows["401"]["native"])  # 发行记录不证明当前硬件可运行。
        self.assertEqual(result["state"]["catalog_shared_games"], 3)

    def test_detail_refines_only_selected_game_without_rewriting_catalog(self):
        self.write("route-all.json", [dict(appid="101", name="待识别", route="unknown", risk=discovery.risk.REPACK),
                                       dict(appid="102", name="未选中", route="unknown", risk=discovery.risk.REPACK)])
        self.write("owned-windows.json", [dict(appid="101", depot="111", gid="222"),
                                           dict(appid="102", depot="999", gid="333")])
        cache = api.STEAM / "depotcache"
        cache.mkdir(parents=True)
        (cache / "111_222.manifest.txt").write_text("1 0 " + "a" * 40 + " 0 www/js/rpg_core.js\n")
        before = (self.games / "route-all.json").read_bytes()
        gate = type("Gate", (), {"returncode": 0, "stdout": "通过", "stderr": ""})()
        with patch.object(api.subprocess, "run", return_value=gate), \
                patch.object(discovery, "manifest_files", wraps=discovery.manifest_files) as manifests, \
                patch.object(discovery, "read_client", side_effect=AssertionError("详情不能读全库")):
            result = self.capture(api.cmd_detail, "101")
        self.assertEqual(result["route"], "nwjs")
        self.assertEqual(result["source"], "manifest")
        self.assertEqual(manifests.call_count, 1)
        self.assertEqual(manifests.call_args[0][0], [{"depot": "111", "gid": "222"}])
        self.assertEqual((self.games / "route-all.json").read_bytes(), before)

    def test_official_detail_does_not_run_repack_gate(self):
        self.write("route-all.json", [dict(appid="101", name="官方发行版", route="steam")])
        with patch.object(api.subprocess, "run", side_effect=AssertionError("官方入口不改包")):
            result = self.capture(api.cmd_detail, "101")
        self.assertEqual(result["route"], "steam")
        self.assertNotIn("risk_gate", result)

    def test_detail_reads_engine_evidence_without_promoting_unknown_platform(self):
        published = self.publish_catalog(shared=True)
        note = "平台资料待核实，不能据此确认 Windows 专属"
        rows = json.loads((published / "route-all.json").read_text())
        rows.append(dict(appid="402", name="平台尚缺", route="unknown", risk=discovery.risk.UNKNOWN,
                         metadata_status="missing", oslist=[], platform_note=note))
        (published / "route-all.json").write_text(json.dumps(rows))
        meta = json.loads((published / "catalog.json").read_text())
        meta["apps"] = [dict(appid="402", depots=[dict(depot="111", gid="222")]),
                        dict(appid="403", depots=[dict(depot="999", gid="333")])]
        (published / "catalog.json").write_text(json.dumps(meta))
        cache = api.STEAM / "depotcache"
        cache.mkdir(parents=True)
        (cache / "111_222.manifest.txt").write_text("1 0 " + "a" * 40 + " 0 UnityPlayer.dll\n")
        before = {p.name: p.read_bytes() for p in published.iterdir()}
        gate = type("Gate", (), {"returncode": 0, "stdout": "单独风险检查", "stderr": ""})()
        with patch.object(discovery, "current_account_id", return_value="123"), \
                patch.object(api.subprocess, "run", return_value=gate), \
                patch.object(discovery, "manifest_files", wraps=discovery.manifest_files) as manifests:
            result = self.capture(api.cmd_detail, "402")
        self.assertEqual(result["engine"], "Unity")
        self.assertEqual(result["route"], "unknown")
        self.assertEqual(result["risk"], discovery.risk.UNKNOWN)
        self.assertFalse(result["native"])
        self.assertEqual(result["download_bytes"], 0)
        self.assertEqual(result["platform_note"], note)
        self.assertEqual(result["risk_gate"], {"code": 3, "text": note})
        self.assertEqual(manifests.call_count, 1)
        self.assertEqual(manifests.call_args[0][0], [{"depot": "111", "gid": "222"}])
        self.assertEqual({p.name: p.read_bytes() for p in published.iterdir()}, before)

    def test_incomplete_platform_preserves_refusal_and_gate_failure(self):
        self.write("route-all.json", [dict(appid="101", name="资料待补齐", route="unknown",
                                          metadata_status="missing")])
        for code in (1, 2, 3, 7, -1):
            with self.subTest(code=code):
                gate = type("Gate", (), {"returncode": code, "stdout": "风险检查结果", "stderr": ""})()
                with patch.object(api.subprocess, "run", return_value=gate):
                    result = self.capture(api.cmd_detail, "101")
                self.assertEqual(result["risk_gate"]["code"], 3 if code == 1 else code)
                if code != 1:
                    self.assertEqual(result["risk_gate"]["text"], "风险检查结果")

    def test_current_catalog_replaces_legacy_without_merging_other_accounts(self):
        self.make_routes()
        self.publish_catalog()
        with patch.object(discovery, "current_account_id", return_value="123"):
            result = self.capture(api.cmd_snapshot)
        self.assertEqual([r["appid"] for r in result["library"]["rows"]], ["201"])
        self.assertEqual(result["library"]["rows"][0]["download_bytes"], 12345)
        self.assertEqual(result["state"]["catalog_status"], "current")
        self.assertEqual(result["state"]["borrowed_count"], 8)
        self.assertTrue((self.games / "route-all.json").is_file())

    def test_account_switch_hides_catalog_but_preserves_local_install(self):
        self.make_routes()
        self.publish_catalog()
        marker = self.games / "local.app/Contents/Resources/steam_appid.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("901")
        with patch.object(discovery, "current_account_id", return_value="456"):
            result = self.capture(api.cmd_snapshot)
        self.assertEqual([r["appid"] for r in result["library"]["rows"]], ["901"])
        self.assertEqual(result["state"]["catalog_status"], "unavailable")
        self.assertIn("账号已切换", result["state"]["catalog_error"])
        self.assertTrue(marker.is_file())

    def test_catalog_switch_during_snapshot_does_not_mix_versions(self):
        self.publish_catalog()
        rows = api.catalog_rows
        switched = False
        def switching_rows(name, context=None):
            nonlocal switched
            data = rows(name, context)
            if not switched:
                switched = True
                self.publish_catalog("202")
            return data
        with patch.object(discovery, "current_account_id", return_value="123"), \
                patch.object(api, "catalog_rows", side_effect=switching_rows):
            result = self.capture(api.cmd_snapshot)
        self.assertEqual([r["appid"] for r in result["library"]["rows"]], ["201"])
        self.assertEqual(result["library"]["rows"][0]["download_bytes"], 12345)

    def test_damaged_new_table_does_not_silently_fall_back(self):
        self.make_routes()
        published = self.publish_catalog()
        (published / "route-all.json").write_text("broken")
        with patch.object(discovery, "current_account_id", return_value="123"):
            result = self.capture(api.cmd_snapshot)
        self.assertEqual(result["state"]["catalog_status"], "unavailable")
        self.assertTrue(result["state"]["catalog_error"])
        self.assertEqual(result["library"]["total"], 0)

    def test_first_app_is_visible_without_an_owned_library_table(self):
        app = self.games / "首次安装.app/Contents/MacOS"
        app.mkdir(parents=True)
        (app / "steam_appid.txt").write_text("901")
        result = self.capture(api.cmd_snapshot)
        self.assertEqual(result["library"]["total"], 1)
        row = result["library"]["rows"][0]
        self.assertEqual((row["appid"], row["name"], row["source"]), ("901", "首次安装", "local"))
        self.assertTrue(row["installed"])
        self.assertEqual(result["state"]["library_size"], 1)
        self.assertEqual(result["state"]["local"], 1)
        self.assertEqual(result["state"]["guessed"], 0)
        self.assertFalse((self.games / "installed.json").exists())

    def test_first_app_play_uses_discovery_without_creating_registry(self):
        app = self.games / "first.app/Contents/MacOS"
        app.mkdir(parents=True)
        (app / "steam_appid.txt").write_text("901")
        with patch.object(api.subprocess, "Popen") as launch:
            result = self.capture(api.cmd_play, "901")
        self.assertEqual(result["event"], "launched")
        launch.assert_called_once_with(["open", "-a", str(self.games / "first.app")])
        self.assertFalse((self.games / "installed.json").exists())

    def test_resource_marker_is_discovered_and_wins_over_legacy_marker(self):
        app = self.games / "new.app"
        marker = app / "Contents/Resources/steam_appid.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("902")
        result = self.capture(api.cmd_snapshot)
        self.assertEqual(result["library"]["rows"][0]["appid"], "902")
        legacy = app / "Contents/MacOS/steam_appid.txt"
        legacy.parent.mkdir()
        legacy.write_text("901")
        rows = self.capture(api.cmd_snapshot)["library"]["rows"]
        self.assertEqual([row["appid"] for row in rows], ["902"])
        self.assertTrue(rows[0]["installed"])

    def test_invalid_resource_marker_cannot_fall_back_to_legacy_marker(self):
        app = self.games / "new.app"
        legacy = app / "Contents/MacOS/steam_appid.txt"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("901")
        marker = app / "Contents/Resources/steam_appid.txt"
        marker.parent.mkdir()
        for value in ("", "not-an-appid"):
            with self.subTest(value=value):
                marker.write_text(value)
                result = self.capture(api.cmd_snapshot)
                self.assertEqual(result["state"]["installed"], 0)
        marker.unlink()
        marker.symlink_to(self.root / "missing-marker")
        self.assertEqual(self.capture(api.cmd_snapshot)["state"]["installed"], 0)

    def test_empty_runtime_directories_do_not_claim_ready(self):
        for name in ("nwjs", "godot", "unity-retarget/venv/bin", "krkrsdl2/build/krkrsdl2.app"):
            (api.RUNTIMES / name).mkdir(parents=True)
        tools = self.capture(api.cmd_state)["tools"]
        self.assertFalse(any(tools.values()))
        bins = [api.RUNTIMES / "nwjs/test/nwjs-vtest-osx-arm64/nwjs.app/Contents/MacOS/nwjs",
                api.RUNTIMES / "unity-retarget/venv/bin/python",
                api.RUNTIMES / "unity-retarget/hlslcc-cli"]
        for path in bins:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
            path.chmod(0o644)
        self.assertFalse(self.capture(api.cmd_state)["tools"]["nwjs"])
        self.assertFalse(self.capture(api.cmd_state)["tools"]["unity_retarget"])
        for path in bins:
            path.chmod(0o755)
        self.assertTrue(self.capture(api.cmd_state)["tools"]["nwjs"])
        self.assertTrue(self.capture(api.cmd_state)["tools"]["unity_retarget"])

    def test_filtered_library_preserves_refusal_rows_by_default(self):
        self.make_routes()
        self.assertEqual(self.capture(api.library, {"route": "refuse"})["total"], 1)
        self.assertEqual(self.capture(api.library, {"only_playable": True})["total"], 2)
        self.assertEqual(self.capture(api.library, {"installed": True})["total"], 0)
        self.assertEqual(self.capture(api.library, {"q": "确证", "limit": 1})["rows"][0]["appid"], "101")

    def test_unimplemented_and_unknown_routes_are_not_claimed_native(self):
        self.write("route-all.json", [dict(appid=str(i), name=route, route=route)
                                      for i, route in enumerate(("renpy", "fna", "vn", "unknown"), 1)])
        rows = self.capture(api.library, {})["rows"]
        self.assertEqual(len(rows), 4)
        self.assertFalse(any(row["native"] for row in rows))


if __name__ == "__main__":
    unittest.main()
