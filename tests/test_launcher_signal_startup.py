# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Signals must be handled both while configuration is read and while the fixture runs."""
import os
import shlex
import signal
import subprocess
import time
import unittest

import test_wine_wrap as fixtures


class LauncherSignalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WineWrapTests("test_launcher_handles_sigterm_while_the_game_runs")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def launch(self, launcher):
        return subprocess.Popen(["/bin/bash", str(launcher)],
                                env={"PATH": "/usr/bin:/bin", "HOME": str(self.fixture.base),
                                     "SN_WINE_NO_NOTIFY": "1"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_signal_during_configuration_returns_143_and_reaps_reader(self):
        fixture = self.fixture
        result = fixture.wrap("Startup Signal")
        self.assertEqual(result.returncode, 0, result.stderr)
        app = fixture.games / "Startup Signal.app"
        config = app / "Contents/Resources/wine-launch.json"
        config.unlink()
        os.mkfifo(config)
        proc = self.launch(app / "Contents/MacOS/launcher")
        writer = None
        try:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                try:
                    writer = os.open(config, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    time.sleep(0.02)
            self.assertIsNotNone(writer, "Configuration reader did not open the fixture FIFO")
            children = subprocess.check_output(["pgrep", "-P", str(proc.pid)], text=True).split()
            self.assertTrue(children)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
            self.assertEqual(proc.returncode, 143)
            for child in children:
                self.assertNotEqual(subprocess.run(["kill", "-0", child], capture_output=True).returncode, 0)
        finally:
            if writer is not None:
                os.close(writer)
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=10)

    def test_signal_after_the_fixture_starts_reaps_its_exact_pid(self):
        fixture = self.fixture
        marker = fixture.base / "fixture-pid"
        (fixture.runtime / "wine").write_text(
            '#!/bin/bash\ncase "${1:-}" in wineboot|reg|regedit) exit 0;; esac\n'
            f'printf "%s" "$$" > {shlex.quote(str(marker))}\nexec sleep 30\n')
        (fixture.runtime / "wineserver").write_text("#!/bin/sh\nexit 0\n")
        (fixture.runtime / "wineserver").chmod(0o755)
        result = fixture.wrap("Running Signal")
        self.assertEqual(result.returncode, 0, result.stderr)
        proc = self.launch(fixture.games / "Running Signal.app/Contents/MacOS/launcher")
        try:
            until = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < until:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "Fixture did not start")
            child = marker.read_text()
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 143)
            self.assertNotEqual(subprocess.run(["kill", "-0", child], capture_output=True).returncode, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=10)
