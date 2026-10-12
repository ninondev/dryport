# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""bench-game 的 steambridge 日志汇总离线测试，不接触真实游戏或运行时目录。"""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench_game", ROOT / "tools/bench-game.py")
bench_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_game)


class BridgeSummaryTests(unittest.TestCase):
    def test_reads_last_two_summaries_and_counts_both_logs(self):
        with tempfile.TemporaryDirectory(prefix="sn-bench-summary-") as tmp:
            state = Path(tmp)
            helper = state / "logs/helper.log"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "helper boot\n"
                "helper summary (first) forwarded=1\n"
                "helper not forwarded: SteamUser\n"
                "helper summary (second) forwarded=2\n",
                encoding="utf8",
            )
            dll = state / "prefix/drive_c/users/fixture/AppData/Local/Temp/steambridge.log"
            dll.parent.mkdir(parents=True)
            dll.write_text(
                "dll boot\n"
                "dll summary (first) calls=3\n"
                "dll not forwarded: SteamUGC\n"
                "dll no C++ vtable for SteamNetworking006\n"
                "dll summary (second) calls=4\n",
                encoding="utf8",
            )

            result = bench_game.bridge_summary(state)

            self.assertEqual(result["helper"], [
                "helper summary (first) forwarded=1",
                "helper summary (second) forwarded=2",
            ])
            self.assertEqual(result["dll"], [
                "dll summary (first) calls=3",
                "dll summary (second) calls=4",
            ])
            self.assertEqual(result["not_forwarded"], 2)
            self.assertEqual(result["no_vtable"], 1)

    def test_missing_logs_return_none_and_zero_counts(self):
        with tempfile.TemporaryDirectory(prefix="sn-bench-summary-empty-") as tmp:
            self.assertEqual(bench_game.bridge_summary(Path(tmp)), {
                "helper": None,
                "dll": None,
                "not_forwarded": 0,
                "no_vtable": 0,
            })


if __name__ == "__main__":
    unittest.main()
