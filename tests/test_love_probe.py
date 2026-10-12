# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只在显式指定原厂运行时后启动 LÖVE 自有无窗口样例。"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("love_probe", ROOT / "tools/probe-love.py")
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)
RUNTIME = os.environ.get("LOVE_TEST_APP")


@unittest.skipUnless(sys.platform == "darwin", "probe 入口仅支持 macOS")
class LoveProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sn-love-safety-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.runtime = self.base / "runtime/love.app"
        binary = self.runtime / "Contents/MacOS/love"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"test fixture; never executed")
        self.archive = self.runtime.parent / "love-11.5-macos.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            for path in [self.runtime, *sorted(self.runtime.rglob("*"))]:
                archive.write(path, Path("love.app") / path.relative_to(self.runtime))

    def test_dangling_output_link_is_rejected_without_creating_target(self):
        target = self.base / "user-destination"
        output = self.base / "existing-link"
        output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            PROBE.probe(self.runtime, output)
        self.assertTrue(output.is_symlink())
        self.assertFalse(target.exists())

    def test_final_fixture_changes_force_failure_even_after_rounds_passed(self):
        original_inventory = PROBE.inventory
        calls = {}
        def changed_at_final_read(path):
            path = Path(path)
            if path.name == "OriginalGame":
                calls[path] = calls.get(path, 0) + 1
                if calls[path] == 3:
                    (path / "protected.txt").write_text("fixture changed before final verification")
            return original_inventory(path)
        def simulated_run(args, *, env=None, **kwargs):
            if env is not None:
                state = Path(env["SN_LOVE_STATE"])
                save = Path(env["SN_LOVE_EXPECTED_SAVE"])
                save.mkdir(parents=True, exist_ok=True)
                (save / "counter.txt").write_text(env["SN_LOVE_ROUND"])
                receipt = {"ok": True, "arch": "arm64", "version_string": "11.5.0",
                           "source": str(args[-1]), "fused": state.name == "exe",
                           "window_module": False, "graphics_module": False}
                Path(env["SN_LOVE_RECEIPT"]).write_text(json.dumps(receipt))
            return {"returncode": 0, "timed_out": False, "stdout": "arm64", "stderr": ""}
        # 只验证报告归并：运行时、ZIP、回执均为自有 mock，不执行 LÖVE 或其他程序。
        with patch.object(PROBE, "ARCHIVE_SHA256", hashlib.sha256(self.archive.read_bytes()).hexdigest()), \
                patch.object(PROBE, "run_bounded", side_effect=simulated_run), \
                patch.object(PROBE, "inventory", side_effect=changed_at_final_read):
            report = PROBE.probe(self.runtime, self.base / "evidence")
        self.assertEqual(len(report["cases"]), 3)
        self.assertTrue(all(not case["source_unchanged"] for case in report["cases"]))
        self.assertTrue(report["runtime_unchanged"])
        self.assertFalse(report["ok"])
        self.assertFalse(json.loads((self.base / "evidence/evidence.json").read_text())["ok"])


@unittest.skipUnless(sys.platform == "darwin" and RUNTIME,
                     "需显式设置 LOVE_TEST_APP；不自动下载运行时或启动游戏")
class LoveProbeTests(unittest.TestCase):
    def test_official_arm64_runtime_reads_all_containers_and_preserves_originals(self):
        # 显式启用的原厂运行时验证保留独立证据，便于审查实际回执及失败现场。
        temporary = tempfile.mkdtemp(prefix="sn-love-regression-")
        output = Path(temporary) / "evidence"
        print("LÖVE evidence: " + str(output / "evidence.json"), file=sys.stderr)
        report = PROBE.probe(RUNTIME, output)
        with self.subTest(evidence=str(output)):
            self.assertTrue(report["ok"], report)
            self.assertTrue(report["runtime_unchanged"])
            self.assertTrue(report["runtime_matches_archive"])
            self.assertEqual(report["archive_sha256"], PROBE.ARCHIVE_SHA256)
            self.assertEqual([case["shape"] for case in report["cases"]], ["directory", "love", "exe"])
            for case in report["cases"]:
                self.assertTrue(case["source_unchanged"])
                self.assertTrue(case["bundle_unchanged"])
                self.assertEqual(case["saved_content"], "2")
                for number, run in enumerate(case["runs"], 1):
                    receipt = run["receipt"]
                    self.assertEqual(receipt["round"], number)
                    self.assertEqual(receipt["arch"], "arm64")
                    self.assertEqual(receipt["save_directory"], case["expected_save"])
                    self.assertEqual(receipt["fused"], case["shape"] == "exe")
                    self.assertFalse(receipt["window_module"])
                    self.assertFalse(receipt["graphics_module"])
                    for label in ("source", "bundle"):
                        for attempt in ("direct", "alias", "hardlink", "parent_rename", "grandparent_rename"):
                            self.assertEqual(receipt[label + "_" + attempt + "_errno"], 1)

    def test_existing_evidence_directory_is_never_reused(self):
        with tempfile.TemporaryDirectory(prefix="sn-love-preserve-") as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            (output / "user.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                PROBE.probe(RUNTIME, output)
            self.assertEqual((output / "user.txt").read_text(), "keep")

    def test_evidence_cannot_be_created_inside_factory_runtime(self):
        output = Path(RUNTIME) / "dryport-test-output-must-not-exist"
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "运行时内"):
            PROBE.probe(RUNTIME, output)
        self.assertFalse(output.exists())

    def test_timeout_reaps_only_the_probe_process(self):
        result = PROBE.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["returncode"], 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(result["pid"], 0)


if __name__ == "__main__":
    unittest.main()
