#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只用自有隐藏窗口样例验证 Electron 的原包直读机制；不接收真实游戏。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
SHARED = importlib.import_module('love-runtime')
VERSION = '11.5.0'
# https://github.com/electron/electron/releases/download/v11.5.0/SHASUMS256.txt
ARCHIVE_SHA256 = '749fb6bd676e174de66845b8ac959985f30a773dcb2c05553890bd99b94c9d60'

MAIN = r'''
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const os = require('os');
const {app, BrowserWindow, session} = require('electron');
const state = process.env.SN_ELECTRON_STATE;
const receipt = {electron:process.versions.electron, arch:process.arch, app_path:app.getAppPath(), resources_path:process.resourcesPath,
  app_name:app.getName(), app_version:app.getVersion(), is_packaged:app.isPackaged, default_app:process.defaultApp, attempts:{}};
function denied(label, operation) {
  try { operation(); receipt.attempts[label] = 'ALLOWED'; }
  catch (error) { receipt.attempts[label] = error.code; }
  assert.equal(receipt.attempts[label], 'EPERM', label + ' was not refused');
}
function protect(label, original) {
  denied(label + '_write', () => fs.writeFileSync(path.join(original,'protected.txt'), 'UNSAFE'));
  denied(label + '_alias', () => fs.writeFileSync(path.join(state,label+'-alias/protected.txt'), 'UNSAFE'));
  denied(label + '_hardlink', () => fs.linkSync(path.join(original,'protected.txt'),path.join(state,label+'-hardlink')));
  denied(label + '_parent_rename', () => fs.renameSync(path.dirname(original),path.join(state,label+'-renamed-parent')));
  denied(label + '_grandparent_rename', () => fs.renameSync(path.dirname(path.dirname(original)),path.join(state,label+'-renamed-grandparent')));
}
(async () => {
  try {
    assert.equal(fs.readFileSync(path.join(__dirname,'payload.txt'),'utf8'), 'immutable fixture payload\n');
    receipt.payload_read = true;
    receipt.user_data = app.getPath('userData'); receipt.session_data = app.getPath('userData');
    receipt.session_data_api = false; // 11.5 将 Chromium session 数据放在 userData，尚无 sessionData API。
    receipt.home = os.homedir(); assert.equal(receipt.home,path.join(state,'home'));
    const gameSave = path.join(os.homedir(),'Library/Application Support/koifarm/save');
    if (!fs.existsSync(gameSave)) fs.mkdirSync(gameSave);
    const gameCounter = path.join(gameSave,'fixture-counter.txt');
    const gamePrevious = fs.existsSync(gameCounter) ? fs.readFileSync(gameCounter,'utf8') : '0';
    assert.equal(gamePrevious,String(Number(process.env.SN_ELECTRON_ROUND)-1));
    fs.writeFileSync(gameCounter,process.env.SN_ELECTRON_ROUND);
    receipt.home_save_readback = fs.readFileSync(gameCounter,'utf8');
    assert.equal(receipt.user_data,path.join(state,'user-data'));
    assert.equal(receipt.session_data,path.join(state,'user-data'));
    const round = Number(process.env.SN_ELECTRON_ROUND);
    const save = path.join(receipt.user_data,'counter.txt');
    const previous = fs.existsSync(save) ? fs.readFileSync(save,'utf8') : '0';
    assert.equal(previous,String(round-1)); fs.writeFileSync(save,String(round));
    assert.equal(fs.readFileSync(save,'utf8'),String(round)); receipt.round = round;
    protect('source',process.env.SN_ELECTRON_SOURCE);
    protect('bundle',process.env.SN_ELECTRON_BUNDLE);
    await app.whenReady();
    // 故意请求全屏和 kiosk；自有 -r 层必须在构造前改成隐藏普通窗口。
    const window = new BrowserWindow({show:false,width:400,height:300,fullscreen:true,kiosk:true,
      webPreferences:{nodeIntegration:true,enableRemoteModule:true,contextIsolation:false,sandbox:false}});
    await window.loadFile(path.join(__dirname,'index.html'));
    receipt.visible = window.isVisible(); receipt.fullscreen = window.isFullScreen(); receipt.kiosk = window.isKiosk();
    assert.equal(receipt.visible,false); assert.equal(receipt.fullscreen,false); assert.equal(receipt.kiosk,false);
    receipt.renderer = await window.webContents.executeJavaScript(`(() => {
      const before = localStorage.getItem('counter') || '0';
      localStorage.setItem('counter', String(Number(before) + 1));
      return {before,after:localStorage.getItem('counter'),payload:document.getElementById('payload').textContent};
    })()`);
    assert.equal(receipt.renderer.before,String(round-1)); assert.equal(receipt.renderer.after,String(round));
    assert.equal(receipt.renderer.payload,'immutable renderer');
    session.defaultSession.flushStorageData();
    await new Promise(resolve => setTimeout(resolve,250));
    receipt.ok = true;
    fs.writeFileSync(process.env.SN_ELECTRON_RECEIPT,JSON.stringify(receipt,null,2));
    window.destroy(); app.exit(0);
  } catch (error) {
    receipt.ok = false; receipt.error = error.stack || String(error);
    fs.writeFileSync(process.env.SN_ELECTRON_RECEIPT,JSON.stringify(receipt,null,2));
    console.error(receipt.error); app.exit(1);
  }
})();
'''


