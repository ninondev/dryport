# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""生产 WebKit 打包边界；仅构建自有资源，绝不启动宿主或真实游戏。"""
import contextlib
import copy
import importlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
BUILD = importlib.import_module('webkit-build')
APPID = '4294967294'  # 自有样例身份，绝不触及生产游戏的持久存储。


def fixture(game):
    (game / 'resources/app/empty').mkdir(parents=True)
    (game / 'resources/app/release.html').write_text('<!doctype html><title>Self fixture</title>')
    (game / 'resources/app/payload.json').write_text('{"fixture":true}')
    (game / 'resources/app/.hidden').write_bytes(b'untouched hidden payload')
    (game / 'Game.exe').write_bytes(b'MZ synthetic Windows executable, never run')
    (game / 'steam_api64.dll').write_bytes(b'synthetic Windows component, never loaded')
    (game / '.dryport-appid').write_text(APPID)
    return game


def fixture_inspect(game, appid):
    # 只替换检查器输入；生产 inspect 没有任何样例或未知版本放行开关。
    return {'profile': 'koi-farm-1518810-v1', 'appid': appid,
            'content_root': 'resources/app', 'entry': 'release.html',
            'lifecycle': 'terminal-beforeunload',
            'resource_inventory': BUILD.INSPECT.inventory(game, 'resources/app')}


