# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""ACE 与 PunkBuster 的拒绝判据：锚定后不误伤普通游戏资源。

2026-09-13 检查资源 4557 份清单时发现两款带腾讯 ACE 全套内核驱动的游戏被路由到 wine。
补规则时踩到的坑是误报：不锚定的话，`vanguard`、`ricochet`、`inca`、`.sys`
这类词在普通游戏资源名里遍地都是（实测 nProtect 的宽松写法命中 109 个游戏，其中 107 个是误报）。
所以判据锚在目录名或精确文件名上，下面的反例就是当时真正命中过的那些资源名。
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import reclassify

# 正例按真实布局写，但用自有游戏名，不复制个人库表。
ACE_DIR = ['Game.exe', 'anticheatexpert/ace-base.dat', 'anticheatexpert/ace-base.sys',
           'anticheatexpert/ace-core.sys', 'anticheatexpert/ace-service64.exe']
ACE_DRIVER_ONLY = ['Game.exe', 'bin/ace-base.sys']
PB_INSTALLER = ['Game.exe', 'installers/pb/pbsvc.exe']
PB_DIR = ['Game.exe', 'install/punkbuster/pbsvc_bc2.exe']

# 反例：这些都是普通资源，一个都不许判成反作弊。
INNOCENT = [
    ['Game.exe', 'art/icons/elf/techs/journey_vanguard.dds'],
    ['Game.exe', 'www/js/plugins/VanguardAndRearguard.js'],
    ['Game.exe', 'text/perks/perk_ricochet.txt'],
    ['Game.exe', 'sprites/inca-art1.cub'],
    ['Game.exe', 'dosbox/resources/freedos-keyboard/keyboard.sys'],
    ['Game.exe', 'squareenix/final fantasy xi/config.sys'],
    ['Game.exe', 'data/asset/d3d11/c_equ800.pkg'],
    ['Game.exe', 'c/asset/common/model/equ8000.mdl'],
    ['Game.exe', 'game/save games/default/icon.sys'],
    # 名字里带 pb 但不是 PunkBuster
    ['Game.exe', 'audio/pbass.ogg', 'tools/pbtool.exe'],
    # 目录名相近但不是 anticheatexpert
    ['Game.exe', 'anticheat_notes.txt', 'docs/ace-guide.pdf'],
]


class AntiCheatRuleTests(unittest.TestCase):
    def test_ace_and_punkbuster_are_refused(self):
        for name, files in (('ACE 完整目录', ACE_DIR), ('ACE 只有驱动', ACE_DRIVER_ONLY),
                            ('PunkBuster 安装器', PB_INSTALLER), ('PunkBuster 目录', PB_DIR)):
            with self.subTest(case=name):
                engine, route, confidence, evidence = reclassify.classify(files)
                self.assertEqual(route, 'refuse', f'{name} 应当拒绝，实得 {engine} / {route}')
                self.assertEqual(confidence, '确证')

    def test_ordinary_resources_are_not_mistaken_for_anti_cheat(self):
        for files in INNOCENT:
            with self.subTest(files=files[1:]):
                engine, route, _, evidence = reclassify.classify(files)
                self.assertNotEqual(route, 'refuse',
                                    f'{files[1:]} 被误判成 {engine}，判据 {evidence}')


if __name__ == '__main__':
    unittest.main()
