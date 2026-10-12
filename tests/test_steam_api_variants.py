# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""steam_api64.dll 变体普查：只读清单文本算 SHA-1 分布，与已知版本串比对得覆盖下限；全部用临时目录。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("variants", ROOT / "tools/steam-api-variants.py")
variants = importlib.util.module_from_spec(spec); spec.loader.exec_module(variants)


def manifest(lines):
    return "\n".join(f"  {size:,}  1  {sha}  0  {name}" for size, sha, name in lines) + "\n"


class VariantsTests(unittest.TestCase):
    def test_census_groups_games_by_dll_sha_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); catalog = base / "catalog"; dc = base / "depotcache"; catalog.mkdir(); dc.mkdir()
            sha_a = "a" * 40; sha_b = "b" * 40
            (catalog / "final-routes.json").write_text(json.dumps([
                {"appid": 1, "route": "wine"}, {"appid": 2, "route": "wine"}, {"appid": 3, "route": "wine"}, {"appid": 4, "route": "unity"}, {"appid": 5, "route": "wine"}]))
            (catalog / "windows-library.json").write_text(json.dumps([
                {"appid": 1, "depots": [{"depot": "11", "gid": "g1"}]}, {"appid": 2, "depots": [{"depot": "21", "gid": "g2"}]},
                {"appid": 3, "depots": [{"depot": "31", "gid": "g3"}]}, {"appid": 5, "depots": [{"depot": "51", "gid": "missing"}]}]))
            (dc / "11_g1.manifest.txt").write_text(manifest([(100, "c" * 40, "Game.exe"), (5000, sha_a, "Plugins/steam_api64.dll")]))
            (dc / "21_g2.manifest.txt").write_text(manifest([(300, sha_a, "steam_api64.dll"), (900000, "d" * 40, "data.pak")]))
            (dc / "31_g3.manifest.txt").write_text(manifest([(300, sha_b, "Engine\\Binaries\\Win64\\steam_api64.dll")]))
            data = variants.census(catalog, dc)
            self.assertEqual((data["wine_games"], data["without_local_manifest"], data["with_steam_api64"]), (4, 1, 3))
            self.assertEqual(sorted(len(v) for v in data["variants"].values()), [1, 2])
            self.assertEqual(data["variants"][sha_a][0][1], "1")           # 最小的先
            known = base / "known"; known.mkdir()
            (known / f"{sha_a}.json").write_text(json.dumps({"interface_versions": {"User": ["SteamUser021"], "Client": ["SteamClient017", "SteamClient020"]}}))
            kv = variants.known_versions(known)
            self.assertEqual(kv[sha_a], {"SteamUser021", "SteamClient020"})
            r = variants.report(data, kv, {"SteamUser021", "SteamClient020"})
            self.assertEqual(r["coverage"], {"all_versions_have_tables": 2, "some_version_missing": 0, "variant_unknown": 1, "share_all": round(2 / 3, 4)})
            self.assertEqual(r["top"][0]["games"], 2); self.assertEqual(r["top"][0]["tables"], "all"); self.assertEqual(r["top"][1]["tables"], "unknown")
            r2 = variants.report(data, kv, {"SteamUser021"})
            self.assertEqual(r2["coverage"]["some_version_missing"], 2)


if __name__ == "__main__":
    unittest.main()
