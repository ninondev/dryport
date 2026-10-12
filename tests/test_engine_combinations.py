# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""完整运行时文件名组合只给候选，不把 SDK、文档或局部清单当作可玩证据。"""
import itertools
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import reclassify

# 全部是自有合成名字，不复制个人库表或游戏源码。
SOLARUS = ['data.solarus', 'libsolarus.dll', 'luasteam.dll', 'Adventure.exe']
HASHLINK = ['hlboot.dat', 'libhl.dll', 'heaps.hdll', 'Adventure.exe']
OLD_NW = ['nw.pak', 'icudtl.dat', 'ffmpegsumo.dll', 'libEGL.dll', 'libGLESv2.dll',
          'd3dcompiler_47.dll', 'Adventure.exe']
PYGAME = ['Adventure.py', 'Adventure.exe', 'requirements.txt', 'SteamAdapter.py',
          '_internal/python310.dll', '_internal/pygame/base.cp310-win_amd64.pyd',
          '_internal/pygame/display.cp310-win_amd64.pyd']
CASES = [('Solarus 候选', SOLARUS), ('HashLink/Heaps 候选', HASHLINK),
         ('旧 NW.js 候选', OLD_NW), ('Python/Pygame 候选', PYGAME)]


class EngineCombinationTests(unittest.TestCase):
    def test_complete_root_runtime_combinations_are_only_candidates(self):
        for engine, files in CASES:
            with self.subTest(engine=engine):
                result = reclassify.classify(files)
                self.assertEqual(result[:3], (engine, 'maybe', '推测'))
                self.assertIn('未接通', result[3])
                self.assertNotIn(result[1], reclassify.NATIVE)

    def test_missing_any_required_component_stays_unknown(self):
        for engine, files in CASES:
            for index, missing in enumerate(files):
                with self.subTest(engine=engine, missing=missing):
                    self.assertEqual(reclassify.classify(files[:index] + files[index + 1:])[1], 'unknown')

    def test_individual_signals_and_help_documents_stay_unknown(self):
        for _, files in CASES:
            for filename in files:
                with self.subTest(filename=filename):
                    self.assertEqual(reclassify.classify([filename])[1], 'unknown')
        files = ['manual/Solarus.html', 'docs/HashLink.md', 'credits/pygame.txt',
                 'readme/nw.pak.txt', 'SteamAdapter.py.html', 'data.solarus.bak', 'license.pygame.txt']
        self.assertEqual(reclassify.classify(files)[1], 'unknown')

    def test_nested_sdk_launcher_or_help_runtime_is_not_the_game(self):
        for engine, files in CASES:
            for prefix in ('sdk/', 'tools/launcher/', 'docs/examples/', 'resources/dependencies/'):
                with self.subTest(engine=engine, prefix=prefix):
                    self.assertEqual(reclassify.classify([prefix + f for f in files])[1], 'unknown')
                    self.assertEqual(reclassify.classify([(prefix + f).replace('/', '\\') for f in files])[1], 'unknown')

    def test_executable_in_subdirectory_cannot_complete_root_runtime(self):
        for engine, files in CASES:
            replaced = ['helpers/' + f if f.endswith('.exe') else f for f in files]
            with self.subTest(engine=engine):
                self.assertEqual(reclassify.classify(replaced)[1], 'unknown')
                self.assertEqual(reclassify.classify([f.replace('/', '\\') for f in replaced])[1], 'unknown')

    def test_python_entry_and_binary_abi_must_match_the_same_distribution(self):
        for replacements in ({'Adventure.py': 'Different.py'},
                             {'_internal/python310.dll': '_internal/python311.dll'},
                             {'_internal/pygame/display.cp310-win_amd64.pyd': '_internal/pygame/display.cp311-win_amd64.pyd'},
                             {'_internal/pygame/display.cp310-win_amd64.pyd': 'vendored/pygame/display.cp310-win_amd64.pyd'},
                             {'_internal/pygame/base.cp310-win_amd64.pyd': '_internal/pygame/base.cp310-win_amd64.pyd.html'},
                             {'SteamAdapter.py': 'dependencies/SteamAdapter.py'}):
            with self.subTest(replacements=replacements):
                self.assertEqual(reclassify.classify([replacements.get(f, f) for f in PYGAME])[1], 'unknown')
        docs = [f.replace('_internal/pygame/', '_internal/docs/pygame/') for f in PYGAME]
        self.assertEqual(reclassify.classify(docs)[1], 'unknown')

    def test_pygame_root_python_layout_and_matching_32_bit_abi_are_candidates(self):
        for prefix in ('', '_internal/'):
            for abi in ('cp310-win_amd64', 'cp311-win32'):
                python_version = abi.split('-')[0][2:]
                files = PYGAME[:4] + [prefix + 'python' + python_version + '.dll',
                    prefix + 'pygame/base.' + abi + '.pyd', prefix + 'pygame/display.' + abi + '.pyd']
                with self.subTest(prefix=prefix, abi=abi):
                    self.assertEqual(reclassify.classify(files)[:3], ('Python/Pygame 候选', 'maybe', '推测'))

    def test_setup_or_helper_executable_does_not_prove_game_entry(self):
        for _, files in CASES:
            for stem in ('setup', 'uninstall', 'python', 'pythonw', 'launcher'):
                replaced = [f.replace('Adventure', stem) for f in files]
                with self.subTest(stem=stem, files=files):
                    self.assertEqual(reclassify.classify(replaced)[1], 'unknown')

    def test_existing_engine_and_anticheat_priority_is_unchanged(self):
        markers = [('UnityPlayer.dll', 'unity'), ('GameAssembly.dll', 'wine'),
                   ('www/js/rpg_core.js', 'nwjs'), ('game.rgss3a', 'mkxp'),
                   ('game-win64-shipping.exe', 'wine'), ('EasyAntiCheat/launcher.exe', 'refuse')]
        for _, files in CASES:
            for marker, route in markers:
                with self.subTest(marker=marker, files=files):
                    self.assertEqual(reclassify.classify(files + [marker])[1], route)
        old = reclassify.classify(['nw.pak', 'package.nw'])
        self.assertEqual(reclassify.classify(OLD_NW + ['package.nw']), old)

    def test_multiple_new_engine_signals_do_not_arbitrarily_choose_one(self):
        for left, right in itertools.combinations(CASES, 2):
            files = left[1] + right[1]
            with self.subTest(left=left[0], right=right[0]):
                result = reclassify.classify(files)
                self.assertEqual(result[:3], ('多个引擎候选', 'maybe', '推测'))
                self.assertIn('主入口待核实', result[3])
                self.assertEqual(reclassify.classify(files + ['UnityPlayer.dll'])[1], 'unity')

    def test_order_case_and_fast_filter_preserve_all_results(self):
        rng = random.Random(20260912)
        for engine, base in CASES:
            for _ in range(15):
                files = [f.upper() if rng.randrange(2) else f for f in base] + ['textures/paint.png'] * 50
                rng.shuffle(files)
                fast = reclassify.classify(files)
                self.assertEqual(fast[:3], (engine, 'maybe', '推测'))
                with patch.object(reclassify, '_FILTER_RULES', ()):
                    self.assertEqual(fast, reclassify.classify(files))


