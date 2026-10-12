# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Ren'Py 生产启动层的自有行为测试；不运行引擎或游戏。"""
import copy
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import plistlib
import shutil
import signal
import time
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
LAUNCH = importlib.import_module("renpy-launch")


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("link", os.readlink(path))
        elif path.is_file():
            result[relative] = ("file", path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            result[relative] = ("directory",)
    return result


class TemporaryFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-renpy-wrapper-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()


@unittest.skipUnless(sys.platform == "darwin", "状态发布使用 macOS RENAME_EXCL")
class StateDirectoryTests(TemporaryFixture):
    def test_first_creation_and_reuse_preserve_existing_saves(self):
        root = self.base / "state"
        state = LAUNCH.state_directory(root, [])
        self.assertEqual(state, root / LAUNCH.APPID)
        self.assertEqual(json.loads((state / ".dryport-renpy.json").read_text()),
                         {"protocol": 1, "kind": "dryport-renpy", "appid": LAUNCH.APPID})
        (state / "saves/persistent").write_bytes(b"original preference bytes")
        (state / "saves/slot1.save").write_bytes(b"original story progress")
        before = inventory(state)
        self.assertEqual(LAUNCH.state_directory(root, []), state)
        self.assertEqual(inventory(state), before)

    def test_unowned_empty_or_foreign_directories_refused_intact(self):
        for kind in ("empty", "foreign", "wrong-owner"):
            with self.subTest(kind=kind):
                root = self.base / kind
                state = root / LAUNCH.APPID
                state.mkdir(parents=True)
                if kind != "empty":
                    (state / "save").write_bytes(b"keep")
                if kind == "wrong-owner":
                    (state / ".dryport-renpy.json").write_text(json.dumps(
                        {"protocol": 1, "kind": "dryport-renpy", "appid": "900001"}))
                before = inventory(root)
                with self.assertRaises(ValueError):
                    LAUNCH.state_directory(root, [])
                self.assertEqual(inventory(root), before)

    def test_state_marker_and_required_directory_links_refused(self):
        external = self.base / "external"
        external.mkdir()
        for kind in ("state", "marker", "saves"):
            with self.subTest(kind=kind):
                root = self.base / kind
                if kind == "state":
                    root.mkdir()
                    (root / LAUNCH.APPID).symlink_to(external)
                else:
                    state = LAUNCH.state_directory(root, [])
                    target = state / (".dryport-renpy.json" if kind == "marker" else kind)
                    target.unlink() if target.is_file() else target.rmdir()
                    target.symlink_to(external)
                before = inventory(self.base)
                with self.assertRaises(ValueError):
                    LAUNCH.state_directory(root, [])
                self.assertEqual(inventory(self.base), before)

    def test_protected_overlap_and_ancestor_are_rejected_before_creation(self):
        root = self.base / "state"
        state = root / LAUNCH.APPID
        for protected in (root, state, state / "game"):
            with self.subTest(protected=protected), self.assertRaisesRegex(ValueError, "重叠"):
                LAUNCH.state_directory(root, [protected])
        self.assertFalse(root.exists())

    def test_concurrent_target_is_preserved_and_stage_is_not_published(self):
        root = self.base / "state"
        real_publish = LAUNCH.publish
        def publish_with_rival(source, target):
            target.mkdir()
            (target / "concurrent-save").write_bytes(b"keep rival")
            real_publish(source, target)
        with mock.patch.object(LAUNCH, "publish", side_effect=publish_with_rival):
            with self.assertRaises(FileExistsError):
                LAUNCH.state_directory(root, [])
        state = root / LAUNCH.APPID
        self.assertEqual(sorted(path.name for path in state.iterdir()), ["concurrent-save"])
        self.assertEqual((state / "concurrent-save").read_bytes(), b"keep rival")
        self.assertEqual(len(list(root.glob(".renpy-state-*"))), 1)

    def test_parent_relocation_into_protected_root_does_not_publish(self):
        root, protected = self.base / "state", self.base / "source"
        protected.mkdir()
        real_write = Path.write_text
        def relocate_after_marker(path, *args, **kwargs):
            result = real_write(path, *args, **kwargs)
            if path.name == ".dryport-renpy.json":
                moved = protected / "moved-state"
                root.rename(moved)
                root.symlink_to(moved)
            return result
        with mock.patch.object(Path, "write_text", relocate_after_marker):
            with self.assertRaises((ValueError, OSError)):
                LAUNCH.state_directory(root, [protected])
        self.assertFalse((protected / "moved-state" / LAUNCH.APPID).exists())


@unittest.skipUnless(sys.platform == "darwin", "状态发布使用 macOS RENAME_EXCL")
class LaunchPreparationTests(TemporaryFixture):
    def setUp(self):
        super().setUp()
        self.app = self.base / "Fixture.app"
        self.resources = self.app / "Contents/Resources"
        self.original = self.resources / "autorun"
        self.original.mkdir(parents=True)
        (self.original / "gundog.py").write_bytes(b"synthetic entry; must never execute")
        self.runtime = self.base / "shared-runtime"
        binaries = self.runtime / "lib/py3-mac-universal"
        binaries.mkdir(parents=True)
        for name in ("pythonw", "librenpython.dylib", "libsteam_api.dylib"):
            (binaries / name).write_bytes(b"synthetic bytes; only mock signature reads")
        self.source, self.repo = self.base / "source", self.base / "repo"
        for directory in (self.source, self.repo):
            directory.mkdir()
            (directory / "canary").write_bytes(b"unchanged original")
        self.state_root = self.base / "state"
        self.config = {"protocol": 2, "appid": LAUNCH.APPID, "version": LAUNCH.VERSION, "entry": "gundog.py",
                       "runtime": str(self.runtime), "source": str(self.source), "repo": str(self.repo),
                       "state_root": str(self.state_root)}
        self.write_config()
        self.signature_calls = []
        def codesign(argv, **kwargs):
            self.assertEqual(argv[0], "/usr/bin/codesign", "prepare 不得启动游戏或任意程序")
            self.assertIn("--verify", argv)
            self.assertNotIn("--force", argv)
            self.assertTrue(kwargs.get("check"))
            self.signature_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0)
        patch = mock.patch.object(LAUNCH.subprocess, "run", side_effect=codesign)
        self.codesign_mock = patch.start()
        self.addCleanup(patch.stop)

    def write_config(self):
        (self.resources / "renpy-launch.json").write_text(json.dumps(self.config))

    def test_native_entry_game_cwd_and_clean_environment(self):
        protected = {path: inventory(path) for path in (self.app, self.runtime, self.source, self.repo)}
        hostile = {"RENPY_NO_STEAM": "1", "PYTHONPATH": "/foreign/modules", "PYTHONHOME": "/foreign/python",
                   "DYLD_INSERT_LIBRARIES": "/foreign/library.dylib", "DYLD_LIBRARY_PATH": "/foreign",
                   "RENPY_SEARCHPATH": "/foreign/scripts", "SN_RENPY_FIRST_RUN": "0"}
        with mock.patch.dict(os.environ, hostile):
            result = LAUNCH.prepare(self.app)
        command, env, state = result["command"], result["env"], result["state"]
        self.assertEqual(command[-2:], [str(self.app / "Contents/MacOS/launcher"), "--engine"])
        self.assertNotIn(str(self.runtime / "lib/py3-mac-universal/pythonw"), command)
        self.assertNotIn(str(self.original / "gundog.py"), command)
        self.assertEqual(result["cwd"], state)
        self.assertNotEqual(state, self.runtime)
        self.assertEqual((state / "steam_appid.txt").read_text().strip(), LAUNCH.APPID)
        self.assertEqual(env["SN_RENPY_FIRST_RUN"], "1")
        self.assertEqual(env["SN_RENPY_AUTORUN"], str(self.original))
        for key in env:
            self.assertFalse(key.startswith("DYLD_"), key)
        for key in ("RENPY_NO_STEAM", "PYTHONHOME", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["RENPY_SEARCHPATH"], str(self.resources / "extensions"))
        self.assertFalse(any(self.runtime.rglob("steam_appid.txt")))
        self.assertEqual({path: inventory(path) for path in protected}, protected)
        self.assertEqual(len(self.signature_calls), 4)

    def test_existing_persistent_and_save_reused_without_changes(self):
        first = LAUNCH.prepare(self.app)
        persistent, save = first["state"] / "saves/persistent", first["state"] / "saves/slot.save"
        persistent.write_bytes(b"user preference payload")
        save.write_bytes(b"user story payload")
        before = inventory(first["state"] / "saves")
        second = LAUNCH.prepare(self.app)
        self.assertEqual(second["state"], first["state"])
        self.assertEqual(second["env"]["SN_RENPY_FIRST_RUN"], "0")
        self.assertNotEqual(second["logs"], first["logs"])
        self.assertEqual(inventory(first["state"] / "saves"), before)

    def test_wrong_or_linked_appid_refused_without_overwriting(self):
        first = LAUNCH.prepare(self.app)
        marker = first["state"] / "steam_appid.txt"
        marker.write_text("900002\n")
        before = inventory(first["state"])
        with self.assertRaisesRegex(ValueError, "AppID"):
            LAUNCH.prepare(self.app)
        self.assertEqual(inventory(first["state"]), before)
        marker.unlink()
        external = self.base / "external-appid"
        external.write_text(LAUNCH.APPID)
        marker.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "AppID"):
            LAUNCH.prepare(self.app)
        self.assertEqual(external.read_text(), LAUNCH.APPID)

    def test_invalid_configuration_or_signature_stops_before_state(self):
        for field, value in (("appid", "900003"), ("version", "8.1.4"), ("entry", "other.py")):
            with self.subTest(field=field):
                original = self.config[field]
                self.config[field] = value
                self.write_config()
                with self.assertRaises(ValueError):
                    LAUNCH.prepare(self.app)
                self.assertFalse(self.state_root.exists())
                self.config[field] = original
        self.write_config()
        self.codesign_mock.side_effect = subprocess.CalledProcessError(1, ["codesign"])
        with self.assertRaises(subprocess.CalledProcessError):
            LAUNCH.prepare(self.app)
        self.assertFalse(self.state_root.exists())

    def test_actual_sandbox_allows_own_state_but_denies_protected_canaries(self):
        prepared = LAUNCH.prepare(self.app)
        state = prepared["state"]
        targets = [self.source / "canary", self.repo / "canary", self.original / "gundog.py",
                   self.runtime / "lib/py3-mac-universal/pythonw", state / "steam_appid.txt",
                   state / ".dryport-renpy.json"]
        alias = state / "source-alias"
        alias.symlink_to(self.source)
        targets.append(alias / "canary")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in targets}
        prefix = prepared["command"][:-2]
        program = ('import errno,json,sys\nfrom pathlib import Path\n'
                   'Path(sys.argv[1]).write_bytes(b"allowed")\nresults=[]\n'
                   'for path in json.loads(sys.argv[2]):\n'
                   ' try:\n  with open(path,"ab") as stream: stream.write(b"forbidden")\n'
                   ' except OSError as error: results.append(error.errno)\n'
                   ' else: results.append(0)\nprint(json.dumps(results))\n')
        # Popen 不在 prepare 的 codesign mock 中；只执行系统 Python 的自有写保护样例。
        child = subprocess.Popen(prefix + ["/usr/bin/python3", "-I", "-S", "-B", "-c", program,
                                           str(state / "saves/allowed"), json.dumps(list(map(str, targets)))],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 cwd=state, env=prepared["env"], start_new_session=True)
        try:
            stdout, stderr = child.communicate(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout), [errno.EPERM] * len(targets))
        self.assertEqual((state / "saves/allowed").read_bytes(), b"allowed")
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in targets}, before)


