# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""find-saves 的排序判据：两个已知答案都要排第一。

这两个布局是 2026-09-13 真机验出来的，当时都踩过坑：
- Gun-Dog 的存档在 `~/Games/.renpy-state/2118420`，不是 Ren'Py 的系统默认位置；
  第一版评分只认 `save` 字样，给正确答案 0 分。
- Koi Farm 的存档是 WebKit 的 localStorage，落在 `~/Library/WebKit/<bundle>/WebsiteDataStore/...`；
  第一版既没把持有它的 XPC 进程算进来，排序时又被通用的 `~/Library` 用文件数压到后面。
"""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('find_saves', REPO / 'tools' / 'find-saves.py')
find_saves = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(find_saves)

HOME = Path.home()
RENPY = [f'{HOME}/Games/.renpy-state/2118420/saves/auto-1-LT1.save',
         f'{HOME}/Games/.renpy-state/2118420/saves/persistent',
         f'{HOME}/Games/.renpy-state/2118420/logs/stderr.log',
         f'{HOME}/Games/Gun-Dog.app/Contents/MacOS/launcher',
         f'{HOME}/Games/Gun-Dog.app/Contents/Resources/game/script.rpa']
WEBKIT = [f'{HOME}/Library/WebKit/io.dryport.webkit.1518810/WebsiteDataStore/'
          f'908fdaa9/Origins/abc/abc/LocalStorage/localstorage.sqlite3',
          f'{HOME}/Library/WebKit/io.dryport.webkit.1518810/WebsiteDataStore/'
          f'908fdaa9/ResourceLoadStatistics/observations.db',
          f'{HOME}/Library/Application Support/dryport/webkit/1518810/process.lock',
          f'{HOME}/Games/Koi Farm.app/Contents/MacOS/WebKitGame']


class FindSavesRankingTests(unittest.TestCase):
    def top(self, paths):
        scored = find_saves.interesting_roots(paths)
        self.assertTrue(scored, '一个候选根都没收敛出来')
        return scored[0]

    def test_renpy_state_directory_ranks_first(self):
        score, _, root = self.top(RENPY)
        self.assertTrue(root.startswith(f'{HOME}/Games/.renpy-state/2118420'), root)
        self.assertGreater(score, 0)

    def test_webkit_website_data_store_ranks_first(self):
        score, _, root = self.top(WEBKIT)
        self.assertIn('WebsiteDataStore', root)
        self.assertGreater(score, 0)

    def test_app_bundle_never_ranks_first(self):
        for paths in (RENPY, WEBKIT):
            with self.subTest(paths=paths[0]):
                _, _, root = self.top(paths)
                self.assertNotIn('.app/', root + '/')

    def test_only_paths_under_data_containers_become_candidates(self):
        noise = [f'{HOME}/source-fixture/lib/route.py',
                 f'{HOME}/Downloads/whatever.zip']
        self.assertEqual(find_saves.interesting_roots(noise), [])


if __name__ == '__main__':
    unittest.main()
