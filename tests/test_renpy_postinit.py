# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""默认不运行 Ren'Py；显式提供材料后验证全无窗口、自有样例。"""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sn_postinit_probe", ROOT / "tools/probe-renpy-postinit.py")
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)
MATERIALS = os.environ.get("RENPY_TEST_MATERIALS")


def preferences():
    return {"fullscreen": True, "language": "sn_fixture_language", "text_cps": 41,
            "volumes": {"music": 0.09, "sfx": 0.25, "voice": 0.49},
            "unrelated_existing_preference": {"custom": [1, 2, 3]}}


def samples():
    before = preferences()
    control = {"before": before, "after_original_init": dict(before, text_cps=37),
               "script_digest": "old-script", "statement_names_sha256": "old-statements"}
    seed = {**control, "after": dict(before, fullscreen=False)}
    precheck = {**seed, "before": seed["after"]}
    return tuple(copy.deepcopy(item) for item in (control, seed, precheck))


class RenpyPostinitSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-postinit-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    @unittest.skipUnless(sys.platform == "darwin", "仅 macOS 入口")
    def test_existing_and_dangling_output_rejected_before_runtime_read(self):
        materials = self.base / "materials"
        materials.mkdir()
        output = self.base / "output"
        output.mkdir()
        (output / "keep").write_text("untouched")
        alias = self.base / "alias"
        alias.symlink_to(self.base / "not-created")
        with patch.object(PROBE.BASE, "verify_materials") as verify:
            for path in (output, alias):
                with self.assertRaises(FileExistsError):
                    PROBE.probe(materials, path)
            verify.assert_not_called()
        self.assertEqual((output / "keep").read_text(), "untouched")
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.base / "not-created").exists())

    @unittest.skipUnless(sys.platform == "darwin", "仅 macOS 入口")
    def test_repository_and_material_output_rejected_before_runtime_read(self):
        materials = self.base / "materials"
        materials.mkdir()
        with patch.object(PROBE.BASE, "verify_materials") as verify:
            for path in (ROOT / "sn-owned-postinit-forbidden", materials / "output"):
                with self.assertRaises(ValueError):
                    PROBE.probe(materials, path)
            verify.assert_not_called()

    def test_readback_requires_all_preferences_and_compiled_identity(self):
        control, seed, precheck = samples()
        PROBE.compare_preferences(control, seed, precheck)
        for field in ("language", "volumes", "unrelated_existing_preference"):
            bad = copy.deepcopy(precheck)
            bad["before"][field] = "changed"
            with self.assertRaisesRegex(ValueError, "独立进程回读"):
                PROBE.compare_preferences(control, seed, bad)
        bad = copy.deepcopy(precheck)
        bad["statement_names_sha256"] = "new-time-based-identities"
        with self.assertRaisesRegex(ValueError, "原编译脚本身份"):
            PROBE.compare_preferences(control, seed, bad)

    def test_official_none_key_remains_distinct_from_string_null(self):
        node = next(node for node in ast.parse(PROBE.RPE).body
                    if isinstance(node, ast.FunctionDef) and node.name == "normalize")
        namespace = {"json": json}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-rpe-normalize", "exec"), namespace)
        normalize = namespace["normalize"]
        value = {"pad_bindings": {None: {"pad_a": ["dismiss"]}, "null": {"pad_a": ["different"]}}}
        result = normalize(value)
        self.assertEqual(len(result["pad_bindings"]["__typed_mapping__"]), 2)
        self.assertEqual(json.loads(json.dumps(result, sort_keys=True)), result)
        self.assertNotEqual(normalize({None: 1}), normalize({"null": 1}))

    def test_seed_must_start_with_original_applied_defaults(self):
        control, seed, precheck = samples()
        seed["before"]["language"] = "foreign-game-language"
        with self.assertRaisesRegex(ValueError, "seed 改写前"):
            PROBE.compare_preferences(control, seed, precheck)
        control, seed, precheck = samples()
        control["before"]["volumes"]["music"] = 1.0
        with self.assertRaisesRegex(ValueError, "偏好顺序不符|原始音量"):
            PROBE.compare_preferences(control, seed, precheck)

    def test_save_failure_cannot_be_accepted_as_regular_seed(self):
        data = {"phase": "seed", "pid": 42, "architecture": "arm64", "version": PROBE.VERSION,
                "interface_attempts": 0, "interface_absent": True, "savedir": "/owned/saves",
                "extra_savedirs": ["/owned/saves/sync"], "preferences_initialized": True, "steam_enabled": False,
                "persistent_marker": "owned-fixture-v1", "active_save_locations": ["/owned/saves"],
                "events": ["rpe_loaded", "queue_finalizer", "original_late_first", "defer_finalizer",
                           "original_late_second", "finalizer"], "deferrals": 1,
                "ok": False, "error": "save_returned_without_persistent_file"}
        run = {"pid": 42, "receipt": data, "timed_out": False, "returncode": 0, "stderr": ""}
        with self.assertRaisesRegex(ValueError, "自有偏好回执失败"):
            PROBE.validate_guard(run, "seed", Path("/owned/saves"))
        data["ok"] = True
        data["active_save_locations"].append("/foreign/saves")
        with self.assertRaisesRegex(ValueError, "活动保存路径不唯一"):
            PROBE.validate_guard(run, "seed", Path("/owned/saves"))

    @unittest.skipUnless(sys.platform == "darwin", "Darwin 文件写入限额")
    def test_output_limit_and_timeout_reap_only_owned_children(self):
        env = {"PATH": "/usr/bin:/bin"}
        limited = PROBE.run_owned([sys.executable, "-c", "import os; os.write(1,b'x'*4000000)"],
            env=env, cwd=self.base, logs=self.base, name="limit", timeout=3)
        self.assertLessEqual(Path(limited["stdout_file"]).stat().st_size, PROBE.OUTPUT_LIMIT)
        timeout = PROBE.run_owned([sys.executable, "-c", "import time; time.sleep(30)"],
            env=env, cwd=self.base, logs=self.base, name="timeout", timeout=0.1)
        self.assertTrue(timeout["timed_out"])
        for run in (limited, timeout):
            with self.assertRaises(ProcessLookupError):
                os.kill(run["pid"], 0)