class ExtensionBehaviorTests(TemporaryFixture):
    def environment(self, first_run):
        return {"SN_RENPY_AUTORUN": str(self.base / "autorun"), "SN_RENPY_SAVES": str(self.base / "saves"),
                "SN_RENPY_LOGDIR": str(self.base / "logs"), "SN_RENPY_FIRST_RUN": "1" if first_run else "0"}

    def renpy_fixture(self, env):
        renpy = types.ModuleType("renpy")
        renpy.version_only = "8.1.3.23091805"
        renpy.macapp = True
        renpy.config = types.SimpleNamespace(renpy_base=env["SN_RENPY_AUTORUN"], savedir=env["SN_RENPY_SAVES"],
                                             logdir=env["SN_RENPY_AUTORUN"], enable_steam=True,
                                             early_script_version=(8, 1, 3))
        self.calls, self.commands = [], {}
        def steam_init():
            self.calls.append("original Steam init")
        def original_callback():
            self.calls.append("original post_init")
        def register(name, callback, **kwargs):
            self.commands[name] = (callback, kwargs)
        renpy.arguments = types.SimpleNamespace(register_command=register)
        renpy.game = types.SimpleNamespace(args=types.SimpleNamespace(command="sn_native", savedir=env["SN_RENPY_SAVES"]),
            preferences=types.SimpleNamespace(fullscreen=True, language="fr", volume={"music": 0.35}),
            post_init=[original_callback], interface=None)
        renpy.steam = types.SimpleNamespace(init=steam_init)
        renpy.script = types.SimpleNamespace(digest="unchanged script", namemap={"original": object()})
        self.achievement = types.ModuleType("store.achievement")
        self.achievement.steam = renpy.steam
        self.steam_appid_query = mock.Mock(return_value=int(LAUNCH.APPID))
        self.achievement.steamapi = types.SimpleNamespace(SteamUtils=lambda: types.SimpleNamespace(GetAppID=self.steam_appid_query))
        return renpy

    def execute(self, renpy, env, late_callback=None):
        with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules, {"renpy": renpy, "store.achievement": self.achievement}):
            namespace = runpy.run_path(str(ROOT / "lib/renpy-extension.py"))
            if late_callback:
                renpy.game.post_init.append(late_callback)
            callbacks_before = list(renpy.game.post_init)
            # 固定原厂 main.py 以 for 遍历同一列表，追加回调也会执行，不移除已执行项。
            for index, callback in enumerate(renpy.game.post_init):
                self.assertLess(index, 64, "自有回调链未收束")
                callback()
        return namespace, callbacks_before

    def test_first_post_init_windows_only_after_original_hooks(self):
        env = self.environment(True)
        (self.base / "logs").mkdir()
        renpy = self.renpy_fixture(env)
        steam_init, script, namemap = renpy.steam.init, renpy.script, renpy.script.namemap
        existing = renpy.game.post_init[0]
        original_prefs = copy.deepcopy(vars(renpy.game.preferences))
        _, callbacks = self.execute(renpy, env)
        self.assertIs(callbacks[0], existing)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(self.calls, ["original post_init"])
        self.assertIs(renpy.steam.init, steam_init)
        self.assertIs(renpy.script, script)
        self.assertIs(renpy.script.namemap, namemap)
        self.assertEqual(vars(renpy.game.preferences), {**original_prefs, "fullscreen": False})
        self.assertEqual(renpy.config.logdir, env["SN_RENPY_LOGDIR"])
        callback, options = self.commands["sn_native"]
        self.assertIs(callback(), True)
        self.assertTrue(options["uses_display"])
        receipt = json.loads((self.base / "logs/initialized.json").read_text())
        self.assertIs(receipt["fullscreen"], False)
        self.assertIs(receipt["interface_absent"], True)
        self.assertIs(receipt["steam_enabled"], True)
        self.assertIs(receipt["steam_initialized"], True)
        self.assertEqual(receipt["steam_appid"], int(LAUNCH.APPID))
        self.assertEqual(receipt["early_script_version"], [8, 1, 3])
        self.steam_appid_query.assert_called_once_with()

    def test_subsequent_launch_respects_user_fullscreen_and_other_preferences(self):
        env = self.environment(False)
        (self.base / "logs").mkdir()
        renpy = self.renpy_fixture(env)
        before = copy.deepcopy(vars(renpy.game.preferences))
        self.execute(renpy, env)
        self.assertEqual(vars(renpy.game.preferences), before)
        self.assertIs(json.loads((self.base / "logs/initialized.json").read_text())["fullscreen"], True)

    def test_first_window_preference_waits_for_callbacks_appended_later(self):
        env = self.environment(True)
        (self.base / "logs").mkdir()
        renpy = self.renpy_fixture(env)
        events = []
        def final_game_callback():
            events.append("final game callback")
            renpy.game.preferences.fullscreen = True
            renpy.game.preferences.language = "zh"
        def appended_game_callback():
            events.append("appended game callback")
            renpy.game.post_init.append(final_game_callback)
        def later_game_callback():
            events.append("later game callback")
            renpy.game.post_init.append(appended_game_callback)
        self.execute(renpy, env, late_callback=later_game_callback)
        self.assertEqual(events, ["later game callback", "appended game callback", "final game callback"])
        self.assertIs(renpy.game.preferences.fullscreen, False)
        self.assertEqual(renpy.game.preferences.language, "zh")
        self.steam_appid_query.assert_called_once_with()

    def test_wrong_engine_or_entry_refuses_before_callbacks_or_configuration_changes(self):
        env = self.environment(True)
        for condition in ("version", "platform", "command", "autorun", "savedir"):
            with self.subTest(condition=condition):
                renpy = self.renpy_fixture(env)
                if condition == "version": renpy.version_only = "8.1.4"
                elif condition == "platform": renpy.macapp = False
                elif condition == "command": renpy.game.args.command = "run"
                elif condition == "autorun": renpy.config.renpy_base = str(self.base / "other")
                else: renpy.game.args.savedir = str(self.base / "other")
                before_config, before_callbacks = vars(renpy.config).copy(), list(renpy.game.post_init)
                with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules, {"renpy": renpy}):
                    with self.assertRaises(RuntimeError):
                        runpy.run_path(str(ROOT / "lib/renpy-extension.py"))
                self.assertEqual(vars(renpy.config), before_config)
                self.assertEqual(renpy.game.post_init, before_callbacks)
                self.assertEqual(self.commands, {})
                self.assertEqual(self.calls, [])


