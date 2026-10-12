# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只读清单映射必须同时满足 profile、完整 depot、当前风险与平台。"""
import contextlib
import copy
import importlib
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
from manifest_route_cache import ManifestRouteCache
import reclassify
import risk
import route
import uninstall_purge

INSPECT = importlib.import_module("webkit-inspect")
MAPPING = importlib.import_module("webkit-route")
APPID = "1518810"
PROFILE = INSPECT.PROFILES[APPID]
FILES = ["resources.pak", "icudtl.dat", "v8_context_snapshot.bin", "resources/app/package.json"]


class WebKitRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-webkit-route-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.steam = self.base / "steam"
        self.games = self.base / "games"
        self.row = dict(appid=APPID, name="owned fixture", route="unknown", engine="未识别",
                        confidence="无", source="appinfo", risk=risk.REPACK,
                        oslist=["windows", "linux"], metadata_status="available")
        self.windows = dict(appid=APPID, oslist=["windows", "linux"], metadata_status="available",
                            depots=[dict(depot=PROFILE["depot"], gid=PROFILE["manifest"])])
        self.manifest = self.steam / "depotcache" / (PROFILE["depot"] + "_" + PROFILE["manifest"] + ".manifest.txt")
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text("".join("12 0 " + "a" * 40 + " 0 " + name + "\n" for name in FILES))

    def refine(self, row=None, windows=None, **kwargs):
        return discovery.refine_route(row or self.row, windows or self.windows, steam=self.steam, **kwargs)

    def test_exact_profile_selects_install_verification_without_reading_resources(self):
        before = self.manifest.read_bytes(), self.manifest.stat().st_mtime_ns
        with patch.object(INSPECT, "inspect", side_effect=AssertionError("目录不读真实资源")), \
                patch.object(discovery, "read_client", side_effect=AssertionError("目录不联系 Steam")):
            result = self.refine()
        self.assertEqual(result["route"], "webkit")
        self.assertEqual(result["engine"], "Electron 候选")
        self.assertEqual(result["route_name"], "WebKit 原生（安装时校验版本）")
        self.assertIn("安装时仍须校验完整原资源 SHA-256", result["evidence"])
        self.assertEqual((self.manifest.read_bytes(), self.manifest.stat().st_mtime_ns), before)
        self.assertFalse(self.games.exists())
        self.assertEqual(self.row["route"], "unknown")

    def test_wrong_appid_depot_gid_and_extra_depots_remain_unpromoted(self):
        cases = []
        wrong_id = {**self.windows, "appid": "99"}
        cases.append(({**self.row, "appid": "99"}, wrong_id))
        for field in ("depot", "gid"):
            wrong = copy.deepcopy(self.windows)
            wrong["depots"][0][field] = "99"
            cases.append((self.row, wrong))
        extra = copy.deepcopy(self.windows)
        extra["depots"].append(dict(depot="99", gid="100"))
        cases.append((self.row, extra))
        external = copy.deepcopy(self.windows)
        external["depots"].append(dict(depot="99", gid=None, shared_from_app="228980"))
        cases.append((self.row, external))
        with patch.object(discovery, "manifest_files", return_value=(FILES, 1)):
            for row, windows in cases:
                with self.subTest(windows=windows):
                    self.assertNotEqual(self.refine(row, windows)["route"], "webkit")
        with patch.object(discovery, "manifest_files", return_value=(FILES, 2)):
            self.assertNotEqual(self.refine(windows=extra)["route"], "webkit")
        # 内容分母虽可完整，现有单 depot profile 不自动扩展到带外置依赖的新包。
        result = self.refine(windows=external)
        self.assertTrue(result["manifest_complete"])
        self.assertEqual(result["external_dependency_depots"], 1)
        self.assertNotEqual(result["route"], "webkit")

    def test_missing_partial_or_ambiguous_metadata_cannot_select_profile(self):
        with patch.object(discovery, "manifest_files", return_value=(FILES, 0)):
            self.assertNotEqual(self.refine()["route"], "webkit")
        for field, value in (("metadata_issues", ["unknown"]), ("optional", True),
                             ("dlc_appid", "123"), ("optional_dlc_appid", "123")):
            windows = copy.deepcopy(self.windows)
            windows["depots"][0][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.refine(windows=windows)["route"], "webkit")
        self.assertNotEqual(self.refine(windows={**self.windows, "depot_metadata_issues": ["pending"]})["route"], "webkit")

    def test_current_risk_refusal_and_non_electron_evidence_take_precedence(self):
        for value, expected in ((risk.REFUSE, "refuse"), (risk.WINE_ONLY, "wine"),
                                (risk.UNKNOWN, "unknown"), ("invalid", "unknown")):
            with self.subTest(value=value):
                self.assertEqual(self.refine(row={**self.row, "risk": value})["route"], expected)
        self.assertEqual(self.refine(row={**self.row, "route": "refuse"})["route"], "refuse")
        for files, expected in ((FILES + ["EasyAntiCheat/launcher.exe"], "refuse"),
                                (["UnityPlayer.dll"], "unity"), (["language.txt"], "unknown")):
            with patch.object(discovery, "manifest_files", return_value=(files, 1)):
                self.assertEqual(self.refine()["route"], expected)

    def test_windows_platform_is_required_on_both_rows_without_notes(self):
        for field, value in (("oslist", []), ("oslist", None), ("oslist", ["windows", None]),
                             ("oslist", ["linux"]), ("oslist", ["windows", "macos"]),
                             ("oslist", "windows"), ("metadata_status", "missing"),
                             ("platform_note", "平台待确认")):
            for side in ("row", "windows"):
                with self.subTest(side=side, field=field, value=value):
                    kwargs = {side: {**getattr(self, side), field: value}}
                    self.assertNotEqual(self.refine(**kwargs)["route"], "webkit")
        for side in ("row", "windows"):
            missing = {k: v for k, v in getattr(self, side).items() if k != "oslist"}
            self.assertNotEqual(self.refine(**{side: missing})["route"], "webkit")

    def test_cache_keeps_electron_classification_and_reapplies_profiles_and_risk(self):
        cache = ManifestRouteCache(self.steam, self.base / "cache")
        first = self.refine(route_cache=cache, cache_write=True)
        self.assertEqual(first["route"], "webkit")
        stored = cache.lookup(self.windows["depots"])
        self.assertEqual(stored["classification"][:2], ("Electron 候选", "maybe"))
        with patch.object(discovery, "manifest_files", side_effect=AssertionError("cache only")):
            fast = self.refine(route_cache=cache, cache_only=True)
            self.assertEqual(fast["route"], "webkit")
            refused = self.refine(row={**self.row, "risk": risk.REFUSE}, route_cache=cache, cache_only=True)
            self.assertEqual(refused["route"], "refuse")
            with patch.object(INSPECT, "PROFILES", {}):
                self.assertEqual(self.refine(route_cache=cache, cache_only=True)["route"], "maybe")

    def test_owned_and_shared_snapshots_select_identically(self):
        app = {"common": {"name": "fixture", "type": "Game", "oslist": "windows,linux",
                          "category": {"category_2": "1"}},
               "depots": {PROFILE["depot"]: {"manifests": {"public": {"gid": PROFILE["manifest"], "download": "12"}}}}}
        for borrowed in (False, True):
            receipt = {"protocol": 2, "account": {"account_id": "123", "steamid64": str(discovery.STEAM_ID_BASE + 123)},
                       "apps": [{"appid": APPID, "name": "fixture", "app_type": 1, "subscribed": True,
                                 "borrowed": borrowed, "owner_account_id": "456" if borrowed else "0",
                                 "ownership": "borrowed" if borrowed else "owned",
                                 "license_source": "family_shared" if borrowed else "self"}]}
            result = discovery.build_snapshot(receipt, {int(APPID): app}, {}, steam=self.steam, deep=True)
            self.assertEqual(result["final-routes"][0]["route"], "webkit")
            self.assertEqual(result["final-routes"][0]["ownership"], "borrowed" if borrowed else "owned")
            self.assertEqual(len(result["windows-library"]), 1)
            self.assertEqual(len(result["owned-windows"]), int(not borrowed))

    def test_api_snapshot_and_detail_share_label_and_native_route_without_writes(self):
        final = self.refine()
        self.games.mkdir()
        for name, data in (("route-all.json", [self.row]), ("final-routes.json", [final]),
                           ("owned-windows.json", [self.windows])):
            (self.games / name).write_text(json.dumps(data))
        before = {p.name: p.read_bytes() for p in self.games.iterdir()}
        gate = type("Gate", (), {"returncode": 0, "stdout": "通过", "stderr": ""})()
        def capture(call, *args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                call(*args)
            return json.loads(out.getvalue())
        with patch.object(api, "GAMES", self.games), patch.object(api, "REGISTRY", self.games / "installed.json"), \
                patch.object(api, "STEAM", self.steam), patch.object(api, "RUNTIMES", self.base / "runtimes"), \
                patch.object(api, "steam_running", return_value=False), \
                patch.object(api, "debug_port_enabled", return_value=False), \
                patch.object(api, "SMOKE_DIR", self.games / ".smoke"), \
                patch.object(uninstall_purge, "SN_GAMES", self.games):
            snapshot = capture(api.cmd_snapshot)
            with patch.object(api.subprocess, "run", return_value=gate):
                detail = capture(api.cmd_detail, APPID)
        row = snapshot["library"]["rows"][0]
        for key in ("route", "route_label", "native"):
            self.assertEqual(row[key], detail[key])
        self.assertTrue(row["native"])
        self.assertEqual(snapshot["state"]["route_counts"]["webkit"], 1)
        self.assertEqual({p.name: p.read_bytes() for p in self.games.iterdir()}, before)
        self.assertEqual(api.ROUTE_LABEL["webkit"], route.ROUTE_NAME["webkit"])
        self.assertEqual(reclassify.ROUTE_NAME["webkit"], MAPPING.LABEL)


if __name__ == "__main__":
    unittest.main()
