# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Cleanup returns only after its process group is observably gone."""
import os
import sys
import unittest
from unittest.mock import patch

import test_scummvm_probe as fixtures


class GroupCleanupTests(unittest.TestCase):
    def test_transient_group_query_denial_does_not_hide_the_receipt_error(self):
        fixture = fixtures.ProbeBoundaryTests("test_start_receipt_failure_still_kills_and_reaps_owned_process")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        logs = fixture.base / "group-query"
        logs.mkdir()
        probe = fixtures.PROBE
        real_killpg = os.killpg
        queries = []
        started = []

        def receipt(pid):
            started.append(pid)
            raise OSError("owned receipt unavailable")

        def killpg(pid, sig):
            if sig == 0:
                queries.append(pid)
                if len(queries) <= 2:
                    raise PermissionError(1, "group query not ready")
                raise ProcessLookupError(3, "group has gone")
            return real_killpg(pid, sig)

        with patch.object(probe.os, "killpg", side_effect=killpg):
            with self.assertRaisesRegex(OSError, "owned receipt unavailable"):
                probe.run_owned([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                                env={"PATH": "/usr/bin:/bin"}, cwd=fixture.base, logs=logs,
                                name="receipt", timeout=1, on_start=receipt)
        self.assertEqual(len(started), 1)
        self.assertGreaterEqual(len(queries), 3, "A denied query is not proof that the group has gone")
        self.assertEqual(set(queries), {started[0]})
        with self.assertRaises(ProcessLookupError):
            os.kill(started[0], 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(started[0], os.WNOHANG)
