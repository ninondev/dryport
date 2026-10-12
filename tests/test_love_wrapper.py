# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""生产 LÖVE 壳：静态拒绝、原件保留和显式启用的无窗口机制验证。"""
import importlib
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'tools'))
INSPECT = importlib.import_module('love-inspect')
BUILD = importlib.import_module('love-build')
LAUNCH = importlib.import_module('love-launch')
SHARED = importlib.import_module('love-runtime')
PROBE = importlib.import_module('probe-love')
RUNTIME = os.environ.get('LOVE_TEST_APP')


def fixture(game, shape='directory', conf=PROBE.CONF, main=PROBE.MAIN):
    game.mkdir(parents=True)
    (game / 'protected.txt').write_text('untouched original\n')
    (game / 'love.dll').write_bytes(b'retained synthetic Windows runtime component')
    files = {'main.lua': main.encode(), 'conf.lua': conf.encode(), 'payload.txt': b'immutable fixture payload\n'}
    if shape == 'directory':
        for name, content in files.items():
            (game / name).write_bytes(content)
    else:
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as package:
            for name, content in files.items():
                package.writestr(name, content)
        prefix = b'MZ' + b'synthetic test prefix\0' * 16 if shape == 'exe' else b''
        (game / ('game.exe' if shape == 'exe' else 'game.love')).write_bytes(prefix + data.getvalue())
    return game


class LoveInspectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sn-love-inspect-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def test_three_original_container_shapes_have_static_version(self):
        for shape in ('directory', 'love', 'exe'):
            game = fixture(self.base / shape, shape)
            before = SHARED.inventory(game)
            info = INSPECT.inspect(game)
            self.assertEqual(info['version'], '11.5')
            self.assertEqual(info['shape'], shape)
            self.assertEqual(info['identity'], PROBE.IDENTITY)
            self.assertEqual(SHARED.inventory(game), before)

    def test_unknown_conf_conflicting_versions_and_dynamic_configuration_stop(self):
        invalid = [
            'function love.conf(t) t.identity="x" end',
            'function love.conf(t) t.version="11.4" t.identity="x" end',
            'function love.conf(t) t.version="11.5" or "11.4" t.identity="x" end',
            PROBE.CONF + '\nfunction love.conf(t) end',
            PROBE.CONF.replace('t.version = "11.5"', '-- t.version = "11.5"'),
            PROBE.CONF.replace('t.version = "11.5"', 'if false then t.version = "11.5" end'),
            PROBE.CONF.replace('t.identity = "dryport-love-probe"', 't.identity = "../outside"'),
            PROBE.CONF.replace('t.identity = "dryport-love-probe"', ''),
            PROBE.CONF.replace('t.window = false', 't.window.fullscreen = true'),
            PROBE.CONF.replace('t.window = false', 't.window.fullscreentype = "desktop"'),
        ]
        for index, conf in enumerate(invalid):
            with self.subTest(conf=conf), self.assertRaises(ValueError):
                INSPECT.inspect(fixture(self.base / str(index), conf=conf))

    def test_archive_does_not_borrow_unread_external_conf(self):
        game = fixture(self.base / 'archive', 'love')
        with zipfile.ZipFile(game / 'game.love', 'w') as package:
            package.writestr('main.lua', 'function love.run() end')
        (game / 'conf.lua').write_text(PROBE.CONF)
        with self.assertRaisesRegex(ValueError, '缺少 conf.lua'):
            INSPECT.inspect(game)

    def test_native_plugins_and_bytecode_stop_without_changing_files(self):
        for index, filename in enumerate(('steam_api64.dll', 'libsteam_api.dylib', 'module.node', 'hidden.luac')):
            game = fixture(self.base / str(index))
            (game / filename).write_bytes(b'plugin fixture')
            before = SHARED.inventory(game)
            with self.assertRaisesRegex(ValueError, '原生插件'):
                INSPECT.inspect(game)
            self.assertEqual(before, SHARED.inventory(game))
        game = fixture(self.base / 'bytecode', main='\x1bLJsome bytecode')
        with self.assertRaisesRegex(ValueError, '字节码'):
            INSPECT.inspect(game)

    def test_large_main_scan_does_not_retain_all_tokens(self):
        source = b'local x = 123\n' * 10000
        tracemalloc.start()
        try:
            INSPECT.check_lua('main.lua', source)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # 自有 140 KB Lua 样例的扫描分配须低于 2 MiB，避免 token 对象倍增。
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_explicit_external_calls_stop_but_comments_are_not_calls(self):
        for index, main in enumerate(('ffi.load("foo")', 'package.loadlib("foo", "bar")', 'os.execute("cmd")', 'io.popen("cmd")')):
            with self.assertRaisesRegex(ValueError, '本机库或外部程序'):
                INSPECT.inspect(fixture(self.base / str(index), main=main))
        INSPECT.inspect(fixture(self.base / 'comments', main='-- ffi.load("foo.dll")\n--[=[ os.execute("x") ]=]\n'))

    def test_ambiguous_entries_symlinks_and_unsafe_archive_stop(self):
        game = fixture(self.base / 'ambiguous', 'love')
        (game / 'other.love').write_bytes((game / 'game.love').read_bytes())
        with self.assertRaisesRegex(ValueError, '唯一'):
            INSPECT.inspect(game)
        game = fixture(self.base / 'linked')
        (game / 'foreign').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, '链接'):
            INSPECT.inspect(game)
        game = fixture(self.base / 'archive', 'love')
        with zipfile.ZipFile(game / 'game.love', 'a') as package:
            package.writestr('../escape', 'fixture')
        with self.assertRaisesRegex(ValueError, '歧义'):
            INSPECT.inspect(game)


