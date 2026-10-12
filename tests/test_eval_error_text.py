# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""求值异常文案试验：用户可见的报错只保留首行消息，不带 V8 堆栈帧。"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import fs from 'node:fs';
import {evalErrorText} from './lib/steam-library.mjs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify({text: evalErrorText(input.details, input.fallback)}));
'''


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class EvalErrorTextTests(unittest.TestCase):
    def text(self, details, fallback):
        result = subprocess.run(['node', '--input-type=module', '-e', RUNNER],
                                input=json.dumps(dict(details=details, fallback=fallback)),
                                text=True, capture_output=True, cwd=ROOT, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['text']

    def test_description_keeps_message_and_drops_stack_frames(self):
        description = ('Error: Steam 尚未登录或游戏库尚未加载；请打开库页面后重试\n'
                       '    at <anonymous>:7:11\n    at <anonymous>:9:3\n    at <anonymous>:61:5')
        self.assertEqual(
            self.text({'exception': {'description': description}}, 'Steam 库读取失败'),
            'Steam 尚未登录或游戏库尚未加载；请打开库页面后重试')

    def test_typed_error_prefix_is_stripped_too(self):
        self.assertEqual(
            self.text({'exception': {'description': 'TypeError: x\n    at <anonymous>:1:1'}}, '备用'), 'x')

    def test_plain_text_details_win_without_exception(self):
        self.assertEqual(self.text({'text': 'fixture command rejected'}, '备用'), 'fixture command rejected')

    def test_missing_details_fall_back(self):
        self.assertEqual(self.text(None, 'Steam 库读取失败'), 'Steam 库读取失败')
        self.assertEqual(self.text({}, '备用'), '备用')

    def test_multiline_message_without_stack_keeps_first_line(self):
        self.assertEqual(self.text({'exception': {'description': '第一行原因\n第二行补充'}}, '备用'), '第一行原因')


if __name__ == '__main__':
    unittest.main()