if __name__ == '__main__':
    unittest.main()


class GodotPairingTests(unittest.TestCase):
    """Godot 判据要求 `游戏名.pck` 与同名 exe 成对。

    2026-09-13 抽查 28 个 `Godot?` 候选，发现只看「根目录有个 .pck」会判错 6 个：
    一类策略游戏 的 gamedata.pck / music.pck / patch.pck、一款战术游戏 的 data.pck、
    一款 SiglusEngine 视觉小说 的 scenezh.pck（它其实是 SiglusEngine）。这些真按 Godot 模板打包出来就是坏的。
    下面的反例按那几个真实布局写，游戏名用自有的。
    """

    PAIRED = ['Adventure.exe', 'Adventure.pck']
    PAIRED_EXTRA = ['Adventure.exe', 'Adventure.pck', 'WorkshopUtility.exe', 'WorkshopUtility.pck']
    # 反例：pck 与 exe 不同名
    UNPAIRED = [
        ['StrategyFixture.exe', 'StrategyLauncher.exe', 'gamedata.pck', 'patch.pck'],
        ['StrategyFixture.exe', 'StrategyLauncher.exe', 'music.pck', 'update.pck'],
        ['TacticsFixture.exe', 'data.pck', 'data2.pck'],
    ]
    SIGLUS = ['SiglusEngine_Steam.exe', 'SceneZH.pck', 'Gameexe.dat']

    def test_paired_pck_and_exe_is_a_godot_candidate(self):
        for files in (self.PAIRED, self.PAIRED_EXTRA):
            with self.subTest(files=files):
                engine, route, confidence, evidence = reclassify.classify(files)
                self.assertEqual((engine, route, confidence), ('Godot?', 'godot', '推测'))
                self.assertIn('同名 exe', evidence)

    def test_unpaired_pck_is_not_godot(self):
        for files in self.UNPAIRED:
            with self.subTest(files=files):
                engine, route, _, _ = reclassify.classify(files)
                self.assertNotEqual(route, 'godot', f'{files} 被判成 Godot（{engine}）')

    def test_siglus_engine_wins_over_a_bare_pck(self):
        engine, route, confidence, _ = reclassify.classify(self.SIGLUS)
        self.assertEqual((engine, route, confidence), ('SiglusEngine', 'vn', '确证'))


