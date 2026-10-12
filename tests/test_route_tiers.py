# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""路线分层：route-tiers.json 的数据、版本细分与三处展示共用的 CLI 约定。
按到期规则，所有路线都在实验档、unity 不再带 experimental_below。"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import route_tiers

CLI = Path(__file__).resolve().parents[1] / "lib" / "route_tiers.py"
LINE = "      这条路线是实验：照样能装，但预览版不承诺能玩到底（真机全链条没凑齐两款）。"


class RouteTierTests(unittest.TestCase):
    def test_every_listed_route_has_a_tier(self):
        routes = route_tiers.load()["routes"]
        self.assertEqual(set(routes), {"unity", "wine", "mkxp", "nwjs", "webkit",
                                       "renpy-native", "godot", "love", "krkr"})
        for route, entry in routes.items():
            self.assertIn(entry["tier"], ("open", "experimental"), route)
        # 截至验证期限，没有路线凑齐两款全链游戏，全部实验
        for route in ("unity", "wine", "mkxp", "nwjs", "webkit", "renpy-native", "godot", "love", "krkr"):
            self.assertEqual(route_tiers.tier_for(route), "experimental", route)
            self.assertIsNone(route_tiers.describe(route)["tier_note"], route)

    def test_unlisted_route_has_no_tier(self):
        self.assertIsNone(route_tiers.tier_for("steam"))
        self.assertEqual(route_tiers.describe("refuse"),
                         {"tier": None, "tier_label": None, "tier_note": None})

    def test_unity_version_takes_the_first_major_minor(self):
        self.assertEqual(route_tiers.unity_version("Unity 5.6.3f1"), (5, 6))
        self.assertEqual(route_tiers.unity_version("Unity 2017.4.40f1 (老形态,引擎编在 exe 里)"), (2017, 4))
        self.assertIsNone(route_tiers.unity_version("Unity 版本未知"))

    def test_unity_tier_no_longer_splits_by_version(self):
        # 整条 unity 路线都是实验：experimental_below 已移除，任何引擎文本都报 experimental
        for engine in ("Unity 5.6.3f1", "Unity 2017.3.0f3", "Unity 2020.3.48f1", "Unity 版本未知", None):
            self.assertEqual(route_tiers.tier_for("unity", engine), "experimental", str(engine))

    def test_describe_attaches_note_only_while_open(self):
        for engine in ("Unity 5.6.3f1", "Unity 2020.3.48f1", "Unity 版本未知", None):
            d = route_tiers.describe("unity", engine)
            self.assertEqual(d, {"tier": "experimental", "tier_label": "实验：照样能装，不承诺", "tier_note": None}, str(engine))

    def test_cli_prints_line_only_for_experimental_and_always_exits_zero(self):
        def run(*argv):
            return subprocess.run([sys.executable, str(CLI), *argv], capture_output=True, text=True)
        for argv in (("--route", "unity", "--engine", "Unity 5.6.3f1"),
                     ("--route", "unity", "--engine", "Unity 2020.3.48f1"),
                     ("--route", "unity", "--engine", "Unity 版本未知"),
                     ("--route", "unity"),
                     ("--route", "wine"),
                     ("--route", "mkxp"),
                     ("--route", "nwjs"),
                     ("--route", "webkit")):
            done = run(*argv)
            self.assertEqual((done.returncode, done.stdout), (0, LINE + "\n"), str(argv))
        done = run("--route", "steam")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
