# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public tests must work without development records."""
import shutil
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("suite_export", ROOT / "tools/release-check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PublicSuiteTests(unittest.TestCase):
    def test_compatibility_checks_without_internal_records(self):
        with tempfile.TemporaryDirectory(prefix="sn-public-tests-") as tmp:
            public = Path(tmp) / "public"
            files, prefixes = release.exclusions(ROOT)

            def ignored(directory, names):
                relative = Path(directory).relative_to(ROOT)
                return [name for name in names if name in {".git", "__pycache__", ".build"}
                        or (relative / name).as_posix() in files
                        or ((relative / name).as_posix() + "/").startswith(prefixes)]

            shutil.copytree(ROOT, public, ignore=ignored)
            self.assertFalse((public / "tools/ingame-save-games.json").exists())
            self.assertFalse((public / "tools/一次性").exists())
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s",
                 str(public / "tests"), "-p", "test_compat_page.py"],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            recipes = subprocess.run(
                [sys.executable, "-B", "-m", "unittest",
                 "test_ingame_save.TableValidationTests"],
                cwd=public / "tests", capture_output=True, text=True, timeout=60)
            self.assertEqual(recipes.returncode, 0, recipes.stdout + recipes.stderr)
