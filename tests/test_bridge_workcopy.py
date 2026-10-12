# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Manual bridge installation must refuse originals and linked work-copy paths."""
import contextlib
import json
import os
from pathlib import Path
import plistlib
import signal
import sys
import unittest

import test_steambridge_tools as fixtures

BRIDGE = fixtures.BRIDGE


class WorkCopyTests(unittest.TestCase):
    setUp = fixtures.BuildAndInstallTest.setUp
    run_script = fixtures.BuildAndInstallTest.run_script

    def successful_installs(self, linked_root=False, count=1):
        game = self.base / "generated games/fixture.app/Contents/Resources/game"
        dll, pid = self.fixture(game, generated=True)
        if linked_root:
            alias = self.base / "games alias"
            alias.symlink_to(Path(self.env["SN_GAMES"]), target_is_directory=True)
            self.env["SN_GAMES"] = str(alias)
            argument = alias / "fixture.app/Contents/Resources/game"
        else:
            argument = game
        pids = []
        try:
            for _ in range(count):
                result = self.run_script(BRIDGE / "install-into-game.sh", argument, 123)
                if pid.exists():
                    pids.append(int(pid.read_text()))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(dll.read_bytes(), b"bridge bytes")
                self.assertEqual(Path(str(dll) + ".orig").read_bytes(), b"original bytes")
            self.assertEqual(len(list(self.out.glob("helper.*"))), count)
            os.replace(str(dll) + ".orig", dll)
            self.assertEqual(dll.read_bytes(), b"original bytes")
        finally:
            for process in set(pids):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(process, signal.SIGTERM)

    def test_install_accepts_a_symlinked_games_root(self):
        self.successful_installs(linked_root=True)

    def test_install_can_run_twice_with_distinct_logs_and_original_backup(self):
        self.successful_installs(count=2)

    def fixture(self, game, generated=False):
        game.mkdir(parents=True)
        dll = game / "steam_api64.dll"
        dll.write_bytes(b"original bytes")
        (self.out / "steam_api64.dll").write_bytes(b"bridge bytes")
        pid = self.base / "helper-pid"
        helper = self.out / "steambridge-helper"
        helper.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pid)!r}).write_text(str(os.getpid()))\nprint("listening on 127.0.0.1:0", flush=True)\ntime.sleep(30)\n')
        helper.chmod(0o755)
        fakebin = self.base / "fakebin"
        fakebin.mkdir()
        nohup = fakebin / "nohup"
        nohup.write_text('#!/bin/sh\nexec "$@"\n')
        nohup.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env["PATH"]
        games = self.base / "generated games"
        games.mkdir(exist_ok=True)
        self.env["SN_GAMES"] = str(games)
        if generated:
            contents = game.parent.parent
            (contents / "Info.plist").write_bytes(plistlib.dumps(dict(
                CFBundleIdentifier="local.winewrap.fixture", CFBundleExecutable="launcher")))
            (game.parent / "wine-launch.json").write_text(json.dumps(dict(appid="123")))
        return dll, pid

    def invoke(self, game, dll, pid):
        try:
            result = self.run_script(BRIDGE / "install-into-game.sh", game, 123)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("工作副本", result.stderr)
            self.assertEqual(dll.read_bytes(), b"original bytes")
            self.assertFalse(Path(str(dll) + ".orig").exists())
            self.assertFalse(pid.exists(), "Refusal must precede starting a helper")
        finally:
            if pid.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pid.read_text()), signal.SIGTERM)

    def test_original_directory_is_refused_before_helper_or_dll_changes(self):
        game = self.base / "owned original"
        dll, pid = self.fixture(game)
        self.invoke(game, dll, pid)

    def test_linked_generated_game_directory_is_refused(self):
        original = self.base / "owned original"
        dll, pid = self.fixture(original)
        game = Path(self.env["SN_GAMES"]) / "fixture.app/Contents/Resources/game"
        game.parent.mkdir(parents=True)
        game.symlink_to(original, target_is_directory=True)
        self.invoke(game, dll, pid)

    def test_generated_metadata_with_wrong_appid_is_refused(self):
        game = self.base / "generated games/fixture.app/Contents/Resources/game"
        dll, pid = self.fixture(game, generated=True)
        (game.parent / "wine-launch.json").write_text(json.dumps(dict(appid="999")))
        self.invoke(game, dll, pid)

    def test_hardlinked_game_dll_is_not_an_independent_copy(self):
        game = self.base / "generated games/fixture.app/Contents/Resources/game"
        dll, pid = self.fixture(game, generated=True)
        original = self.base / "original.dll"
        os.link(dll, original)
        self.invoke(game, dll, pid)
        self.assertEqual(original.read_bytes(), b"original bytes")

    def test_valid_workcopy_with_fake_helper_preserves_its_original(self):
        game = self.base / "generated games/fixture.app/Contents/Resources/game"
        dll, pid = self.fixture(game, generated=True)
        try:
            result = self.run_script(BRIDGE / "install-into-game.sh", game, 123)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(dll.read_bytes(), b"bridge bytes")
            self.assertEqual(Path(str(dll) + ".orig").read_bytes(), b"original bytes")
            self.assertTrue(pid.exists())
        finally:
            if pid.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pid.read_text()), signal.SIGTERM)