class WebKitIdentityTests(unittest.TestCase):
    def test_storage_identity_is_fixed_and_canonical(self):
        self.assertEqual(BUILD.store_uuid(APPID), BUILD.store_uuid('00' + APPID))
        self.assertNotEqual(BUILD.store_uuid(APPID), BUILD.store_uuid('1518810'))
        # 发布后的常量变化会移动存储 origin；该回归固定既有派生结果。
        self.assertEqual(BUILD.store_uuid('1518810'), '908fdaa9-c68f-5b87-a34a-0c5d4de41b92')

    def test_invalid_appids_cannot_enter_configuration(self):
        for value in ('0', '-1', '４２', '4294967296', '12;true', '', 1, True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BUILD.canonical_appid(value)

    def test_missing_developer_tools_fail_before_xcrun(self):
        with patch.object(BUILD.subprocess, 'run', side_effect=subprocess.CalledProcessError(2, ['xcode-select'])) as run:
            with self.assertRaisesRegex(ValueError, 'xcode-select --install'):
                BUILD.compile_host(Path('/unused-output'))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/xcode-select', '-p'])


@unittest.skipUnless(sys.platform == 'darwin', '真实构建与独占发布要求 macOS')
class WebKitBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = tempfile.TemporaryDirectory(prefix='sn-webkit-build-host-')
        cls.addClassCleanup(cls.compiled.cleanup)
        cls.host = Path(cls.compiled.name) / 'WebKitGame'
        # 编译真实生产 core/host 一次；其余边界样例复用这份自有二进制，均不执行它。
        cls.host_build = BUILD.compile_host(cls.host)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sn-webkit-wrapper-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.game = fixture(self.base / 'Original')
        self.output = self.base / 'Fixture.app'
        self.before = BUILD.inventory(self.game)
        self.inspection = self.enterContext(patch.object(BUILD.INSPECT, 'inspect', side_effect=fixture_inspect))
        self.compiler = self.enterContext(patch.object(BUILD, 'compile_host', side_effect=self.copy_host))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def copy_host(self, destination):
        shutil.copy2(self.host, destination)
        return copy.deepcopy(self.host_build)

    def build(self, name='自有 WebKit 样例', output=None):
        return BUILD.build(self.game, name, output or self.output, APPID)

    def assert_original_unchanged(self):
        self.assertEqual(BUILD.inventory(self.game), self.before)

    def test_real_clone_sign_and_complete_original_inventory(self):
        result = self.build('中' * 85 + 'a')  # 恰好 256 个 UTF-8 字节，宿主接受的上界。
        resources = result / 'Contents/Resources'
        self.assertEqual(BUILD.inventory(resources / 'OriginalGame'), self.before)
        self.assert_original_unchanged()
        self.assertEqual(len(self.inspection.call_args_list), 2)
        self.assertEqual(self.inspection.call_args_list[1].args[0].name, 'OriginalGame')
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(result)], check=True, capture_output=True)
        subprocess.run(['/usr/bin/lipo', str(result / 'Contents/MacOS/WebKitGame'), '-verify_arch', 'arm64'], check=True)
        self.assertEqual(list((result / 'Contents/MacOS').iterdir()), [result / 'Contents/MacOS/WebKitGame'])
        self.assertFalse(list(result.rglob('*.py')))
        self.assertFalse((result / 'Contents/Frameworks').exists())
        config = json.loads((resources / 'webkit-launch.json').read_text())
        self.assertEqual(config['content_root'], 'OriginalGame/resources/app')
        self.assertEqual(config['inventory'], self.before)
        self.assertEqual(config['resource_inventory'], fixture_inspect(self.game, APPID)['resource_inventory'])
        self.assertEqual(config['store_uuid'], BUILD.store_uuid(APPID))
        self.assertEqual(config['host_build']['target'], 'arm64-apple-macosx14.0')
        self.assertEqual(set(config['host_build']['source_sha256']), {'webkit-core.swift', 'webkit-host.swift'})
        self.assertFalse(list(self.base.glob('.webkit-stage-*')))

    def test_display_name_is_data_and_fixed_identity_survives_renaming(self):
        marker = self.base / 'must-not-execute'
        name = '$(touch ' + str(marker) + ') `false` <&> 中文'
        self.build(name)
        info = plistlib.loads((self.output / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleIdentifier'], 'io.dryport.webkit.' + APPID)
        self.assertEqual(info['CFBundleName'], 'WebKitGame' + APPID)
        self.assertEqual(info['CFBundleExecutable'], 'WebKitGame')
        self.assertEqual(info['CFBundleDisplayName'], name)
        self.assertFalse(marker.exists())
        second = self.build('另一个显示名', self.base / 'Renamed.app')
        a = json.loads((self.output / 'Contents/Resources/webkit-launch.json').read_text())
        b = json.loads((second / 'Contents/Resources/webkit-launch.json').read_text())
        self.assertEqual(a['store_uuid'], b['store_uuid'])
        self.assert_original_unchanged()

    def test_names_rejected_by_host_are_rejected_before_build(self):
        for name in ('', ' ', 'a\nb', 'a\0b', '中' * 86):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.build(name)
        self.inspection.assert_not_called()
        self.compiler.assert_not_called()

    def test_existing_file_directory_and_symlink_are_preserved(self):
        for index, kind in enumerate(('file', 'empty', 'nonempty', 'symlink')):
            output = self.base / (str(index) + '.app')
            if kind == 'file':
                output.write_bytes(b'existing user data')
            elif kind == 'symlink':
                output.symlink_to(self.base / 'missing target')
            else:
                output.mkdir()
                if kind == 'nonempty':
                    (output / 'canary').write_bytes(b'keep me')
            before = (output.lstat().st_ino, output.lstat().st_mode)
            with self.subTest(kind=kind), self.assertRaises(FileExistsError):
                self.build(output=output)
            self.assertEqual((output.lstat().st_ino, output.lstat().st_mode), before)
            if kind == 'file': self.assertEqual(output.read_bytes(), b'existing user data')
            if kind == 'nonempty': self.assertEqual((output / 'canary').read_bytes(), b'keep me')
        self.inspection.assert_not_called()
        self.assert_original_unchanged()

    def test_protected_roots_and_symlink_alias_cannot_receive_output(self):
        protected = [self.game]
        for key in ('SN_HOME', 'SN_RUNTIMES', 'STEAM'):
            path = self.base / key
            path.mkdir()
            self.enterContext(patch.object(BUILD, key, path))
            protected.append(path)
        for index, path in enumerate(protected):
            alias = self.base / ('alias' + str(index))
            alias.symlink_to(path, target_is_directory=True)
            for parent in (path, alias):
                with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, '输出不能'):
                    self.build(output=parent / 'MustNotWrite.app')
        self.inspection.assert_not_called()
        self.assert_original_unchanged()

    def test_original_symlink_and_special_file_refused(self):
        link = self.game / 'resources/app/link'
        link.symlink_to('payload.json')
        with self.assertRaisesRegex(ValueError, '链接'):
            self.build()
        link.unlink()
        os.mkfifo(self.game / 'fifo')
        with self.assertRaisesRegex(ValueError, '特殊文件'):
            self.build()
        self.compiler.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unknown_profile_and_resource_mismatch_stop_before_clone(self):
        for field, value in (('profile', 'unverified-profile'), ('resource_inventory', {})):
            info = fixture_inspect(self.game, APPID)
            info[field] = value
            self.inspection.side_effect = None
            self.inspection.return_value = info
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build()
        self.compiler.assert_not_called()
        self.assertFalse(list(self.base.glob('.webkit-stage-*')))
        self.assert_original_unchanged()

    def test_copy_is_independently_inspected_and_must_match(self):
        count = [0]
        def inspect(game, appid):
            result = fixture_inspect(game, appid)
            count[0] += 1
            if count[0] == 2:
                result['lifecycle'] = 'different-contract'
            return result
        self.inspection.side_effect = inspect
        with self.assertRaisesRegex(ValueError, '副本未通过'):
            self.build()
        self.compiler.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assert_original_unchanged()

    def test_compile_sign_and_verify_failure_never_publish(self):
        real_run = subprocess.run
        for index, failure in enumerate(('compile', 'sign', 'verify')):
            output = self.base / (failure + '.app')
            def run(argv, *args, **kwargs):
                should_fail = argv[0] == '/usr/bin/codesign' and (('--sign' in argv) if failure == 'sign' else ('--verify' in argv))
                if should_fail:
                    raise subprocess.CalledProcessError(9, argv)
                return real_run(argv, *args, **kwargs)
            self.compiler.side_effect = subprocess.CalledProcessError(7, ['self compile']) if failure == 'compile' else self.copy_host
            with self.subTest(failure=failure), patch.object(BUILD.subprocess, 'run', side_effect=run), self.assertRaises(subprocess.CalledProcessError):
                self.build(output=output)
            self.assertFalse(output.exists())
            self.assert_original_unchanged()
        self.assertEqual(len(list(self.base.glob('.webkit-stage-*'))), 3)

    def test_original_or_copy_changes_after_compile_stop_publication(self):
        original_bytes = (self.game / 'Game.exe').read_bytes()
        for target in ('source', 'copy'):
            def compile_and_mutate(destination):
                metadata = self.copy_host(destination)
                changed = self.game / 'Game.exe' if target == 'source' else destination.parent.parent / 'Resources/OriginalGame/Game.exe'
                changed.write_bytes(b'changed by this test')
                return metadata
            self.compiler.side_effect = compile_and_mutate
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, '签名后原件'):
                self.build(output=self.base / (target + '.app'))
            self.assertFalse((self.base / (target + '.app')).exists())
            (self.game / 'Game.exe').write_bytes(original_bytes)
            self.assert_original_unchanged()

    def test_concurrent_output_is_preserved_by_exclusive_publish(self):
        publish = BUILD.FILES.publish
        for collision in ('empty', 'nonempty', 'symlink'):
            identity = []
            output = self.base / (collision + '.app')
            def race(source, destination):
                if collision == 'symlink':
                    destination.symlink_to(self.base / 'missing target')
                else:
                    destination.mkdir()
                    if collision == 'nonempty':
                        (destination / 'canary').write_bytes(b'concurrent user data')
                identity.append(destination.lstat().st_ino)
                return publish(source, destination)
            with self.subTest(collision=collision), patch.object(BUILD.FILES, 'publish', side_effect=race), self.assertRaises(OSError):
                self.build(output=output)
            self.assertEqual(output.lstat().st_ino, identity[0])
            self.assertFalse((output / 'Contents').exists())
            if collision == 'nonempty':
                self.assertEqual((output / 'canary').read_bytes(), b'concurrent user data')
            self.assert_original_unchanged()