class XnaFamilyTests(unittest.TestCase):
    """`.xnb` 不能当 FNA 的确证判据。

    2026-09-13 逐个核过 17 个被判成 FNA 的游戏，只有 3 个真是 FNA。`.xnb` 只是 XNA 时代的
    内容格式，SFML.Net 游戏和 WinForms 加 Lua 游戏 都带它。
    三类的结局完全不同：FNA 官方有 mac 构建；MonoGame 的 WindowsDX 后端拉一整套 SharpDX
    绑死 DirectX；微软 XNA 早已停更且只有 Windows。下面的布局按那几个游戏的真实根目录写。
    """

    FNA = ['Game.exe', 'FNA.dll', 'FNA3D.dll', 'FAudio.dll', 'SDL2.dll', 'Content/tex.xnb']
    MG_GL = ['Game.exe', 'MonoGame.Framework.DesktopGL.dll', 'SDL2.dll', 'Content/tex.xnb']
    MG_DX = ['Game.exe', 'MonoGame.Framework.dll', 'SharpDX.dll', 'SharpDX.Direct3D11.dll',
             'SharpDX.XAudio2.dll', 'Content/tex.xnb']
    MG_UNKNOWN = ['Game.exe', 'MonoGame.Framework.dll', 'Content/tex.xnb']
    XNA = ['Game.exe', 'Microsoft.Xna.Framework.dll', 'Content/tex.xnb']
    # 只有内容格式，没有任何框架：SFML.Net 游戏与 WinForms 加 Lua 游戏
    SFML = ['Game.exe', 'csfml-audio-2.dll', 'sfmlnet-window-2.dll', 'Content/tex.xnb']
    WINFORMS = ['Game.exe', 'Interop.WMPLib.dll', 'lua51.dll', 'Content/tex.xnb']

    def test_real_fna_goes_to_the_fna_route(self):
        for files in (self.FNA, self.MG_GL):
            with self.subTest(files=files[1]):
                engine, route, confidence, _ = reclassify.classify(files)
                self.assertEqual((route, confidence), ('fna', '确证'), f'{engine} / {route}')

    def test_windowsdx_and_xna_go_to_wine(self):
        for files, expected in ((self.MG_DX, 'MonoGame WindowsDX'), (self.XNA, '微软 XNA')):
            with self.subTest(engine=expected):
                engine, route, _, _ = reclassify.classify(files)
                self.assertEqual((engine, route), (expected, 'wine'))

    def test_monogame_without_a_known_backend_is_only_a_candidate(self):
        engine, route, confidence, _ = reclassify.classify(self.MG_UNKNOWN)
        self.assertEqual((route, confidence), ('maybe', '推测'), engine)

    def test_bare_xnb_content_is_not_fna(self):
        for files in (self.SFML, self.WINFORMS):
            with self.subTest(files=files[1]):
                engine, route, _, _ = reclassify.classify(files)
                self.assertNotEqual(route, 'fna', f'{files[1:]} 被判成 {engine}')
