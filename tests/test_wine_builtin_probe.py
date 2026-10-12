# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""加载判据须拒绝原件回退、逻辑路径冒充物理来源和不完整的宿主观察。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("builtin_probe", ROOT / "tools/probe-wine-builtin.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.overlay = Path("/owned-overlay/x86_64-windows/steam_api64.dll")
        self.local = Path("/fake-game/plugins/steam_api64.dll")
        self.good = {"rc": 0, "timeout": False, "observed": {"loaded": "1", "builtin": "1", "fixture": "0",
                       "bridge_export": "1", "logical_path": str(self.local)},
                     "lsof": {"rc": 0, "timeout": False}, "physical_dll_paths": [str(self.overlay)]}

    def verdict(self, result, expected="bridge"):
        return probe.accepted(result, expected, self.overlay, self.local)

    def test_wine_logical_original_path_requires_external_physical_mapping(self):
        self.assertTrue(self.verdict(self.good))
        bad = copy.deepcopy(self.good)
        bad["physical_dll_paths"] = [str(self.local)]
        self.assertFalse(self.verdict(bad))
        bad["physical_dll_paths"] = [str(self.local), str(self.overlay)]
        self.assertFalse(self.verdict(bad))

    def test_missing_overlay_cannot_pass_by_native_fallback(self):
        missing = {"rc": 23, "timeout": False, "observed": {"loaded": "0", "error": "126"}}
        self.assertTrue(self.verdict(missing, "missing"))
        self.assertFalse(self.verdict(self.good, "missing"))
        missing["observed"]["error"] = "193"
        self.assertFalse(self.verdict(missing, "missing"))

    def test_wrong_identity_and_incomplete_observation_are_failures(self):
        for key, value in (("builtin", "0"), ("fixture", "1"), ("bridge_export", "0")):
            bad = copy.deepcopy(self.good)
            bad["observed"][key] = value
            self.assertFalse(self.verdict(bad))
        bad = copy.deepcopy(self.good)
        bad["lsof"]["timeout"] = True
        self.assertFalse(self.verdict(bad))
        bad = copy.deepcopy(self.good)
        bad["timeout"] = True
        self.assertFalse(self.verdict(bad))


@unittest.skipUnless(os.environ.get("SN_RUN_WINE_BUILTIN") == "1", "真实 Wine fixture 须显式启用")
class LiveFixtureTest(unittest.TestCase):
    def test_six_actual_loader_cases(self):
        self.assertIn("SN_BUILTIN_TEST_DLL", os.environ, "显式提供待测桥 DLL，不自动编译或更新运行时")
        dll = Path(os.environ["SN_BUILTIN_TEST_DLL"]).resolve(strict=True)
        wine = probe.SN_RUNTIMES / "wine-cx/bin/wine"
        with tempfile.TemporaryDirectory(prefix="sn-builtin-regression-", dir=probe.SN_RUNTIMES) as directory:
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe-wine-builtin.py"),
                                     "--dll", str(dll), "--wine", str(wine)],
                                    env=dict(os.environ, SN_RUNTIMES=directory),
                                    capture_output=True, text=True, timeout=660)
            reports = list(Path(directory).glob("sn-wine-builtin-*/evidence.json"))
            self.assertEqual(len(reports), 1, result.stdout + result.stderr)
            report = json.loads(reports[0].read_text())
            self.assertEqual(result.returncode, 0, json.dumps(report) + result.stderr)
            self.assertTrue(report["ok"])
            self.assertEqual(len(report["cases"]), 6)
            self.assertTrue(report["local_dll_unchanged"])
            self.assertTrue(report["source_dll_unchanged"])
            self.assertFalse(report["steam_contacted"])


if __name__ == "__main__":
    unittest.main()