@unittest.skipUnless(sys.platform == "darwin", "包内原生入口使用 macOS arm64 原厂库")
class NativeEntryTests(TemporaryFixture):
    @classmethod
    def setUpClass(cls):
        runtime = importlib.import_module("renpy-runtime")
        cls.runtime = (runtime.DEFAULT_OUTPUT / "Runtime").resolve()
        if not (cls.runtime / "lib/py3-mac-universal/librenpython.dylib").is_file():
            raise unittest.SkipTest("尚未准备固定版本原厂库；本测试只执行自有脚本")
        temporary = tempfile.TemporaryDirectory(prefix="sn-renpy-native-entry-test-")
        cls.addClassCleanup(temporary.cleanup)
        cls.compiled = Path(temporary.name).resolve() / "launcher"
        importlib.import_module("renpy-build").compile_entry(cls.compiled)
        cls.runtime_before = inventory(cls.runtime)
        cls.runs = []

    @classmethod
    def tearDownClass(cls):
        unchanged = inventory(cls.runtime) == cls.runtime_before
        print("NATIVE_ENTRY_EVIDENCE " + json.dumps({"runs": cls.runs, "runtime_unchanged": unchanged,
                                                    "game_or_steam_imported": False}, sort_keys=True), flush=True)
        if not unchanged:
            raise AssertionError("共享原厂运行时在自有机制测试期间变化")

    def setUp(self):
        super().setUp()
        self.app = self.base / "Self Fixture.app"
        self.resources = self.app / "Contents/Resources"
        self.original = self.resources / "autorun"
        self.original.mkdir(parents=True)
        (self.app / "Contents/MacOS").mkdir()
        self.launcher = self.app / "Contents/MacOS/launcher"
        shutil.copy2(self.compiled, self.launcher)
        self.source, self.repo = self.base / "source", self.base / "repo"
        for path in (self.source, self.repo):
            path.mkdir()
            (path / "canary").write_bytes(b"keep original")
        self.state_root = self.base / "state"
        self.config = {"protocol": 2, "appid": LAUNCH.APPID, "version": LAUNCH.VERSION,
                       "entry": "gundog.py", "runtime": str(self.runtime), "state_root": str(self.state_root),
                       "source": str(self.source), "repo": str(self.repo)}
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "io.dryport.renpy." + LAUNCH.APPID,
            "CFBundleName": "SelfOwnedRenPyFixture", "CFBundleExecutable": "launcher",
            "CFBundlePackageType": "APPL"}))
        # 只读 Foundation 身份与 dyld 映像；没有 NSApplication、Ren'Py 或 Steam 导入。
        helper = '''import ctypes,json,os,sys
from pathlib import Path
def observe():
    process = ctypes.CDLL(None)
    process.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    process.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    if process.proc_pidpath(os.getpid(), buffer, len(buffer)) <= 0: raise RuntimeError("proc_pidpath")
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
    def message(target, selector): return send(target, objc.sel_registerName(selector))
    bundle = message(objc.objc_getClass(b'NSBundle'), b'mainBundle')
    def string(selector): return ctypes.string_at(message(message(bundle, selector), b'UTF8String')).decode()
    process._dyld_image_count.restype = ctypes.c_uint32
    process._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    process._dyld_get_image_name.restype = ctypes.c_char_p
    images = [os.path.realpath(process._dyld_get_image_name(i).decode()) for i in range(process._dyld_image_count())]
    check = process.sn_renpy_bootstrap_verified
    check.argtypes = [ctypes.c_char_p]
    check.restype = ctypes.c_int
    app = os.path.realpath(string(b'bundlePath'))
    return dict(pid=os.getpid(), image=os.path.realpath(buffer.value.decode()), bundle=app,
        bundle_id=string(b'bundleIdentifier'), argv=sys.argv, executable=sys.executable,
        cwd=os.getcwd(), env=dict(os.environ), no_bytecode=sys.dont_write_bytecode,
        loaded_runtime=[p for p in images if p.endswith('/librenpython.dylib')],
        bootstrap_verified=check(os.fsencode(app)), foreign_verified=check(b'/foreign.app'),
        script_path=sys.path[0])
'''
        for directory in (self.resources, self.original):
            (directory / "own_helper.py").write_text(helper)
        (self.resources / "renpy-launch.py").write_text(
            "from own_helper import observe\nimport json\nprint(json.dumps(observe()))\n")
        (self.original / "gundog.py").write_text('''from own_helper import observe
import errno,json,os
from pathlib import Path
record = observe()
record['module_name'] = __name__
record['script_file'] = __file__
targets = json.loads((Path(__file__).parent / 'canaries.json').read_text())
record['write_errnos'] = []
for target in targets:
    try:
        with open(target, 'ab') as stream: stream.write(b'forbidden')
    except OSError as error: record['write_errnos'].append(error.errno)
    else: record['write_errnos'].append(0)
Path(os.environ['SN_RENPY_SAVES'], 'own-fixture-write').write_bytes(b'allowed')
print(json.dumps(record))
''')
        self.targets = [self.source / "canary", self.repo / "canary", self.original / "gundog.py",
                        self.runtime / "lib/py3-mac-universal/pythonw"]
        (self.original / "canaries.json").write_text(json.dumps(list(map(str, self.targets))))
        self.external = self.base / "foreign"
        self.external.mkdir()
        self.canary = self.base / "foreign-module-executed"
        payload = "from pathlib import Path\nPath(" + repr(str(self.canary)) + ").write_text('unexpected')\n"
        for name in ("sitecustomize.py", "usercustomize.py", "own_helper.py"):
            (self.external / name).write_text(payload)
        self.hostile = {"HOME": str(self.base), "LANG": "en_US.UTF-8", "PATH": "/usr/bin:/bin",
                        "PYTHONHOME": str(self.base / "missing"), "PYTHONPATH": str(self.external),
                        "RENPY_NO_STEAM": "1", "RENPY_SEARCHPATH": str(self.external),
                        "DYLD_LIBRARY_PATH": str(self.external), "SN_RENPY_FIRST_RUN": "0"}
        self.sign()

    def sign(self):
        (self.resources / "renpy-launch.json").write_text(json.dumps(self.config))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(self.app)],
                       check=True, capture_output=True, timeout=15)

    def run_owned(self, command, *, env=None, cwd=None):
        started = time.monotonic()
        child = subprocess.Popen(list(map(str, command)), env=env or self.hostile, cwd=cwd or self.base,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        timed_out = False
        try:
            stdout, stderr = child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            timed_out = True
            raise
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            self.runs.append({"pid": child.pid, "returncode": child.returncode, "reaped": child.poll() is not None,
                              "elapsed_seconds": time.monotonic() - started, "timed_out": timed_out})
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)

    def assert_identity(self, record, *, bootstrap):
        self.assertEqual(record["image"], str(self.launcher))
        self.assertEqual(record["bundle"], str(self.app))
        self.assertEqual(record["bundle_id"], "io.dryport.renpy." + LAUNCH.APPID)
        self.assertEqual(record["loaded_runtime"], [str(self.runtime / "lib/py3-mac-universal/librenpython.dylib")])
        self.assertEqual(record["bootstrap_verified"], int(bootstrap))
        self.assertEqual(record["foreign_verified"], 0)
        self.assertTrue(record["no_bytecode"])
        for key in ("PYTHONHOME", "PYTHONPATH", "RENPY_NO_STEAM", "DYLD_LIBRARY_PATH"):
            self.assertNotIn(key, record["env"])
        self.assertFalse(self.canary.exists())
        self.assertFalse(list(self.app.rglob("__pycache__")))
        self.runs[-1]["observation"] = record

    def test_bootstrap_keeps_main_image_bundle_and_clean_python_environment(self):
        before = inventory(self.app)
        result = self.run_owned([self.launcher])
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assert_identity(record, bootstrap=True)
        self.assertEqual(record["argv"], [str(self.resources / "renpy-launch.py"), str(self.app)])
        self.assertEqual(record["script_path"], str(self.resources))
        self.assertEqual(inventory(self.app), before)

    def test_engine_keeps_identity_sandbox_and_existing_saves_on_two_runs(self):
        state = LAUNCH.state_directory(self.state_root, [self.app, self.runtime, self.source, self.repo])
        persistent, save = state / "saves/persistent", state / "saves/slot1.save"
        persistent.write_bytes(b"existing user preference")
        save.write_bytes(b"existing user story save")
        original_saves = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (persistent, save)}
        protected = {p: inventory(p) for p in (self.app, self.source, self.repo)}
        for _ in range(2):
            prepared = LAUNCH.prepare(self.app)
            env = {**prepared["env"], **self.hostile,
                   "SN_RENPY_AUTORUN": str(self.original), "SN_RENPY_SAVES": str(state / "saves"),
                   "SN_RENPY_LOGDIR": str(prepared["logs"])}
            result = self.run_owned(prepared["command"], env=env, cwd=state)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            self.assert_identity(record, bootstrap=False)
            self.assertEqual(record["argv"], [str(self.original / "gundog.py"), "--savedir",
                str(state / "saves"), str(self.original), "sn_native"])
            self.assertEqual(record["module_name"], "__main__")
            self.assertEqual(record["script_path"], str(self.original))
            self.assertEqual(record["cwd"], str(state))
            self.assertEqual(record["write_errnos"], [errno.EPERM] * len(self.targets))
            self.assertEqual(record["env"]["SN_RENPY_FIRST_RUN"], "0")
            self.assertEqual(record["env"]["HOME"], str(state / "home"))
        self.assertEqual((state / "steam_appid.txt").read_text().strip(), LAUNCH.APPID)
        self.assertEqual((state / "saves/own-fixture-write").read_bytes(), b"allowed")
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original_saves}, original_saves)
        self.assertEqual({p: inventory(p) for p in protected}, protected)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(self.app)],
                       check=True, capture_output=True, timeout=15)

    def test_unknown_modes_and_external_arguments_rejected_before_payload(self):
        for arguments in (("--other",), ("--engine", "/foreign/game.py"), ("/foreign.app",)):
            with self.subTest(arguments=arguments):
                result = self.run_owned([self.launcher, *arguments])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        result = self.run_owned([self.launcher, "--engine"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.state_root.exists())
        self.assertFalse(self.canary.exists())
        prepared = LAUNCH.prepare(self.app)
        for key in ("SN_RENPY_AUTORUN", "SN_RENPY_SAVES", "SN_RENPY_LOGDIR"):
            with self.subTest(external_environment_path=key):
                env = {**prepared["env"], key: str(self.external)}
                result = self.run_owned(prepared["command"], env=env, cwd=prepared["cwd"])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        result = self.run_owned(prepared["command"], env=prepared["env"], cwd=self.external)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse((prepared["state"] / "saves/own-fixture-write").exists())

    def test_wrong_configuration_or_signed_foreign_runtime_rejected_before_payload(self):
        for field, value in (("protocol", 1), ("entry", "/foreign.py"), ("appid", "900005")):
            with self.subTest(field=field):
                original = self.config[field]
                self.config[field] = value
                self.sign()
                result = self.run_owned([self.launcher])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.config[field] = original
        foreign = self.base / "foreign-runtime/lib/py3-mac-universal"
        foreign.mkdir(parents=True)
        for name in ("pythonw", "librenpython.dylib", "libsteam_api.dylib"):
            path = foreign / name
            shutil.copy2(self.compiled, path)
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(path)],
                           check=True, capture_output=True, timeout=10)
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--architecture", "arm64", str(path)],
                           check=True, capture_output=True, timeout=10)
        self.config["runtime"] = str(foreign.parent.parent)
        self.sign()
        result = self.run_owned([self.launcher])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("原厂身份", result.stderr)
        self.assertFalse(self.state_root.exists())


