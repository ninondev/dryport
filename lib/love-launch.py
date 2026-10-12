#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""在独立 HOME 内启动经原厂 ZIP 核对的 LÖVE；不修改原件或迁移存档。"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from runtime_archive import check_love
SHARED = importlib.import_module('love-runtime')


def state_directory(root, appid, protected):
    root = Path(root).expanduser().resolve()
    state = root / appid
    for path in protected:
        if state == path or state.is_relative_to(path) or path.is_relative_to(state):
            raise ValueError('存档目录与原件、app、运行时或代码仓库重叠，未写入。')
    owner = {'protocol': 1, 'kind': 'dryport-love', 'appid': appid, 'layout': 1}
    marker = state / '.dryport-love.json'
    if os.path.lexists(state):
        if state.is_symlink() or not state.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError(f'存档位置没有本工具归属凭据，已有内容保留：{state}')
        if json.loads(marker.read_text()) != owner:
            raise ValueError(f'存档目录身份或格式不符，已有内容保留：{state}')
        for name in ('home', 'tmp'):
            path = state / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f'存档目录结构异常，未自动修复或覆盖：{path}')
    else:
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.love-state-', dir=root))
        (stage / 'home').mkdir()
        (stage / 'tmp').mkdir()
        (stage / marker.name).write_text(json.dumps(owner), encoding='utf-8')
        # 回执和 HOME 一次提交；中断不会留下看似已归属的半个最终目录。
        SHARED.publish_new(stage, state)
    return state


def prepare(app, state_root=None):
    app = Path(app).resolve(strict=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
    resources = app / 'Contents/Resources'
    config = json.loads((resources / 'love-launch.json').read_text(encoding='utf-8'))
    appid = config.get('appid', '')
    if (config.get('protocol') != 1 or not isinstance(appid, str) or not appid.isascii()
            or not appid.isdecimal() or not 0 < int(appid) <= 4294967295):
        raise ValueError('启动配置身份或协议不符。')
    info = config['input']
    if info.get('version') != '11.5' or info.get('shape') not in ('directory', 'love', 'exe'):
        raise ValueError('启动配置不是已检查的 LÖVE 11.5 输入。')
    original = resources / 'OriginalGame'
    entry = (original / info['entry']).resolve(strict=True)
    if not entry.is_relative_to(original) or original.is_symlink():
        raise ValueError('游戏入口越出原件目录。')
    runtime = Path(config['runtime']).resolve(strict=True)
    archive = Path(config['archive']).resolve(strict=True)
    check_love(runtime, archive)
    protected = [app, Path(config['source']).resolve(), runtime, archive, Path(config['repo']).resolve()]
    root = state_root or os.environ.get('LOVE_STATE_ROOT') or Path.home() / 'Library/Application Support/dryport/love'
    state = state_directory(root, appid, protected)
    env = dict(os.environ)
    env.update(HOME=str(state / 'home'), TMPDIR=str(state / 'tmp') + '/')
    for variable in list(env):
        if variable.startswith(('LUA_', 'DYLD_')):
            env.pop(variable)
    command = SHARED.sandbox(state, protected + [state / '.dryport-love.json'])
    command += [str(runtime / 'Contents/MacOS/love')]
    if info['shape'] == 'exe':
        command += ['--fused']
    command += ['--game', str(entry)]
    return {'app': app, 'state': state, 'entry': entry, 'command': command, 'env': env, 'cwd': original}


def main():
    if len(sys.argv) != 2:
        raise ValueError('启动器只接受自己的 app 路径，不接受覆盖游戏入口的参数。')
    launch = prepare(sys.argv[1])
    os.chdir(launch['cwd'])
    os.execve(launch['command'][0], launch['command'], launch['env'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'LÖVE 未启动：{error}', file=sys.stderr)
        sys.exit(1)
