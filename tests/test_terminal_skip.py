# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Console-dependent checks detect a missing controlling terminal themselves."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_steambridge_tools as fixtures

ROOT = Path(__file__).resolve().parents[1]
REASON = "Requires a controlling terminal; /dev/tty is unavailable"
NAMES = (
    "test_failed_helper_leaves_game_and_backup_untouched",
    "test_install_uses_runtime_and_preserves_original",
)


class TerminalSkipTests(unittest.TestCase):
    def test_only_the_two_console_tests_skip_in_a_session_without_a_terminal(self):
        code = '''
import os, sys, unittest
import test_steambridge_tools as checks
try:
    fd = os.open('/dev/tty', os.O_RDONLY)
except OSError:
    pass
else:
    os.close(fd)
    raise AssertionError('Fixture unexpectedly has a controlling terminal')
names = sys.argv[1:]
result = unittest.TextTestRunner().run(unittest.TestSuite(
    checks.BuildAndInstallTest(name) for name in names))
assert result.testsRun == 2
assert len(result.skipped) == 2, result.skipped
assert all(reason == 'Requires a controlling terminal; /dev/tty is unavailable'
           for _, reason in result.skipped), result.skipped
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, "-B", "-c", code, *NAMES],
                                cwd=ROOT / "tests", capture_output=True, text=True,
                                start_new_session=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("test_generator_default_output_is_outside_source",
                     "test_compile_failure_preserves_all_previous_artifacts",
                     "test_missing_or_multiple_dlls_do_not_install"):
            self.assertFalse(getattr(getattr(fixtures.BuildAndInstallTest, name), "__unittest_skip__", False))

    def test_terminal_probe_accepts_an_actual_terminal_and_closes_its_descriptor(self):
        with mock.patch.object(fixtures.os, "open", return_value=19), \
             mock.patch.object(fixtures.os, "isatty", return_value=True), \
             mock.patch.object(fixtures.os, "close") as close:
            self.assertTrue(fixtures.controlling_terminal_available())
        close.assert_called_once_with(19)