@unittest.skipUnless(sys.platform == "darwin", "完整编译及 ad-hoc 签名需要 macOS")
class BuilderReproducibilityTests(TemporaryFixture):
    def test_same_inputs_across_clocks_and_output_directories_have_identical_bytes(self):
        build = importlib.import_module("renpy-build")
        game, runtime = self.base / "Game", self.base / "Runtime"
        game.mkdir()
        runtime.mkdir()
        (game / "gundog.py").write_bytes(b"# self-owned fixture; never executed\n")
        (game / "data.txt").write_bytes(b"unchanged original data\n")
        before = inventory(game)
        expected_script = (ROOT / "lib/renpy-extension.py").read_bytes()
        apps, manifests = [], []
        # 只替代外部许可与运行时验证；实际执行生产组装、clang、codesign 和原子发布。
        checked = {"runtime": str(runtime), "payload": {"manifest_sha256": "a" * 64}}
        with mock.patch.object(build, "PAYLOAD_SHA256", build.SHARED.inventory_digest(build.SHARED.inventory(game))), \
                mock.patch.object(build, "check_access") as access, \
                mock.patch.object(build.RUNTIME, "check", return_value=checked):
            for index, clock in enumerate((946684800, 2051222400)):
                parent = self.base / ("build-" + str(index))
                parent.mkdir()
                app = parent / "Reproducible Fixture.app"
                # 控制外部时钟，避免睡眠或把实现的 ZIP 元数据复制进测试。
                with mock.patch("time.time", return_value=clock):
                    result = build.build(game, "Reproducible Fixture", app,
                                         package=runtime, state_root=self.base / "state")
                self.assertFalse(result["game_started"])
                self.assertTrue(result["original_unchanged"])
                subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)],
                               check=True, capture_output=True, timeout=10)
                archive_path = app / "Contents/Resources/extensions/sn-native.rpe"
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(archive.namelist(), ["autorun.py"])
                    self.assertEqual(archive.read("autorun.py"), expected_script)
                apps.append(app)
                manifests.append({path.relative_to(app).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(app.rglob("*")) if path.is_file()})
            self.assertEqual(access.call_args_list, [mock.call(game), mock.call(game)])
        changed = sorted(key for key in set(manifests[0]) | set(manifests[1])
                         if manifests[0].get(key) != manifests[1].get(key))
        print("REPRODUCIBLE_BUILD_EVIDENCE " + json.dumps({
            "packing_clocks": [946684800, 2051222400], "apps": [str(app) for app in apps],
            "files": [len(items) for items in manifests], "changed": changed,
            "manifests": manifests, "strict_signature_checks": 2,
            "source_unchanged": inventory(game) == before, "game_started": False,
        }, sort_keys=True), flush=True)
        self.assertEqual(inventory(game), before)
        self.assertEqual((apps[0] / "Contents/Resources/extensions/sn-native.rpe").read_bytes(),
                         (apps[1] / "Contents/Resources/extensions/sn-native.rpe").read_bytes())
        self.assertEqual(manifests[0], manifests[1], "全部 app 文件内容必须可复现，包括签名")


