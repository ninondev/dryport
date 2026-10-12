# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""磁盘空间核算:下载前预算、Steam 输出定性、可用空间读取。纯模块,不碰 Steam 与网络。"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import fs from 'node:fs';
import {budget, gb, budgetMessage, classifySpew, freeBytes, sameVolume, RESERVE_MIN, DEST_OVERHEAD}
  from './lib/disk-budget.mjs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
try {
  let result;
  if (input.op === 'budget') result = budget(input.args);
  if (input.op === 'gb') result = gb(input.bytes);
  if (input.op === 'message') result = budgetMessage(input.result);
  if (input.op === 'classify') result = classifySpew(input.text);
  if (input.op === 'free') result = freeBytes(input.path);
  if (input.op === 'same') result = sameVolume(input.a, input.b);
  if (input.op === 'constants') result = {RESERVE_MIN, DEST_OVERHEAD};
  console.log(JSON.stringify({ok:true, result}));
} catch (error) { console.log(JSON.stringify({ok:false, error:error.message})); }
'''

MB = 1024 ** 2
GB = 1024 ** 2 * 1024
RESERVE_MIN = 512 * MB
DEST_OVERHEAD = 64 * MB
TOTAL = GB + 512 * MB            # 1.5 GB 的内容计划
# ceil(1610612736 * 0.05) = 80530637 < RESERVE_MIN,所以 5% 余量按 RESERVE_MIN 兜底。
NEED_STAGING = TOTAL + RESERVE_MIN
NEED_DEST_SAME = DEST_OVERHEAD
NEED_DEST_SPLIT = TOTAL + DEST_OVERHEAD


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class DiskBudgetTests(unittest.TestCase):
    def run_js(self, **data):
        result = subprocess.run(['node','--input-type=module','-e',RUNNER], input=json.dumps(data),
                                text=True,capture_output=True,cwd=ROOT,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def budget(self, sizes, staged=None, free_staging=10*GB, free_dest=10*GB, same=True):
        selected = [dict(depot=str(43 + 2*i), size=size) for i, size in enumerate(sizes)]
        result = self.run_js(op='budget', args=dict(selected=selected, stagedBytes=staged or {},
            freeStaging=free_staging, freeDest=free_dest, sameVolume=same))
        self.assertTrue(result['ok'], result)
        return result['result']

    def test_constants_are_pinned(self):
        constants = self.run_js(op='constants')['result']
        self.assertEqual(constants['RESERVE_MIN'], 512 * MB)
        self.assertEqual(constants['DEST_OVERHEAD'], 64 * MB)

    def test_enough_space_on_one_volume(self):
        result = self.budget([GB, 512*MB])
        self.assertTrue(result['known'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['total'], TOTAL)
        self.assertEqual(result['needStaging'], NEED_STAGING)
        self.assertEqual(result['needDest'], NEED_DEST_SAME)
        self.assertEqual(result['shortBytes'], 0)

    def test_not_enough_on_one_volume_reports_exact_shortfall(self):
        result = self.budget([GB, 512*MB], free_staging=GB, free_dest=GB)
        self.assertFalse(result['ok'])
        self.assertEqual(result['shortBytes'], NEED_STAGING + NEED_DEST_SAME - GB)

    def test_different_volumes_check_each_pool(self):
        # 交付池差多少:暂存 10 GB 够用,交付池只有 100 MB。
        result = self.budget([GB, 512*MB], free_staging=10*GB, free_dest=100*MB, same=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['needDest'], NEED_DEST_SPLIT)
        self.assertEqual(result['shortBytes'], NEED_DEST_SPLIT - 100*MB)
        # 暂存池差多少:两个池各报各的,shortBytes 取更大的那笔缺口。
        result = self.budget([GB, 512*MB], free_staging=3*GB//2, free_dest=10*GB, same=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['shortBytes'], NEED_STAGING - 3*GB//2)

    def test_unknown_size_does_not_block(self):
        for size in [None, 'big', -1]:
            with self.subTest(size=size):
                result = self.budget([GB, size])
                self.assertFalse(result['known'])
                self.assertTrue(result['ok'])
                for field in ['total','needStaging','needDest','shortBytes']:
                    self.assertIsNone(result[field])

    def test_staged_bytes_reduce_needStaging(self):
        self.assertEqual(self.budget([GB, 512*MB])['needStaging'], NEED_STAGING)
        result = self.budget([GB, 512*MB], staged={'43': 256*MB})
        self.assertEqual(result['needStaging'], (GB - 256*MB) + 512*MB + RESERVE_MIN)

    def test_gb_formatting(self):
        for value, text in [(0,'0.0 GB'), (512*MB,'0.5 GB'), (1610612736,'1.5 GB'), (10*GB,'10.0 GB')]:
            self.assertEqual(self.run_js(op='gb', bytes=value)['result'], text)

    def test_budget_message_not_ok_carries_the_real_numbers(self):
        tight = self.budget([GB, 512*MB], free_staging=GB, free_dest=GB)
        message = self.run_js(op='message', result=tight)['result']
        self.assertTrue(message.startswith('磁盘空间不够，未开始下载：'), message)
        self.assertIn('这次要 2.1 GB 的空间（暂存 2.0 GB + 交付 0.1 GB）', message)
        self.assertIn('可用只有 1.0 GB', message)
        self.assertIn('还差 1.1 GB', message)
        self.assertTrue(message.endswith('腾出空间后重跑同一条命令。'), message)
        split = self.budget([GB, 512*MB], free_staging=10*GB, free_dest=100*MB, same=False)
        message = self.run_js(op='message', result=split)['result']
        self.assertIn('可用只有 暂存盘 10.0 GB / 交付盘 0.1 GB', message)
        self.assertIn('还差 1.5 GB', message)

    def test_budget_message_ok_and_unknown_branches(self):
        roomy = self.budget([GB, 512*MB])
        self.assertEqual(self.run_js(op='message', result=roomy)['result'],
                         '空间核算：这次要 2.1 GB，可用 10.0 GB，够用。')
        unknown = self.budget([None])
        self.assertEqual(self.run_js(op='message', result=unknown)['result'],
                         '有内容包没有标大小，无法事前核算空间，照常下载。')

    def test_classify_spew(self):
        cases = [('ERROR: Disk write failure while saving chunk','disk_full'),
                 ('Update aborted: not enough disk space on volume','disk_full'),
                 ('extractchunk: No space left on device','disk_full'),
                 ('ERROR: disk full','disk_full'),
                 ('Download failed (10).','failed'),
                 ('no subscription for depot 43','failed'),
                 ('ERROR! license expired','failed'),
                 ('Depot download complete : 43 "app_42/depot_43"',None)]
        for text, verdict in cases:
            with self.subTest(text=text):
                self.assertEqual(self.run_js(op='classify', text=text)['result'], verdict)

    def test_free_bytes_of_temp_dir_and_missing_child(self):
        with tempfile.TemporaryDirectory() as td:
            free = self.run_js(op='free', path=td)['result']
            self.assertIsInstance(free, int)
            self.assertGreater(free, 0)
            # 两次读数分属两个进程，这台机器上别的任务在写盘，逐字节相等会偶发失败；同一卷的差应远小于 64 MB。
            self.assertAlmostEqual(self.run_js(op='free', path=os.path.join(td,'not-created','deep'))['result'], free,
                                   delta=64 * MB)
            self.assertTrue(self.run_js(op='same', a=td, b=os.path.join(td,'not-created'))['result'])


if __name__ == '__main__':
    unittest.main()
