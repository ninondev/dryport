# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自有元资料与文件名清单验证内容/公共依赖分母；不访问真实 Steam。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import library_discovery as discovery
from manifest_route_cache import ManifestRouteCache
import risk


class ContentScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-content-scope-")
        self.addCleanup(self.tmp.cleanup)
        # macOS 的 /var 是软链；缓存按约定拒绝祖先软链，样例使用真实目录。
        self.root = Path(self.tmp.name).resolve()
        self.steam = self.root / "steam"
        (self.steam / "depotcache").mkdir(parents=True)
        self.row = dict(appid="42", name="自有样例", route="unknown", engine="未识别",
                        risk=risk.REPACK, confidence="无", source="appinfo", oslist=["windows"])
        self.core = dict(depot="43", gid="99", mount_order=0)
        self.redist = dict(depot="44", gid=None, mount_order=1, shared_from_app="228980")
        self.windows = dict(appid="42", depots=[self.core, self.redist])
        self.write_manifest("43", "99", ["rgss301.dll"])

    def write_manifest(self, depot, gid, files):
        # 此处仅测试文件名分类；Steam 交付的全清单校验另由 depot-delivery 验证。
        (self.steam / f"depotcache/{depot}_{gid}.manifest.txt").write_text(
            "".join(f"12 0 {'0' * 40} 0 {name}\n" for name in files))

    def refine(self, windows=None, row=None, **kwargs):
        return discovery.refine_route(row or self.row, windows or self.windows, steam=self.steam, **kwargs)

    def test_common_missing_gid_is_separate_and_input_is_unchanged(self):
        before = copy.deepcopy(self.windows)
        r = self.refine()
        self.assertEqual(self.windows, before)
        self.assertEqual((r["manifest_depots"], r["total_depots"], r["listed_depots"]), (1, 1, 2))
        self.assertEqual(r["manifest_scope"], "listed_content_depots")
        self.assertTrue(r["manifest_complete"])
        self.assertEqual(r["external_dependency_depots"], 1)
        self.assertEqual(r["external_dependencies"][0]["status"], "not_checked")
        self.assertEqual(r["route"], "mkxp")

    def test_empty_content_is_never_complete(self):
        for depots in ([], [self.redist]):
            with self.subTest(depots=depots):
                r = self.refine(dict(appid="42", depots=depots))
                self.assertFalse(r["manifest_complete"])
                self.assertEqual(r["total_depots"], 0)
                self.assertEqual(r["route"], "unknown")

    def test_other_missing_content_shared_dlc_or_number_is_not_excluded(self):
        for change in ({"shared_from_app": "999"}, {"shared_from_app": ""},
                       {"shared_from_app": None, "dlc_appid": "90"},
                       {"shared_from_app": "", "depot": "228980"},
                       {"shared_from_app": "228980-invalid"}):
            with self.subTest(change=change):
                w = copy.deepcopy(self.windows)
                w["depots"][1].update(change)
                r = self.refine(w)
                self.assertEqual(r["external_dependency_depots"], 0)
                self.assertEqual(r["total_depots"], 2)
                self.assertFalse(r["manifest_complete"])

    def test_redist_files_cannot_supply_game_engine_or_anticheat(self):
        self.write_manifest("43", "99", ["content.bin"])
        self.write_manifest("44", "100", ["rgss301.dll", "EasyAntiCheat/launcher.exe"])
        self.redist["gid"] = "100"
        r = self.refine()
        self.assertEqual((r["route"], r["files"]), ("unknown", 1))
        self.redist["shared_from_app"] = "999"
        self.assertEqual(self.refine()["route"], "refuse")

    def test_risk_and_platform_are_not_granted_by_complete_content(self):
        for verdict, expected in ((risk.UNKNOWN, "unknown"), (risk.REFUSE, "refuse"),
                                  (risk.WINE_ONLY, "wine")):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.refine(row={**self.row, "risk": verdict})["route"], expected)
        self.assertEqual(self.refine(row={**self.row, "oslist": []})["route"], "unknown")

    def test_metadata_errors_still_prevent_complete_even_from_cache(self):
        cache = ManifestRouteCache(self.steam, self.root / "cache")
        self.refine(route_cache=cache, cache_write=True)
        for where in ("row", "core", "redist", "public"):
            with self.subTest(where=where):
                w = copy.deepcopy(self.windows)
                if where == "row": w["depot_metadata_issues"] = ["invalid condition"]
                elif where == "public": w["depots"][0]["manifests"] = {"public": {"metadata_issues": ["bad size"]}}
                else: w["depots"][0 if where == "core" else 1]["metadata_issues"] = ["bad field"]
                with patch.object(discovery, "manifest_files", side_effect=AssertionError("valid cache expected")):
                    r = self.refine(w, route_cache=cache)
                self.assertEqual(r["manifest_scan"], "cached")
                self.assertFalse(r["manifest_metadata_valid"])
                self.assertFalse(r["manifest_complete"])

    def test_cache_uses_content_and_recomputes_dependency_metadata(self):
        cache = ManifestRouteCache(self.steam, self.root / "cache")
        self.refine(route_cache=cache, cache_write=True)
        self.assertIsNotNone(cache.lookup([{"depot": "43", "gid": "99"}]))
        self.assertIsNone(cache.lookup([{"depot": "43", "gid": "99"}, {"depot": "44", "gid": None}]))
        self.redist["gid"] = "101"
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("redist change must not reread content")):
            r = self.refine(route_cache=cache)
        self.assertTrue(r["manifest_complete"])
        self.assertEqual(r["external_dependencies"][0]["gid"], "101")
        self.redist["shared_from_app"] = "999"
        r = self.refine(route_cache=cache, cache_only=True)
        self.assertEqual(r["manifest_scan"], "deferred")
        self.assertFalse(r["manifest_complete"])
        self.assertEqual((r["total_depots"], r["external_dependency_depots"]), (2, 0))

    def test_falsey_malformed_issue_fields_cannot_confirm_content(self):
        cache = ManifestRouteCache(self.steam, self.root / "cache")
        self.refine(route_cache=cache, cache_write=True)
        for value in ({}, "", False, 0, (), ["invalid"]):
            for where in ("route", "row", "core", "redist", "public"):
                with self.subTest(value=value, where=where):
                    w, row = copy.deepcopy(self.windows), copy.deepcopy(self.row)
                    if where == "route": row["depot_metadata_issues"] = value
                    elif where == "row": w["depot_metadata_issues"] = value
                    elif where == "public": w["depots"][0]["manifests"] = {"public": {"metadata_issues": value}}
                    else: w["depots"][0 if where == "core" else 1]["metadata_issues"] = value
                    with patch.object(discovery, "manifest_files", side_effect=AssertionError("valid cache expected")):
                        r = self.refine(w, row, route_cache=cache)
                    self.assertEqual(r["manifest_scan"], "cached")
                    self.assertFalse(r["manifest_metadata_valid"])
                    self.assertFalse(r["manifest_complete"])

    def test_deferred_and_unavailable_keep_the_new_scope(self):
        r = self.refine(cache_only=True)
        self.assertEqual((r["listed_depots"], r["total_depots"], r["external_dependency_depots"]), (2, 1, 1))
        with patch.object(discovery, "manifest_files", side_effect=OSError("fixture denied")):
            r = self.refine()
        self.assertEqual(r["manifest_scan"], "unavailable")
        self.assertEqual((r["listed_depots"], r["total_depots"]), (2, 1))

    def test_duplicate_and_invalid_depots_are_not_silently_ignored(self):
        for depots in ([self.core, self.core], [self.core, {**self.redist, "depot": "../44"}]):
            with self.subTest(depots=depots), self.assertRaises((discovery.DiscoveryError, ValueError)):
                self.refine(dict(appid="42", depots=depots))

    def test_snapshot_propagates_scope_when_engine_remains_unknown(self):
        self.write_manifest("43", "99", ["content.bin"])
        receipt = {"protocol": 2, "account": {"steamid64": str(discovery.STEAM_ID_BASE + 123), "account_id": "123"},
                   "apps": [dict(appid="42", name="fixture", app_type=1, subscribed=True, borrowed=False,
                                 owner_account_id="0", ownership="owned", license_source="self")]}
        app = {"common": {"name": "fixture", "type": "Game", "oslist": "windows", "category": {"category_2": "1"}},
               "depots": {"43": {"manifests": {"public": {"gid": "99"}}}, "44": {"depotfromapp": "228980"}}}
        result = discovery.build_snapshot(receipt, {42: app}, {}, steam=self.steam, deep=True)
        for name in ("route-all", "windows-library"):
            row = result[name][0]
            self.assertEqual(row["route"], "unknown")
            self.assertEqual((row["listed_depots"], row["total_depots"], row["external_dependency_depots"]), (2, 1, 1))
            self.assertTrue(row["manifest_complete"])
        self.assertEqual(len(result["windows-library"][0]["depots"]), 2)

    def test_python_scope_agrees_with_planner_but_cannot_override_install_conditions(self):
        script = """import fs from 'node:fs'; import {planDepots} from './lib/depot-plan.mjs';
const row=JSON.parse(fs.readFileSync(0,'utf8'));
try { console.log(JSON.stringify({ok:true,plan:planDepots(row)})); }
catch(error) { console.log(JSON.stringify({ok:false,error:error.message})); }"""
        def plan(row):
            p = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(row),
                               text=True, capture_output=True, cwd=ROOT, check=True, timeout=10)
            return json.loads(p.stdout)
        positive = plan(self.windows)
        self.assertTrue(positive["ok"])
        checked, scope = discovery.manifest_content_scope(self.windows)
        self.assertEqual([p["depot"] for p in checked], [p["depot"] for p in positive["plan"]["selected"]])
        self.assertEqual([p["depot"] for p in scope["external_dependencies"]],
                         [p["depot"] for p in positive["plan"]["external_dependencies"]])
        self.core["systemdefined"] = 1
        self.assertTrue(self.refine()["manifest_complete"])
        blocked = plan(self.windows)
        self.assertFalse(blocked["ok"])
        self.assertIn("系统挂载条件", blocked["error"])


if __name__ == "__main__":
    unittest.main()
