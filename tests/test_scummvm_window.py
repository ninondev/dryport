# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自有目录与模拟子进程；不启动 ScummVM、GUI 或真实偏好域操作。"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scummvm_window", ROOT / "tools/probe-scummvm-window.py")
W = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W)
P = W.P


def process_result(stdout="", **changes):
    result = {"pid": 1000000000, "returncode": 0, "timed_out": False,
              "output_limit_reached": False, "stdout": stdout, "stderr": ""}
    result.update(changes)
    return result


def detected(game):
    return ("GameID Description Full Path\n"
            "------------------------------ ---------------------------------------------------------- ----------------\n"
            + "agi:lsl1".ljust(30) + " " + "Owned fixture (DOS/English)".ljust(58) + " " + str(game) + "\n")


class ScummVMWindowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-scummvm-window-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.runtime = self.base / "Owned.app"
        (self.runtime / "Contents/MacOS").mkdir(parents=True)
        self.binary = self.runtime / "Contents/MacOS/scummvm"
        self.binary.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000c) + b"\0" * 24)
        self.binary.chmod(0o755)
        self.domain = "io.dryport.prefprobe." + "a" * 32
        (self.runtime / "Contents/Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": self.domain, "CFBundleShortVersionString": P.VERSION}))
        self.game = self.base / "Original game"
        self.game.mkdir()
        (self.game / "VOL.0").write_bytes(b"owned original")
        self.archive = self.base / "owned-not-official.dmg"
        self.archive.write_bytes(b"mock archive; no runtime download")

    def test_helper_denies_only_exact_preferences_services_and_has_minimal_environment(self):
        state = self.base / "state"
        state.mkdir()
        policy = W.RUNTIME.preferences_guard(state, [self.game])
        self.assertIn('(deny network*)', policy[-1])
        self.assertIn('(deny mach-lookup (global-name "com.apple.cfprefsd.agent") '
                      '(global-name "com.apple.cfprefsd.daemon"))', policy[-1])
        self.assertIn('(deny file-link)', policy[-1])
        self.assertIn('(deny file-write-unlink ', policy[-1])
        with patch.dict(os.environ, {"HOME": "/wrong", "DYLD_INSERT_LIBRARIES": "bad",
                                     "SDL_VIDEODRIVER": "bad", "CFFIXED_USER_HOME": "/wrong"}):
            headless = W.RUNTIME.environment(state)
            window = W.RUNTIME.environment(state, headless=False)
        self.assertEqual(headless['HOME'], str(state / "home"))
        self.assertEqual(headless['CFFIXED_USER_HOME'], headless['HOME'])
        self.assertEqual(headless['SDL_VIDEODRIVER'], 'dummy')
        self.assertEqual(headless['SDL_AUDIODRIVER'], 'dummy')
        self.assertNotIn('SDL_VIDEODRIVER', window)
        self.assertNotIn('SDL_AUDIODRIVER', window)
        self.assertNotIn('DYLD_INSERT_LIBRARIES', window)

    def test_physical_and_cf_snapshots_preserve_nonempty_existing_owned_domain(self):
        home = self.base / "host-home"
        prefs = home / "Library/Preferences" / (self.domain + ".plist")
        prefs.parent.mkdir(parents=True)
        value = {"ExistingOwnedKey": "keep", "OwnedData": b"sample"}
        prefs.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_BINARY))
        raw = prefs.read_bytes()
        logs = self.base / "logs"
        logs.mkdir()
        export = process_result(plistlib.dumps(value).decode())
        with patch.object(P, "run_owned", return_value=export) as run:
            first = W.preferences_snapshot(self.domain, home, logs, "before")
            second = W.preferences_snapshot(self.domain, home, logs, "after")
        self.assertTrue(W.preferences_equal(first, second))
        self.assertEqual(first['key_count'], 2)
        self.assertEqual(first['physical_path'], str(prefs))
        self.assertEqual(prefs.read_bytes(), raw)
        for call in run.call_args_list:
            self.assertEqual(call.args[0], ["/usr/bin/defaults", "export", self.domain, "-"])
            self.assertNotIn("CFFIXED_USER_HOME", call.kwargs['env'])

    def test_export_errors_do_not_turn_into_empty_domain(self):
        logs = self.base / "logs"
        logs.mkdir()
        for changes in ({"returncode": 2, "stderr": "failed"}, {"timed_out": True},
                        {"stdout": "not a plist"}, {"output_limit_reached": True}):
            with self.subTest(changes=changes), patch.object(P, "run_owned", return_value=process_result(**changes)):
                with self.assertRaises(ValueError):
                    W.preferences_snapshot(self.domain, self.base, logs, "bad")
        with patch.object(P, "run_owned", return_value=process_result(returncode=1, stderr="Domain does not exist")):
            result = W.preferences_snapshot(self.domain, self.base, logs, "absent")
        self.assertFalse(result['export_present'])
        self.assertIsNone(result['physical'])

    def test_snapshot_refuses_path_change_symlink_or_invalid_domain_without_cleanup(self):
        logs = self.base / "logs"
        logs.mkdir()
        prefs = self.base / "Library/Preferences" / (self.domain + ".plist")
        prefs.parent.mkdir(parents=True)
        prefs.write_bytes(plistlib.dumps({"Owned": "before"}))
        def changed(*args, **kwargs):
            prefs.write_bytes(plistlib.dumps({"Owned": "after"}))
            return process_result(plistlib.dumps({"Owned": "after"}).decode())
        with patch.object(P, "run_owned", side_effect=changed), self.assertRaisesRegex(ValueError, "已变化"):
            W.preferences_snapshot(self.domain, self.base, logs, "changed")
        self.assertEqual(plistlib.loads(prefs.read_bytes()), {"Owned": "after"})
        link = self.base / "owned-link"
        link.symlink_to(prefs)
        with self.assertRaises(ValueError):
            W.physical_preferences(link)
        with patch.object(P, "run_owned", side_effect=AssertionError("must not execute")), self.assertRaises(ValueError):
            W.preferences_snapshot("../../foreign", self.base, logs, "invalid")

    def mock_probe(self, name, *, runs=2, game_changes=None, detect_output=None,
                   deny_canary=False, mutate_game=False, changed_preferences=False, final_query_error=False):
        calls = []
        baseline = {"domain": self.domain, "physical_path": "/owned/actual-domain.plist",
                    "physical": {"bytes": 80, "sha256": "existing-owned-value"},
                    "export_present": True, "value_sha256": "nonempty-existing-owned-value", "key_count": 1}
        def snapshot(domain, home, logs, stage):
            self.assertEqual(domain, self.domain)
            if final_query_error and stage == "preferences-final":
                raise ValueError("owned final query failed")
            result = copy.deepcopy(baseline)
            if changed_preferences and any(call[0] == 'game' for call in calls):
                result['value_sha256'] = 'changed'
            return result
        def run(args, **kwargs):
            kind = kwargs['name']
            calls.append((kind, args, kwargs))
            if kind == "write-preflight":
                return process_result(json.dumps({"write_denied": not deny_canary}))
            if kind == "detect":
                return process_result(detected(self.game) if detect_output is None else detect_output)
            self.assertEqual(kind, 'game')
            index = sum(call[0] == 'game' for call in calls)
            kwargs['on_start'](1000000000 + index)
            state = kwargs['cwd']
            if index == 1:
                with (state / 'scummvm.ini').open('a') as config:
                    config.write('owned_saved_setting=preserved\n')
                (state / 'saves/owned-save').write_bytes(b'owned saved bytes')
            else:
                self.assertIn('owned_saved_setting=preserved', (state / 'scummvm.ini').read_text())
                self.assertEqual((state / 'saves/owned-save').read_bytes(), b'owned saved bytes')
            if mutate_game:
                (self.game / 'VOL.0').write_bytes(b'changed owned fixture')
            return process_result(**(game_changes or {}))
        output = self.base / name
        with patch.object(P, 'verify_factory', return_value={'bundle_matches_archive': True}), \
                patch.object(P, 'run_owned', side_effect=run), \
                patch.object(W, 'preferences_snapshot', side_effect=snapshot), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = W.probe(self.runtime, self.archive, self.game, output, runs=runs)
        return result, calls, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_two_rounds_share_state_keep_settings_and_emit_start_pid(self):
        result, calls, events = self.mock_probe('two-rounds')
        self.assertTrue(result['ok'])
        self.assertFalse(result['manual_acceptance'])
        self.assertEqual(result['detection_helper_sha256'], P.sha256(Path(P.__file__)))
        self.assertEqual(result['shared_runtime_sha256'], P.sha256(ROOT / 'lib/love-runtime.py'))
        self.assertEqual(result['window_environment']['CFFIXED_USER_HOME'], result['window_environment']['HOME'])
        self.assertEqual(result['headless_environment']['SDL_VIDEODRIVER'], 'dummy')
        games = [call for call in calls if call[0] == 'game']
        self.assertEqual(len(games), 2)
        self.assertIn('--window-size=800,600', games[0][1])
        self.assertFalse(any(arg.startswith('--window-size') for arg in games[1][1]))
        self.assertEqual(games[0][2]['cwd'], games[1][2]['cwd'])
        for call in games:
            self.assertIn('--no-fullscreen', call[1])
            self.assertEqual(call[1][-1], 'agi:lsl1')
            self.assertNotIn('SDL_VIDEODRIVER', call[2]['env'])
            config = (call[2]['cwd'] / 'scummvm.ini').read_text()
            for value in ['fullscreen=false', 'updates_check=0', 'macos_savepath_migrated=true']:
                self.assertIn(value, config)
        self.assertEqual([e['round'] for e in events], [1, 2])
        self.assertEqual([e['pid'] for e in events], [1000000001, 1000000002])
        self.assertTrue(result['preferences_unchanged'])

    def test_timeout_failed_exit_or_output_limit_stops_without_second_window(self):
        for index, changes in enumerate([{'returncode': 2}, {'timed_out': True}, {'output_limit_reached': True}]):
            with self.subTest(changes=changes):
                result, calls, _ = self.mock_probe('failed-'+str(index), game_changes=changes)
                self.assertFalse(result['ok'])
                self.assertEqual(sum(call[0] == 'game' for call in calls), 1)
                self.assertFalse(result['rounds'][0]['ok'])

    def test_failed_detection_or_write_canary_prevents_window(self):
        for index, changes in enumerate([{'detect_output': ''}, {'deny_canary': True}]):
            with self.subTest(changes=changes):
                result, calls, _ = self.mock_probe('no-window-'+str(index), **changes)
                self.assertFalse(result['ok'])
                self.assertFalse(any(call[0] == 'game' for call in calls))

    def test_original_change_stops_reopen_and_overrides_clean_exit(self):
        result, calls, _ = self.mock_probe('changed-original', mutate_game=True)
        self.assertFalse(result['ok'])
        self.assertFalse(result['integrity_final']['game']['unchanged'])
        self.assertEqual(sum(call[0] == 'game' for call in calls), 1)

    def test_actual_domain_change_stops_without_erasing_or_reopening(self):
        result, calls, _ = self.mock_probe('changed-preferences', changed_preferences=True)
        self.assertFalse(result['ok'])
        self.assertFalse(result['preferences_unchanged'])
        self.assertEqual(sum(call[0] == 'game' for call in calls), 1)
        self.assertFalse(any('delete' in call[1] for call in calls))

    def test_final_query_failure_overrides_success(self):
        result, _, _ = self.mock_probe('final-query-failed', final_query_error=True)
        self.assertFalse(result['ok'])
        self.assertIn('owned final query failed', result['preferences_error'])

    def test_bad_factory_does_not_read_host_preferences_or_launch(self):
        with patch.object(W, 'preferences_snapshot', side_effect=AssertionError('must not query')), \
                patch.object(P, 'run_owned', side_effect=AssertionError('must not execute')):
            result = W.probe(self.runtime, self.archive, self.game, self.base / 'bad-factory')
        self.assertFalse(result['ok'])
        self.assertIn('SHA-256', result['error'])

    def test_existing_output_and_invalid_bounds_do_not_start(self):
        existing = self.base / 'existing'
        existing.mkdir()
        (existing / 'keep').write_bytes(b'owned keep')
        with patch.object(P, 'run_owned', side_effect=AssertionError('must not execute')):
            with self.assertRaises(FileExistsError):
                W.probe(self.runtime, self.archive, self.game, existing)
            for timeout, runs in [(9, 1), (181, 1), (60, 0), (60, 3), (float('nan'), 1), (60, True)]:
                with self.subTest(timeout=timeout, runs=runs), self.assertRaises(ValueError):
                    W.probe(self.runtime, self.archive, self.game, self.base / 'invalid', timeout=timeout, runs=runs)
        self.assertEqual((existing / 'keep').read_bytes(), b'owned keep')

    def test_main_reports_failed_status(self):
        args = ['--runtime-app', str(self.runtime), '--archive', str(self.archive),
                '--game', str(self.game), '--output', str(self.base / 'cli')]
        with patch.object(W, 'probe', return_value={'ok': False, 'output': str(self.base / 'cli')}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(W.main(args), 1)
        self.assertFalse(json.loads(output.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
