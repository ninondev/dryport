# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""tools/wine-trial.py：受控真机试跑工具。

只测不启动游戏的部分：清单字段契约、策略生成、祖先推导、引擎窗口化参数。
两轮真实 harness（Unbound、Fixture）如果还在本机，就拿它们的 policy.sb 作对照。
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
_spec = importlib.util.spec_from_file_location("wine_trial", ROOT / "tools/wine-trial.py")
wt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wt)


class Policy(unittest.TestCase):
    def test_parameter_count_follows_path_count(self):
        """手抄版把 PROTECTED_n 个数写死，换游戏后 sandbox-exec 会因未定义参数拒绝执行。"""
        for n_prot, n_anc in ((1, 1), (5, 9), (6, 7), (12, 3)):
            policy = wt.build_policy(['/p%d' % i for i in range(n_prot)],
                                     ['/a%d' % i for i in range(n_anc)])
            for i in range(n_prot):
                self.assertIn(f'(param "PROTECTED_{i}")', policy)
            self.assertNotIn(f'(param "PROTECTED_{n_prot}")', policy)
            for i in range(n_anc):
                self.assertIn(f'(param "ANCESTOR_{i}")', policy)
            self.assertNotIn(f'(param "ANCESTOR_{n_anc}")', policy)

    def test_policy_denies_writes_by_default(self):
        policy = wt.build_policy(['/g'], ['/'])
        self.assertIn('(deny file-write*)', policy)
        self.assertIn('(deny file-link)', policy)
        self.assertIn('(require-not (subpath (param "PROTECTED_0")))', policy)

    def test_ancestors_include_root_and_dedupe(self):
        anc = wt.ancestors_of(['/a/b/c', '/a/b/d'])
        self.assertEqual(len(anc), len(set(anc)))
        self.assertIn('/', anc)
        self.assertIn('/a/b', anc)


class Inventory(unittest.TestCase):
    def test_source_inventory_uses_bytes_key(self):
        """字段名必须是 bytes：run 阶段按这个键读，用 size 会 KeyError 并中止。"""
        with tempfile.TemporaryDirectory() as d:
            game = pathlib.Path(d) / 'game'
            (game / 'sub').mkdir(parents=True)
            (game / 'a.exe').write_bytes(b'abc')
            (game / 'sub/b.dat').write_bytes(b'defg')
            inv = wt.build_source_inventory(game)
            self.assertEqual(inv['count'], 2)
            self.assertEqual(inv['total_bytes'], 7)
            for row in inv['files']:
                self.assertIn('bytes', row)
                self.assertNotIn('size', row)
                self.assertEqual(len(row['sha256']), 64)

    def test_symlink_in_game_stops_preparation(self):
        with tempfile.TemporaryDirectory() as d:
            game = pathlib.Path(d) / 'game'
            game.mkdir()
            (game / 'real').write_bytes(b'x')
            (game / 'link').symlink_to(game / 'real')
            with self.assertRaises(SystemExit):
                wt.build_source_inventory(game)

    def test_hardlink_in_game_stops_preparation(self):
        """写保护排除的是路径；已有硬链接能绕过去，所以 nlink 必须为 1。"""
        with tempfile.TemporaryDirectory() as d:
            game = pathlib.Path(d) / 'game'
            game.mkdir()
            (game / 'real').write_bytes(b'x')
            os.link(game / 'real', game / 'alias')
            with self.assertRaises(SystemExit):
                wt.build_source_inventory(game)

    def test_saves_inventory_shape(self):
        """protected[0] 固定是游戏目录，run 阶段从 [1:] 遍历存档路径。"""
        with tempfile.TemporaryDirectory() as d:
            game = pathlib.Path(d) / 'game'
            game.mkdir()
            inv = wt.build_saves_inventory(game, ['Library/Application Support/NoSuchGameXYZ'])
            self.assertEqual(inv['protected'][0], str(game))
            self.assertEqual(len(inv['protected']), 2)
            self.assertEqual(inv['files'], [])


class Windowed(unittest.TestCase):
    def test_each_engine_has_windowed_args(self):
        """给错引擎会全屏铺满桌面，09-11 出过把桌面盖死的事故。"""
        self.assertIn('-screen-fullscreen', wt.WINDOWED['unity'])
        self.assertIn('0', wt.WINDOWED['unity'])
        self.assertIn('-windowed', wt.WINDOWED['unreal'])
        self.assertNotIn('-screen-fullscreen', wt.WINDOWED['unreal'])
        self.assertIn('other', wt.WINDOWED)


class AgainstRealHarnesses(unittest.TestCase):
    """两轮手写材料还在本机时，生成结果必须与它们逐字一致。"""

    # 留存的两轮手写 harness 只在这台机器上有；用主目录拼路径，源码里不留个人路径（发布检查器会拦）
    inventory = pathlib.Path(__file__).resolve().parents[1] / "docs/internal/withheld-titles.txt"
    CASES = tuple(str(pathlib.Path.home() / "Games/runtimes" / name)
                  for name in (json.loads(inventory.read_text())["policy_harnesses"] if inventory.is_file() else []))

    def test_generated_policy_matches_hand_written(self):
        checked = 0
        for h in self.CASES:
            base = pathlib.Path(h)
            saves = base / 'existing-saves-inventory.json'
            policy = base / 'policy.sb'
            if not (saves.is_file() and policy.is_file()):
                continue
            protected = json.loads(saves.read_text())['protected']
            generated = wt.build_policy(protected, wt.ancestors_of(protected))
            self.assertEqual(generated, policy.read_text(), f'{h} 的 policy.sb 与生成结果不一致')
            checked += 1
        if checked == 0:
            self.skipTest('本机没有留存的 harness 可对照')


if __name__ == '__main__':
    unittest.main()
