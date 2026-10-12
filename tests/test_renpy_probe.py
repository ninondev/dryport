# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""显式指定材料后才运行自有 Ren'Py 窗口；默认只验证无窗口边界。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("renpy_probe", ROOT / "tools/probe-renpy.py")
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)
MATERIALS = os.environ.get("RENPY_TEST_MATERIALS")


class RenpyProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-renpy-safety-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    @unittest.skipUnless(sys.platform == "darwin", "运行入口只支持 macOS")
    def test_existing_output_and_dangling_alias_remain_untouched(self):
        materials = self.base / "materials"
        materials.mkdir()
        output = self.base / "output"
        output.mkdir()
        keep = output / "keep.txt"
        keep.write_text("keep")
        with self.assertRaises(FileExistsError):
            PROBE.probe(materials, output)
        self.assertEqual(keep.read_text(), "keep")
        alias = self.base / "alias"
        alias.symlink_to(self.base / "uncreated")
        with self.assertRaises(FileExistsError):
            PROBE.probe(materials, alias)
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.base / "uncreated").exists())

    def test_late_original_change_cannot_leave_success_receipt(self):
        original = self.base / "source"
        original.mkdir()
        (original / "data").write_text("before")
        snapshots = {"source": (original, PROBE.inventory(original))}
        (original / "data").write_text("after")
        report = {"ok": True}
        PROBE.finish_integrity(report, snapshots)
        self.assertFalse(report["ok"])
        self.assertFalse(report["integrity"]["source"]["unchanged"])

    @unittest.skipUnless(sys.platform == "darwin", "运行入口只支持 macOS")
    def test_repository_output_is_rejected_before_material_loading(self):
        materials = self.base / "material"
        materials.mkdir()
        output = ROOT / (".renpy-probe-" + self.base.name)
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "源码仓库"):
            PROBE.probe(materials, output)
        self.assertFalse(output.exists())

    def test_factory_library_collision_stops_without_overwriting(self):
        sdk = self.base / "sdk"
        leaf = sdk / "lib/py3-mac-universal/libsteam_api.dylib"
        leaf.parent.mkdir(parents=True)
        leaf.write_text("keep factory bytes")
        destination = self.base / "new-view"
        with self.assertRaises(FileExistsError):
            PROBE.prepare_view(sdk, self.base / "extension", destination)
        self.assertEqual(leaf.read_text(), "keep factory bytes")
        self.assertFalse(destination.exists())

    def test_bad_archive_hash_never_creates_execution_view(self):
        material = self.base / "material"
        archives = material / "runtime-archives"
        archives.mkdir(parents=True)
        (archives / "renpy-8.1.3-sdk.zip").write_bytes(b"not the official SDK")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            PROBE.verify_materials(material)

    def test_timeout_cleans_only_its_own_process_group(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = PROBE.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
            self.assertTrue(result["timed_out"])
            self.assertNotEqual(result["returncode"], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(result["pid"], 0)
            self.assertIsNone(other.poll())
        finally:
            other.terminate()
            other.wait(timeout=5)

    def test_false_success_precheck_never_dispatches_a_window(self):
        state = self.base / "state"
        state.mkdir()
        (state / "saves").mkdir()
        phases = []

        def engine(args, *, env, cwd, timeout):
            phase = env["SN_RENPY_WINDOW_PHASE"]
            phases.append(phase)
            self.assertEqual(env.get("SDL_VIDEODRIVER"), "dummy")
            self.assertNotIn("RENPY_DISABLE_FULLSCREEN", env)
            guard = {"ok": True, "interface_absent": True, "default_fullscreen": True,
                     "pid": 987654, "architecture": "arm64", "version": PROBE.VERSION,
                     "macapp": True, "steam_enabled": False, "savedir": env["SN_RENPY_SAVE"],
                     "fullscreen": phase in ("control", "precheck"),
                     "preferences_initialized": True, "seed": "official-engine-window-v1",
                     "before_fullscreen": True}
            Path(env["SN_RENPY_RECEIPT"] + ".guard").write_text(json.dumps(guard))
            if phase == "seed":
                (state / "saves/persistent").write_bytes(b"owned mock seed")
            return {"pid": 987654, "returncode": 0, "timed_out": False, "stderr": ""}

        with patch.object(PROBE, "run_bounded", side_effect=engine):
            with self.assertRaisesRegex(ValueError, "precheck"):
                PROBE.windowing_runs({}, {"SN_RENPY_VIEW": str(self.base / "runtime"),
                    "RENPY_DISABLE_FULLSCREEN": "1"}, state, self.base / "autorun", [], 20)
        self.assertEqual(phases, ["control", "seed", "precheck"])


@unittest.skipUnless(sys.platform == "darwin" and MATERIALS,
                     "需显式设置 RENPY_TEST_MATERIALS；不自动下载运行时或执行游戏")
class RenpyRuntimeTests(unittest.TestCase):
    def test_external_official_persistent_overrides_true_fullscreen_default(self):
        parent = Path(tempfile.mkdtemp(prefix="sn-renpy-window-regression-")).resolve()
        output = parent / "evidence"
        print("Ren'Py window evidence: " + str(output / "evidence.json"), file=sys.stderr)
        report = PROBE.probe(MATERIALS, output, windowing=True)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["windowing"]["ok"])
        self.assertTrue(all(item["unchanged"] for item in report["integrity"].values()))
        runs = report["windowing"]["runs"]
        self.assertEqual([run["phase"] for run in runs], ["control", "seed", "precheck", "window"])
        self.assertEqual([run["guard"]["fullscreen"] for run in runs], [True, False, False, False])
        self.assertTrue(all(run["guard"]["default_fullscreen"] for run in runs))
        self.assertTrue(all(run["guard"]["interface_absent"] for run in runs))
        self.assertEqual(runs[0]["guard"]["video_driver"], "dummy")
        self.assertEqual(runs[2]["guard"]["video_driver"], "dummy")
        display = runs[-1]["display"]
        self.assertFalse(display["window_flags"] & display["fullscreen_bits"])
        self.assertFalse(display["fullscreen"])
        self.assertIsNone(display["video_driver"])
        for run in runs:
            self.assertEqual(run["returncode"], 0)
            self.assertFalse(run["timed_out"])
            with self.assertRaises(ProcessLookupError):
                os.kill(run["pid"], 0)

    def test_original_arm64_engine_loads_save_after_restart_without_writing_originals(self):
        parent = Path(tempfile.mkdtemp(prefix="sn-renpy-regression-")).resolve()
        output = parent / "evidence"
        print("Ren'Py evidence: " + str(output / "evidence.json"), file=sys.stderr)
        report = PROBE.probe(MATERIALS, output)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["fixture_compilation"]["returncode"], 0)
        self.assertFalse(report["fixture_compilation"]["timed_out"])
        self.assertEqual(len(report["runs"]), 2)
        self.assertTrue(all(item["unchanged"] for item in report["integrity"].values()))
        for number, run in enumerate(report["runs"], 1):
            self.assertFalse(run["timed_out"])
            self.assertEqual(run["returncode"], 0)
            receipt = run["receipt"]
            self.assertEqual(receipt["architecture"], "arm64")
            self.assertEqual(receipt["version"], PROBE.VERSION)
            self.assertEqual(receipt["persistent_before"], number - 1)
            self.assertEqual(receipt["persistent_after"], number)
            self.assertEqual(receipt["restored"], number == 2)
            self.assertFalse(receipt["fullscreen"])
            self.assertEqual(set(receipt["protection"]), {"source", "bundle", "view"})
            for attempts in receipt["protection"].values():
                self.assertEqual(set(attempts), {"direct", "symlink", "hardlink", "parent_rename", "grandparent_rename"})
                self.assertEqual(set(attempts.values()), {1})
            with self.assertRaises(ProcessLookupError):
                os.kill(run["pid"], 0)


if __name__ == "__main__":
    unittest.main()
