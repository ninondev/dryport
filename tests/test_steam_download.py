# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""伪 Steam CDP 驱动真实下载入口；所有文件都在临时目录，不接触客户端。"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { EventEmitter } from 'node:events';
const scenario = JSON.parse(fs.readFileSync(process.env.SN_REVIEW_SCENARIO, 'utf8'));
const realTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => realTimeout(fn, ms >= 10000 ? 100 : Math.min(ms, 2), ...args);
const record = value => fs.appendFileSync(scenario.trace, JSON.stringify(value) + '\n');
const originalCopy = fs.copyFileSync;
fs.copyFileSync = (source, target, flags) => {
  if (scenario.mode === 'copy-failure') { fs.writeFileSync(path.join(path.dirname(target), 'partial'), 'fragment'); throw new Error('fixture copy failed'); }
  if (scenario.mode === 'copy-enospc') throw Object.assign(new Error('ENOSPC: no space left on device, copyfile'), {code:'ENOSPC'});
  return originalCopy(source, target, flags);
};
// 保留真实的 --path 清单/账号验证，只替代会连接 Steam 的即时许可子进程。
childProcess.spawnSync = (command, args, options) => {
  if (command === 'python3' && args[1] === '--depot-metadata' && scenario.metadata) {
    record({metadata:args}); return {status:0, stdout:JSON.stringify(scenario.metadata), stderr:''};
  }
  if (command !== 'python3' || args.length !== 3 || path.basename(args[0]) !== 'library_discovery.py'
      || args[1] !== '--license' || !scenario.licenseProbe) {
    throw new Error('unexpected subprocess in isolated Steam fixture');
  }
  record({license: {command, args, timeout: options.timeout}});
  const probe = scenario.licenseProbe;
  return {status: probe.status, stdout: probe.stdout, stderr: '',
          ...(probe.error ? {error: Object.assign(new Error(probe.error), {code:probe.error})} : {})};
};
// 网络探针替身:只记录一次探测,下一拍连上;offline-stall 模拟断网报错,任何测试都不碰真实网络。
net.connect = options => {
  record({net: {host: options?.host, port: options?.port}});
  const socket = new EventEmitter();
  socket.destroy = () => {};
  socket.setTimeout = () => socket;
  // 用 nextTick 而不是 setTimeout(0)：上面把 5 s 探针超时压成 2 ms，0 ms 的定时器在负载下会输给它，把「网通」误判成断网。
  process.nextTick(() => socket.emit(scenario.mode === 'offline-stall' ? 'error' : 'connect',
                                     new Error('fixture network unreachable')));
  return socket;
};
syncBuiltinESMExports();
http.get = (options, callback) => {
  record({http: options});
  const req = new EventEmitter();
  req.destroy = error => { if (error) req.emit('error', error); };
  req.setTimeout = (ms, callback) => { if (scenario.mode === 'http-timeout') setTimeout(callback, ms); return req; };
  if (scenario.mode === 'steam-closed') setTimeout(() =>
    req.emit('error', Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8080'), {code:'ECONNREFUSED'})), 0);
  else if (scenario.mode !== 'http-timeout') setTimeout(() => {
    const response = new EventEmitter(); response.statusCode = 200;
    callback(response);
    response.emit('data', JSON.stringify([{title:'SharedJSContext', webSocketDebuggerUrl:'ws://127.0.0.1:8080/devtools/page/fixture'}]));
    response.emit('end');
  }, 0);
  return req;
};
globalThis.WebSocket = class extends EventTarget {
  static OPEN = 1;
  constructor(url) { super(); this.readyState = 0; this.polls = 0; this.identities = 0; this.keep = setInterval(() => {}, 1000);
    if (scenario.mode !== 'connect-timeout') setTimeout(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); }, 0); }
  send(raw) {
    const request = JSON.parse(raw); const expression = request.params.expression;
    record({expression});
    const message = value => this.dispatchEvent(new MessageEvent('message', {data: JSON.stringify(value)}));
    if (scenario.mode === 'rpc-timeout') return;
    setTimeout(() => {
      if (scenario.mode === 'runtime-error' && expression.includes('ExecCommand')) {
        message({id:request.id,result:{exceptionDetails:{text:'fixture command rejected'}}}); return;
      }
      let value = 1;
      const command = expression.match(/download_depot (\d+) (\d+) (\d+)/);
      if (command) this.depot = command[2];
      if (expression.includes('ConvertTo64BitString')) {
        const accounts = scenario.accounts || [];
        value = accounts[Math.min(this.identities++, accounts.length - 1)];
      }
      if (expression.includes('.join(')) {
        this.polls++;
        if (this.polls === 1 && scenario.swapParent) {
          fs.unlinkSync(scenario.swapParent.alias); fs.symlinkSync(scenario.swapParent.target, scenario.swapParent.alias);
        }
        if (this.polls === 1 && scenario.swapAncestor) {
          fs.renameSync(scenario.swapAncestor, scenario.swapAncestor + '-old'); fs.mkdirSync(scenario.swapAncestor);
        }
        if (scenario.mode === 'disconnect') { this.close(); return; }
        if (scenario.mode === 'grow-empty') fs.mkdirSync(scenario.dest, {recursive:true});
        if (scenario.mode === 'grow-dest') {
          fs.mkdirSync(scenario.dest, {recursive:true}); fs.writeFileSync(path.join(scenario.dest,'user.txt'),'preserve user data');
        }
        if (scenario.mode === 'stall' || scenario.mode === 'offline-stall') value = '';
        else if (scenario.mode === 'no-license') value = 'Depot download failed : no subscription';
        else if (scenario.mode === 'disk-full') value = 'Depot download failed : Disk write failure';
        else if (scenario.mode === 'unrelated-completion') value = this.polls === 1
          ? 'Depot download complete : "steamapps/content/app_999/depot_999"\n' : 'Depot download failed : fixture';
        else if (scenario.failDepot === this.depot) value = "Depot download failed : fixture missing package";
        else value = `Depot download complete : "${scenario.contents?.[this.depot] || scenario.content}"\n`;
      }
      message({id:request.id,result:{result:{value}}});
    }, 0);
  }
  close() { if (this.readyState === 3) return; this.readyState = 3; clearInterval(this.keep); this.dispatchEvent(new Event('close')); }
};
'''


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class SteamDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-download-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.games = self.base / "games with spaces"
        self.steam = self.base / "fake Steam"
        self.dest = self.games / "fixture"
        self.content = self.steam / "Steam.AppBundle/Steam/Contents/MacOS/steamapps/content/app_42/depot_43"
        self.trace = self.base / "trace"
        self.mock = self.base / "steam-mock.mjs"
        self.mock.write_text(MOCK)
        self.scenario = self.base / "scenario.json"
        self.metadata = None
        self.contents = {}
        self.fail_depot = None
        self.swap_parent = None
        self.swap_ancestor = None
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_GAMES": str(self.games),
                    "SN_RUNTIMES": str(self.base / "rt"), "STEAM": str(self.steam),
                    "SN_REVIEW_SCENARIO": str(self.scenario)}

    def run_download(self, mode="success", args=None, accounts=None, license_probe=None):
        self.scenario.write_text(json.dumps(dict(mode=mode, dest=str(self.dest), content=str(self.content),
                                                 trace=str(self.trace), accounts=accounts if accounts is not None else [self.account()],
                                                 licenseProbe=license_probe, metadata=self.metadata, contents=self.contents, failDepot=self.fail_depot, swapParent=self.swap_parent, swapAncestor=self.swap_ancestor)))
        return subprocess.run(['node', '--import', str(self.mock), str(ROOT / 'lib/steam-download.mjs'),
                               *(args if args is not None else ['42', '43', '44', str(self.dest)])],
                              env=self.env, capture_output=True, text=True, timeout=4)

    def payload(self):
        self.content.mkdir(parents=True)
        (self.content / 'game.dat').write_bytes(b'complete fixture game')
        self.manifest('43', '44', self.content)

    def manifest(self, depot, gid, directory):
        records = []
        total = 0
        for item in sorted(directory.rglob('*')):
            name = str(item.relative_to(directory)).replace('/', '\\')
            data = item.read_bytes() if item.is_file() else b''
            sha = hashlib.sha1(data).hexdigest() if item.is_file() else '0' * 40
            flag = '0' if item.is_file() else '40'
            total += len(data)
            records.append(f"{len(data):,} 1 {sha} {flag} {name}")
        cache = self.steam / 'depotcache'
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f'{depot}_{gid}.manifest.txt').write_text(
            f'Content Manifest for Depot {depot}\nManifest ID / date : {gid} / fixture\n'
            f'Total number of files : {len(records)}\nTotal bytes on disk : {total:,}\n'
            'Size Chunks File SHA Flags Name\n' + '\n'.join(records) + '\n')

    @staticmethod
    def account(account_id=123):
        return {"steamid64": str(76561197960265728 + account_id),
                "account_id": str(account_id), "id": str(account_id)}

    def catalog(self, *, active_account=123, multiple_depots=False, protocol=1, borrowed=False):
        directory = self.games / '.dryport-catalog' / ('generation-' + 'a' * 32)
        directory.mkdir(parents=True)
        account = self.account()
        (directory / 'catalog.json').write_text(json.dumps({"protocol": protocol, "account": account}))
        row = dict(appid='42', name='fixture', depot='43', gid='44', dl=40,
                   depots=[dict(depot='43', gid='44', dl=40)],
                   ownership='borrowed' if borrowed else 'owned',
                   license_source='family_shared' if borrowed else 'self',
                   owner_account_id='456' if borrowed else '0')
        if multiple_depots:
            row['depots'].append(dict(depot='45', gid='46', dl=50))
        for name, value in (('route-all.json', [dict(appid='42', name='fixture', route='unknown')]),
                            ('final-routes.json', []), ('owned-windows.json', [] if borrowed else [row])):
            (directory / name).write_text(json.dumps(value))
        if protocol == 2:
            (directory / 'windows-library.json').write_text(json.dumps([row]))
        (directory.parent / 'current').symlink_to(directory.name)
        config = self.steam / 'config'
        config.mkdir(parents=True)
        sid = self.account(active_account)['steamid64']
        (config / 'loginusers.vdf').write_text(
            f'"users"\n{{\n\t"{sid}"\n\t{{\n\t\t"AccountName" "fixture"\n\t\t"AutoLogin" "1"\n\t}}\n}}\n')
        (self.steam / 'registry.vdf').write_text('"AutoLoginUser" "fixture"\n')
        self.metadata = dict(protocol=1, appid='42', account=account,
                             depots=[dict(d, mount_order=i, config={}) for i, d in enumerate(row['depots'])],
                             depot_metadata_issues=[], sharedApps={})
        return directory

    def expressions(self):
        return [item['expression'] for item in map(json.loads, self.trace.read_text().splitlines())
                if 'expression' in item] if self.trace.exists() else []

    def assert_only_content_commands(self):
        commands = [expression for expression in self.expressions() if 'ExecCommand' in expression]
        self.assertTrue(commands)
        self.assertEqual([expression for expression in commands
                          if 'download_depot ' not in expression and 'manifest_dump ' not in expression], [])

    def test_complete_download_preserves_client_filter_settings(self):
        self.payload()
        result = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_only_content_commands()
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'complete fixture game')

    def test_filtered_partial_completion_fails_without_resetting_client_settings(self):
        self.payload()
        omitted = self.content / 'filtered-out.dat'
        omitted.write_bytes(b'also required by the manifest')
        self.manifest('43', '44', self.content)
        omitted.unlink()
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assert_only_content_commands()
        self.assert_no_delivery(result)

    def license_probe(self, status='available', *, borrowed=False, **overrides):
        receipt = dict(protocol=2, appid='42', account=self.account(), status=status,
                       allowed=status == 'available', subscribed=True, source_confirmed=True,
                       ownership='borrowed' if borrowed else 'owned',
                       license_source='family_shared' if borrowed else 'self',
                       owner_account_id='456' if borrowed else '0',
                       locked_by_other=False, sharing_excluded=False)
        receipt.update(overrides)
        return dict(status=0 if status == 'available' else 3, stdout=json.dumps(receipt))

    def assert_license_probe_only(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(trace, [dict(license=dict(command='python3',
                         args=[str(ROOT / 'lib/library_discovery.py'), '--license', '42'], timeout=25000))])
        self.assert_no_delivery(result)

    def assert_no_delivery(self, result):
        self.assertFalse(self.dest.exists())
        self.assertEqual((self.content / 'game.dat').read_bytes(), b'complete fixture game')
        self.assertFalse(list(self.games.glob('.dryport-download-*')))
        self.assertNotIn('已交付副本', result.stdout)

    def assert_available_license_download(self, *, borrowed):
        self.catalog(protocol=2, borrowed=borrowed)
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account()],
                                   license_probe=self.license_probe(borrowed=borrowed))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'complete fixture game')
        self.assertEqual((self.dest / '.dryport-appid').read_text(), '42')
        trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(trace[0]['license']['args'], [str(ROOT / 'lib/library_discovery.py'), '--license', '42'])
        self.assertEqual(sum('license' in row for row in trace), 1)
        self.assertTrue(any('download_depot 42 43 44' in value for value in self.expressions()))
        self.assertEqual(sum('ConvertTo64BitString' in value for value in self.expressions()), 3)
        self.assertNotIn('接下来由 Steam 下载服务确认授权', result.stdout)

    def test_protocol_two_available_owned_license_downloads(self):
        self.assert_available_license_download(borrowed=False)

    def test_protocol_two_available_shared_license_uses_complete_windows_table(self):
        self.assert_available_license_download(borrowed=True)

    def test_protocol_two_unknown_matching_account_defers_to_steam_completion(self):
        self.catalog(protocol=2, borrowed=True)
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account()],
                                   license_probe=self.license_probe('unknown', borrowed=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('接下来由 Steam 下载服务确认授权', result.stdout)
        self.assertEqual((self.dest / '.dryport-appid').read_text(), '42')
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'complete fixture game')
        self.assertTrue(any('download_depot 42 43 44' in value for value in self.expressions()))
        self.assertEqual(sum('ConvertTo64BitString' in value for value in self.expressions()), 3)

    def test_protocol_two_unknown_never_overrides_steam_download_denial(self):
        self.catalog(protocol=2)
        self.payload()
        result = self.run_download('no-license', args=['42'], accounts=[self.account()],
                                   license_probe=self.license_probe('unknown'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no subscription', result.stderr)
        self.assertTrue(any('download_depot 42 43 44' in value for value in self.expressions()))
        self.assert_no_delivery(result)

    def test_protocol_two_explicit_denials_stop_before_downloader_connects(self):
        self.catalog(protocol=2)
        self.payload()
        for status, message in (('not_subscribed', '订阅许可'), ('sharing_excluded', '不允许这个游戏共享'),
                                ('in_use', '正在使用中')):
            with self.subTest(status=status):
                self.trace.unlink(missing_ok=True)
                result = self.run_download(args=['42'], accounts=[self.account()],
                                           license_probe=self.license_probe(status))
                self.assert_license_probe_only(result)
                self.assertIn(message, result.stderr)

    def test_protocol_two_license_account_change_stops_available_and_unknown(self):
        self.catalog(protocol=2)
        self.payload()
        for status in ('available', 'unknown'):
            with self.subTest(status=status):
                self.trace.unlink(missing_ok=True)
                result = self.run_download(args=['42'], accounts=[self.account()],
                                           license_probe=self.license_probe(status, account=self.account(456)))
                self.assert_license_probe_only(result)
                self.assertIn('许可核实时 Steam 账号已变化', result.stderr)

    def test_protocol_two_malformed_license_receipts_never_reach_steam(self):
        self.catalog(protocol=2)
        self.payload()
        cases = {
            'invalid-json': dict(status=0, stdout='{'),
            'empty-stdout': dict(status=0, stdout=''),
            'null': dict(status=0, stdout='null'),
            'array': dict(status=0, stdout='[]'),
            'old-protocol': self.license_probe(protocol=1),
            'string-protocol': self.license_probe(protocol='2'),
            'wrong-game': self.license_probe(appid='99'),
            'string-allowed': self.license_probe(allowed='true'),
            'contradictory-available': self.license_probe(allowed=False),
            'contradictory-unknown': self.license_probe('unknown', allowed=True),
            'available-wrong-exit': {**self.license_probe(), 'status': 3},
            'unknown-wrong-exit': {**self.license_probe('unknown'), 'status': 0},
            'missing-account': self.license_probe(account=None),
            'numeric-steamid': self.license_probe(account={**self.account(), 'steamid64': int(self.account()['steamid64'])}),
            'inconsistent-account': self.license_probe(account={**self.account(), 'account_id': '456'}),
            'subprocess-timeout': {**self.license_probe(), 'status': None, 'error': 'ETIMEDOUT'},
            'subprocess-error-even-with-success-stdout': {**self.license_probe(), 'error': 'ENOENT'},
        }
        for name, probe in cases.items():
            with self.subTest(case=name):
                self.trace.unlink(missing_ok=True)
                result = self.run_download(args=['42'], accounts=[self.account()], license_probe=probe)
                self.assert_license_probe_only(result)

    def test_protocol_two_unknown_bad_live_identity_stops_before_download_command(self):
        self.catalog(protocol=2)
        self.payload()
        for account in (None, {}, self.account(456), {**self.account(), 'account_id': 123}):
            with self.subTest(account=account):
                self.trace.unlink(missing_ok=True)
                result = self.run_download(args=['42'], accounts=[account],
                                           license_probe=self.license_probe('unknown'))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('账号与游戏清单不一致', result.stderr)
                self.assertFalse(any('ExecCommand' in value for value in self.expressions()))
                self.assert_no_delivery(result)

    def test_protocol_two_unknown_account_switch_after_completion_blocks_delivery(self):
        self.catalog(protocol=2)
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account(), self.account(456)],
                                   license_probe=self.license_probe('unknown'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('账号与游戏清单不一致', result.stderr)
        self.assertTrue(any('download_depot 42 43 44' in value for value in self.expressions()))
        self.assertEqual(sum('ConvertTo64BitString' in value for value in self.expressions()), 2)
        self.assert_no_delivery(result)

    def test_explicit_depot_without_catalog_still_checks_account_switch(self):
        self.payload()
        result=self.run_download(accounts=[self.account(),self.account(456)])
        self.assertNotEqual(result.returncode,0)
        self.assert_no_delivery(result)
        self.assertEqual(sum('ConvertTo64BitString' in e for e in self.expressions()),2)

    def test_explicit_depot_without_catalog_requires_valid_initial_identity(self):
        self.payload()
        result=self.run_download(accounts=[None])
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any('ExecCommand' in e for e in self.expressions()))
        self.assert_no_delivery(result)

    def test_catalog_single_depot_downloads_with_current_account_before_and_after(self):
        self.catalog()
        # 根部遗留表故意给错包，证明本次读取新目录且不混进历史账号数据。
        (self.games / 'owned-windows.json').write_text(json.dumps([dict(appid=42, depot='999', gid='888', name='wrong')]))
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'complete fixture game')
        self.assertEqual((self.dest / '.dryport-appid').read_text(), '42')
        expressions = self.expressions()
        self.assertEqual(sum('ConvertTo64BitString' in value for value in expressions), 3)
        self.assertTrue(any('download_depot 42 43 44' in value for value in expressions))
        self.assertFalse(any('download_depot 42 999' in value for value in expressions))

    def test_catalog_live_account_mismatch_stops_before_download_command(self):
        self.catalog()
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account(456)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('账号与游戏清单不一致', result.stderr)
        self.assertFalse(any('ExecCommand' in value for value in self.expressions()))
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())

    def test_catalog_account_switch_during_download_preserves_content_without_delivery(self):
        self.catalog()
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account(), self.account(456)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('账号与游戏清单不一致', result.stderr)
        expressions = self.expressions()
        self.assertTrue(any('download_depot 42 43 44' in value for value in expressions))
        self.assertEqual(sum('ConvertTo64BitString' in value for value in expressions), 2)
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())
        self.assertFalse(list(self.games.glob('.dryport-download-*')))
        self.assertNotIn('已交付副本', result.stdout)

    def test_catalog_local_account_mismatch_never_falls_back_to_legacy_table(self):
        self.catalog(active_account=456)
        (self.games / 'owned-windows.json').write_text(json.dumps([dict(appid=42, depot='43', gid='44', name='fixture')]))
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account(456)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('账号已切换', result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())

    def test_catalog_multiple_depots_never_uses_first_depot_without_explicit_choice(self):
        self.catalog(multiple_depots=True)
        self.payload()
        result = self.run_download(args=['42'], accounts=[self.account()])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mount_order', result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())

    def second_depot(self, *, replacement=b'patched fixture game'):
        self.catalog(protocol=2, multiple_depots=True)
        self.payload()
        second = self.content.parent / 'depot_45'
        second.mkdir()
        (second / 'game.dat').write_bytes(replacement)
        (second / 'extra.dat').write_bytes(b'necessary second package')
        self.manifest('45', '46', second)
        self.contents = {'43':str(self.content), '45':str(second)}
        return second

    def test_automatic_two_required_packages_verify_then_overlay_in_official_order(self):
        second = self.second_depot()
        result = self.run_download(args=['42'], accounts=[self.account()], license_probe=self.license_probe())
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.dest/'game.dat').read_bytes(),b'patched fixture game')
        self.assertEqual((self.dest/'extra.dat').read_bytes(),b'necessary second package')
        self.assertEqual((self.content/'game.dat').read_bytes(),b'complete fixture game')
        self.assertEqual((second/'game.dat').read_bytes(),b'patched fixture game')
        receipt=json.loads((self.dest/'.dryport-download.json').read_text())
        self.assertEqual([p['depot'] for p in receipt['packages']],['43','45'])
        self.assertEqual(receipt['overlays'][0]['before']['gid'],'44')
        self.assertEqual(receipt['overlays'][0]['after']['gid'],'46')
        self.assertEqual(receipt['overlays'][0]['winner'],'45')
        self.assertEqual(sum('download_depot ' in e for e in self.expressions()),2)
        self.assertEqual(sum('ConvertTo64BitString' in e for e in self.expressions()),5)

    def test_second_required_package_denied_never_delivers_first_package(self):
        second=self.second_depot();self.fail_depot='45'
        result=self.run_download(args=['42'],accounts=[self.account()],license_probe=self.license_probe())
        self.assertNotEqual(result.returncode,0)
        self.assert_no_delivery(result)
        self.assertEqual((second/'extra.dat').read_bytes(),b'necessary second package')

    def test_second_package_missing_manifest_file_is_not_complete(self):
        second=self.second_depot();(second/'extra.dat').unlink()
        result=self.run_download(args=['42'],accounts=[self.account()],license_probe=self.license_probe())
        self.assertNotEqual(result.returncode,0)
        self.assert_no_delivery(result)

    def test_shared_core_download_uses_host_appid_and_source_manifest(self):
        self.catalog(protocol=2)
        self.payload()
        self.metadata['sharedApps']={'77':{'depots':[self.metadata['depots'][0]]}}
        self.metadata['depots']=[dict(depot='43',shared_from_app='77',mount_order=0,sharedinstall=2)]
        result=self.run_download(args=['42'],accounts=[self.account()],license_probe=self.license_probe())
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(any('download_depot 42 43 44' in e for e in self.expressions()))
        self.assertFalse(any('download_depot 77' in e for e in self.expressions()))

    def test_final_account_switch_after_copy_preserves_stage_without_publication(self):
        self.catalog(protocol=2);self.payload()
        result=self.run_download(args=['42'],accounts=[self.account(),self.account(),self.account(456)],license_probe=self.license_probe())
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.dest.exists())
        self.assertIn('账号与游戏清单不一致',result.stderr)
        self.assertEqual(len(list(self.games.glob('.dryport-download-*/game/game.dat'))),1)
        self.assertEqual((self.content/'game.dat').read_bytes(),b'complete fixture game')

    def test_metadata_account_mismatch_stops_before_downloader_connects(self):
        self.catalog(protocol=2);self.payload();self.metadata['account']=self.account(456)
        result=self.run_download(args=['42'],accounts=[self.account()],license_probe=self.license_probe())
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any('http' in json.loads(line) for line in self.trace.read_text().splitlines()))
        self.assert_no_delivery(result)

    def test_stale_extra_cache_file_is_preserved_but_not_delivered(self):
        self.payload();(self.content/'stale-old-version').write_bytes(b'keep cache')
        result=self.run_download()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.dest/'stale-old-version').exists())
        self.assertEqual((self.content/'stale-old-version').read_bytes(),b'keep cache')

    def test_verify_delivery_reads_all_hashes_and_rejects_extra_or_missing_files(self):
        self.payload();result=self.run_download()
        self.assertEqual(result.returncode,0,result.stderr)
        args=['--verify-delivery','42',str(self.dest)]
        self.trace.unlink()
        result=self.run_download(args=args)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['scope'],'explicit_depot')
        self.assertFalse(self.trace.exists())
        (self.dest/'extra').write_bytes(b'extra')
        self.assertNotEqual(self.run_download(args=args).returncode,0)
        (self.dest/'extra').unlink();(self.dest/'game.dat').write_bytes(b'changed')
        self.assertNotEqual(self.run_download(args=args).returncode,0)
        (self.dest/'game.dat').unlink()
        self.assertNotEqual(self.run_download(args=args).returncode,0)

    def test_existing_receipt_cannot_satisfy_different_requested_manifest(self):
        self.payload();self.assertEqual(self.run_download().returncode,0)
        self.trace.unlink()
        result=self.run_download(args=['42','43','999',str(self.dest)])
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.trace.exists())
        self.assertEqual((self.dest/'game.dat').read_bytes(),b'complete fixture game')

    def test_empty_concurrent_destination_is_not_overwritten(self):
        self.payload();result=self.run_download('grow-empty')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(list(self.dest.iterdir()),[])
        self.assertEqual((self.content/'game.dat').read_bytes(),b'complete fixture game')

    def test_unowned_existing_destination_is_preserved_without_contacting_steam(self):
        self.dest.mkdir(parents=True)
        (self.dest / 'user.txt').write_text('preserve user data')
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.exists())
        self.assertEqual((self.dest / 'user.txt').read_text(), 'preserve user data')
        self.assertFalse((self.dest / '.dryport-appid').exists())

    def test_old_marker_without_receipt_is_preserved_without_contacting_steam(self):
        self.dest.mkdir(parents=True)
        (self.dest / 'game.dat').write_bytes(b'existing game')
        (self.dest / '.dryport-appid').write_text('42')
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'existing game')
        self.assertFalse(self.trace.exists())

    def test_empty_staging_cannot_claim_concurrently_created_user_data(self):
        result = self.run_download('grow-dest')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.dest / 'user.txt').read_text(), 'preserve user data')
        self.assertFalse((self.dest / '.dryport-appid').exists())

    def test_completed_copy_uses_overridden_paths(self):
        self.payload()
        result = self.run_download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / 'game.dat').read_bytes(), b'complete fixture game')
        self.assertEqual((self.dest / '.dryport-appid').read_text(), '42')
        self.assertTrue((self.content / 'game.dat').exists())

    def test_table_is_read_from_overridden_games_root(self):
        self.games.mkdir()
        (self.games / 'owned-windows.json').write_text(json.dumps([dict(appid=42,depot='43',gid='44',name='fixture')]))
        self.payload()
        result = self.run_download(args=['42'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / '.dryport-appid').read_text(), '42')

    def test_destination_race_preserves_both_user_files_and_source(self):
        self.payload()
        result = self.run_download('grow-dest')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dest / 'user.txt').read_text(), 'preserve user data')
        self.assertFalse((self.dest / '.dryport-appid').exists())
        self.assertTrue((self.content / 'game.dat').exists())

    def test_copy_failure_keeps_partial_files_out_of_destination(self):
        self.payload()
        result = self.run_download('copy-failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())
        partial = list(self.games.glob('.dryport-download-*/game/partial'))
        self.assertEqual(len(partial), 1)
        self.assertFalse((partial[0].parent / '.dryport-appid').exists())

    def test_space_budget_short_stops_before_any_steam_command(self):
        # 内容包标了大小且放不下时，停在本地，一条命令都不发给 Steam。
        self.catalog(protocol=2)
        self.payload()
        self.metadata['depots'][0]['size'] = 2 ** 60
        result = self.run_download(args=['42'], accounts=[self.account()], license_probe=self.license_probe())
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('磁盘空间不够，未开始下载', result.stderr)
        self.assertEqual([e for e in self.expressions() if 'ExecCommand' in e], [])
        self.assert_no_delivery(result)

    def test_disk_full_spew_stops_with_code_4_and_keeps_staging(self):
        self.payload()
        result = self.run_download('disk-full')
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('磁盘写满，下载停在 depot 43', result.stderr)
        self.assert_no_delivery(result)

    def test_enospc_during_delivery_is_reported_as_disk_full(self):
        self.payload()
        result = self.run_download('copy-enospc')
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('交付副本时磁盘写满；游戏副本未交付', result.stderr)
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())

    def test_parent_symlink_swap_during_download_cannot_write_steam(self):
        self.payload()
        intended=self.base/'intended';intended.mkdir()
        alias=self.base/'parent-alias';alias.symlink_to(intended)
        self.swap_parent={'alias':str(alias),'target':str(self.steam)}
        result=self.run_download(args=['42','43','44',str(alias/'new-game')])
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertFalse((self.steam/'new-game').exists())
        self.assertFalse((intended/'new-game').exists())
        self.assertFalse(list(self.steam.glob('.dryport-download-*')))

    def test_same_path_ancestor_replacement_is_detected_by_identity(self):
        self.payload()
        parent=self.base/'target-parent';parent.mkdir()
        self.swap_ancestor=str(parent)
        result=self.run_download(args=['42','43','44',str(parent/'new-game')])
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertEqual(list(parent.iterdir()),[])
        self.assertEqual(list((self.base/'target-parent-old').iterdir()),[])

    def test_repository_destination_is_refused_before_steam(self):
        self.payload()
        pretend=self.base/'pretend-code-repository';pretend.mkdir()
        self.env['SN_HOME']=str(pretend)
        result=self.run_download(args=['42','43','44',str(pretend/'game-data')])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('源码仓库',result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertEqual(list(pretend.iterdir()),[])

    def test_symlink_destination_is_never_claimed(self):
        outside = self.base / 'outside'; outside.mkdir()
        self.dest.parent.mkdir(); self.dest.symlink_to(outside, target_is_directory=True)
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.dest.is_symlink())
        self.assertFalse(self.trace.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_parent_cannot_redirect_destination_into_steam(self):
        self.steam.mkdir()
        alias = self.base / 'steam-alias'; alias.symlink_to(self.steam, target_is_directory=True)
        result = self.run_download(args=['42', '43', '44', str(alias / 'new-game')])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.exists())
        self.assertEqual(list(self.steam.iterdir()), [])

    def test_stall_never_copies_partial_content(self):
        self.payload()
        result = self.run_download('stall')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('十分钟没有新数据', result.stderr)
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.content / 'game.dat').exists())
        trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertGreaterEqual(sum('net' in row for row in trace), 1)

    def test_steam_closed_reports_not_ready_with_code_five(self):
        self.payload()
        result = self.run_download('steam-closed')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('Steam 没准备好', result.stderr)
        self.assertFalse(any('ExecCommand' in value for value in self.expressions()))
        self.assert_no_delivery(result)

    def test_license_probe_error_event_reports_not_ready_with_code_five(self):
        self.catalog(protocol=2)
        self.payload()
        probe = dict(status=3, stdout=json.dumps(
            {"event": "error", "msg": "Steam 尚未登录或游戏库尚未加载；请打开库页面后重试"}))
        result = self.run_download(args=['42'], accounts=[self.account()], license_probe=probe)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('Steam 没准备好', result.stderr)
        self.assertIn('尚未登录', result.stderr)
        self.assertFalse(any('http' in json.loads(line) for line in self.trace.read_text().splitlines()))
        self.assert_no_delivery(result)

    def test_offline_stall_reports_network_down_with_code_six(self):
        self.payload()
        result = self.run_download('offline-stall')
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn('网络断了，下载停在 depot 43', result.stderr)
        self.assertTrue((self.content / 'game.dat').exists())
        self.assert_no_delivery(result)
        trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(sum('net' in row for row in trace), 3)

    def test_unrelated_completion_does_not_authorize_copy(self):
        self.payload()
        result = self.run_download('unrelated-completion')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.dest.exists())

    def test_disconnect_fails_with_explanation(self):
        self.payload()
        result = self.run_download('disconnect')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('连接', result.stderr)
        self.assertFalse(self.dest.exists())

    def test_rpc_timeout_is_a_bounded_failure(self):
        result = self.run_download('rpc-timeout')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('超时', result.stderr)
        self.assertFalse(self.dest.exists())

    def test_http_timeout_is_a_bounded_failure(self):
        result = self.run_download('http-timeout')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('超时', result.stderr)

    def test_websocket_connect_timeout_is_a_bounded_failure(self):
        result = self.run_download('connect-timeout')
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('超时', result.stderr)

    def test_steam_evaluation_error_stops_before_copy(self):
        self.payload()
        result = self.run_download('runtime-error')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.dest.exists())

    def test_runtime_error_message_keeps_first_line_without_stack_frames(self):
        # CDP 的异常描述自带「    at」堆栈行，用户只该看到第一句话本身。
        self.payload()
        result = self.run_download('runtime-error')
        self.assertIn('Steam 未执行请求：fixture command rejected', result.stderr)
        self.assertFalse(any(line.startswith('    at ') for line in result.stderr.splitlines()))

    def test_console_identifiers_reject_injection_and_overflow(self):
        for args in (['42;quit','43','44'], ['42','43;quit','44'], ['42','43','44\nquit'],
                     ['42','4294967296','44'], ['42','43','18446744073709551616']):
            with self.subTest(args=args):
                result = self.run_download(args=[*args, str(self.dest)])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.trace.exists())


if __name__ == '__main__':
    unittest.main()
