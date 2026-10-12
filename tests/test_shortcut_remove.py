# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""在自有 Node VM 中执行生产删除表达式；不连接 Steam、不写快捷方式。"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIRST, SECOND, OTHER = 0xF0000001, 0xF0000002, 0xF0000003
EXE = '/fixture games/Owned Game.app'

HARNESS = r'''
import vm from 'node:vm';
import {removalExpression} from './lib/steam-shortcut-remove.mjs';
const scenario = JSON.parse(process.argv[1]);
const trace = {removed: [], registered: [], unregistered: [], timerCallbacks: 0};
let account = scenario.account ?? '42';
let apps = scenario.apps.map(a => ({...a}));
let clock = 0;
const timerHandles = new Set();
const timers = {
  setTimeout(callback, ms) {
    const handle = setTimeout(() => {
      timerHandles.delete(handle); clock += ms; trace.timerCallbacks++; callback();
    }, 0);
    timerHandles.add(handle); return handle;
  },
  clearTimeout(handle) { clearTimeout(handle); timerHandles.delete(handle); },
};
const overview = a => a ? {appid: a.appid, BIsShortcut: () => a.shortcut !== false} : null;
const appStore = {
  m_bIsInitialized: true,
  m_cm: {logged_on: true, steamid: {
    BIsIndividualAccount: () => true, BIsValid: () => true,
    GetAccountID: () => Number(account),
    ConvertTo64BitString: () => (76561197960265728n + BigInt(account)).toString(),
  }},
  get allApps() { return apps.map(overview); },
  GetAppOverviewByAppID(id) {
    if (scenario.mode === 'changed-kind') return {appid: id, BIsShortcut: () => false};
    return overview(apps.find(a => a.appid === id));
  },
};
const Apps = {
  RegisterForAppDetails(id, callback) {
    trace.registered.push(id);
    const deliver = () => {
      if (scenario.mode === 'details-timeout') return;
      const a = apps.find(a => a.appid === id);
      if (scenario.mode === 'account-during-details') account = '43';
      callback({unAppID: scenario.mode === 'wrong-detail-id' ? id + 1 : id,
        strShortcutExe: scenario.mode === 'missing-detail-path' ? undefined : a?.exe});
    };
    if (scenario.mode === 'synchronous-details') deliver();
    else queueMicrotask(deliver);
    return {unregister() { trace.unregistered.push(id); }};
  },
  RemoveShortcut(id) {
    trace.removed.push(id);
    if (scenario.mode === 'remove-throws') throw new Error('fixture native removal failed');
    const remove = () => { apps = apps.filter(a => a.appid !== id); };
    if (scenario.mode === 'delayed-removal') queueMicrotask(remove);
    else if (scenario.mode !== 'noop') remove();
    if (scenario.mode === 'account-after-first') account = '43';
    if (scenario.mode === 'remove-unrelated') apps = apps.filter(a => a.appid !== scenario.other);
    // 本机官方接口没有可靠返回值；此处刻意不返回成功值。
  },
};
if (scenario.mode === 'missing-remove') delete Apps.RemoveShortcut;
let result;
try {
  const expression = removalExpression(scenario.expected);
  const context = vm.createContext({appStore, SteamClient: {Apps}, ...timers,
    Date: class extends Date { static now() { return clock; } }});
  const value = await vm.runInContext(expression, context, {timeout: 1000});
  result = {ok: true, value};
} catch (error) {
  result = {ok: false, error: String(error.message)};
} finally {
  for (const handle of timerHandles) clearTimeout(handle);
}
console.log(JSON.stringify({...result, ...trace, remaining: apps}));
'''


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class ShortcutRemovalTests(unittest.TestCase):
    def scenario(self, mode='success'):
        return {
            'mode': mode, 'other': OTHER,
            'expected': {'uid': '42', 'exe': EXE, 'ids': [FIRST, SECOND]},
            'apps': [
                {'appid': FIRST, 'exe': EXE, 'name': 'own first'},
                {'appid': SECOND, 'exe': '"' + EXE + '"', 'name': 'own second'},
                {'appid': OTHER, 'exe': '/fixture games/Other.app', 'name': 'keep all fields'},
            ],
        }

    def execute(self, scenario):
        proc = subprocess.run(
            ['node', '--input-type=module', '-e', HARNESS, json.dumps(scenario)],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertCountEqual(result['registered'], result['unregistered'], result)
        return result

    def assert_rejected_without_removal(self, scenario):
        result = self.execute(scenario)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['removed'], [], result)
        self.assertEqual(result['remaining'], scenario['apps'], result)
        return result

    def test_exact_ids_and_quoted_paths_remove_only_targets(self):
        scenario = self.scenario()
        result = self.execute(scenario)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['removed'], [FIRST, SECOND])
        self.assertEqual(result['remaining'], [scenario['apps'][2]])
        self.assertEqual(result['value']['removed_ids'], [FIRST, SECOND])
        self.assertIs(result['value']['client_verified'], True)
        self.assertEqual(result['value']['uid'], '42')

    def test_disk_id_order_does_not_change_exact_set_requirement(self):
        scenario = self.scenario()
        scenario['expected']['ids'].reverse()
        result = self.execute(scenario)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['removed'], [SECOND, FIRST])
        self.assertEqual(result['remaining'], [scenario['apps'][2]])

    def test_empty_matching_set_is_a_verified_noop(self):
        scenario = self.scenario()
        scenario['apps'] = [scenario['apps'][2]]
        scenario['expected']['ids'] = []
        result = self.execute(scenario)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['removed'], [])
        self.assertEqual(result['remaining'], scenario['apps'])

    def test_wrong_or_missing_target_ids_are_not_removed(self):
        for ids in ([FIRST, OTHER], [FIRST, SECOND + 99], []):
            with self.subTest(ids=ids):
                scenario = self.scenario()
                scenario['expected']['ids'] = ids
                self.assert_rejected_without_removal(scenario)

    def test_extra_live_target_missing_from_disk_receipt_is_rejected(self):
        scenario = self.scenario()
        scenario['expected']['ids'] = [FIRST]
        self.assert_rejected_without_removal(scenario)

    def test_nonshortcut_target_is_rejected(self):
        scenario = self.scenario()
        scenario['apps'][0]['shortcut'] = False
        self.assert_rejected_without_removal(scenario)

    def test_changed_kind_at_removal_is_rejected(self):
        self.assert_rejected_without_removal(self.scenario('changed-kind'))

    def test_path_mismatch_does_not_follow_prefix_or_extra_quotes(self):
        for path in (EXE + '/Other', EXE + '-copy', '""' + EXE + '""', EXE + '/'):
            with self.subTest(path=path):
                scenario = self.scenario()
                scenario['apps'][0]['exe'] = path
                self.assert_rejected_without_removal(scenario)

    def test_wrong_account_is_rejected_before_detail_registration(self):
        scenario = self.scenario()
        scenario['account'] = '43'
        result = self.assert_rejected_without_removal(scenario)
        self.assertEqual(result['registered'], [])

    def test_account_switch_during_details_is_rejected_before_any_removal(self):
        self.assert_rejected_without_removal(self.scenario('account-during-details'))

    def test_account_switch_after_first_removal_stops_remaining_targets(self):
        scenario = self.scenario('account-after-first')
        result = self.execute(scenario)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['removed'], [FIRST])
        self.assertEqual(result['remaining'], scenario['apps'][1:])

    def test_undefined_return_requires_actual_appstore_removal(self):
        scenario = self.scenario('noop')
        result = self.execute(scenario)
        self.assertFalse(result['ok'], result)
        self.assertIn('尚未确认', result['error'])
        self.assertEqual(result['removed'], [FIRST, SECOND])
        self.assertEqual(result['remaining'], scenario['apps'])
        self.assertGreater(result['timerCallbacks'], 0)

    def test_delayed_actual_removal_after_void_return_can_succeed(self):
        scenario = self.scenario('delayed-removal')
        result = self.execute(scenario)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['remaining'], [scenario['apps'][2]])
        self.assertGreater(result['timerCallbacks'], 0)

    def test_loss_of_unrelated_shortcut_is_not_reported_as_success(self):
        result = self.execute(self.scenario('remove-unrelated'))
        self.assertFalse(result['ok'], result)
        self.assertIn('尚未确认', result['error'])

    def test_detail_failure_always_unregisters_before_any_removal(self):
        for mode in ('details-timeout', 'wrong-detail-id', 'missing-detail-path'):
            with self.subTest(mode=mode):
                self.assert_rejected_without_removal(self.scenario(mode))

    def test_synchronous_detail_callback_still_unregisters(self):
        result = self.execute(self.scenario('synchronous-details'))
        self.assertTrue(result['ok'], result)
        self.assertCountEqual(result['unregistered'], [FIRST, SECOND, OTHER])

    def test_native_error_or_missing_method_does_not_fake_success(self):
        result = self.execute(self.scenario('remove-throws'))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['removed'], [FIRST])
        self.assertEqual(result['remaining'], self.scenario()['apps'])
        self.assert_rejected_without_removal(self.scenario('missing-remove'))

    def test_invalid_receipt_rejected_without_entering_client(self):
        for update in ({'uid': 'invalid'}, {'exe': 'relative.app'}, {'exe': '/bad\npath'},
                       {'ids': [FIRST, FIRST]}, {'ids': [0]}, {'ids': [2**32]},
                       {'ids': [str(FIRST)]}):
            with self.subTest(update=update):
                scenario = self.scenario()
                scenario['expected'].update(update)
                result = self.assert_rejected_without_removal(scenario)
                self.assertEqual(result['registered'], [])


if __name__ == '__main__':
    unittest.main()
