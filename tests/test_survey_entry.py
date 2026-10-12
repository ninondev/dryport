# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""用户主动刷新应重算失效的本地识别结果，不只发布元资料待定表。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SurveyEntryTests(unittest.TestCase):
    def test_refresh_requests_local_manifest_scan_and_propagates_failure(self):
        with tempfile.TemporaryDirectory(prefix="sn-survey-entry-") as directory:
            home = Path(directory)
            lib, binary = home / "lib", home / "bin"
            lib.mkdir()
            binary.mkdir()
            (lib / "repack-gate.sh").write_text("# Not invoked by survey.\n")
            (lib / "library_discovery.py").write_text(
                "import json,os,pathlib,sys\n"
                "pathlib.Path(os.environ['TEST_ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))\n"
                "sys.exit(int(os.environ['TEST_EXIT']))\n")
            (binary / "node").write_text("#!/bin/sh\nexit 99\n")
            (binary / "node").chmod(0o755)
            arguments = home / "arguments.json"
            env = {**os.environ, "SN_HOME": str(home), "TEST_ARGUMENTS": str(arguments),
                   "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
            for extra in ([], ["--deep"]):
                for code in (0, 3):
                    result = subprocess.run([str(ROOT / "bin/dryport"), "survey", *extra],
                        env={**env, "TEST_EXIT": str(code)}, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(json.loads(arguments.read_text()), ["--write", "--human", "--deep", *extra])
            self.assertFalse((home / "games").exists())


if __name__ == "__main__":
    unittest.main()