def make_asar(files):
    """只生成自有平面样例；布局按 Electron 官方 ASAR Pickle 格式，不处理用户归档。"""
    header, offset = {'files': {}}, 0
    payload = []
    for name, data in files.items():
        header['files'][name] = {'size': len(data), 'offset': str(offset)}
        offset += len(data); payload.append(data)
    encoded = json.dumps(header, separators=(',', ':')).encode()
    padded = encoded + b'\0' * (-len(encoded) % 4)
    header_pickle = struct.pack('<II', 4 + len(padded), len(encoded)) + padded
    return struct.pack('<II', 4, len(header_pickle)) + header_pickle + b''.join(payload)


def factory_check(runtime, archive, expected_sha, version):
    if version != VERSION or expected_sha != ARCHIVE_SHA256:
        raise ValueError('此探针仅接受已固定的 Electron 11.5.0 darwin-arm64 原厂 ZIP。')
    if archive.is_symlink():
        raise ValueError('原厂 ZIP 不能是软链。')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected_sha:
        raise ValueError('Electron 原厂 ZIP 哈希不符，未启动。')
    expected = {}
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if not item.filename.startswith('Electron.app/'):
                continue
            name = item.filename[len('Electron.app/'):].rstrip('/')
            if not name:
                continue
            if name in expected or any(p in ('', '.', '..') for p in name.split('/')) or '\\' in name:
                raise ValueError('原厂 ZIP app 清单路径异常。')
            if stat.S_ISLNK(item.external_attr >> 16):
                entry = ['link',package.read(item).decode()]
            elif item.is_dir():
                entry = ['directory']
            else:
                with package.open(item) as stream:
                    digest = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                entry = ['file',item.file_size,digest.hexdigest()]
            expected[name] = entry
            for parent in Path(name).parents:
                if str(parent) != '.':
                    expected.setdefault(parent.as_posix(),['directory'])
    if not expected or SHARED.inventory(runtime) != expected:
        raise ValueError('Electron.app 文件、目录或软链与原厂 ZIP 不一致。')
    with (runtime / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleShortVersionString') != version:
        raise ValueError('Electron 原厂 plist 版本不符。')
    architecture = SHARED.run_bounded(['/usr/bin/lipo','-archs',str(runtime/'Contents/MacOS/Electron')])
    if architecture['returncode'] or 'arm64' not in architecture['stdout'].split():
        raise ValueError('Electron 主程序不是 arm64。')
    return architecture


def probe(runtime, archive, version, sha256, output):
    runtime, archive = Path(runtime).resolve(strict=True), Path(archive).absolute()
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError(f'证据目录已存在，已保留：{output}')
    output = output.parent.resolve(strict=True) / output.name
    if output.is_relative_to(runtime) or output.is_relative_to(ROOT):
        raise ValueError('证据不能放在原厂 runtime 或仓库内。')
    architecture = factory_check(runtime,archive,sha256,version)
    before_runtime = SHARED.inventory(runtime)
    output.mkdir()
    report = {'protocol':1,'kind':'synthetic hidden-window mechanism only','created_at':datetime.now(timezone.utc).isoformat(),
              'runtime':str(runtime),'version':version,'official_url':f'https://github.com/electron/electron/releases/download/v{version}/electron-v{version}-darwin-arm64.zip',
              'archive_sha256':sha256,'architecture':architecture,'cases':[],'ok':False}
    fixtures = []
    try:
        report['factory_signature'] = SHARED.run_bounded(['/usr/bin/codesign','--verify','--deep','--strict',str(runtime)])
        for shape in ('directory','asar'):
            state = output / shape; state.mkdir()
            source = state / 'source-grandparent/source-parent/OriginalGame'
            bundle = state / 'bundle-grandparent/bundle-parent/Fixture.app'
            for folder in (source,bundle):
                folder.mkdir(parents=True); (folder/'protected.txt').write_text('untouched fixture\n')
            files = {'package.json':json.dumps({'name':'dryport-electron-probe','version':'1.2.3','main':'main.cjs'}).encode(),
                     'main.cjs':MAIN.encode(),'payload.txt':b'immutable fixture payload\n',
                     'index.html':b'<!doctype html><meta charset="utf-8"><p id="payload">immutable renderer</p>'}
            if shape == 'directory':
                entry = source/'app'; entry.mkdir()
                for name,data in files.items(): (entry/name).write_bytes(data)
            else:
                entry=source/'app.asar'; entry.write_bytes(make_asar(files))
            for label,target in [('source',source),('bundle',bundle)]: (state/(label+'-alias')).symlink_to(target)
            for folder in ('home','tmp','user-data','logs','crashes'): (state/folder).mkdir()
            (state/'home/Library/Application Support/koifarm').mkdir(parents=True)
            before_source,before_bundle=SHARED.inventory(source),SHARED.inventory(bundle)
            fixtures.append((source,before_source,bundle,before_bundle))
            case={'shape':shape,'source':str(source),'entry':str(entry),'state':str(state),'runs':[]};report['cases'].append(case)
            for round_number in (1,2):
                receipt=state/f'round-{round_number}.json'
                env={**os.environ,'HOME':str(state/'home'),'TMPDIR':str(state/'tmp')+'/',
                     'SN_ELECTRON_STATE':str(state),'SN_ELECTRON_SOURCE':str(source),'SN_ELECTRON_BUNDLE':str(bundle),
                     'SN_ELECTRON_ROUND':str(round_number),'SN_ELECTRON_RECEIPT':str(receipt)}
                for key in list(env):
                    if key.startswith(('NODE_','ELECTRON_','DYLD_')): env.pop(key)
                args=SHARED.sandbox(state,[source,bundle,runtime,ROOT])+[str(runtime/'Contents/MacOS/Electron'),
                     '--user-data-dir='+str(state/'user-data'),'-r',str(ROOT/'tools/probe-electron.cjs'),str(entry)]
                run=SHARED.run_bounded(args,env=env,cwd=state,timeout=25);case['runs'].append(run)
                run['receipt']=json.loads(receipt.read_text()) if receipt.is_file() else None
                if run['timed_out'] or run['returncode'] or not run['receipt'] or not run['receipt'].get('ok'):
                    raise ValueError(f'{shape} 第 {round_number} 轮失败；证据已保留。')
                if run['receipt']['electron'] != version or run['receipt']['arch'] != 'arm64' or run['receipt']['app_path'] != str(entry):
                    raise ValueError('实际运行版本、架构或app入口不符。')
            case['source_unchanged']=SHARED.inventory(source)==before_source
            case['bundle_unchanged']=SHARED.inventory(bundle)==before_bundle
        report['ok']=True
    except Exception as error:
        report['error']=str(error)
    finally:
        report['runtime_unchanged']=SHARED.inventory(runtime)==before_runtime
        for case,(source,before_source,bundle,before_bundle) in zip(report['cases'],fixtures):
            case['source_unchanged']=SHARED.inventory(source)==before_source
            case['bundle_unchanged']=SHARED.inventory(bundle)==before_bundle
        report['ok']=report['ok'] and report['runtime_unchanged'] and all(case['source_unchanged'] and case['bundle_unchanged'] for case in report['cases'])
        (output/'evidence.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','archive','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--version',default=VERSION)
    parser.add_argument('--sha256',default=ARCHIVE_SHA256)
    args=parser.parse_args()
    try:
        report=probe(**vars(args));print(json.dumps({'ok':report['ok'],'evidence':str(Path(args.output)/'evidence.json'),'error':report.get('error')},ensure_ascii=False));sys.exit(0 if report['ok'] else 1)
    except (OSError,ValueError,zipfile.BadZipFile) as error:
        print(f'Electron 探针未运行：{error}',file=sys.stderr);sys.exit(1)
