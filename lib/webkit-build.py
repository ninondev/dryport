#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""完整保留已核实原包，构建仅依赖系统 WebKit 的自有启动壳。"""
import importlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import uuid

from paths import SN_HOME, SN_LIB, SN_RUNTIMES, STEAM

INSPECT = importlib.import_module('webkit-inspect')
FILES = importlib.import_module('nwjs-build')

# 已发布后不可变：只用 appid 派生 origin，改展示名、安装位置或内容版本不会更换存储身份。
STORE_NAMESPACE = uuid.UUID('e0a39fd6-84b4-497c-84b2-cef8b9cfd471')
TARGET = 'arm64-apple-macosx14.0'
COMPILE_FLAGS = ['-parse-as-library', '-swift-version', '6', '-strict-concurrency=complete',
                 '-enable-upcoming-feature', 'NonisolatedNonsendingByDefault', '-O', '-target', TARGET]


def canonical_appid(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 4294967295:
        raise ValueError('Steam appid 必须是有效的十进制数字。')
    return str(int(value))


def store_uuid(appid):
    return str(uuid.uuid5(STORE_NAMESPACE, canonical_appid(appid)))


def inventory(root):
    result = FILES.inventory(root)
    if any(item[0] == 'link' for item in result.values()):
        raise ValueError('原包含链接，尚未支持；原件已保留。')
    return result


def compile_host(destination):
    try:
        subprocess.run(['/usr/bin/xcode-select', '-p'], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('缺少可用的 Apple 开发工具。请先运行 xcode-select --install 安装命令行工具，再重试打包。') from error
    sources = [SN_LIB / 'webkit-core.swift', SN_LIB / 'webkit-host.swift']
    fingerprints = {path.name: FILES.digest(path) for path in sources}
    version = subprocess.check_output(['/usr/bin/xcrun', 'swiftc', '--version'], text=True, timeout=30).strip()
    subprocess.run(['/usr/bin/xcrun', 'swiftc', *COMPILE_FLAGS, *map(str, sources), '-o', str(destination)],
                   check=True, timeout=180)
    subprocess.run(['/usr/bin/lipo', str(destination), '-verify_arch', 'arm64'], check=True, timeout=10)
    if any(FILES.digest(path) != fingerprints[path.name] for path in sources):
        raise ValueError('构建期间宿主源码变化，产物尚未发布。')
    return {'target': TARGET, 'swift_version': version, 'source_sha256': fingerprints, 'flags': COMPILE_FLAGS}


def resource_records(items, relative_root):
    prefix = relative_root + '/'
    return {path[len(prefix):]: {'bytes': item[1], 'sha256': item[2]}
            for path, item in items.items() if path.startswith(prefix) and item[0] == 'file'}


def directory_identity(path):
    status = path.stat()
    return status.st_dev, status.st_ino


def build(game, name, output, appid):
    if sys.platform != 'darwin':
        raise ValueError('WebKit 原生打包需要 macOS。')
    appid = canonical_appid(appid)
    if (not isinstance(name, str) or not name.strip() or any(ord(char) < 32 for char in name)
            or len(name.encode('utf-8')) > 256):
        raise ValueError('显示名不能为空、含控制字符或超过 256 个 UTF-8 字节。')
    game = Path(game).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError(f'输出已存在，已完整保留：{output}')
    if not game.is_dir() or output.suffix != '.app':
        raise ValueError('游戏必须是完整目录，输出必须以 .app 结尾。')
    output = output.parent.resolve(strict=True) / output.name
    protected_sources = (game, SN_HOME, SN_RUNTIMES, STEAM)
    protected_roots = tuple(path.resolve() for path in protected_sources)
    for protected in protected_roots:
        if output.is_relative_to(protected):
            raise ValueError('输出不能放在原游戏、代码、运行时或 Steam 目录内。')
    parent_identity = directory_identity(output.parent)

    def check_destination():
        # 同一 inode 可以被移进保护根，再由旧路径的软链指回；身份相同不等于位置没变。
        try:
            unchanged = (output.parent.resolve(strict=True) == output.parent
                         and directory_identity(output.parent) == parent_identity)
        except OSError:
            unchanged = False
        if not unchanged:
            raise ValueError('输出父目录的规范路径或身份变化，产物尚未发布。')
        roots = protected_roots + tuple(path.resolve() for path in protected_sources)
        if any(output.is_relative_to(root) for root in roots):
            raise ValueError('输出已进入原游戏、代码、运行时或 Steam 保护目录，产物尚未发布。')
        if os.path.lexists(output):
            raise FileExistsError(f'输出已出现，已完整保留：{output}')

    source_identity = directory_identity(game)
    before = inventory(game)
    info = INSPECT.inspect(game, appid)
    if (info.get('profile') != 'koi-farm-1518810-v1' or info.get('content_root') != 'resources/app'
            or info.get('entry') != 'release.html' or info.get('lifecycle') != 'terminal-beforeunload'
            or info.get('appid') != appid):
        raise ValueError('检查器没有返回已接通的 WebKit 入口。')
    expected_resources = resource_records(before, info['content_root'])
    if not expected_resources or expected_resources != info.get('resource_inventory'):
        raise ValueError('入口检查与完整原包清单不一致，产物尚未发布。')
    check_destination()
    stage = Path(tempfile.mkdtemp(prefix='.webkit-stage-', dir=output.parent))
    app = stage / output.name
    try:
        resources, macos = app / 'Contents/Resources', app / 'Contents/MacOS'
        check_destination()
        resources.mkdir(parents=True)
        macos.mkdir()
        original = resources / 'OriginalGame'
        check_destination()
        FILES.clone(game, original)
        check_destination()
        if inventory(original) != before or inventory(game) != before or directory_identity(game) != source_identity:
            raise ValueError('复制期间原游戏变化或副本清单不符，已停止。')
        if INSPECT.inspect(original, appid) != info:
            raise ValueError('原包副本未通过同一入口检查，产物尚未发布。')
        check_destination()
        host_build = compile_host(macos / 'WebKitGame')
        check_destination()
        config = {'protocol': 1, 'appid': appid, 'name': name, 'profile': info['profile'],
                  'content_root': 'OriginalGame/' + info['content_root'], 'entry': info['entry'],
                  'store_uuid': store_uuid(appid), 'lifecycle': info['lifecycle'],
                  'resource_inventory': expected_resources, 'source': str(game), 'inventory': before,
                  'host_build': host_build}
        (resources / 'webkit-launch.json').write_text(json.dumps(config, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        check_destination()
        (resources / 'steam_appid.txt').write_text(appid, encoding='ascii')
        check_destination()
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'io.dryport.webkit.' + appid,
            'CFBundleName': 'WebKitGame' + appid, 'CFBundleDisplayName': name,
            'CFBundleExecutable': 'WebKitGame', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
            'LSMinimumSystemVersion': '14.0', 'LSArchitecturePriority': ['arm64'],
            'NSHighResolutionCapable': True,
        }))
        # 只签自有 app；不递归重签或改写 OriginalGame 里的任何文件。
        check_destination()
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True, timeout=60)
        check_destination()
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True, timeout=60)
        check_destination()
        if (inventory(original) != before or inventory(game) != before
                or directory_identity(game) != source_identity
                or directory_identity(output.parent) != parent_identity):
            raise ValueError('签名后原件、副本或输出父目录变化，产物尚未发布。')
        check_destination()
        FILES.publish(app, output)
        stage.rmdir()
    except Exception:
        print(f'构建未完成；原件和已有输出保留，暂存位置：{stage}', file=sys.stderr)
        raise
    print(f'完成：{output}')
    print(f'完整原包清单与 SHA-256 一致：{len(before)} 项；Windows 文件一并保留。')
    print('启动使用系统 WebKit；浏览器存储身份固定，重新打包和改名不会更换身份。')
    return output


if __name__ == '__main__':
    try:
        if len(sys.argv) != 5:
            raise ValueError('用法：webkit-build.py <游戏目录> <显示名> <输出.app> <appid>')
        build(*sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'WebKit 打包失败：{error}', file=sys.stderr)
        sys.exit(1)