@unittest.skipUnless(sys.platform == "darwin", "原生 Ren'Py 构建入口")
class BuilderRejectionTests(TemporaryFixture):
    def setUp(self):
        super().setUp()
        self.build = importlib.import_module("renpy-build")
        self.game = self.base / "Game"
        self.game.mkdir()
        (self.game / "gundog.py").write_bytes(b"self-owned synthetic source, never executed")

    def test_existing_file_directory_and_dangling_output_are_preserved(self):
        for kind in ("directory", "file", "dangling"):
            output = self.base / (kind + ".app")
            if kind == "directory":
                output.mkdir()
                (output / "mine").write_bytes(b"keep")
            elif kind == "file": output.write_bytes(b"keep")
            else: output.symlink_to(self.base / "absent")
            before = inventory(self.base)
            with self.subTest(kind=kind), mock.patch.object(self.build, "check_access", side_effect=AssertionError("must not query Steam")):
                with self.assertRaises(FileExistsError):
                    self.build.build(self.game, "Fixture", output)
            self.assertEqual(inventory(self.base), before)

    def test_source_and_shared_runtime_output_overlap_stop_before_assembly(self):
        with mock.patch.object(self.build, "check_access", side_effect=AssertionError("must not query Steam")):
            with self.assertRaisesRegex(ValueError, "重叠"):
                self.build.build(self.game, "Fixture", self.game / "Nested.app")
        runtime = self.base / "Runtime"
        runtime.mkdir()
        original = inventory(self.game)
        with mock.patch.object(self.build, "PAYLOAD_SHA256", self.build.SHARED.inventory_digest(self.build.SHARED.inventory(self.game))), \
                mock.patch.object(self.build, "check_access"), \
                mock.patch.object(self.build.RUNTIME, "check", return_value={"runtime": str(runtime)}), \
                mock.patch.object(self.build, "CLONE", side_effect=AssertionError("must not assemble")):
            with self.assertRaisesRegex(ValueError, "共享运行时"):
                self.build.build(self.game, "Fixture", runtime / "Nested.app")
        self.assertEqual(inventory(self.game), original)
        self.assertEqual(list(runtime.iterdir()), [])

    def test_unknown_original_profile_stops_before_access_or_runtime(self):
        original = inventory(self.game)
        with mock.patch.object(self.build, "check_access", side_effect=AssertionError("must not query Steam")), \
                mock.patch.object(self.build.RUNTIME, "check", side_effect=AssertionError("must not inspect runtime")):
            with self.assertRaisesRegex(ValueError, "尚未匹配"):
                self.build.build(self.game, "Fixture", self.base / "Fixture.app")
        self.assertEqual(inventory(self.game), original)
        self.assertFalse((self.base / "Fixture.app").exists())


if __name__ == "__main__":
    unittest.main()