@unittest.skipUnless(sys.platform == 'darwin', '真实克隆与签名要求 macOS')
class WebKitDestinationRaceTests(unittest.TestCase):
    """不调用 Swift 编译器，直接复现构建各阶段的父目录换位。"""
    def run_race(self, phase='compile', protected='STEAM', replacement='same-inode'):
        with tempfile.TemporaryDirectory(prefix='sn-webkit-parent-race-') as directory, contextlib.ExitStack() as stack:
            base = Path(directory).resolve()
            game = fixture(base / 'Original')
            before = BUILD.inventory(game)
            parent = base / 'Output'
            parent.mkdir()
            output = parent / 'Fixture.app'
            protected_paths = {}
            for key in ('SN_HOME', 'SN_RUNTIMES', 'STEAM'):
                protected_paths[key] = base / key
                protected_paths[key].mkdir()
                stack.enter_context(patch.object(BUILD, key, protected_paths[key]))
            moved = protected_paths[protected] / 'moved-parent'
            original_inode = parent.stat().st_ino
            changed = []
            def move():
                self.assertFalse(changed)
                changed.append(True)
                parent.rename(moved)
                if replacement == 'same-inode':
                    parent.symlink_to(moved, target_is_directory=True)
                    self.assertEqual(parent.stat().st_ino, original_inode)
                elif replacement == 'different-inode':
                    parent.mkdir()
                    self.assertNotEqual(parent.stat().st_ino, original_inode)
            def inspect(path, appid):
                info = fixture_inspect(path, appid)
                if phase == 'inspect' and Path(path) == game:
                    move()
                return info
            clone = BUILD.FILES.clone
            def clone_and_move(source, destination):
                clone(source, destination)
                if phase == 'clone': move()
            def compile_and_move(destination):
                if phase == 'compile' and replacement == 'same-inode': move()
                # 只复制一个系统工具到自有样例，绝不执行；避免重复真实 Swift 编译。
                shutil.copyfile('/bin/echo', destination)
                destination.chmod(0o755)
                if phase == 'compile' and replacement != 'same-inode': move()
                return {'fixture': True}
            real_run = subprocess.run
            command_log = []
            def run(argv, *args, **kwargs):
                command_log.append(argv)
                result = real_run(argv, *args, **kwargs)
                if argv[0] == '/usr/bin/codesign':
                    if (phase == 'sign' and '--sign' in argv) or (phase == 'verify' and '--verify' in argv):
                        move()
                return result
            stack.enter_context(patch.object(BUILD.INSPECT, 'inspect', side_effect=inspect))
            compiler = stack.enter_context(patch.object(BUILD, 'compile_host', side_effect=compile_and_move))
            stack.enter_context(patch.object(BUILD.FILES, 'clone', side_effect=clone_and_move))
            stack.enter_context(patch.object(BUILD.subprocess, 'run', side_effect=run))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            with self.assertRaisesRegex(ValueError, '输出父目录的规范路径或身份变化'):
                BUILD.build(game, 'Self parent race', output, APPID)
            self.assertFalse((moved / 'Fixture.app').exists(), '保护根出现正式产物')
            self.assertFalse(output.exists(), '原目标出现正式产物')
            self.assertEqual(BUILD.inventory(game), before)
            if phase in ('inspect', 'clone'):
                compiler.assert_not_called()
            if phase in ('inspect', 'clone', 'compile'):
                self.assertFalse(list(moved.rglob('webkit-launch.json')), '发现路径换位后仍写入配置')
                self.assertFalse(any(argv[0] == '/usr/bin/codesign' for argv in command_log))
            if phase == 'sign':
                self.assertFalse(any(argv[0] == '/usr/bin/codesign' and '--verify' in argv for argv in command_log))

    def test_same_inode_moved_into_each_protected_root_is_rejected(self):
        for root in ('STEAM', 'SN_HOME', 'SN_RUNTIMES'):
            with self.subTest(root=root):
                self.run_race(protected=root)

    def test_recheck_after_inspection_clone_sign_and_verify(self):
        for phase in ('inspect', 'clone', 'sign', 'verify'):
            with self.subTest(phase=phase):
                self.run_race(phase=phase)

    def test_same_canonical_path_with_replaced_parent_inode_is_rejected(self):
        for replacement in ('different-inode', 'missing'):
            with self.subTest(replacement=replacement):
                self.run_race(replacement=replacement)

    def test_changed_protected_root_is_checked_without_parent_move(self):
        with tempfile.TemporaryDirectory(prefix='sn-webkit-protected-map-') as directory:
            base = Path(directory).resolve()
            game = fixture(base / 'Original')
            parent = base / 'Output'
            parent.mkdir()
            old_steam = base / 'OldSteam'
            old_steam.mkdir()
            steam_alias = base / 'SteamAlias'
            steam_alias.symlink_to(old_steam, target_is_directory=True)
            def compile_and_redirect(destination):
                shutil.copyfile('/bin/echo', destination)
                steam_alias.unlink()
                steam_alias.symlink_to(parent, target_is_directory=True)
                return {'fixture': True}
            with patch.object(BUILD, 'STEAM', steam_alias), patch.object(BUILD.INSPECT, 'inspect', side_effect=fixture_inspect), patch.object(BUILD, 'compile_host', side_effect=compile_and_redirect), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, '保护目录'):
                    BUILD.build(game, 'Self boundary race', parent / 'Fixture.app', APPID)
            self.assertFalse((parent / 'Fixture.app').exists())
            self.assertFalse(list(parent.rglob('webkit-launch.json')))


