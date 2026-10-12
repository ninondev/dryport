# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""拥有证明、多 depot 与原子私人清单；仅在临时目录写入，不连接真实 Steam。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import library_discovery as discovery
import risk


def receipt(appids=(42,), account=123, shared_appids=()):
    return {"protocol": 2,
            "account": {"steamid64": str(discovery.STEAM_ID_BASE + account), "account_id": str(account)},
            "apps": [{"appid": str(appid), "name": f"fixture {appid}", "app_type": 1,
                      "subscribed": True, "borrowed": False, "owner_account_id": "0",
                      "ownership": "owned", "license_source": "self"} for appid in appids]
                    + [{"appid": str(appid), "name": f"shared {appid}", "app_type": 1,
                        "subscribed": True, "borrowed": True, "owner_account_id": "456",
                        "ownership": "borrowed", "license_source": "family_shared"} for appid in shared_appids],
            "excluded": {"shortcuts": 1, "unsubscribed": 0}}


def metadata(name="fixture", platforms="windows"):
    return {"common": {"name": name, "type": "Game", "oslist": platforms, "category": {"category_2": "1"}},
            "config": {"launch": {"0": {"executable": "nw.exe"}}},
            "depots": {"43": {"manifests": {"public": {"gid": "18446744073709551615", "download": "40", "size": 80}}}}}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-catalog-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.games = self.root / "games"
        self.steam = self.root / "steam"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(discovery, "current_account_id", return_value="123"))
        self.client = self.stack.enter_context(patch.object(discovery, "read_client", return_value=receipt()))

    def snapshot(self):
        return discovery.build_snapshot(receipt(), {42: metadata()}, {}, steam=self.steam)

    def test_appinfo_is_metadata_only_not_ownership(self):
        snapshot = discovery.build_snapshot(receipt(), {42: metadata(), 99: metadata("not owned")}, {}, steam=self.steam)
        self.assertEqual([row["appid"] for row in snapshot["catalog"]["apps"]], ["42"])
        self.assertEqual([row["appid"] for row in snapshot["owned-windows"]], ["42"])
        self.assertEqual(snapshot["catalog"]["borrowed_count"], 0)
        self.assertEqual(snapshot["catalog"]["account"]["steamid64"], str(discovery.STEAM_ID_BASE + 123))

    def test_subscribed_family_library_keeps_provenance_without_inflating_owned(self):
        data = receipt(shared_appids=(99,))
        apps = {42: metadata(), 99: metadata("shared game"), 100: metadata("unlicensed cache")}
        snapshot = discovery.build_snapshot(data, apps, {}, steam=self.steam)
        self.assertEqual([row["appid"] for row in snapshot["owned-windows"]], ["42"])
        self.assertEqual([row["appid"] for row in snapshot["windows-library"]], ["42", "99"])
        self.assertEqual([row["appid"] for row in snapshot["route-all"]], ["42", "99"])
        shared = snapshot["windows-library"][1]
        self.assertEqual((shared["ownership"], shared["license_source"], shared["owner_account_id"]),
                         ("borrowed", "family_shared", "456"))
        self.assertEqual(shared["availability"], "not_checked")
        self.assertEqual(snapshot["catalog"]["owned_count"], 1)
        self.assertEqual(snapshot["catalog"]["shared_count"], 1)
        self.assertEqual(snapshot["catalog"]["windows_owned_count"], 1)
        self.assertEqual(snapshot["catalog"]["windows_shared_count"], 1)
        self.assertNotIn("borrowed", snapshot["catalog"]["excluded"])

    def test_shared_games_keep_identical_anticheat_refusal(self):
        snapshot = discovery.build_snapshot(receipt((), shared_appids=(99,)), {99: metadata()},
                                            {99: {"anticheats": ["fixture AC"]}}, steam=self.steam)
        self.assertEqual(snapshot["owned-windows"], [])
        self.assertEqual(snapshot["windows-library"][0]["route"], "refuse")
        self.assertEqual(snapshot["windows-library"][0]["risk"], risk.REFUSE)

    def test_missing_or_forged_ownership_never_uses_appinfo_as_fallback(self):
        for replacement in (None, {}, [], {"protocol": True, **{k: v for k, v in receipt().items() if k != "protocol"}}):
            with self.subTest(replacement=replacement):
                with self.assertRaises(discovery.DiscoveryError):
                    discovery.build_snapshot(replacement, {42: metadata()}, {}, steam=self.steam)
        for key, value in (("borrowed", True), ("owner_account_id", "456"), ("subscribed", 1)):
            bad = receipt()
            bad["apps"][0][key] = value
            with self.assertRaises(discovery.DiscoveryError):
                discovery.validate_receipt(bad)

    def test_missing_metadata_keeps_visible_catalog_identity_but_not_guessed_windows(self):
        snapshot = discovery.build_snapshot(receipt(), {}, {}, steam=self.steam)
        self.assertEqual(snapshot["catalog"]["metadata_missing"], 1)
        self.assertEqual(snapshot["catalog"]["apps"][0]["name"], "fixture 42")
        self.assertEqual(snapshot["owned-windows"], [])
        self.assertEqual(snapshot["route-all"][0]["name"], "fixture 42")
        self.assertEqual(snapshot["route-all"][0]["route"], "unknown")
        self.assertEqual(snapshot["catalog"]["games"], 1)

    def test_platform_missing_everywhere_defaults_to_windows_by_steam_convention(self):
        # 老游戏的 common 里常常没有 oslist；Steam 自己的规则是省略即 Windows。有内容包的 game 按此推断，并把证据写明。
        app = metadata(platforms='')
        del app["common"]["oslist"]
        snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)
        row = snapshot['route-all'][0]
        self.assertEqual((row['engine'], row['confidence'], row['source']), ('NW.js', '推测', 'appinfo'))
        self.assertEqual((row['oslist'], row['platform_evidence'], row['metadata_status']), (['windows'], 'default', 'default'))
        self.assertIn('按 Steam 惯例视为 Windows', row['platform_note'])
        self.assertEqual(snapshot['catalog']['metadata_missing'], 0)
        self.assertEqual([r['appid'] for r in snapshot['owned-windows']], ['42'])
        self.assertFalse(any('缺平台字段' in w for w in snapshot['catalog']['warnings']))

    def test_platform_from_launch_or_depot_config_when_common_has_none(self):
        app = metadata(); del app["common"]["oslist"]
        app["config"]["launch"]["0"]["config"] = {"oslist": "windows"}
        row = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)['route-all'][0]
        self.assertEqual((row['oslist'], row['platform_evidence'], row['metadata_status']), (['windows'], 'launch', 'derived'))
        app = metadata(); del app["common"]["oslist"]
        app["depots"]["43"]["config"] = {"oslist": "macos"}
        row = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)['route-all'][0]
        self.assertEqual((row['oslist'], row['platform_evidence'], row['route']), (['macos'], 'depot', 'steam'))

    def test_explicit_non_windows_platform_keeps_unknown_route(self):
        app = metadata(platforms='linux')
        snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)
        row = snapshot['route-all'][0]
        self.assertEqual((row['route'], row['risk'], row['oslist'], row['platform_evidence']), ('unknown', risk.UNKNOWN, ['linux'], 'oslist'))
        self.assertEqual(snapshot['owned-windows'], [])
        self.assertEqual(snapshot['catalog']['metadata_missing'], 0)   # 资料是全的，只是不含 Windows
        self.assertEqual(row['metadata_status'], 'available')
        self.assertIn('nw.exe', row['evidence'])
        self.assertIn('不代表 Windows 专属', row['platform_note'])
        self.assertEqual(snapshot['windows-library'], [])
        self.assertEqual(snapshot['catalog']['games'], 1)

    def test_missing_platform_manifest_evidence_and_refusal_survive_deep_and_cache(self):
        from manifest_route_cache import ManifestRouteCache
        app = metadata(platforms='linux')   # 明说了别的平台：不走 Windows 入口，但清单证据与拒绝照样成立
        path = self.steam / 'depotcache/43_18446744073709551615.manifest.txt'
        path.parent.mkdir(parents=True)
        path.write_text(f"12 0 {'0'*40} 0 UnityPlayer.dll\n")
        cache = ManifestRouteCache(self.steam, self.root.resolve() / 'cache')
        deep = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, deep=True,
                                         route_cache=cache, cache_write=True)
        result = deep['final-routes'][0]
        self.assertEqual((result['engine'], result['source'], result['confidence']), ('Unity', 'manifest', '确证'))
        self.assertEqual((result['route'], result['risk'], result['oslist']), ('unknown', risk.UNKNOWN, ['linux']))
        self.assertEqual(deep['windows-library'], [])
        with patch.object(discovery, 'manifest_files', side_effect=AssertionError('cache only')):
            fast = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, route_cache=cache)
        self.assertEqual(fast['final-routes'][0]['engine'], 'Unity')
        self.assertEqual(fast['final-routes'][0]['route'], 'unknown')
        path.write_text(f"12 0 {'0'*40} 0 EasyAntiCheat/launcher.exe\n")
        refused = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, deep=True)
        self.assertEqual(refused['final-routes'][0]['route'], 'refuse')
        self.assertEqual(refused['windows-library'], [])

    def test_unknown_manifest_keeps_confident_engine_even_when_platform_or_risk_pending(self):
        app = metadata(platforms='linux')   # 平台明说不含 Windows：引擎判断照样确证，路线待定
        app['config']['launch'] = {'0': {'executable': 'game-win64-shipping.exe'}}
        with patch.object(discovery, 'manifest_files', return_value=(['locales/english.txt'], 1)):
            snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, deep=True)
        result = snapshot['route-all'][0]
        self.assertEqual((result['engine'], result['confidence'], result['source']), ('Unreal Engine', '确证', 'appinfo'))
        self.assertEqual(result['route'], 'unknown')
        self.assertIn('game-win64-shipping.exe', result['evidence'])
        self.assertTrue(result['manifest_note'])
        self.assertEqual(snapshot['windows-library'], [])

    def test_combination_candidate_does_not_replace_confident_appinfo_engine(self):
        app = metadata()
        app['config']['launch'] = {'0': {'executable': 'game-win64-shipping.exe'}}
        files = ['resources.pak', 'icudtl.dat', 'v8_context_snapshot.bin', 'resources/app.asar']
        with patch.object(discovery, 'manifest_files', return_value=(files, 1)):
            snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, deep=True)
        row = snapshot['route-all'][0]
        self.assertEqual((row['engine'], row['confidence'], row['source']), ('Unreal Engine', '确证', 'appinfo'))
        self.assertEqual(row['route'], 'wine')
        self.assertTrue(row['manifest_note'])
        with patch.object(discovery, 'manifest_files', return_value=(files + ['EasyAntiCheat/launcher.exe'], 1)):
            refused = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam, deep=True)
        self.assertEqual(refused['final-routes'][0]['route'], 'refuse')

    def test_mac_native_game_is_catalogued_but_not_repack_candidate(self):
        snapshot = discovery.build_snapshot(receipt(), {42: metadata(platforms="windows,macos")}, {}, steam=self.steam)
        self.assertEqual(len(snapshot["catalog"]["apps"]), 1)
        self.assertEqual(snapshot["route-all"][0]["route"], "steam")
        self.assertEqual(snapshot["windows-library"], [])
        self.assertEqual(snapshot["final-routes"], [])

    def test_every_windows_depot_is_kept_and_multiple_have_no_legacy_default(self):
        app = metadata()
        app["depots"].update({
            "44": {"config": {"oslist": "windows", "language": "schinese"},
                   "manifests": {"public": {"gid": "9007199254740993", "download": 30}}},
            "45": {"config": {"oslist": "macos"}, "manifests": {"public": {"gid": "123"}}},
            "46": {"dlcappid": 50, "optional": 1, "config": {"oslist": "windows"}},
        })
        row = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)["owned-windows"][0]
        self.assertEqual([depot["depot"] for depot in row["depots"]], ["43", "44", "46"])
        self.assertEqual(row["depots"][0]["gid"], "18446744073709551615")
        self.assertEqual(row["depots"][1]["gid"], "9007199254740993")
        self.assertEqual(row["depots"][1]["language"], "schinese")
        self.assertTrue(row["depots"][2]["optional"])
        self.assertEqual(row["depots"][2]["metadata_status"], "public_manifest_missing")
        self.assertIsNone(row["depot"])
        self.assertIsNone(row["gid"])

    def test_single_depot_compatibility_and_unknown_risk(self):
        row = self.snapshot()["owned-windows"][0]
        self.assertEqual((row["depot"], row["gid"], row["dl"]), ("43", "18446744073709551615", 40))
        self.assertEqual(row["route"], "nwjs")
        snapshot = discovery.build_snapshot(receipt(), {42: metadata()}, {}, ac_error="AC database unavailable", steam=self.steam)
        self.assertEqual(snapshot["route-all"][0]["route"], "unknown")
        self.assertEqual(snapshot["route-all"][0]["risk"], risk.UNKNOWN)

    def test_depot_evidence_preserves_order_constraints_and_branch_precision(self):
        app = metadata()
        app["depots"] = {
            "90": {"depotfromapp": 70, "sharedinstall": 2, "systemdefined": 1,
                   "config": {"oslist": "windows", "osarch": 64, "language": "schinese", "realm": "steamchina",
                              "steamdeck": 1, "lowviolence": 1},
                   "manifests": {"public": {"gid": "18446744073709551615", "download": 40, "size": 80},
                                 "preview": {"gid": "9007199254740993", "size": 2**60, "download": 0}}},
            "44": {"config": {"oslist": "linux"}},
            "43": {"sharedinstall": "", "manifests": {"public": "99"}},
            "hasdepotsindlc": 1, "baselanguages": "english,schinese", "branches": {"public": {"buildid": 123}},
        }
        info = discovery.depot_metadata(app)
        self.assertEqual([row["depot"] for row in info["depots"]], ["90", "43"])
        self.assertEqual([row["mount_order"] for row in info["depots"]], [0, 2])
        row = info["depots"][0]
        self.assertEqual(row["sharedinstall"], 2)
        self.assertEqual(row["shared_from_app"], "70")
        self.assertEqual(row["config"], app["depots"]["90"]["config"])
        self.assertEqual(row["manifests"]["preview"]["gid"], "9007199254740993")
        self.assertEqual(row["manifests"]["preview"]["size"], str(2**60))
        self.assertEqual(info["depots"][1]["sharedinstall"], "")
        self.assertEqual(info["depot_properties"]["hasdepotsindlc"], 1)
        self.assertEqual(info["depot_metadata_issues"], [])

    def test_dlc_optional_and_absent_flags_remain_distinct(self):
        app = metadata()
        app["depots"]["43"].update(dlcappid=50, config={"optionaldlc": 51}, optional=1)
        row = discovery.windows_depots(app)[0]
        self.assertEqual((row["dlc_appid"], row["optional_dlc_appid"]), ("50", "51"))
        self.assertTrue(row["optional_present"])
        self.assertTrue(row["optional"])
        del app["depots"]["43"]["dlcappid"]
        del app["depots"]["43"]["optional"]
        snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, steam=self.steam)
        self.assertFalse(snapshot["windows-library"][0]["depots"][0]["optional_present"])
        self.assertIsNone(snapshot["windows-library"][0]["depot"])

    def test_malformed_depot_metadata_does_not_hide_game_or_silently_drop_constraints(self):
        app = metadata()
        app["depots"].update({"44bad": {"config": {"oslist": "windows"}},
                              "null": {"depotfromapp": 70}, "45": {"config": {"oslist": ["windows"]}, "optional": "maybe"}})
        snapshot = discovery.build_snapshot(receipt((), shared_appids=(42,)), {42: app}, {}, steam=self.steam)
        self.assertEqual(snapshot["catalog"]["games"], 1)
        row = snapshot["windows-library"][0]
        self.assertEqual(len(row["depot_metadata_issues"]), 2)
        self.assertEqual(row["depots"][1]["depot"], "45")
        self.assertTrue(row["depots"][1]["metadata_issues"])
        self.assertIsNone(row["depots"][1]["optional"])
        self.assertIsNone(row["depot"])

    def test_invalid_public_manifest_remains_explicit(self):
        app = metadata()
        app["depots"]["43"]["manifests"]["public"]["gid"] = True
        row = discovery.windows_depots(app)[0]
        self.assertIsNone(row["gid"])
        self.assertTrue(row["manifests"]["public"]["metadata_issues"])

    def test_selected_metadata_expands_shared_sources_without_claiming_license(self):
        host = metadata()
        host["depots"]["43"] = {"depotfromapp": 70, "sharedinstall": 2}
        source = metadata()
        source["depots"]["43"]["config"] = {"oslist": "windows", "language": "schinese", "osarch": 64}
        available = {42: host, 70: source}
        def selected(_path, *, appids):
            return {appid: available[appid] for appid in appids if appid in available}
        with patch.object(risk, "load_appinfo", side_effect=selected) as read, \
                patch.object(discovery, "manifest_files", side_effect=AssertionError("metadata must not scan files")):
            result = discovery.get_depot_metadata("42", steam=self.steam)
        self.assertEqual([call.kwargs["appids"] for call in read.call_args_list], [{42}, {70}])
        self.assertEqual(result["sharedApps"]["70"]["depots"][0]["config"]["language"], "schinese")
        self.assertFalse(result["license_evidence"])
        self.assertIsNone(result["depots"][0]["gid"])
        self.assertEqual(result["depot_metadata_issues"], [])
        self.assertFalse(self.games.exists())

    def test_shared_source_cycle_and_missing_records_are_visible_gaps(self):
        host = {"depots": {"43": {"depotfromapp": 70}}}
        source = {"depots": {"43": {"depotfromapp": 42}}}
        for available, word in (({42: host, 70: source}, "循环"), ({42: host}, "缺少")):
            with self.subTest(word=word), patch.object(risk, "load_appinfo", side_effect=lambda _path, appids: {a: available[a] for a in appids if a in available}):
                result = discovery.get_depot_metadata("42", steam=self.steam)
            self.assertTrue(any(word in issue for issue in result["depots"][0]["metadata_issues"]))

    def test_shared_sources_are_bounded_without_marking_partial_data_complete(self):
        with patch.object(risk, "load_appinfo", return_value={42: {"depots": {"43": {"depotfromapp": 70}}}}) as read:
            result = discovery.get_depot_metadata("42", steam=self.steam, max_apps=1)
        self.assertEqual(read.call_count, 1)
        self.assertTrue(result["depot_metadata_issues"])

    def test_language_preferences_only_read_current_account_and_keep_provenance(self):
        local = self.steam / "userdata/123/config/localconfig.vdf"
        local.parent.mkdir(parents=True)
        local.write_text('"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "42" { "Language" "schinese" "Secret" "not-returned" } } } } } }')
        (self.steam / "registry.vdf").write_text('"Registry" { "HKLM" { "Language" "french" } "HKCU" { "Software" { "Valve" { "Steam" { "language" "english" } } } } }')
        result = discovery.language_preferences("42", "123", steam=self.steam)
        self.assertEqual(result["current_app_language"], "schinese")
        self.assertEqual(result["steam_language"], "english")
        self.assertEqual(result["current_app_language_status"], "set")
        self.assertNotIn("not-returned", json.dumps(result))
        local.write_text('"UserLocalConfigStore" { "Software" {')
        failed = discovery.language_preferences("42", "123", steam=self.steam)
        self.assertIsNone(failed["current_app_language"])
        self.assertEqual(failed["current_app_language_status"], "unavailable")
        self.assertEqual(failed["steam_language"], "english")

    def test_depot_metadata_checks_account_again_after_reading(self):
        self.client.side_effect = [receipt(), receipt(account=456)]
        with patch.object(risk, "load_appinfo", return_value={42: metadata()}):
            with self.assertRaisesRegex(discovery.DiscoveryError, "账号发生切换"):
                discovery.get_depot_metadata("42", steam=self.steam)

    def test_depot_metadata_cli_is_read_only_and_cannot_publish(self):
        with patch.object(discovery, "get_depot_metadata", return_value={"protocol": 1, "appid": "42"}) as read, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(discovery.main(["--depot-metadata", "42"]), 0)
            self.assertEqual(json.loads(output.getvalue())["appid"], "42")
            read.assert_called_once_with("42")
        with patch.object(discovery, "get_depot_metadata", side_effect=AssertionError("must not read")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(discovery.main(["--depot-metadata", "42", "--write"]), 3)

    def test_cached_manifests_merge_depots_and_refusal_wins(self):
        app = metadata()
        app["depots"]["44"] = {"manifests": {"public": {"gid": "123"}}}
        cache = self.steam / "depotcache"
        cache.mkdir(parents=True)
        for depot, gid, name in (("43", "18446744073709551615", "nw.dll"), ("44", "123", "EasyAntiCheat/launcher.exe")):
            (cache / f"{depot}_{gid}.manifest.txt").write_text(f" 12 0 {'0'*40} 0 {name}\n")
        snapshot = discovery.build_snapshot(receipt(), {42: app}, {}, ac_error="no AC db", steam=self.steam, deep=True)
        final = snapshot["final-routes"][0]
        self.assertEqual(final["route"], "refuse")
        self.assertEqual(final["manifest_depots"], 2)
        self.assertTrue(final["manifest_complete"])

    def test_default_preserves_complete_library_without_opening_manifests(self):
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("default must not scan files")):
            snapshot = discovery.build_snapshot(receipt(shared_appids=(99,)),
                                                {42: metadata(), 99: metadata()}, {}, steam=self.steam)
        self.assertEqual(len(snapshot["windows-library"]), 2)
        self.assertEqual(snapshot["final-routes"], [])
        self.assertEqual(snapshot["catalog"]["manifest_scan"], "deferred")
        for row in snapshot["route-all"] + snapshot["windows-library"]:
            self.assertEqual(row["manifest_scan"], "deferred")
            self.assertFalse(row["manifest_complete"])
            self.assertEqual(row["source"], "appinfo")

    def test_unrecognized_resource_manifest_preserves_positive_metadata_hint(self):
        row = dict(appid="42", name="候选", route="unity", engine="Unity", risk=risk.REPACK,
                   confidence="推测", evidence="云存档线索", source="appinfo")
        windows = dict(appid="42", depots=[dict(depot="43", gid="99"), dict(depot="44", gid="100")])
        with patch.object(discovery, "manifest_files", return_value=(["languages/zh-cn.txt"], 1)):
            result = discovery.refine_route(row, windows, steam=self.steam)
        self.assertEqual((result["route"], result["source"], result["confidence"]), ("unity", "appinfo", "推测"))
        self.assertEqual(result["evidence"], "云存档线索")
        self.assertFalse(result["manifest_complete"])
        self.assertTrue(result["manifest_note"])

    def test_deep_evidence_survives_fast_refresh_and_risk_is_recomputed(self):
        from manifest_route_cache import ManifestRouteCache
        cache = ManifestRouteCache(self.steam, self.root.resolve() / "cache")
        path = self.steam / "depotcache/43_18446744073709551615.manifest.txt"
        path.parent.mkdir(parents=True)
        path.write_text(f"12 0 {'0'*40} 0 rgss301.dll\n")
        apps = {42: metadata()}
        deep = discovery.build_snapshot(receipt(), apps, {}, steam=self.steam, deep=True,
                                         route_cache=cache, cache_write=True)
        self.assertEqual(deep["final-routes"][0]["route"], "mkxp")
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("命中缓存无需重读清单")):
            fast = discovery.build_snapshot(receipt(), apps, {}, steam=self.steam, route_cache=cache)
            refused = discovery.build_snapshot(receipt(), apps, {42: {"anticheats": ["fixture AC"]}},
                                                 steam=self.steam, route_cache=cache)
        self.assertEqual(fast["final-routes"][0]["route"], "mkxp")
        self.assertEqual(fast["catalog"]["manifest_cached"], 1)
        self.assertEqual(refused["final-routes"][0]["route"], "refuse")
        path.write_text(f"12 0 {'0'*40} 0 nw.dll\n")
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("快速扫描不扫失效项")):
            changed = discovery.build_snapshot(receipt(), apps, {}, steam=self.steam, route_cache=cache)
        self.assertEqual(changed["final-routes"], [])
        self.assertEqual(changed["catalog"]["manifest_cached"], 0)

    def test_cache_write_rejects_source_repository_before_client_read(self):
        with patch.object(discovery, "SN_GAMES", ROOT / "private-cache-canary"), \
                patch.object(discovery, "read_client", side_effect=AssertionError("不应开始发现")):
            with self.assertRaisesRegex(discovery.DiscoveryError, "源码仓库"):
                discovery.discover(deep=True, cache_write=True)
        self.assertFalse((ROOT / "private-cache-canary").exists())

    def test_unreadable_manifest_keeps_all_games_and_other_classifications(self):
        with patch.object(discovery, "manifest_files", side_effect=[PermissionError("fixture denied"), (["rgss301.dll"], 1)]):
            result = discovery.build_snapshot(receipt(shared_appids=(99,)), {42: metadata(), 99: metadata()},
                                               {}, steam=self.steam, deep=True)
        rows = {row["appid"]: row for row in result["route-all"]}
        self.assertEqual(set(rows), {"42", "99"})
        self.assertEqual(rows["42"]["manifest_scan"], "unavailable")
        self.assertFalse(rows["42"]["manifest_complete"])
        self.assertEqual(rows["42"]["route"], "nwjs")
        self.assertIn("fixture denied", rows["42"]["manifest_note"])
        self.assertEqual(result["final-routes"][0]["route"], "mkxp")
        self.assertEqual(result["catalog"]["manifest_unavailable"], 1)
        self.assertTrue(result["catalog"]["warnings"])

    def test_single_game_refinement_matches_deep_scan_without_client_or_appinfo(self):
        cache = self.steam / "depotcache"
        cache.mkdir(parents=True)
        (cache / "43_18446744073709551615.manifest.txt").write_text(f" 12 0 {'0'*40} 0 rgss301.dll\n")
        initial = self.snapshot()
        with patch.object(discovery, "read_client", side_effect=AssertionError("no CDP")), \
                patch.object(risk, "load_appinfo", side_effect=AssertionError("no full appinfo")):
            refined = discovery.refine_route(initial["route-all"][0], initial["windows-library"][0], steam=self.steam)
        deep = discovery.build_snapshot(receipt(), {42: metadata()}, {}, steam=self.steam, deep=True)
        self.assertEqual(refined, deep["final-routes"][0])
        self.assertEqual(refined["route"], "mkxp")
        self.assertEqual(refined["source"], "manifest")
        self.assertTrue(refined["manifest_complete"])
        self.assertEqual(deep["catalog"]["manifest_scan"], "complete")
        self.assertEqual(initial["route-all"][0]["route"], "nwjs")
        self.assertEqual(initial["route-all"][0]["manifest_scan"], "deferred")

    def test_refinement_preserves_risk_and_does_not_invent_missing_manifest(self):
        snapshot = self.snapshot()
        row, windows = snapshot["route-all"][0], snapshot["windows-library"][0]
        absent = discovery.refine_route(row, windows, steam=self.steam)
        self.assertFalse(absent["manifest_complete"])
        self.assertEqual(absent["manifest_depots"], 0)
        self.assertEqual(absent["source"], "appinfo")
        self.assertEqual(absent["route"], row["route"])
        for verdict, selected in ((risk.REFUSE, "refuse"), (risk.UNKNOWN, "unknown"),
                                  (risk.WINE_ONLY, "wine"), ("invalid", "unknown")):
            with self.subTest(verdict=verdict), patch.object(discovery, "manifest_files", return_value=(["nw.dll"], 1)):
                result = discovery.refine_route({**row, "risk": verdict}, windows, steam=self.steam)
                self.assertEqual(result["route"], selected)
        with patch.object(discovery, "manifest_files", return_value=(["nw.dll"], 1)):
            self.assertEqual(discovery.refine_route({**row, "route": "refuse"}, windows, steam=self.steam)["route"], "refuse")

    def test_refinement_checks_appid_and_depot_paths_before_reading(self):
        snapshot = self.snapshot()
        row, windows = snapshot["route-all"][0], snapshot["windows-library"][0]
        invalid = ({**windows, "appid": "99"}, {**windows, "depots": [{"depot": "../43", "gid": "1"}]},
                   {**windows, "depots": [{"depot": "43", "gid": "../1"}]})
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("invalid path read")):
            for candidate in invalid:
                with self.subTest(candidate=candidate), self.assertRaises(discovery.DiscoveryError):
                    discovery.refine_route(row, candidate, steam=self.steam)

    def test_cli_deep_flag_reaches_discovery_and_default_is_deferred(self):
        for flags, deep in (([], False), (["--deep"], True)):
            snapshot = discovery.build_snapshot(receipt(), {42: metadata()}, {}, steam=self.steam, deep=deep)
            with self.subTest(flags=flags), patch.object(discovery, "discover", return_value=snapshot) as discover, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(discovery.main(flags), 0)
                discover.assert_called_once_with(deep=deep, cache_write=False)
                self.assertEqual(json.loads(output.getvalue())["manifest_scan"], "complete" if deep else "deferred")

    def test_account_switch_during_discovery_stops_before_publish(self):
        self.client.side_effect = [receipt(), receipt(account=456)]
        with patch.object(risk, "load_appinfo", return_value={42: metadata()}), patch.object(risk, "load_ac", return_value={}):
            with self.assertRaisesRegex(discovery.DiscoveryError, "账号发生切换"):
                discovery.discover()

    def test_discovery_decodes_only_current_accessible_appids(self):
        self.client.return_value = receipt(shared_appids=(99,))
        with patch.object(risk, "load_appinfo", return_value={42: metadata(), 99: metadata()}) as load, \
                patch.object(risk, "load_ac", return_value={}):
            snapshot = discovery.discover()
        load.assert_called_once_with(appids={42, 99})
        self.assertEqual({row["appid"] for row in snapshot["route-all"]}, {"42", "99"})
        self.assertFalse(self.games.exists())

    def test_broken_appinfo_keeps_client_games_without_inventing_routes(self):
        for failure in (OSError("missing cache"), ValueError("truncated cache")):
            with self.subTest(failure=failure), patch.object(risk, "load_appinfo", side_effect=failure), \
                    patch.object(risk, "load_ac", return_value={}):
                snapshot = discovery.discover()
                self.assertEqual(snapshot["catalog"]["games"], 1)
                self.assertEqual(snapshot["route-all"][0]["route"], "unknown")
                self.assertEqual(snapshot["route-all"][0]["risk"], risk.UNKNOWN)
                self.assertEqual(snapshot["windows-library"], [])
                self.assertTrue(snapshot["catalog"]["warnings"])
        self.assertFalse(self.games.exists())

    def test_all_subscribed_games_remain_visible_regardless_of_platform_or_metadata(self):
        data = receipt((42,), shared_appids=(99, 100, 101))
        apps = {42: metadata(), 99: metadata("mac game", "windows,macos"), 100: metadata("linux game", "linux")}
        snapshot = discovery.build_snapshot(data, apps, {99: {"anticheats": ["fixture AC"]}}, steam=self.steam, deep=True)
        self.assertEqual([row["appid"] for row in snapshot["route-all"]], ["42", "99", "100", "101"])
        self.assertEqual([row["route"] for row in snapshot["route-all"]], ["nwjs", "steam", "unknown", "unknown"])
        self.assertEqual(snapshot["route-all"][1]["risk"], risk.REFUSE)
        self.assertEqual([row["appid"] for row in snapshot["windows-library"]], ["42"])
        self.assertEqual(snapshot["catalog"]["games"], 4)
        self.assertEqual(snapshot["catalog"]["games_owned_count"], 1)
        self.assertEqual(snapshot["catalog"]["games_shared_count"], 3)

    def test_refine_keeps_official_mac_route_and_can_read_legacy_single_depot(self):
        snapshot = self.snapshot()
        row, windows = snapshot["route-all"][0], snapshot["windows-library"][0]
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("must not repackage macOS")):
            result = discovery.refine_route({**row, "route": "steam"}, windows, steam=self.steam)
            self.assertEqual(result["route"], "steam")
        legacy = {key: value for key, value in windows.items() if key != "depots"}
        with patch.object(discovery, "manifest_files", return_value=(["nw.dll"], 1)) as files:
            result = discovery.refine_route(row, legacy, steam=self.steam)
            self.assertEqual(result["route"], "nwjs")
            self.assertTrue(result["manifest_complete"])
            files.assert_called_once_with([{"depot": "43", "gid": "18446744073709551615"}], self.steam)

    def test_fresh_publish_preserves_legacy_files_and_previous_generation(self):
        self.games.mkdir()
        legacy = self.games / "owned-windows.json"
        legacy.write_bytes(b"legacy personal bytes")
        first = discovery.publish(self.snapshot(), self.games)
        second = discovery.publish(self.snapshot(), self.games)
        self.assertNotEqual(first, second)
        self.assertEqual(discovery.current_catalog_dir(self.games), second)
        self.assertTrue((first / "catalog.json").is_file())
        self.assertEqual(legacy.read_bytes(), b"legacy personal bytes")
        self.assertEqual((second / "catalog.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(second.stat().st_mode & 0o777, 0o700)

    def test_failed_pointer_switch_keeps_complete_previous_snapshot(self):
        first = discovery.publish(self.snapshot(), self.games)
        with patch.object(discovery.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                discovery.publish(self.snapshot(), self.games)
        self.assertEqual(discovery.current_catalog_dir(self.games), first)
        self.assertTrue(all((first / f"{table}.json").exists() for table in discovery.TABLES))

    def test_account_switch_after_staging_never_replaces_current(self):
        first = discovery.publish(self.snapshot(), self.games)
        self.client.side_effect = [receipt(), receipt(account=456)]
        with self.assertRaisesRegex(discovery.DiscoveryError, "写入期间"):
            discovery.publish(self.snapshot(), self.games)
        self.assertEqual(discovery.current_catalog_dir(self.games), first)

    def test_account_mismatch_or_corrupt_catalog_never_falls_back(self):
        directory = discovery.publish(self.snapshot(), self.games)
        with patch.object(discovery, "current_account_id", return_value="456"):
            with self.assertRaisesRegex(discovery.DiscoveryError, "账号已切换"):
                discovery.current_catalog_dir(self.games)
        (directory / "catalog.json").write_text("corrupt")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.current_catalog_dir(self.games)

    def test_corrupt_route_table_is_a_discovery_error(self):
        directory = discovery.publish(self.snapshot(), self.games)
        (directory / "route-all.json").write_text("broken")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.current_catalog_dir(self.games)

    def test_protocol_two_requires_fifth_table_and_refresh_can_repair_it(self):
        directory = discovery.publish(self.snapshot(), self.games)
        (directory / "windows-library.json").unlink()
        with self.assertRaises(discovery.DiscoveryError):
            discovery.current_catalog_dir(self.games)
        refreshed = discovery.publish(self.snapshot(), self.games)
        self.assertEqual(discovery.current_catalog_dir(self.games), refreshed)
        self.assertTrue(directory.exists())

    def test_protocol_one_four_table_generation_stays_readable(self):
        snapshot = self.snapshot()
        snapshot["catalog"]["protocol"] = 1
        del snapshot["windows-library"]
        directory = discovery.publish(snapshot, self.games)
        self.assertEqual(discovery.current_catalog_dir(self.games), directory)
        self.assertFalse((directory / "windows-library.json").exists())

    def test_symlink_escape_and_repository_personal_data_are_refused(self):
        self.games.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.games / discovery.CATALOG_DIR).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(discovery.DiscoveryError):
            discovery.publish(self.snapshot(), self.games)
        self.assertEqual(list(outside.iterdir()), [])
        with self.assertRaisesRegex(discovery.DiscoveryError, "源码仓库"):
            discovery.publish(self.snapshot(), ROOT / "personal-data-canary")
        self.assertFalse((ROOT / "personal-data-canary").exists())

    def test_path_cli_and_human_failures_have_no_json_payload(self):
        with patch.object(discovery, "current_catalog_dir", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(discovery.main(["--path"]), 1)
            self.assertEqual(out.getvalue(), "")
        with patch.object(discovery, "discover", side_effect=discovery.DiscoveryError("fixture failed")):
            out, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
                self.assertEqual(discovery.main(["--write", "--human"]), 3)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("fixture failed", error.getvalue())


class CollectorExpressionTests(unittest.TestCase):
    def test_collector_includes_shared_but_excludes_shortcuts_and_unsubscribed_without_rounding_id(self):
        script = """
import {libraryExpression} from './lib/steam-library.mjs';
const id = {BIsIndividualAccount:()=>true,BIsValid:()=>true,ConvertTo64BitString:()=> '76561202255233023',GetAccountID:()=>4294967295};
const app=(appid,owner,subscribed=true,shortcut=false)=>({appid,owner_account_id:owner,subscribed_to:subscribed,display_name:'fixture',app_type:1,BIsBorrowed:()=>!!owner,BIsShortcut:()=>shortcut});
globalThis.appStore={m_bIsInitialized:true,m_cm:{steamid:id,logged_on:true},allApps:[app(42),app(43,999),app(44,0,false),app(45,0,true,true)]};
console.log(JSON.stringify(eval(libraryExpression)));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["account"]["steamid64"], "76561202255233023")
        self.assertEqual([app["appid"] for app in data["apps"]], ["42", "43"])
        self.assertEqual(data["apps"][1]["license_source"], "family_shared")
        self.assertEqual(data["apps"][1]["owner_account_id"], "999")
        self.assertEqual(data["apps"][0]["owner_account_id"], "0")
        self.assertEqual(data["excluded"], {"shortcuts": 1, "unsubscribed": 1})
        self.assertEqual((data["owned_count"], data["shared_count"]), (1, 1))

    def test_live_license_uses_native_details_and_unregisters_for_every_result(self):
        script = """
import {licenseExpression} from './lib/steam-library.mjs';
const mode=process.argv[1], base=76561197960265728n;
class ID {
  constructor(value){this.value=String(value)}
  BIsIndividualAccount(){return true}
  BIsValid(){return BigInt(this.value)>base}
  ConvertTo64BitString(){return this.value}
  GetAccountID(){return Number(BigInt(this.value)-base)}
}
const id=new ID(base+123n), owner=mode==='owned'?0:999;
let unregistered=0, registered=0, account=id;
const app={owner_account_id:owner};
globalThis.appStore={m_bIsInitialized:true,m_cm:{get steamid(){return account},logged_on:true},GetAppOverviewByAppID:()=>app};
const detail={unAppID:42,bIsSubscribedTo:mode!=='not_subscribed',bIsExcludedFromSharing:mode==='sharing_excluded',
 strOwnerSteamID:String(base+BigInt(owner||123)),strLockedBySteamID:String(base+(mode==='in_use'?456n:0n)),
 unMemberCopies:1,unMembersPlaying:mode==='in_use'?1:0};
globalThis.SteamClient={Apps:{
 RegisterForAppDetails:(appid,cb)=>{registered++;queueMicrotask(()=>cb(detail));return {unregister:()=>unregistered++}},
 GetIsSubscribedApp:async()=>{if(mode==='account_switch')account=new ID(base+456n);return mode!=='not_subscribed'}},
 FamilySharing:{GetAvailableLenders:async()=>mode==='unknown'?[]:[{steamid:detail.strOwnerSteamID}]}};
try{console.log(JSON.stringify({value:await eval(licenseExpression('00042')),registered,unregistered}))}
catch(error){console.log(JSON.stringify({error:error.message,registered,unregistered}))}
"""
        for mode in ("available", "owned", "not_subscribed", "sharing_excluded", "in_use", "unknown", "account_switch"):
            with self.subTest(mode=mode):
                result = subprocess.run(["node", "--input-type=module", "-e", script, mode], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual((data["registered"], data["unregistered"]), (1, 1))
                if mode == "account_switch":
                    self.assertIn("账号或许可来源已变化", data["error"])
                else:
                    expected = "available" if mode == "owned" else mode
                    self.assertEqual(data["value"]["status"], expected)
                    self.assertEqual(data["value"]["allowed"], expected == "available")
                    self.assertEqual(data["value"]["license_source"], "self" if mode == "owned" else "family_shared")


if __name__ == "__main__":
    unittest.main()