@unittest.skipUnless(sys.platform == "darwin" and MATERIALS,
                     "需显式 RENPY_TEST_MATERIALS；不下载、不运行游戏或 GUI")
class RenpyPostinitMechanismTests(unittest.TestCase):
    def test_external_rpe_preserves_defaults_late_callbacks_and_rejects_bad_saves(self):
        parent = Path(tempfile.mkdtemp(prefix="sn-renpy-postinit-mechanism-")).resolve()
        output = parent / "evidence"
        print("Ren'Py headless evidence: " + str(output / "evidence.json"), file=sys.stderr)
        report = PROBE.probe(MATERIALS, output)
        self.assertTrue(report["ok"], report.get("error"))
        self.assertTrue(report["cross_process_preferences_verified"])
        self.assertTrue(all(row["unchanged"] for row in report["preflight_integrity"].values()))
        self.assertTrue(all(row["unchanged"] for row in report["integrity"].values()))
        phases = [run["phase"] for run in report["runs"]]
        self.assertEqual(phases, ["generation", "control", "seed", "precheck", "denied_seed", "denied_precheck", "extra"])
        for run in report["runs"]:
            self.assertFalse(run["timed_out"])
            self.assertTrue(run["pid_reaped"])
            self.assertEqual(run["receipt"]["interface_attempts"], 0)
        guard = report["runs"][3]["receipt"]
        self.assertFalse(guard["before"]["fullscreen"])
        self.assertEqual(guard["before"]["language"], "sn_fixture_language")
        self.assertEqual(guard["before"]["text_cps"], 41)
        self.assertEqual(guard["deferrals"], 1)
        self.assertEqual(report["runs"][1]["receipt"]["after_original_init"]["text_cps"], 37)
        self.assertEqual(report["runs"][2]["receipt"]["after_original_init"]["text_cps"], 37)
        self.assertTrue(report["runs"][4]["receipt"]["save_call_returned"])
        self.assertFalse(report["runs"][4]["receipt"]["ok"])
        self.assertFalse(report["runs"][5]["receipt"]["ok"])
        self.assertEqual(report["runs"][6]["receipt"]["error"], "unexpected_extra_savedirs")


if __name__ == "__main__":
    unittest.main()