class WebKitGateTests(unittest.TestCase):
    def test_wrapper_requires_valid_non_refusing_receipt_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory(prefix='sn-webkit-gate-') as directory:
            base = Path(directory)
            lib = base / 'lib'
            lib.mkdir()
            for name in ('paths.sh', 'repack-gate.sh', 'webkit-wrap.sh'):
                shutil.copyfile(ROOT / 'lib' / name, lib / name)
            marker = base / 'builder-arguments.json'
            (lib / 'webkit-build.py').write_text('import json,os,sys\nopen(os.environ["TEST_MARKER"],"w").write(json.dumps(sys.argv[1:]))\n')
            (lib / 'risk.py').write_text('import os,sys\nprint(os.environ["TEST_RECEIPT"])\nsys.exit(int(os.environ["TEST_CODE"]))\n')
            env = {**os.environ, 'SN_HOME': str(base), 'SN_GAMES': str(base / 'games'),
                   'SN_RUNTIMES': str(base / 'runtimes'), 'STEAM': str(base / 'steam'),
                   'TEST_MARKER': str(marker), 'SN_FORCE_REPACK': '1'}
            verdicts = {0: '可原生改包', 2: '一律不碰', 3: '风险待核实'}
            for code in (2, 3, 1, 0):
                receipt = {'protocol': 1, 'appid': int(APPID), 'code': code, 'name': 'self',
                           'verdict': verdicts.get(code, 'broken'), 'why': ['self fixture']}
                env.update(TEST_CODE=str(code), TEST_RECEIPT=json.dumps(receipt))
                args = [str(base / 'game $(false)'), 'literal `false`', str(base / 'Output.app'), APPID]
                run = subprocess.run(['/bin/bash', str(lib / 'webkit-wrap.sh'), *args], env=env, capture_output=True, text=True)
                self.assertEqual(run.returncode, code if code in (0, 2, 3) else 3, run.stderr)
                if code:
                    self.assertFalse(marker.exists())
                else:
                    self.assertEqual(json.loads(marker.read_text()), args)


if __name__ == '__main__':
    unittest.main()
