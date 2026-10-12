# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""真实初始化工具的自有对象回归；不加载游戏、官方引擎或 Steam。"""
import ast
import copy
import errno
import hashlib
import importlib.util
import json
import logging
import math
import os
import resource
import time
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sn_realinit_probe", ROOT / "tools/probe-renpy-realinit.py")
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)
SID = str(76561197960265728 + 1)


class Quit(Exception):
    def __init__(self, status):
        self.status = status


class RevertableDict(dict):
    pass


class RevertableList(list):
    pass


class RevertableSet(set):
    pass


REVERTABLE = types.SimpleNamespace(RevertableDict=RevertableDict, RevertableList=RevertableList,
                                  RevertableSet=RevertableSet)


def fake_renpy(base, *, save=True, steam=True):
    """执行完整 RPE 的最小自有引擎对象，观察其调用和状态而非匹配源码文本。"""
    autorun, savedir = base / "autorun", base / "saves"
    autorun.mkdir()
    savedir.mkdir()
    module = types.ModuleType("renpy")
    module.__file__ = str(autorun / "renpy/__init__.py")
    for name in ("bootstrap", "main", "script"):
        setattr(module, name, types.SimpleNamespace(__file__=str(autorun / ("renpy/" + name + ".py"))))
    module.version_only = PROBE.BASE.VERSION
    module.macapp = True
    module.revertable = REVERTABLE
    module.config = types.SimpleNamespace(renpy_base=str(autorun), commondir=str(autorun / "renpy/common"),
        extra_savedirs=[], savedir=str(savedir), enable_steam=True, logdir=str(autorun))
    prefs = types.SimpleNamespace(fullscreen=True, language="original_language", text_cps=37,
        volumes={"music": 0.09, "sfx": 0.25}, pad_bindings={None: ["a"], "null": ["b"]})
    module.game = types.SimpleNamespace(args=types.SimpleNamespace(command=PROBE.COMMAND, savedir=str(savedir)),
        interface=None, preferences=prefs, persistent=types.SimpleNamespace(_set_preferences=True),
        post_init=[], QuitException=Quit, script=types.SimpleNamespace(digest=hashlib.sha256(b"compiled"),
        namemap={("original.rpy", 1): types.SimpleNamespace(filename="original.rpy", linenumber=1),
                 ("original.rpy", 2): types.SimpleNamespace(filename="original.rpy", linenumber=2)}))
    module.game.script.all_stmts = list(module.game.script.namemap.values())
    calls = []

    class Interface:
        def __init__(self):
            calls.append("unsafe_interface_body")

    class FileLocation:
        def __init__(self, directory):
            calls.append(("file_location", directory))
            self.directory = directory

    location = FileLocation(str(savedir))
    calls.clear()
    module.display = types.SimpleNamespace(core=types.SimpleNamespace(Interface=Interface))
    module.savelocation = types.SimpleNamespace(FileLocation=FileLocation)
    module.savetoken = types.SimpleNamespace(token_dir=None)
    module.loadsave = types.SimpleNamespace(location=types.SimpleNamespace(locations=[location],
        active_locations=lambda: [location]))
    module.arguments = types.SimpleNamespace(register_command=lambda name, fn, **kw: calls.append((name, kw)))

    def save_persistent():
        calls.append("official_save")
        if save:
            (savedir / "persistent").write_bytes(b"owned test persistent")

    module.exports = types.SimpleNamespace(save_persistent=save_persistent)
    api = types.SimpleNamespace(SteamUtils=lambda: types.SimpleNamespace(GetAppID=lambda: int(PROBE.APPID)),
        SteamUser=lambda: types.SimpleNamespace(GetSteamID=lambda: int(SID)))
    achievement = types.SimpleNamespace(steam=object() if steam else None, steamapi=api if steam else None)
    logs = base / "logs/real-init/seed"
    (logs / "game-logs").mkdir(parents=True)
    env = {"SN_REAL_PHASE": "seed", "SN_REAL_SAVE": str(savedir), "SN_REAL_AUTORUN": str(autorun),
           "SN_REAL_RECEIPT": str(logs / "receipt.json"), "SN_REAL_EVENTS": str(logs / "events.jsonl"),
           "SN_REAL_LOGDIR": str(logs / "game-logs"),
           "SN_REAL_STEAMID": SID, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"}
    return module, achievement, env, calls


class RealInitSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-realinit-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def execute_rpe(self, *, save=True, steam=True, action=None):
        module, achievement, env, calls = fake_renpy(self.base, save=save, steam=steam)
        scope = {}
        with patch.dict(sys.modules, {"renpy": module, "store.achievement": achievement,
                                    "store": types.SimpleNamespace(steam_build=True)}), \
                patch.dict(os.environ, env, clear=True), patch("platform.machine", return_value="arm64"):
            exec(compile(PROBE.RPE, "owned-rpe", "exec"), scope)
            # 真实 common 的 init1100 增加 sync；不让工具替它清空或补造。
            module.config.extra_savedirs.append(env["SN_REAL_SAVE"] + "/sync")
            # 本游戏原 options Steam 分支随后主动清空，工具必须保留该结果。
            module.config.extra_savedirs.clear()
            module.config.has_sync = None
            if action:
                action(module, scope)
            else:
                with self.assertRaises(Quit) as stopped:
                    for callback in module.game.post_init:
                        callback()
                self.assertEqual(stopped.exception.status, 0)
        receipt = json.loads(Path(env["SN_REAL_RECEIPT"]).read_text())
        return module, receipt, calls

    def test_real_rpe_waits_for_late_callbacks_then_changes_only_fullscreen(self):
        def action(module, scope):
            def late_second():
                scope["record"]("owned_original_late_second")
                module.game.preferences.text_cps = 41

            def late_first():
                scope["record"]("owned_original_late_first")
                module.game.post_init.append(late_second)

            module.game.post_init.append(late_first)
            with self.assertRaises(Quit):
                for callback in module.game.post_init:
                    callback()

        module, data, calls = self.execute_rpe(action=action)
        self.assertTrue(data["ok"])
        self.assertEqual(data["deferrals"], 1)
        self.assertLess(data["events"].index("owned_original_late_second"),
                        data["events"].index("original_post_init_finished"))
        self.assertEqual(data["before"]["text_cps"], 41)
        self.assertEqual(data["after"], dict(data["before"], fullscreen=False))
        self.assertEqual(calls.count("official_save"), 1)
        self.assertNotIn("unsafe_interface_body", calls)
        self.assertEqual(vars(module.game.persistent), {"_set_preferences": True})
        self.assertTrue(module.config.enable_steam)
        self.assertEqual(data["save_layout"]["extra"], [])
        self.assertTrue(data["original_steam_layout"]["steam_build"])
        self.assertEqual(data["statement_identity_input"], {"namemap_type": "builtins.dict", "namemap_count": 2,
            "all_stmts_type": "builtins.list", "all_stmts_count": 2})

    def test_unfamiliar_extra_path_still_refused_with_actual_layout_recorded(self):
        foreign = str(self.base / "foreign")
        def action(module, scope):
            module.config.extra_savedirs.append(foreign)
            with self.assertRaises(SystemExit):
                for callback in module.game.post_init:
                    callback()

        _, data, calls = self.execute_rpe(action=action)
        self.assertEqual(data["error"], "save_locations_changed")
        self.assertEqual(data["save_layout"]["extra"], [foreign])
        self.assertNotIn("official_save", calls)

    def test_non_file_location_is_refused_even_with_same_path(self):
        def action(module, scope):
            module.loadsave.location.locations = [types.SimpleNamespace(directory=str(self.base / "saves"))]
            with self.assertRaises(SystemExit):
                for callback in module.game.post_init:
                    callback()

        _, data, calls = self.execute_rpe(action=action)
        self.assertEqual(data["error"], "unexpected_save_location_type")
        self.assertEqual(data["save_layout"]["all"][0]["type"], "types.SimpleNamespace")
        self.assertNotIn("official_save", calls)

    def test_original_steam_failure_is_not_reinitialized_or_saved(self):
        def stop(module, scope):
            with self.assertRaises(SystemExit):
                for callback in module.game.post_init:
                    callback()

        _, data, calls = self.execute_rpe(steam=False, action=stop)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "original_steam_initialization_failed")
        self.assertNotIn("official_save", calls)

    def test_original_basicconfig_keeps_format_level_and_creates_external_log(self):
        root = logging.RootLogger(logging.WARNING)
        self.addCleanup(lambda: [handler.close() for handler in root.handlers])
        original_basicconfig, original_filehandler = logging.basicConfig, logging.FileHandler
        format_ = "%(asctime)s|%(levelname)s|%(name)s|%(msg)s"
        datefmt = "%y-%m-%d %H:%M:%S"

        def action(module, scope):
            # 与原配置相同的标准 logging 调用；真正写自有临时文件，不执行游戏脚本。
            logging.basicConfig(level=logging.DEBUG, filename=os.path.join(module.config.logdir, "gundog.log"),
                                format=format_, datefmt=datefmt)
            root.debug("owned logging canary")
            for handler in root.handlers:
                handler.flush()
            with self.assertRaises(Quit):
                for callback in module.game.post_init:
                    callback()

        with patch.object(logging, "root", root):
            module, data, _ = self.execute_rpe(action=action)
        self.assertIs(logging.basicConfig, original_basicconfig)
        self.assertIs(logging.FileHandler, original_filehandler)
        self.assertEqual(root.level, logging.DEBUG)
        self.assertEqual(root.handlers[0].formatter._fmt, format_)
        self.assertEqual(root.handlers[0].formatter.datefmt, datefmt)
        self.assertTrue(data["original_game_log_present"])
        log = Path(module.config.logdir) / "gundog.log"
        self.assertIn("|DEBUG|root|owned logging canary", log.read_text())
        self.assertFalse((self.base / "autorun/gundog.log").exists())

    def test_late_logdir_change_is_not_silently_accepted(self):
        def action(module, scope):
            module.config.logdir = str(self.base / "foreign")
            with self.assertRaises(SystemExit):
                for callback in module.game.post_init:
                    callback()

        _, data, calls = self.execute_rpe(action=action)
        self.assertEqual(data["error"], "logdir_changed_after_original_init")
        self.assertNotIn("official_save", calls)

    def test_original_silent_save_failure_is_not_success(self):
        def stop(module, scope):
            with self.assertRaises(SystemExit):
                for callback in module.game.post_init:
                    callback()

        _, data, calls = self.execute_rpe(save=False, action=stop)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "save_returned_without_persistent")
        self.assertEqual(calls.count("official_save"), 1)

    def test_interface_is_rejected_before_original_constructor(self):
        def action(module, scope):
            with self.assertRaises(SystemExit):
                module.display.core.Interface()

        _, data, calls = self.execute_rpe(action=action)
        self.assertFalse(data["ok"])
        self.assertEqual(data["interface_attempts"], 1)
        self.assertNotIn("unsafe_interface_body", calls)

    def test_extra_save_location_is_rejected_before_constructor(self):
        def action(module, scope):
            with self.assertRaises(SystemExit):
                module.savelocation.FileLocation(str(self.base / "foreign"))

        _, data, calls = self.execute_rpe(action=action)
        self.assertEqual(data["error"], "unexpected_save_location_before_write")
        self.assertFalse(any(isinstance(item, tuple) and item[0] == "file_location" for item in calls))

    def test_new_process_requires_all_preferences_and_compiled_identity(self):
        before = {"fullscreen": True, "language": "original", "text_cps": 37, "volumes": {"music": .09}}
        seed = {"before": before, "after": dict(before, fullscreen=False),
                "script_digest": "original-script", "statement_names_sha256": "original-statements"}
        check = dict(seed, before=seed["after"])
        PROBE.compare_readback(seed, check)
        for field in ("language", "text_cps", "volumes"):
            bad = copy.deepcopy(check)
            bad["before"][field] = "changed"
            with self.assertRaises(ValueError):
                PROBE.compare_readback(seed, bad)
        bad = dict(check, statement_names_sha256="regenerated")
        with self.assertRaisesRegex(ValueError, "编译脚本"):
            PROBE.compare_readback(seed, bad)
        # Python 原生 equality 会把 37 与 37.0 当相等，完整类型快照不可这样放行。
        bad = copy.deepcopy(check)
        bad["before"]["text_cps"] = 37.0
        with self.assertRaisesRegex(ValueError, "完整恢复"):
            PROBE.compare_readback(seed, bad)

    def actual_normalizer(self):
        node = next(item for item in ast.parse(PROBE.RPE).body
                    if isinstance(item, ast.FunctionDef) and item.name == "normalize")
        scope = {"json": json, "math": math, "renpy": types.SimpleNamespace(revertable=REVERTABLE)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-normalizer", "exec"), scope)
        return scope["normalize"]

    def test_revertable_types_keys_and_unordered_elements_remain_distinct(self):
        normalize = self.actual_normalizer()
        canonical = lambda value: json.dumps(normalize(value), sort_keys=True, allow_nan=False)
        for original, builtin in ((RevertableDict({None: 1, "null": 2}), {None: 1, "null": 2}),
                                  (RevertableList([1, "a"]), [1, "a"]),
                                  (RevertableSet([1, "a"]), {1, "a"})):
            self.assertNotEqual(canonical(original), canonical(builtin))
            self.assertEqual(canonical(original), canonical(copy.deepcopy(original)))
            self.assertNotEqual(canonical(original), canonical(normalize(original)))
        self.assertEqual(canonical(RevertableSet([1, "a", None])), canonical(RevertableSet([None, "a", 1])))
        self.assertNotEqual(canonical({True: "same"}), canonical({1: "same"}))
        self.assertNotEqual(canonical({1: "same"}), canonical({1.0: "same"}))
        self.assertEqual(len(normalize(RevertableDict({None: 1, "null": 2}))["items"]), 2)

    def test_unknown_subclasses_and_depth_size_limits_still_fail(self):
        normalize = self.actual_normalizer()
        class CustomDict(RevertableDict):
            pass
        class CustomList(list):
            pass
        for value in (CustomDict(a=1), CustomList([1]), object()):
            with self.assertRaisesRegex(ValueError, "unsupported_preference_type:preferences.custom"):
                normalize(value, path="preferences.custom")
        nested = None
        for _ in range(18):
            nested = [nested]
        for value in (nested, list(range(4097)), [[0] * 100 for _ in range(42)], float("nan")):
            with self.assertRaises(ValueError):
                normalize(value)

    def test_reused_or_dangling_output_is_preserved_before_any_check(self):
        materials, game = self.base / "materials", self.base / "game"
        materials.mkdir()
        game.mkdir()
        output = self.base / "output"
        output.mkdir()
        (output / "keep").write_text("unchanged")
        alias = self.base / "alias"
        alias.symlink_to(self.base / "missing")
        with patch.object(PROBE.sys, "platform", "darwin"), patch.object(PROBE, "authorization") as auth, \
                patch.object(PROBE, "verify_delivery") as delivery:
            for path in (output, alias):
                with self.assertRaises(FileExistsError):
                    PROBE.prepare(materials, game, path)
            auth.assert_not_called()
            delivery.assert_not_called()
        self.assertEqual((output / "keep").read_text(), "unchanged")
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.base / "missing").exists())

    def test_plain_root_symlink_rejected(self):
        real = self.base / "real"
        real.mkdir()
        (real / "file").write_bytes(b"bytes")
        alias = self.base / "alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            PROBE.plain_tree(alias)

    def test_origin_only_allows_own_script_directory_and_original_stdlib(self):
        runtime, script = self.base / "Runtime", self.base / "guarded/origin.py"
        data = {"architecture": "arm64", "renpy_absent": True,
                "executable": str(runtime / "lib/py3-mac-universal/pythonw"),
                "path": [str(script.parent), str(runtime / "lib/python3.9")]}
        PROBE.validate_origin(data, runtime, script)
        for paths in ([str(self.base / "foreign"), *data["path"][1:]],
                      [*data["path"], str(self.base / "foreign")]):
            with self.assertRaises(ValueError):
                PROBE.validate_origin(dict(data, path=paths), runtime, script)
        with self.assertRaises(ValueError):
            PROBE.validate_origin(dict(data, renpy_absent=False), runtime, script)

    def test_locale_preserved_without_steam_disabling_environment(self):
        with patch.dict(os.environ, {"LANG": "fr_FR.UTF-8", "RENPY_NO_STEAM": "1", "PYTHONPATH": "/foreign"}, clear=True):
            env = PROBE.environment(self.base, self.base / "rpe")
        self.assertEqual(env["LANG"], "fr_FR.UTF-8")
        self.assertNotIn("LC_ALL", env)
        self.assertNotIn("RENPY_NO_STEAM", env)
        self.assertNotIn("PYTHONPATH", env)

    def test_unreadable_unrelated_preferences_are_explicit_not_falsely_hashed(self):
        path = self.base / "protected.plist"
        path.write_bytes(b"owned fixture")
        with patch.object(PROBE, "sha", side_effect=PermissionError(errno.EPERM, "denied")):
            result = PROBE.preference_files(self.base)
        self.assertEqual(result["protected.plist"], {"unreadable_errno": errno.EPERM})
        self.assertEqual(path.read_bytes(), b"owned fixture")

    def test_authorization_rejects_account_mismatch_and_missing_permission(self):
        gate = {"protocol": 1, "code": 0, "appid": int(PROBE.APPID)}
        license_ = {"protocol": 2, "appid": PROBE.APPID, "status": "available", "allowed": True,
                    "account": {"steamid64": SID, "account_id": "1"}}
        with patch.object(PROBE, "json_command", side_effect=[gate, license_]):
            self.assertEqual(PROBE.authorization()["steamid64"], SID)
        for bad in (dict(license_, allowed=False), dict(license_, account={"steamid64": SID, "account_id": "2"}),
                    dict(license_, status="unavailable"), dict(license_, appid="1")):
            with patch.object(PROBE, "json_command", side_effect=[gate, bad]), self.assertRaises(ValueError):
                PROBE.authorization()

    def test_alive_pid_timeout_or_output_limit_prevents_success(self):
        state = self.base / "state"
        data = {"ok": True, "pid": 42, "phase": "seed", "version": PROBE.BASE.VERSION,
                "architecture": "arm64", "interface_attempts": 0, "interface_absent": True,
                "steam_initialized": True, "appid": PROBE.APPID, "steamid64": SID,
                "logdir": str(state / "logs/real-init/seed/game-logs"), "original_game_log_present": True,
                "active_save_locations": [str(state / "saves")], "save_call_returned": True,
                "persistent_file_present": True}
        run = {"pid": 42, "returncode": 0, "timed_out": False, "pid_reaped": True,
               "output_limit_reached": False, "receipt": data}
        prepared = {"state": str(state), "authorization": {"steamid64": SID}}
        PROBE.validate_run(run, "seed", prepared)
        for key, value in (("timed_out", True), ("pid_reaped", False), ("output_limit_reached", True)):
            with self.assertRaises(ValueError):
                PROBE.validate_run(dict(run, **{key: value}), "seed", prepared)

    def capture_names(self, rows, suffix, epoch=(1000.1, 1000.9), limits=None, all_stmts=None, detail_files=()):
        directory = self.base / suffix
        with patch.dict(PROBE.NAMEMAP_LIMITS, limits or {}):
            receipt = PROBE._namemap_helpers["capture_statement_identity"](dict(rows), str(directory), *epoch, all_stmts, detail_files)
            snapshot = PROBE.read_statement_identity(directory, receipt)
        return receipt, snapshot, directory

    def test_namemap_typed_keys_and_nodes_survive_shards_without_repr(self):
        node = types.SimpleNamespace(filename="原文件.rpy", linenumber=42)
        values = [None, True, 9, 1.5, "label", ("script.rpy", 1000, (2, "内层"))]
        receipt, snapshot, _ = self.capture_names([(value, node) for value in values], "typed", detail_files=[node.filename])
        self.assertEqual(receipt["count"], 6)
        entries = snapshot["by_file"][node.filename]
        self.assertEqual({item["key"]["type"] for item in entries}, {"none", "bool", "int", "float", "str", "tuple"})
        self.assertTrue(all(item["linenumber"] == 42 and item["node_type"] == "types.SimpleNamespace" for item in entries))
        self.assertEqual(next(item["key"] for item in entries if item["key"]["type"] == "int")["value"], "9")
        captures = [self.capture_names([(value, node)], "scalar-" + str(index))[0]
                    for index, value in enumerate((True, 1, 1.0))]
        self.assertEqual(len({item["canonical_key_sha256"] for item in captures}), 3)

    def test_namemap_order_is_stable_and_utf8_parts_obey_actual_byte_limit(self):
        node = types.SimpleNamespace(filename="file.rpy", linenumber=1)
        rows = [(str(index) + "键" * 200, node) for index in range(30)]
        a, left, folder = self.capture_names(rows, "forward", limits={"part_bytes": 4096}, detail_files=[node.filename])
        b, right, _ = self.capture_names(list(reversed(rows)), "reverse", limits={"part_bytes": 4096}, detail_files=[node.filename])
        self.assertEqual(a["canonical_sha256"], b["canonical_sha256"])
        self.assertEqual(a["canonical_key_sha256"], b["canonical_key_sha256"])
        self.assertGreater(len(left["index"]["files"][0]["parts"]), 1)
        self.assertTrue(all(path.stat().st_size <= 4096 for path in folder.iterdir()))
        compared = PROBE.compare_statement_identities(left, right, "same", "same")
        self.assertEqual(compared["classification"], "same")
        self.assertEqual(compared["changed_file_count"], 0)

    def test_namemap_unknown_types_and_all_limits_stop_without_success_index(self):
        node = types.SimpleNamespace(filename="file.rpy", linenumber=1)
        class StringSubclass(str):
            pass
        nested = None
        for _ in range(10):
            nested = (nested,)
        cases = [([(object(), node)], {}), ([(StringSubclass("name"), node)], {}),
                 ([(nested, node)], {}), ([("键" * 2000, node)], {}),
                 ([("a", node), ("b", node)], {"entries": 1}),
                 ([("a", node)], {"total_bytes": 10}),
                 ([("a", node), ("b", types.SimpleNamespace(filename="other.rpy", linenumber=2))], {"files": 1})]
        for index, (rows, limits) in enumerate(cases):
            with self.assertRaises(ValueError):
                self.capture_names(rows, "failure-" + str(index), limits=limits)
            self.assertFalse((self.base / ("failure-" + str(index)) / "index.json").exists())

    def test_namemap_readback_rejects_corrupt_missing_and_symlinked_parts(self):
        rows = [("label", types.SimpleNamespace(filename="file.rpy", linenumber=1))]
        for index, mutation in enumerate(("corrupt", "missing", "symlink")):
            receipt, snapshot, folder = self.capture_names(rows, mutation, detail_files=["file.rpy"])
            part = folder / snapshot["index"]["files"][0]["parts"][0]["path"]
            if mutation == "corrupt":
                content = part.read_bytes()
                part.write_bytes(content.replace(b"label", b"wrong"))
            else:
                content = part.read_bytes()
                part.unlink()
                if mutation == "symlink":
                    target = self.base / "external"
                    target.write_bytes(content)
                    part.symlink_to(target)
            with self.assertRaises((ValueError, FileNotFoundError)):
                PROBE.read_statement_identity(folder, receipt)

    def test_namemap_timestamp_changes_are_preserved_and_distinguished_from_repr(self):
        node = types.SimpleNamespace(filename="script_ren.py", linenumber=7)
        _, before, _ = self.capture_names([(("script_ren.py", 1000, 3), node)], "before", (1000.1, 1000.9), detail_files=[node.filename])
        _, after, _ = self.capture_names([(("script_ren.py", 2000, 3), node)], "after", (2000.1, 2000.9), detail_files=[node.filename])
        diff = PROBE.compare_statement_identities(before, after, "old", "new")
        self.assertEqual(diff["classification"], "key_values_changed")
        self.assertFalse(diff["same_canonical_keys"])
        self.assertEqual(diff["files"][0]["time_name_candidate_count"], 1)
        pair = diff["files"][0]["time_name_candidate_samples"][0]
        self.assertEqual(pair["before"]["items"][1]["value"], "1000")
        self.assertEqual(pair["after"]["items"][1]["value"], "2000")
        repr_only = PROBE.compare_statement_identities(before, before, "old_repr", "new_repr")
        self.assertEqual(repr_only["classification"], "legacy_repr_only")
        self.assertTrue(repr_only["same_canonical_keys"])

    def test_namemap_early_python_source_and_all_statement_next_edges_are_observed(self):
        early_type = type("EarlyPython", (), {"__module__": "renpy.ast"})
        early = early_type()
        early.filename, early.linenumber, early.name = "script_ren.py", 1, ("script_ren.py", 1000, 3)
        early.code = types.SimpleNamespace(source="print('原始代码')\n")
        early.hide, early.store, early.next = False, "store", None
        named = types.SimpleNamespace(filename="story.rpy", linenumber=2, name="named", next=early)
        hidden = types.SimpleNamespace(filename="story.rpy", linenumber=3, name="shadowed", next=early)
        receipt, snapshot, _ = self.capture_names([(early.name, early), ("named", named)], "edges",
            all_stmts=[early, named, hidden, hidden], detail_files=["story.rpy"])
        row = snapshot["by_file"]["script_ren.py"][0]
        self.assertEqual(row["incoming_next_count"], 2)
        self.assertFalse(row["next_present"])
        self.assertIsNone(row["next_key"])
        self.assertEqual(row["early_python"], {"source_type": "str",
            "source_bytes": len(early.code.source.encode("utf-8")),
            "source_sha256": hashlib.sha256(early.code.source.encode("utf-8")).hexdigest(),
            "hide": False, "store": "store"})
        self.assertNotIn(early.code.source, json.dumps(snapshot))
        self.assertEqual(receipt["edge_scan"], {"source": "all_stmts", "list_count": 4,
            "unique_node_count": 3, "edge_count": 2})
        self.assertEqual(snapshot["by_file"]["story.rpy"][0]["next_key"], row["key"])
        early.code.source += "# difference\n"
        _, after, _ = self.capture_names([(early.name, early), ("named", named)], "code-change",
            all_stmts=[early, named, hidden, hidden], detail_files=["story.rpy"])
        diff = PROBE.compare_statement_identities(snapshot, after, "same", "same")
        self.assertEqual(diff["classification"], "node_metadata_only")
        self.assertEqual(diff["files"][0]["changed_node_metadata_count"], 1)

    def test_namemap_early_python_unknown_source_and_all_statement_overflow_stop(self):
        early_type = type("EarlyPython", (), {"__module__": "renpy.ast"})
        early = early_type()
        early.filename, early.linenumber, early.next = "file.rpy", 1, None
        early.code, early.hide, early.store = types.SimpleNamespace(source=object()), False, "store"
        with self.assertRaisesRegex(ValueError, "early_python_metadata"):
            self.capture_names([("early", early)], "bad-source")
        with self.assertRaisesRegex(ValueError, "all_stmts_limit"):
            self.capture_names([], "bad-edges", limits={"entries": 1}, all_stmts=[early, early])

    @staticmethod
    def early_node(filename="early_ren.py", name=("early_ren.py", 1000, 1)):
        node = type("EarlyPython", (), {"__module__": "renpy.ast"})()
        node.filename, node.linenumber, node.name = filename, 1, name
        node.code, node.hide, node.store, node.next = types.SimpleNamespace(source="own = 1\n"), False, "store", None
        return node

    def test_summary_detects_story_key_early_code_incoming_and_count_changes(self):
        early = self.early_node()
        story = types.SimpleNamespace(filename="story.rpy", linenumber=2, name=("story.rpy", 700, 1), next=None)
        rows = [(early.name, early), (story.name, story)]
        _, original, _ = self.capture_names(rows, "summary-original")
        summaries = {item["filename"]: item for item in original["index"]["files"]}
        self.assertEqual(set(original["by_file"]), {early.filename})
        self.assertEqual(summaries[story.filename]["parts"], [])
        self.assertEqual(summaries[story.filename]["count"], 1)
        self.assertEqual(original["index"]["non_early_count"], 1)

        _, renamed, _ = self.capture_names([rows[0], (("story.rpy", 701, 1), story)], "story-key-changed")
        diff = PROBE.compare_statement_identities(original, renamed, "same", "same")
        self.assertFalse(diff["same_canonical_keys"])
        self.assertFalse(diff["same_non_early_records"])
        self.assertFalse(diff["only_early_python_records_changed"])
        self.assertEqual(diff["changed_file_count"], 1)
        self.assertEqual(diff["files"][0]["filename"], "story.rpy")
        self.assertFalse(diff["files"][0]["details_available"])
        self.assertNotIn("removed_count", diff["files"][0])  # 未展开不能伪造精确差异数。

        early.code.source += "own = 2\n"
        _, changed, _ = self.capture_names(rows, "early-code-changed")
        diff = PROBE.compare_statement_identities(original, changed, "same", "same")
        self.assertTrue(diff["same_canonical_keys"])
        self.assertTrue(diff["same_non_early_records"])
        self.assertTrue(diff["only_early_python_records_changed"])
        self.assertEqual(diff["files"][0]["changed_node_metadata_count"], 1)
        early.code.source = "own = 1\n"

        hidden = types.SimpleNamespace(name="shadowed", next=early)
        _, edged, _ = self.capture_names(rows, "new-hidden-incoming", all_stmts=[early, story, hidden])
        diff = PROBE.compare_statement_identities(original, edged, "same", "same")
        self.assertFalse(diff["same_canonical_records"])
        self.assertEqual(edged["by_file"][early.filename][0]["incoming_next_count"], 1)
        self.assertEqual(diff["edge_scan"][1]["edge_count"], 1)
        self.assertFalse(diff["only_early_python_records_changed"])
        hidden.next = None
        _, changed_graph, _ = self.capture_names(rows, "unmapped-node-added", all_stmts=[early, story, hidden])
        diff = PROBE.compare_statement_identities(original, changed_graph, "same", "same")
        self.assertTrue(diff["same_canonical_records"])
        self.assertFalse(diff["same_edge_scan"])
        self.assertEqual(diff["classification"], "edge_scan_counts_changed")

        _, removed, _ = self.capture_names(rows[:1], "story-removed")
        diff = PROBE.compare_statement_identities(original, removed, "same", "same")
        self.assertEqual((diff["before_count"], diff["after_count"]), (2, 1))
        self.assertFalse(diff["same_non_early_records"])
        self.assertEqual(diff["files"][0]["after_count"], 0)

    def test_details_include_whole_early_file_and_remain_bounded(self):
        early = self.early_node()
        story = types.SimpleNamespace(filename=early.filename, linenumber=8, next=None)
        rows = [(early.name, early), ("label_in_same_file", story)]
        receipt, snapshot, _ = self.capture_names(rows, "whole-early-file")
        self.assertEqual(receipt["detail_count"], 2)
        self.assertEqual(len(snapshot["by_file"][early.filename]), 2)
        with self.assertRaisesRegex(ValueError, "detail_entries"):
            self.capture_names(rows, "details-overflow", limits={"detail_entries": 1})
        self.assertFalse((self.base / "details-overflow/index.json").exists())

    def test_digest_preserves_multiplicity_and_summary_tampering_fails(self):
        digest = hashlib.sha256(b"own canonical record").digest()
        aggregate = PROBE._namemap_helpers["nm_digest"]
        self.assertNotEqual(aggregate(digest), aggregate(digest * 2))
        self.assertNotEqual(aggregate(b""), aggregate(digest * 2))  # XOR 会在此错误相等。
        node = types.SimpleNamespace(filename="story.rpy", linenumber=1, next=None)
        receipt, _, folder = self.capture_names([("story", node)], "summary-integrity")
        index_file = folder / "index.json"
        index = json.loads(index_file.read_text())
        index["files"][0]["sha256"] = "0" * 64
        index_file.write_bytes(PROBE.nm_json(index))
        with self.assertRaisesRegex(ValueError, "摘要不符"):
            PROBE.read_statement_identity(folder, receipt)
        # 即使单独改 index 回执，文件摘要与全局摘要不一致仍须拒绝。
        blob = index_file.read_bytes()
        receipt["index"].update(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with self.assertRaises(ValueError):
            PROBE.read_statement_identity(folder, receipt)

    def test_run_deadline_accepts_thirty_but_rejects_outside_before_reading(self):
        missing = self.base / "not-prepared.json"
        for timeout in (0, 31):
            with self.assertRaisesRegex(ValueError, "1–30"):
                PROBE.run(missing, timeout=timeout)
        with self.assertRaises(FileNotFoundError):
            PROBE.run(missing, timeout=30)

    def test_namemap_405583_nodes_bounded_output(self):
        # 与实物相同分母的自有节点图；不导入引擎、不执行剧情、不访问 Steam。
        class Statement:
            __slots__ = ("filename", "linenumber", "name", "next")
        count = 405583
        start = time.monotonic()
        nodes, names, filenames = [], {}, ["story-%04d.rpy" % i for i in range(800)]
        for index in range(count - 7):
            node = Statement()
            node.filename = filenames[index % len(filenames)]
            node.linenumber, node.name, node.next = index + 1, (node.filename, 700, index), None
            if nodes:
                nodes[-1].next = node
            nodes.append(node)
            names[node.name] = node
        for index in range(7):
            filename = "early-%d_ren.py" % index
            node = self.early_node(filename, (filename, 1000, 1))
            nodes.append(node)
            names[node.name] = node
        prepared = time.monotonic()
        fixture_peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        receipt = PROBE._namemap_helpers["capture_statement_identity"](
            names, str(self.base / "large"), 1000.1, 1000.9, nodes)
        captured = time.monotonic()
        snapshot = PROBE.read_statement_identity(self.base / "large", receipt)
        finished = time.monotonic()
        output_bytes = sum(path.stat().st_size for path in (self.base / "large").iterdir())
        self.assertEqual(receipt["count"], count)
        self.assertEqual(receipt["detail_count"], 7)
        self.assertEqual(receipt["non_early_count"], count - 7)
        self.assertEqual(receipt["edge_scan"]["list_count"], count)
        self.assertEqual(receipt["edge_scan"]["edge_count"], count - 8)
        self.assertEqual(len(snapshot["by_file"]), 7)
        self.assertLess(output_bytes, 1024 * 1024)
        print(json.dumps({"kind": "own_statement_identity_scale", "nodes": count,
            "fixture_seconds": prepared - start, "capture_seconds": captured - prepared,
            "readback_seconds": finished - captured, "output_bytes": output_bytes,
            "detail_count": receipt["detail_count"], "file_count": receipt["file_count"],
            "fixture_peak_rss_bytes": fixture_peak_rss,
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "peak_rss_units": "bytes on macOS; includes Python, own fixture and diagnostic",
            "python": sys.version, "canonical_sha256": receipt["canonical_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
