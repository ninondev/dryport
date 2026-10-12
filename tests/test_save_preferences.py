# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Per-game Cloud choices persist, default off, and expose synchronization conflicts."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_save_conflicts as fixtures

ROOT = Path(__file__).resolve().parents[1]


class SavePreferenceTests(unittest.TestCase):
    install = fixtures.SaveLauncherTests.install

    def setUp(self):
        fixtures.SaveLauncherTests.setUp(self)
        self.settings = ROOT / "lib/savebridge/settings.py"
        if self.settings.is_file():
            shutil.copyfile(self.settings, self.tool / "settings.py")
        shutil.copyfile(ROOT / "lib/bundle_safety.py", self.base / "bundle_safety.py")
        self.game_marker = self.base / "game-ran"
        (self.macos / "fixture").write_text('#!/bin/sh\nprintf ran > "$SAVE_MOCK_GAME_RAN"\n')
        (self.tool / "savebridge").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$2" >> "$SAVE_MOCK_LOG"\n'
            'if [ "$2" = "${SAVE_MOCK_CONFLICT:-}" ]; then echo "Sync conflict: both versions retained" >&2; exit 3; fi\n')
        self.fake = self.base / "fakebin"
        self.fake.mkdir()
        self.dialog = self.base / "dialog"
        (self.fake / "osascript").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SAVE_MOCK_DIALOG"\n')
        (self.fake / "osascript").chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.base), "SN_GAMES": str(self.base / "Games"),
                    "PATH": str(self.fake) + os.pathsep + os.environ["PATH"],
                    "SAVE_MOCK_LOG": str(self.log), "SAVE_MOCK_GAME_RAN": str(self.game_marker),
                    "SAVE_MOCK_DIALOG": str(self.dialog)}
        self.env.pop("SN_SAVEBRIDGE", None)

    def choice(self, command):
        return subprocess.run([sys.executable, str(self.settings), command, "480", "--yes"],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def launch(self, **extra):
        return subprocess.run(["/bin/bash", str(self.macos / "launcher")],
                              env={**self.env, **extra}, capture_output=True, text=True, timeout=10)

    def test_legacy_launch_environment_cannot_enable_an_unset_game(self):
        self.install()
        result = self.launch(SN_SAVEBRIDGE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists(), "An old environment flag cannot grant a persistent opt-in")

    def test_choice_persists_across_launches_and_hook_reinstallation(self):
        self.install()
        enabled = self.choice("enable")
        self.assertEqual(enabled.returncode, 0, enabled.stdout + enabled.stderr)
        self.assertEqual(self.launch().returncode, 0)
        self.install()
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["pull", "push", "pull", "push"])
        self.assertEqual(self.choice("disable").returncode, 0)
        self.assertEqual(self.launch(SN_SAVEBRIDGE="1").returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["pull", "push", "pull", "push"])

    def test_pull_conflict_aborts_launch_and_push_and_shows_a_user_message(self):
        self.install()
        self.assertEqual(self.choice("enable").returncode, 0)
        result = self.launch(SAVE_MOCK_CONFLICT="pull")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("conflict", result.stderr.lower())
        self.assertFalse(self.game_marker.exists())
        self.assertEqual(self.log.read_text().splitlines(), ["pull"])
        self.assertTrue(self.dialog.is_file(), "Finder launches need a visible conflict message")

    def test_push_conflict_is_reported_after_play_and_preserves_exit_three(self):
        self.install()
        self.assertEqual(self.choice("enable").returncode, 0)
        result = self.launch(SAVE_MOCK_CONFLICT="push")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("conflict", result.stderr.lower())
        self.assertTrue(self.game_marker.exists())
        self.assertEqual(self.log.read_text().splitlines(), ["pull", "push"])
        self.assertTrue(self.dialog.is_file())

    def test_public_cli_changes_only_the_selected_game(self):
        self.install()
        env = {**self.env, "SN_HOME": str(ROOT)}
        result = subprocess.run([str(ROOT / "bin/dryport"), "cloud", "enable", "480", "--yes"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(ROOT / "bin/dryport"), "cloud", "status", "481"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("disabled", result.stdout)
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["pull", "push"])

    def test_linked_choice_stays_off_and_preserves_the_other_file(self):
        self.install()
        choice = self.base / "Games/.savebridge-state/480/choice"
        other = self.base / "other-choice"
        other.write_bytes(b"enabled\n")
        choice.unlink()
        choice.symlink_to(other)
        result = self.choice("enable")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(other.read_bytes(), b"enabled\n")
        self.assertTrue(choice.is_symlink())
        result = self.launch(SN_SAVEBRIDGE="1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
