# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""appinfo 的线索是候选证据；风险特征须优先，题材不能当引擎。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import route


class AppInfoRouteTests(unittest.TestCase):
    def test_anticheat_launch_wins_even_when_other_launchers_look_native(self):
        app = {"config": {"launch": {"0": {"executable": "nw.exe"},
                                    "1": {"executable": "start_protected_game.exe"},
                                    "2": {"executable": "game-win64-shipping.exe"}}}}
        self.assertEqual(route.classify(app)[2], "refuse")

    def test_cloud_save_suffix_is_a_clue_not_engine_confirmation(self):
        app = {"ufs": {"savefiles": {"0": {"pattern": "*.rpgsave"}}}}
        self.assertEqual(route.classify(app)[1:3], ("推测", "nwjs"))

    def test_nw_launcher_identifies_runtime_without_asserting_rpg_maker(self):
        self.assertEqual(route.classify({"config": {"launch": {"0": {"executable": "nw.exe"}}}})[:3],
                         ("NW.js", "推测", "nwjs"))

    def test_numeric_or_string_visual_novel_tag_remains_engine_unconfirmed(self):
        for value in (3799, "3799"):
            with self.subTest(value=value):
                result = route.classify({"common": {"store_tags": {"0": value}}})
                self.assertEqual(result[1:3], ("推测", "vn"))

    def test_unknown_has_no_automatic_wine_playability_claim(self):
        self.assertEqual(route.classify({})[2], "unknown")
        self.assertEqual(route.ROUTE_NAME["unknown"], "待识别")
        self.assertIn("未接通", route.ROUTE_NAME["renpy"])


if __name__ == "__main__":
    unittest.main()
