# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自动安装必须拿到基础游戏完整回执；单 depot 成功不代表游戏装齐。"""
import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallDeliveryTests(unittest.TestCase):
    def test_displayed_shell_argument_preserves_utf8_and_literal_metacharacters(self):
        value = "中文 path's $value `command` \\name\nsecond line"
        result = subprocess.run(['/bin/bash', '-c',
                                 '. "$1"; sn_quote_argument "$2"',
                                 'test', str(ROOT / 'lib/paths.sh'), value],
                                capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(shlex.split(result.stdout), [value])
        restored = subprocess.run(['/bin/bash', '-c', 'printf "%s" ' + result.stdout],
                                  capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(restored.stdout, value)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-install-receipt-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        binary = self.base / "bin"
        binary.mkdir()
        node = binary / "node"
        node.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_DELIVERY_RECEIPT"\nexit "${TEST_DELIVERY_RC:-0}"\n')
        node.chmod(0o755)
        self.env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ['PATH'],
                    "SN_HOME": str(ROOT), "SN_GAMES": str(self.base / "games")}

    def invoke(self, receipt, rc=0):
        env = {**self.env, "TEST_DELIVERY_RECEIPT": json.dumps(receipt), "TEST_DELIVERY_RC": str(rc)}
        return subprocess.run(['/bin/bash', '-c',
                               '. "$1" __test_delivery__ >/dev/null; verify_game_delivery 42 "$2"',
                               'test', str(ROOT / 'bin/dryport'), str(self.base / 'game')],
                              env=env, capture_output=True, text=True, timeout=10)

    def invoke_stop_hint(self, rc):
        return subprocess.run(['/bin/bash', '-c',
                               '. "$1" __test_hint__ >/dev/null; download_stop_hint "$2" 424242',
                               'test', str(ROOT / 'bin/dryport'), str(rc)],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_stop_hint_matches_downloader_terminal_codes(self):
        expectations = {
            4: '      停在第 2/5 步（下载）：磁盘空间不够，上面是原因。腾出空间后重跑：dryport install 424242',
            5: '      停在第 2/5 步（下载）：Steam 没准备好，上面是原因。打开 Steam 并登录后重跑：dryport install 424242',
            6: '      停在第 2/5 步（下载）：网络断了，已下的部分留着。联网后重跑：dryport install 424242',
        }
        for rc, expected in expectations.items():
            with self.subTest(rc=rc):
                result = self.invoke_stop_hint(rc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected + '\n')

    def test_stop_hint_is_silent_for_unrecognized_code(self):
        result = self.invoke_stop_hint(1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')

    def test_verified_base_game_is_accepted(self):
        receipt = dict(protocol=1, appid='42', complete=True, scope='base_game')
        self.assertEqual(self.invoke(receipt).returncode, 0)

    def test_partial_wrong_identity_or_failed_verifier_is_rejected(self):
        receipt = dict(protocol=1, appid='42', complete=True, scope='base_game')
        for change in ({'scope': 'explicit_depot'}, {'appid': '43'}, {'complete': False}, {'protocol': True}):
            with self.subTest(change=change):
                result = self.invoke({**receipt, **change})
                self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.invoke(receipt, rc=1).returncode, 0)
        self.assertFalse((self.base / 'games').exists())

    def test_detect_love_candidate_does_not_execute_configuration(self):
        game = self.base / 'source'
        game.mkdir()
        (game / 'main.lua').write_text('error("not executed")')
        (game / 'conf.lua').write_text('error("not executed")')
        result = subprocess.run([str(ROOT / 'bin/dryport'), 'detect', str(game)],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LÖVE 候选', result.stdout)
        self.assertIn('真实游戏待验', result.stdout)

    def detect(self, game):
        return subprocess.run([str(ROOT / 'bin/dryport'), 'detect', str(game)],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_detect_package_nw_zip_is_nwjs(self):
        game = self.base / 'nw-packaged'
        game.mkdir()
        (game / 'fixture-game.exe').write_text('')
        (game / 'nw.pak').write_text('')
        (game / 'package.nw').write_text('')
        result = self.detect(game)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NW.js（package.nw 打包）', result.stdout)
        self.assertIn('原生 arm64', result.stdout)

    def test_detect_renamed_executable_without_package_nw_is_not_nwjs(self):
        game = self.base / 'nw-no-pack'
        game.mkdir()
        (game / 'fixture-game.exe').write_text('')
        (game / 'nw.pak').write_text('')
        result = self.detect(game)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('未识别', result.stdout)
        self.assertNotIn('NW.js', result.stdout)

    def test_detect_package_json_still_reports_rpg_maker(self):
        game = self.base / 'nw-package-json'
        game.mkdir()
        (game / 'package.json').write_text('{}')
        result = self.detect(game)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RPG Maker MV/MZ (NW.js)', result.stdout)


if __name__ == '__main__':
    unittest.main()
