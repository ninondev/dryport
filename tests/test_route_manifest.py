# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""分类快筛必须保持规则优先级、证据及未知状态；不连接 Steam。"""
import contextlib
import io
import json
import random
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import reclassify


def original_classify(files):
    for name, rx, route, conf in reclassify.COMPILED:
        for filename in files:
            if rx.search(filename):
                return name, route, conf, filename[:80]
    return "未识别", "unknown", "无", reclassify.unknown_evidence(files)


class ManifestRouteTests(unittest.TestCase):
    def test_reviewed_rules_enable_the_candidate_filter(self):
        self.assertEqual(tuple(reclassify.COMPILED), reclassify._FILTER_RULES)

    def test_all_existing_rule_variants_keep_identical_result_and_evidence(self):
        examples = ["easyanticheat/logo.png", "start_protected_game.exe", "BEClient_other/logo.png", "battleye/config.ini",
                    "gameguard/data.bin", "npgmup.des.backup", "gameassembly.dll", "global-metadata.dat",
                    "managed/assembly-csharp.dll", "monobleedingedge/data.bin", "unityplayer.dll", "unity default resources",
                    "archive.unity3d", "nw.dll", "www/js/rpg_core.js", "rmmz_core.js", "save.rpgsave", "data.rgssa",
                    "data.rgss3a", "rgss301.dll", "rgss102e.dll", "data.rvdata", "data.rvdata2", "data.rxdata",
                    "renpy/common/base.png", "script.rpa", "script.rpyc", "godot_export.exe", "root.pck", "a/b.xp3",
                    "kirikiri2/a.bin", "krkr_data/a.png", "game-win64-shipping.exe", "x.uasset", "x.upk", "x.utoc",
                    "some/engine/binaries/shader.bin", "data.xnb", "microsoft.xna.framework.xml", "monogame.framework.info",
                    "libcocos2d/assets.png", "love.dll", "archive/game.love", "javaw.exe", "jvm.dll", "jre/lib/data.bin", "data.win", "data.wolf",
                    "guruguru_image.png", "nscript.dat.bak", "arc.nsa.bak", "text.ns2", "room.crm", "acsetup.cfg",
                    "adobe air.dll", "movie.swf", "UnıtyPlayer.dll", "DİRECTORY/GODOT.PCK", "data.pck\n"]
        for example in examples:
            with self.subTest(example=example):
                files = ["textures/image.png", example, "audio/song.ogg"]
                self.assertEqual(reclassify.classify(files), original_classify(files))

    def test_randomized_order_retains_rule_priority_and_first_matching_filename(self):
        rng = random.Random(20260912)
        candidates = ["nw.dll", "data.rgss3a", "easyanticheat/a.png", "unityplayer.dll", "根目录/rgss301.dll",
                      "texture.png", "manual.pdf", "mygame.pck", "folder/mygame.pck", "app.exe", "empty"]
        for _ in range(300):
            files = rng.choices(candidates, k=rng.randint(0, 80))
            self.assertEqual(reclassify.classify(files), original_classify(files))

    def test_unreviewed_rule_change_falls_back_instead_of_filtering_new_evidence(self):
        with patch.object(reclassify, "COMPILED", [("fixture", re.compile(r"mystery\.png$"), "maybe", "推测")]):
            self.assertEqual(reclassify.classify(["mystery.png"]), ("fixture", "maybe", "推测", "mystery.png"))

    def test_resources_without_engine_evidence_stay_unknown(self):
        files = [f"textures/image-{index}.png" for index in range(2000)]
        self.assertEqual(reclassify.classify(files), ("未识别", "unknown", "无", "2000 个文件里没有已知特征；根目录没有主程序 exe；下载后走 Wine 兜底，未验证"))

    def test_unknown_evidence_states_bitness_directx_era_and_main_exe(self):
        files = ["Game.exe", "steam_api64.dll", "d3dx9_43.dll", "data/pak0.dat", "Uninstall.exe"]
        self.assertEqual(reclassify.classify(files),
                         ("未识别", "unknown", "无", "5 个文件里没有已知特征；64 位（带 steam_api64.dll）、DirectX 9 时代文件；下载后走 Wine 兜底，未验证"))
        files = ["bin/game.exe", "bin/steam_api.dll", "bin/d3dcompiler_47.dll", "bin/vulkan-1.dll"]
        self.assertIn("32 位（带 steam_api.dll）、带 Vulkan 库、DirectX 11 时代文件、根目录没有主程序 exe", reclassify.classify(files)[3])
        self.assertEqual(reclassify.classify(["a.exe", "b.exe", "readme.txt"])[3], "3 个文件里没有已知特征；根目录 2 个 exe；下载后走 Wine 兜底，未验证")

    def test_nw_bundle_requires_root_app_evidence_and_anticheat_still_wins(self):
        self.assertEqual(reclassify.classify(["nw.pak", "package.nw"])[:3], ("NW.js", "nwjs", "确证"))
        self.assertEqual(reclassify.classify(["nw.pak", "package.json", "index.html"])[:3], ("NW.js", "nwjs", "推测"))
        for files in (["nw.pak"], ["launcher/nw.pak", "launcher/package.nw"], ["resources/app.asar", "electron.exe"]):
            with self.subTest(files=files):
                self.assertEqual(reclassify.classify(files)[1], "unknown")
        self.assertEqual(reclassify.classify(["nw.pak", "package.nw", "easyanticheat/a.bin"])[1], "refuse")

    def test_unity_metadata_identifies_family_without_overriding_il2cpp(self):
        self.assertEqual(reclassify.classify(["game_data/globalgamemanagers"])[:3], ("Unity（后端待确认）", "unity", "确证"))
        self.assertEqual(reclassify.classify(["game_data/globalgamemanagers", "gameassembly.dll"])[1], "wine")
        self.assertEqual(reclassify.classify(["docs/globalgamemanagers.txt"])[1], "unknown")

    def test_emulators_and_save_extensions_remain_unverified_candidates(self):
        for filename in ("scummvm/scummvm.exe", "dosbox.exe", "save.rpgsave"):
            with self.subTest(filename=filename):
                result = reclassify.classify([filename])
                self.assertEqual(result[2], "推测")
        self.assertNotIn("renpy", reclassify.NATIVE)

    def test_weak_save_or_pck_clues_do_not_override_confirmed_engine_files(self):
        self.assertEqual(reclassify.classify(["save.rpgsave", "archive.rgss3a"])[1], "mkxp")
        self.assertEqual(reclassify.classify(["root.pck", "game-win64-shipping.exe"])[1], "wine")

    def test_new_pattern_variants_are_not_lost_by_candidate_filter(self):
        for filename in ("Game_Data/GlobalGameManagers.assets", "game_data/globalgamemanagers", "DOSBOX-X.EXE", "scummvm.exe"):
            with self.subTest(filename=filename):
                self.assertEqual(reclassify.classify([filename]), original_classify([filename]))

    def test_electron_needs_complete_root_chromium_and_application_bundle(self):
        core = ['resources.pak', 'icudtl.dat', 'v8_context_snapshot.bin']
        for package in ('resources/app.asar', 'resources/app/package.json'):
            files = core + [package]
            self.assertEqual(reclassify.classify(files)[:3], ('Electron 候选', 'maybe', '推测'))
            self.assertEqual(reclassify.classify([f.upper() for f in files])[:3],
                             ('Electron 候选', 'maybe', '推测'))
            for index in range(len(files)):
                self.assertEqual(reclassify.classify(files[:index] + files[index+1:])[1], 'unknown')
            self.assertEqual(reclassify.classify(['launcher/' + f for f in files])[1], 'unknown')
            self.assertEqual(reclassify.classify(core + ['launcher/' + package])[1], 'unknown')
        for files in (['manual/index.html'], ['Support/webhelp.jar'], ['electron.exe', 'resources/app.asar']):
            self.assertEqual(reclassify.classify(files)[1], 'unknown')

    def test_easyrpg_needs_database_tree_and_numbered_map_in_one_supported_directory(self):
        for directory in ('', 'data/'):
            files = [directory + name for name in ('RPG_RT.ldb', 'RPG_RT.lmt', 'Map0001.lmu')]
            self.assertEqual(reclassify.classify(files)[:3],
                             ('RPG Maker 2000/2003（EasyRPG 候选）', 'maybe', '推测'))
            for index in range(3):
                self.assertEqual(reclassify.classify(files[:index] + files[index+1:])[1], 'unknown')
        for files in (['EasyRPG.dll', 'Dlg/EasyRPG.dlg'], ['single.ldb'],
                      ['RPG_RT.ldb', 'data/RPG_RT.lmt', 'Map0001.lmu'],
                      ['RPG_RT.ldb', 'RPG_RT.lmt', 'MapBackup.lmu'],
                      ['launcher/RPG_RT.ldb', 'launcher/RPG_RT.lmt', 'launcher/Map0001.lmu']):
            self.assertEqual(reclassify.classify(files)[1], 'unknown')

    def test_combination_candidates_keep_strong_priority_and_fast_filter_equivalence(self):
        combinations = [['resources.pak', 'icudtl.dat', 'v8_context_snapshot.bin', 'resources/app.asar'],
                        ['data/RPG_RT.ldb', 'data/RPG_RT.lmt', 'data/Map0001.lmu']]
        for combination in combinations:
            for extra, route in (([], 'maybe'), (['EasyAntiCheat/launcher.exe'], 'refuse'),
                                 (['UnityPlayer.dll'], 'unity'), (['GameAssembly.dll'], 'wine'),
                                 (['www/js/rpg_core.js'], 'nwjs')):
                files = ['texture.png'] * 100 + combination + extra
                fast = reclassify.classify(files)
                self.assertEqual(fast[1], route)
                with patch.object(reclassify, '_FILTER_RULES', ()):
                    self.assertEqual(fast, reclassify.classify(files))

    def test_cli_classifies_current_shared_table_instead_of_legacy_owned_subset(self):
        import library_discovery as discovery
        with tempfile.TemporaryDirectory(prefix="sn-reclassify-test-") as temporary:
            directory = Path(temporary)
            (directory / "windows-library.json").write_text(json.dumps([{"appid": "42", "name": "owned"},
                                                                        {"appid": "99", "name": "shared"}]))
            (directory / "owned-windows.json").write_text(json.dumps([{"appid": "42", "name": "owned"}]))
            def refined(row, windows):
                return {**row, "engine": "未识别", "route": "unknown", "route_name": "待识别", "files": 0, "confidence": "无"}
            with patch.object(discovery, "current_catalog_dir", return_value=directory), \
                    patch.object(discovery, "refine_route", side_effect=refined) as refine, \
                    patch.object(sys, "argv", ["reclassify.py"]), contextlib.redirect_stdout(io.StringIO()) as output:
                reclassify.main()
            self.assertEqual([call.args[0]["appid"] for call in refine.call_args_list], ["42", "99"])
            self.assertIn("当前账号目录中的 Windows 专属游戏 2 个", output.getvalue())

    def test_cli_legacy_reads_custom_games_path_and_labels_snapshot(self):
        import library_discovery as discovery
        with tempfile.TemporaryDirectory(prefix="sn-reclassify-test-") as temporary:
            directory = Path(temporary)
            (directory / "owned-windows.json").write_text("[]")
            with patch.object(discovery, "current_catalog_dir", return_value=None), \
                    patch.object(reclassify, "SN_GAMES", directory), patch.object(sys, "argv", ["reclassify.py"]), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                reclassify.main()
            self.assertIn("旧快照", output.getvalue())
            self.assertIn("尚未确认当前账号", output.getvalue())


if __name__ == "__main__":
    unittest.main()
