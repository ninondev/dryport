# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Electron 报告与写入边界回归；真实机制只在显式指定运行时后运行。"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('electron_probe', ROOT / 'tools/probe-electron.py')
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)


class ElectronProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sn-electron-safety-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.runtime = self.base / 'Electron.app'
        self.runtime.mkdir()
        (self.runtime / 'fixture.txt').write_text('never executed')
        self.archive = self.base / 'fixture.zip'
        self.archive.write_bytes(b'not an official runtime')
        self.output = self.base / 'evidence'

    def probe(self):
        return PROBE.probe(self.runtime, self.archive, PROBE.VERSION,
                           PROBE.ARCHIVE_SHA256, self.output)

    def simulated_run(self, args, *, env=None, **kwargs):
        if env is not None:
            receipt = {'ok': True, 'electron': PROBE.VERSION, 'arch': 'arm64',
                       'app_path': str(args[-1])}
            Path(env['SN_ELECTRON_RECEIPT']).write_text(json.dumps(receipt))
        return {'returncode': 0, 'timed_out': False, 'stdout': '', 'stderr': ''}

    def test_existing_directory_and_dangling_link_are_preserved(self):
        self.output.mkdir()
        (self.output / 'user.txt').write_text('keep')
        with self.assertRaises(FileExistsError):
            self.probe()
        self.assertEqual((self.output / 'user.txt').read_text(), 'keep')
        self.output = self.base / 'dangling-link'
        outside = self.base / 'not-created'
        self.output.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.probe()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(outside.exists())

    def test_repository_and_runtime_cannot_receive_evidence(self):
        for output in (ROOT / ('probe-must-not-create-' + self.base.name),
                       self.runtime / 'must-not-create'):
            with self.subTest(output=output):
                self.output = output
                with self.assertRaises(ValueError):
                    self.probe()
                self.assertFalse(output.exists())

    def test_unpinned_version_or_hash_cannot_start_processes(self):
        with patch.object(PROBE.SHARED, 'run_bounded') as run:
            for version, checksum in [('11.2.0', PROBE.ARCHIVE_SHA256),
                                      (PROBE.VERSION, '0' * 64)]:
                with self.subTest(version=version, checksum=checksum):
                    with self.assertRaises(ValueError):
                        PROBE.factory_check(self.runtime, self.archive, checksum, version)
            run.assert_not_called()

    def test_tampered_official_archive_fails_before_output_or_launch(self):
        with patch.object(PROBE.SHARED, 'run_bounded') as run:
            with self.assertRaisesRegex(ValueError, '哈希不符'):
                self.probe()
            run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_nonzero_exit_timeout_or_missing_receipt_never_succeed(self):
        for mode in ('nonzero-with-success-receipt', 'timeout', 'missing-receipt'):
            with self.subTest(mode=mode):
                self.output = self.base / mode
                def failed_run(args, *, env=None, **kwargs):
                    result = self.simulated_run(args, env=env, **kwargs)
                    if env is not None:
                        if mode.startswith('nonzero'):
                            result['returncode'] = -5
                        elif mode == 'timeout':
                            result['timed_out'] = True
                        else:
                            Path(env['SN_ELECTRON_RECEIPT']).unlink()
                        result['stderr'] = 'synthetic helper sandbox initialization failure'
                    return result
                with patch.object(PROBE, 'factory_check', return_value={}), \
                        patch.object(PROBE.SHARED, 'run_bounded', side_effect=failed_run):
                    report = self.probe()
                saved = json.loads((self.output / 'evidence.json').read_text())
                self.assertFalse(report['ok'])
                self.assertFalse(saved['ok'])
                self.assertTrue(saved['runtime_unchanged'])
                self.assertTrue(saved['cases'][0]['source_unchanged'])
                self.assertIn('sandbox initialization failure', saved['cases'][0]['runs'][0]['stderr'])

    def test_final_original_changes_override_success_receipts(self):
        inventory = PROBE.SHARED.inventory
        calls = {}
        def changed_at_final_read(path):
            path = Path(path)
            if path.name in ('OriginalGame', 'Fixture.app'):
                calls[path] = calls.get(path, 0) + 1
                if calls[path] == 3:
                    (path / 'protected.txt').write_text('fixture changed before final verification')
            return inventory(path)
        with patch.object(PROBE, 'factory_check', return_value={}), \
                patch.object(PROBE.SHARED, 'run_bounded', side_effect=self.simulated_run), \
                patch.object(PROBE.SHARED, 'inventory', side_effect=changed_at_final_read):
            report = self.probe()
        self.assertEqual([case['shape'] for case in report['cases']], ['directory', 'asar'])
        self.assertTrue(all(not case['source_unchanged'] and not case['bundle_unchanged']
                            for case in report['cases']))
        self.assertFalse(report['ok'])
        self.assertFalse(json.loads((self.output / 'evidence.json').read_text())['ok'])

    def test_runtime_changes_override_success_receipts(self):
        def changed_runtime(args, *, env=None, **kwargs):
            result = self.simulated_run(args, env=env, **kwargs)
            if env is not None:
                (self.runtime / 'fixture.txt').write_text('changed fixture runtime')
            return result
        with patch.object(PROBE, 'factory_check', return_value={}), \
                patch.object(PROBE.SHARED, 'run_bounded', side_effect=changed_runtime):
            report = self.probe()
        self.assertFalse(report['runtime_unchanged'])
        self.assertFalse(report['ok'])


@unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('ELECTRON_TEST_APP')
                     and os.environ.get('ELECTRON_TEST_ARCHIVE'),
                     '需显式设置 ELECTRON_TEST_APP 和 ELECTRON_TEST_ARCHIVE；不自动下载或运行真实游戏')
class ElectronMechanismTests(unittest.TestCase):
    def test_factory_runtime_completes_both_original_containers_and_restarts(self):
        # 当前机器已实测 GPU helper 的 Seatbelt 初始化失败；保留真实断言，不能把失败改成通过。
        output = Path(tempfile.mkdtemp(prefix='sn-electron-regression-')) / 'evidence'
        print('Electron evidence: ' + str(output / 'evidence.json'), file=sys.stderr)
        report = PROBE.probe(os.environ['ELECTRON_TEST_APP'], os.environ['ELECTRON_TEST_ARCHIVE'],
                             PROBE.VERSION, PROBE.ARCHIVE_SHA256, output)
        self.assertTrue(report['ok'], report.get('error'))
        self.assertEqual([case['shape'] for case in report['cases']], ['directory', 'asar'])
        self.assertTrue(report['runtime_unchanged'])
        for case in report['cases']:
            self.assertTrue(case['source_unchanged'])
            self.assertTrue(case['bundle_unchanged'])
            self.assertEqual(len(case['runs']), 2)
            for number, run in enumerate(case['runs'], 1):
                receipt = run['receipt']
                self.assertEqual(receipt['round'], number)
                self.assertEqual(receipt['home_save_readback'], str(number))
                self.assertEqual(receipt['renderer']['after'], str(number))
                self.assertTrue(all(value == 'EPERM' for value in receipt['attempts'].values()))
                self.assertEqual(len(receipt['attempts']), 10)


if __name__ == '__main__':
    unittest.main()