@unittest.skipUnless(sys.platform == 'darwin', '原子发布使用 macOS RENAME_EXCL')
class LoveStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sn-love-state-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def launcher_fixture(self):
        # 只运行生产 Python 入口直到缺配置失败，不装运行时，也不运行 Lua。
        app = self.base / 'Fixture.app'
        macos, resources = app / 'Contents/MacOS', app / 'Contents/Resources'
        macos.mkdir(parents=True)
        resources.mkdir()
        for name in ('love-launch.py', 'love-runtime.py', 'runtime_archive.py', 'paths.py'):
            shutil.copyfile(ROOT / 'lib' / name, resources / name)
        launcher = macos / 'launcher'
        launcher.write_text((ROOT / 'lib/love-launcher.sh').read_text().replace(
            '__SN_LOVE_PYTHON__', shlex.quote(sys.executable)))
        launcher.chmod(0o755)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'io.dryport.love-launch-test',
            'CFBundleExecutable': 'launcher', 'CFBundlePackageType': 'APPL',
        }))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True,
                       capture_output=True)
        return app, launcher, resources

    def test_launcher_ignores_python_environment_before_loading_bundled_modules(self):
        app, launcher, resources = self.launcher_fixture()
        external = self.base / 'external-python'
        external.mkdir()
        marker = self.base / 'unexpected-module-executed'
        payload = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("unexpected")\n'
        for name in ('sitecustomize.py', 'usercustomize.py', 'runtime_archive.py'):
            (external / name).write_text(payload)
        for overrides in ({'PYTHONPATH': str(external)},
                          {'PYTHONPATH': str(external), 'PYTHONHOME': str(self.base / 'invalid-python-home')}):
            with self.subTest(overrides=overrides):
                run = subprocess.run([str(launcher)], env={**os.environ, **overrides}, capture_output=True, text=True)
                self.assertEqual(run.returncode, 1, run.stderr)
                self.assertIn('LÖVE 未启动', run.stderr)
                self.assertIn('love-launch.json', run.stderr)
                self.assertFalse(marker.exists(), '外部 Python 模块在验签或沙箱前执行')
        self.assertFalse((resources / '__pycache__').exists())
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True,
                       capture_output=True)

    def test_launcher_verifies_bundle_before_executing_python(self):
        app, launcher, resources = self.launcher_fixture()
        marker = self.base / 'unsigned-resource-executed'
        (resources / 'love-launch.py').write_text(
            'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("unexpected")\n')
        run = subprocess.run([str(launcher)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(marker.exists(), '签名不符的资源在检查前执行')

    def test_foreign_empty_and_link_states_preserved(self):
        for kind in ('empty', 'foreign', 'link'):
            root = self.base / kind
            root.mkdir()
            state = root / '123'
            if kind == 'link':
                state.symlink_to(self.base / 'missing')
            else:
                state.mkdir()
                if kind == 'foreign':
                    (state / 'save.txt').write_text('existing progress')
            with self.assertRaisesRegex(ValueError, '归属'):
                LAUNCH.state_directory(root, '123', [])
            self.assertTrue(os.path.lexists(state))
            if kind == 'foreign':
                self.assertEqual((state / 'save.txt').read_text(), 'existing progress')

    def test_atomic_state_interruption_is_retryable_and_existing_saves_survive(self):
        root = self.base / 'state'
        with patch.object(SHARED, 'publish_new', side_effect=InterruptedError('fixture interrupted')):
            with self.assertRaises(InterruptedError):
                LAUNCH.state_directory(root, '123', [])
        self.assertFalse((root / '123').exists())
        state = LAUNCH.state_directory(root, '123', [])
        (state / 'home/save.txt').write_text('existing progress')
        self.assertEqual(LAUNCH.state_directory(root, '123', []), state)
        self.assertEqual((state / 'home/save.txt').read_text(), 'existing progress')

    def test_exclusive_publish_preserves_concurrent_empty_target(self):
        stage, target = self.base / 'stage', self.base / 'target'
        stage.mkdir(); target.mkdir()
        (stage / 'payload').write_text('new')
        with self.assertRaises(FileExistsError):
            SHARED.publish_new(stage, target)
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual((stage / 'payload').read_text(), 'new')

    def test_runtime_rejection_creates_no_app_or_stage(self):
        game = fixture(self.base / 'game')
        before = SHARED.inventory(game)
        with patch.object(BUILD, 'check_love', side_effect=ValueError('official archive mismatch')):
            with self.assertRaisesRegex(ValueError, 'archive mismatch'):
                BUILD.build(game, 'Fixture', self.base / 'Fixture.app', '123')
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['game'])
        self.assertEqual(SHARED.inventory(game), before)

    def test_output_existing_and_protected_destinations_stop_before_runtime(self):
        game = fixture(self.base / 'game')
        foreign = self.base / 'Foreign.app'
        foreign.mkdir(); (foreign / 'data').write_text('keep')
        dangling = self.base / 'Link.app'
        dangling.symlink_to(self.base / 'user-new-destination')
        for output in (foreign, dangling):
            with self.assertRaises(FileExistsError):
                BUILD.build(game, 'Fixture', output, '123')
        with self.assertRaisesRegex(ValueError, '原游戏'):
            BUILD.build(game, 'Fixture', game / 'Nested.app', '123')
        self.assertEqual((foreign / 'data').read_text(), 'keep')
        self.assertFalse((self.base / 'user-new-destination').exists())
        with self.assertRaisesRegex(ValueError, '重叠'):
            LAUNCH.state_directory(game, '123', [game])
        self.assertFalse((game / '123').exists())


@unittest.skipUnless(sys.platform == 'darwin' and RUNTIME,
                     '需显式 LOVE_TEST_APP；不下载运行时或启动真实游戏')
class LoveWrapperMechanismTests(unittest.TestCase):
    def test_game_exit_code_and_damaged_bundle_fail_honestly(self):
        base = Path(tempfile.mkdtemp(prefix='sn-love-failure-')).resolve()
        game = fixture(base / 'game', main='function love.run() return function() return 7 end end')
        before = SHARED.inventory(game)
        app = base / 'Fixture.app'
        with patch.dict(os.environ, {'LOVE_APP': RUNTIME}):
            BUILD.build(game, '自有失败样例', app, '900010')
        env = {**os.environ, 'LOVE_STATE_ROOT': str(base / 'state')}
        run = SHARED.run_bounded([str(app / 'Contents/MacOS/launcher')], env=env)
        self.assertEqual(run['returncode'], 7, run)
        self.assertFalse(run['timed_out'])
        # 只故意损坏本测试生成的副本，原始 fixture 和原厂运行时不动。
        (app / 'Contents/Resources/OriginalGame/payload.txt').write_text('damaged synthetic copy')
        env['LOVE_STATE_ROOT'] = str(base / 'never-created-state')
        run = SHARED.run_bounded([str(app / 'Contents/MacOS/launcher')], env=env)
        self.assertNotEqual(run['returncode'], 0, run)
        self.assertIn('LÖVE 未启动', run['stderr'])
        self.assertFalse((base / 'never-created-state').exists())
        self.assertEqual(SHARED.inventory(game), before)

    def test_signed_wrapper_all_shapes_save_restart_and_write_protection(self):
        base = Path(tempfile.mkdtemp(prefix='sn-love-wrapper-')).resolve()
        evidence = {'kind': 'synthetic windowless mechanism only', 'cases': [], 'ok': False}
        print('LÖVE wrapper evidence: ' + str(base / 'evidence.json'), file=sys.stderr)
        runtime_before = SHARED.inventory(Path(RUNTIME))
        try:
            for number, shape in enumerate(('directory', 'love', 'exe'), 1):
                game = fixture(base / shape / 'source-grandparent/source-parent/Game', shape)
                app = base / shape / 'bundle-grandparent/bundle-parent/Fixture.app'
                app.parent.mkdir(parents=True)
                before = SHARED.inventory(game)
                with patch.dict(os.environ, {'LOVE_APP': RUNTIME}):
                    BUILD.build(game, '自有无窗口样例', app, str(900000 + number))
                original = app / 'Contents/Resources/OriginalGame'
                self.assertEqual(SHARED.inventory(original), before)
                full_app = SHARED.inventory(app)
                root = base / shape / 'state'
                prepared = LAUNCH.prepare(app, root)
                state = prepared['state']
                (state / 'source-alias').symlink_to(game)
                (state / 'bundle-alias').symlink_to(original)
                save = state / 'home/Library/Application Support'
                if shape != 'exe':
                    save /= 'LOVE'
                save /= PROBE.IDENTITY
                case = {'shape': shape, 'game': str(game), 'app': str(app), 'state': str(state), 'runs': []}
                evidence['cases'].append(case)
                for round_number in (1, 2):
                    receipt = state / f'round-{round_number}.json'
                    env = {**os.environ, 'LOVE_STATE_ROOT': str(root), 'SN_LOVE_STATE': str(state),
                           'SN_LOVE_SOURCE': str(game), 'SN_LOVE_BUNDLE': str(original),
                           'SN_LOVE_RECEIPT': str(receipt), 'SN_LOVE_ROUND': str(round_number),
                           'SN_LOVE_EXPECTED_SAVE': str(save)}
                    run = SHARED.run_bounded([str(app / 'Contents/MacOS/launcher')], env=env, cwd=base)
                    case['runs'].append(run)
                    run['receipt'] = json.loads(receipt.read_text()) if receipt.is_file() else None
                    self.assertFalse(run['timed_out'], run)
                    self.assertEqual(run['returncode'], 0, run)
                    self.assertTrue(run['receipt']['ok'], run)
                    self.assertEqual(run['receipt']['source'], str(prepared['entry']))
                    self.assertEqual(run['receipt']['arch'], 'arm64')
                    self.assertEqual(run['receipt']['version_string'], '11.5.0')
                    self.assertEqual(run['receipt']['fused'], shape == 'exe')
                    self.assertFalse(run['receipt']['window_module'])
                    self.assertFalse(run['receipt']['graphics_module'])
                    for label in ('source', 'bundle'):
                        for attempt in ('direct', 'alias', 'hardlink', 'parent_rename', 'grandparent_rename'):
                            self.assertEqual(run['receipt'][label + '_' + attempt + '_errno'], 1)
                self.assertEqual((save / 'counter.txt').read_text(), '2')
                self.assertEqual(SHARED.inventory(game), before)
                self.assertEqual(SHARED.inventory(original), before)
                self.assertEqual(SHARED.inventory(app), full_app)
                subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
            self.assertEqual(SHARED.inventory(Path(RUNTIME)), runtime_before)
            evidence['ok'] = True
        finally:
            (base / 'evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
