# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""clean 的一次性 prefix 与旧 catalog generation 只在显式确认后删除。"""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/dryport"


class CleanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-clean-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.runtimes = self.base / "runtimes"
        self.games = self.base / "games"
        self.steam = self.base / "steam"
        self.runtimes.mkdir()
        self.games.mkdir()
        inventory = ROOT / "docs/internal/cleanup.json"
        private_prefix = (json.loads(inventory.read_text())["cleanup_prefixes"][0].replace("*", "fixture")
                          if inventory.is_file() else "sn-trial-private-fixture")
        self.prefixes = [
            self.runtimes / name for name in (
                "sn-wine-fixture", "sn-steambridge-fixture", "sn-trial-fixture",
                private_prefix, "sn-unbound-fixture",
            )
        ]
        for prefix in self.prefixes:
            prefix.mkdir()
            (prefix / "marker").write_text("one-off\n")
        self.kept_runtime = self.runtimes / "sn-runtime-kept"
        self.kept_runtime.mkdir()
        (self.kept_runtime / "marker").write_text("keep\n")

        self.catalog = self.games / ".dryport-catalog"
        self.catalog.mkdir()
        self.current_generation = self.catalog / "generation-current"
        self.old_generation = self.catalog / "generation-old"
        self.current_generation.mkdir()
        self.old_generation.mkdir()
        (self.current_generation / "current.json").write_text("current\n")
        (self.old_generation / "old.json").write_text("old\n")
        (self.catalog / "current").symlink_to("generation-current")

        self.env = {
            **os.environ,
            "HOME": str(self.base / "isolated-home"),
            "SN_HOME": str(ROOT),
            "SN_RUNTIMES": str(self.runtimes),
            "SN_GAMES": str(self.games),
            "STEAM": str(self.steam),
        }

    def invoke(self, *args, input_text=None):
        return subprocess.run(
            [str(CLI), "clean", *args],
            env=self.env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_default_only_accounts_for_new_prefixes_and_old_generations(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in [*self.prefixes, self.old_generation, self.current_generation, self.kept_runtime]:
            self.assertTrue(path.exists(), path)
        for path in [*self.prefixes, self.old_generation]:
            self.assertIn(str(path), result.stdout)
        self.assertIn("一次性 Wine prefix", result.stdout)
        self.assertIn("目录旧 generation", result.stdout)
        self.assertIn("只算账, 一个字节都不动", result.stdout)
        self.assertRegex(result.stdout, r"sn-wine-fixture.*\([0-9]+ (?:KB|MB|GB)\)")

    def test_clean_without_private_inventory_preserves_all_previewed_data(self):
        public = self.base / "public"
        (public / "bin").mkdir(parents=True)
        shutil.copyfile(CLI, public / "bin/dryport")
        shutil.copytree(ROOT / "lib", public / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        self.assertFalse((public / "docs/internal/cleanup.json").exists())
        result = subprocess.run(["/bin/bash", str(public / "bin/dryport"), "clean"],
                                env={**self.env, "SN_HOME": str(public)},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in [*self.prefixes, self.old_generation, self.current_generation, self.kept_runtime]:
            self.assertTrue(path.exists(), path)
        for path in (self.prefixes[0], self.prefixes[1], self.prefixes[2], self.old_generation):
            self.assertIn(str(path), result.stdout)
        self.assertNotIn(str(self.current_generation), result.stdout)
        self.assertIn("只算账, 一个字节都不动", result.stdout)

    def test_go_asks_by_category_and_deletes_only_confirmed_items(self):
        result = self.invoke("--go", input_text="y\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.prefixes:
            self.assertFalse(path.exists(), path)
        self.assertFalse(self.old_generation.exists())
        self.assertTrue(self.current_generation.exists())
        self.assertTrue((self.catalog / "current").is_symlink())
        self.assertTrue(self.kept_runtime.exists())
        self.assertGreaterEqual(result.stdout.count("删这一类? [y/N]"), 2)
        self.assertIn("一次性 Wine prefix", result.stdout)
        self.assertIn("目录旧 generation", result.stdout)

    @unittest.skipUnless(shutil.which("lsof"), "需要 lsof 判断占用目录")
    def test_go_skips_trial_prefix_with_open_file(self):
        busy = self.runtimes / "sn-trial-fixture"
        holder = subprocess.Popen(
            [sys.executable, "-c", "import sys,time; handle=open(sys.argv[1], 'rb'); time.sleep(30)",
             str(busy / "marker")],
            stdin=subprocess.DEVNULL,
        )
        self.addCleanup(self._stop_process, holder)
        for _ in range(20):
            probe = subprocess.run(
                ["lsof", "-nP", "+D", str(busy)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if str(busy / "marker") in probe.stdout:
                break
            time.sleep(0.05)
        else:
            self.fail("lsof 没有看到测试占用进程打开 sn-trial fixture")

        result = self.invoke("--go", input_text="y\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(busy.exists())
        self.assertIn("正在使用，跳过", result.stdout)
        self.assertIn("lsof", result.stdout)
        for path in self.prefixes:
            if path != busy:
                self.assertFalse(path.exists(), path)
        self.assertFalse(self.old_generation.exists())
        self.assertTrue(self.current_generation.exists())

    def test_current_generation_is_never_accounted_for_or_deleted(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.current_generation), result.stdout)
        self.assertIn(str(self.old_generation), result.stdout)

        result = self.invoke("--go", input_text="y\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.old_generation.exists())
        self.assertTrue(self.current_generation.exists())
        self.assertTrue((self.catalog / "current").is_symlink())

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def test_old_app_backups_keep_the_newest_of_each_kind(self):
        import time
        dirs = {}
        for kind, names in (("backup", ("a1", "a2", "a3")), ("previous", ("p1", "p2")), ("rolledback", ("r1",))):
            for i, name in enumerate(names):
                d = self.games / f".dryport-{kind}.{name}"
                (d / "Dryport.app/Contents/MacOS").mkdir(parents=True)
                (d / "Dryport.app/Contents/MacOS/Dryport").write_text("old\n")
                stamp = time.time() - 1000 + i * 10
                os.utime(d, (stamp, stamp))
                dirs[(kind, name)] = d
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("伴侣 app 的旧版备份", result.stdout)
        for key in (("backup", "a1"), ("backup", "a2"), ("previous", "p1"), ("rolledback", "r1")):
            self.assertIn(str(dirs[key]), result.stdout, key)
        for key in (("backup", "a3"), ("previous", "p2")):
            self.assertNotIn(str(dirs[key]), result.stdout, key)
        result = self.invoke("--go", input_text="y\ny\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        for key in (("backup", "a1"), ("backup", "a2"), ("previous", "p1"), ("rolledback", "r1")):
            self.assertFalse(dirs[key].exists(), key)
        for key in (("backup", "a3"), ("previous", "p2")):
            self.assertTrue(dirs[key].exists(), key)


if __name__ == "__main__":
    unittest.main()
