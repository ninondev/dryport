# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""RGSS/mkxp-z 打包只修改临时 bundle，不触碰用户已装的 app。"""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/dryport"


class MkxpWrapTests(unittest.TestCase):
    def test_bundle_identity_uses_game_name_and_appid(self):
        with tempfile.TemporaryDirectory(prefix="sn-mkxp-wrap-") as directory:
            base = Path(directory)
            games = base / "games"
            runtimes = base / "runtimes"
            steam = base / "steam"
            source = base / "source"
            games.mkdir()
            template = runtimes / "mkxp-build/Build/Products/Release/Z-universal.app"
            (template / "Contents/MacOS").mkdir(parents=True)
            (template / "Contents/Game").mkdir()
            (template / "Contents/MacOS/Z-universal").write_text("fixture executable\n")
            (template / "Contents/MacOS/Z-universal").chmod(0o755)
            (template / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "Z-universal",
                "CFBundleIdentifier": "org.struma.mkxp-z",
                "CFBundleName": "mkxp-z",
                "CFBundleDisplayName": "mkxp-z",
                "CFBundlePackageType": "APPL",
            }))
            source.mkdir()
            (source / "Game.ini").write_text("[Game]\nLibrary=RGSS3\n")
            (source / "Save01.rvdata2").write_text("save fixture\n")

            env = {
                **os.environ,
                "SN_HOME": str(ROOT),
                "SN_GAMES": str(games),
                "SN_RUNTIMES": str(runtimes),
                "STEAM": str(steam),
                "STEAM_APPID": "123456",
            }
            command = [
                "/bin/bash", "-c",
                ". \"$1\" __test_mkxp_wrap__ >/dev/null; "
                "require_repack_permission(){ return 0; }; "
                "wrap_done(){ return 0; }; "
                "cmd_wrap \"$2\" \"$3\"",
                "test", str(CLI), str(source), "Fixture Game",
            ]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

            app = games / "Fixture Game.app"
            self.assertTrue((app / "Contents/MacOS/Fixture Game").is_file())
            self.assertFalse((app / "Contents/MacOS/Z-universal").exists())
            with (app / "Contents/Info.plist").open("rb") as stream:
                metadata = plistlib.load(stream)
            self.assertEqual(metadata["CFBundleExecutable"], "Fixture Game")
            self.assertEqual(metadata["CFBundleName"], "Fixture Game")
            self.assertEqual(metadata["CFBundleDisplayName"], "Fixture Game")
            self.assertEqual(metadata["CFBundleIdentifier"], "org.dryport.mkxp.123456")
            config = (app / "Contents/Game/mkxp.json").read_text()
            self.assertIn('"windowTitle": "Fixture Game"', config)


if __name__ == "__main__":
    unittest.main()
