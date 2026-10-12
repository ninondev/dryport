#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""创建自有启动壳；完整原件和官方 LÖVE 运行时均不改写。"""
import importlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile

from paths import SN_HOME, SN_LIB, SN_RUNTIMES
from runtime_archive import check_love, LOVE_ARCHIVE
SHARED = importlib.import_module('love-runtime')
INSPECT = importlib.import_module('love-inspect')
CLONE = importlib.import_module('nwjs-build').clone


def build(game, name, output, appid):
    if sys.platform != 'darwin':
        raise ValueError('LÖVE 原生打包需要 macOS。')
    if not appid.isascii() or not appid.isdecimal() or not 0 < int(appid) <= 4294967295:
        raise ValueError('Steam appid 必须是有效的十进制数字。')
    game = Path(game).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError(f'输出已存在，已完整保留：{output}')
    output = output.parent.resolve(strict=True) / output.name
    if not game.is_dir() or output.suffix != '.app':
        raise ValueError('游戏必须是完整目录，输出必须以 .app 结尾。')
    if output.is_relative_to(game) or output.is_relative_to(SN_HOME.resolve()):
        raise ValueError('输出不能放在原游戏或代码仓库内。')
    before = SHARED.inventory(game)
    info = INSPECT.inspect(game)
    runtime = Path(os.environ.get('LOVE_APP') or SN_RUNTIMES / 'love/11.5/love.app')
    check_love(runtime)
    runtime = runtime.resolve(strict=True)
    archive = runtime.parent / LOVE_ARCHIVE
    if not archive.is_file():
        archive = SN_RUNTIMES / 'build' / LOVE_ARCHIVE
    archive = archive.resolve(strict=True)
    if output.is_relative_to(runtime) or game.is_relative_to(runtime):
        raise ValueError('游戏或输出不能放在原厂运行时内。')
    stage = Path(tempfile.mkdtemp(prefix='.love-stage-', dir=output.parent))
    app = stage / output.name
    try:
        resources, macos = app / 'Contents/Resources', app / 'Contents/MacOS'
        resources.mkdir(parents=True)
        macos.mkdir()
        original = resources / 'OriginalGame'
        CLONE(game, original)
        if SHARED.inventory(original) != before or SHARED.inventory(game) != before:
            raise ValueError('复制期间原游戏变化或副本清单不符，已停止。')
        config = {'protocol': 1, 'appid': appid, 'name': name, 'source': str(game),
                  'repo': str(SN_HOME.resolve()), 'runtime': str(runtime), 'archive': str(archive),
                  'content_id': SHARED.inventory_digest(before), 'inventory': before, 'input': info}
        (resources / 'love-launch.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        (resources / 'steam_appid.txt').write_text(appid, encoding='ascii')
        for filename in ('love-launch.py', 'love-runtime.py', 'runtime_archive.py', 'paths.py'):
            shutil.copy2(SN_LIB / filename, resources / filename)
        launcher = (SN_LIB / 'love-launcher.sh').read_text(encoding='utf-8')
        (macos / 'launcher').write_text(launcher.replace('__SN_LOVE_PYTHON__', shlex.quote(sys.executable)), encoding='utf-8')
        (macos / 'launcher').chmod(0o755)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleName': name, 'CFBundleDisplayName': name,
            'CFBundleIdentifier': 'io.dryport.love.' + appid,
            'CFBundleExecutable': 'launcher', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'NSHighResolutionCapable': True,
        }))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
        if SHARED.inventory(original) != before or SHARED.inventory(game) != before:
            raise ValueError('最终原件清单或 SHA-256 不符，产物尚未发布。')
        SHARED.publish_new(app, output)
        stage.rmdir()
    except Exception:
        print(f'构建未完成；原件和已有输出保留，暂存位置：{stage}', file=sys.stderr)
        raise
    print(f'完成：{output}')
    print(f'原游戏清单与 SHA-256 一致：{len(before)} 项；LÖVE 11.5 原包直读，存档位于独立 HOME。')
    print('已完成静态版本及扩展检查；真实游戏画面、操作和性能仍需逐款验证。')
    return output


if __name__ == '__main__':
    try:
        if len(sys.argv) != 5:
            raise ValueError('用法：love-build.py <游戏目录> <显示名> <输出.app> <appid>')
        build(*sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'LÖVE 打包失败：{error}', file=sys.stderr)
        sys.exit(1)
