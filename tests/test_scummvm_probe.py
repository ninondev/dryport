# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自有检测输出/进程验证，不运行 ScummVM、Steam 或真实游戏。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scummvm_probe", ROOT / "tools/probe-scummvm.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

HEADER = ("GameID                         Description                                                Full Path\n"
          "------------------------------ ---------------------------------------------------------- ---------------------------------------------------------\n")


def row(path, game_id="agi:lsl1", description="Owned description (DOS/English)"):
    # 固定版 C printf 的 %-30s / %-58s 宽度是 UTF-8 字节，不截断超长名称。
    padded = description + " " * max(0, 58 - len(description.encode("utf-8")))
    return game_id.ljust(30) + " " + padded + " " + str(path) + "\n"


class DetectionOutputTests(unittest.TestCase):
    def test_one_exact_game_and_path_accepts_long_description_and_spaces(self):
        for description in ("Owned game", "A very long owned game description " * 4, "自有样例 (DOS/English)"):
            path = Path("/private/tmp/自有 Game's directory")
            with self.subTest(description=description):
                result = PROBE.parse_detection(HEADER + row(path, description=description), "", path)
                self.assertEqual(result["game_id"], "agi:lsl1")
                self.assertEqual(result["path"], str(path))
                self.assertEqual(result["description"], description.rstrip())

    def test_empty_missing_duplicate_wrong_id_or_wrong_path_are_rejected(self):
        path = Path("/private/tmp/owned")
        cases = ["", "WARNING: ScummVM could not find any game in " + str(path), HEADER,
                 HEADER + row(path) + row(path), HEADER + row(path, "sci:lsl1"),
                 HEADER + row(str(path) + "-other"), HEADER + row("/prefix " + str(path)),
                 HEADER + row(path) + "unexpected trailing output\n", row(path)]
        for output in cases:
            with self.subTest(output=output), self.assertRaises(ValueError):
                PROBE.parse_detection(output, "", path)

    def test_unknown_report_in_either_stream_is_not_accepted_with_valid_match(self):
        path = Path("/private/tmp/owned")
        unknown = "The game in 'owned' seems to be an\nunknown game variant.\nMatched game IDs for the agi engine:\n"
        for stdout, stderr in ((unknown + HEADER + row(path), ""), (HEADER + row(path), unknown)):
            with self.subTest(stderr=bool(stderr)), self.assertRaisesRegex(ValueError, "未知游戏变体"):
                PROBE.parse_detection(stdout, stderr, path)
        # 单独的 unknown 字样可以来自路径或说明，不伪造未知变体报告。
        unknown_path = Path("/private/tmp/unknown folder")
        self.assertTrue(PROBE.parse_detection(HEADER + row(unknown_path), "", unknown_path)["unique"])

    def test_ambiguous_description_controls_warning_and_oversize_are_rejected(self):
        path = Path("/private/tmp/owned")
        cases = [(HEADER + row(path, description="Owned / ambiguous"), ""),
                 (HEADER + row(path), "ERROR: owned failure"),
                 ("\x1b[31m" + HEADER + row(path), ""),
                 ("x" * (PROBE.OUTPUT_LIMIT + 1), ""),
                 ((HEADER + row(path)).ljust(PROBE.OUTPUT_LIMIT), "")]
        for stdout, stderr in cases:
            with self.subTest(stderr=stderr), self.assertRaises(ValueError):
                PROBE.parse_detection(stdout, stderr, path)
        with self.assertRaises(ValueError):
            PROBE.parse_detection(HEADER, "", Path("/tmp/owned\nother"))


class ProbeBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-scummvm-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.runtime = self.base / "OwnedRuntime.app"
        (self.runtime / "Contents/MacOS").mkdir(parents=True)
        self.binary = self.runtime / "Contents/MacOS/scummvm"
        self.binary.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000c) + b"\0" * 24)
        self.binary.chmod(0o755)
        with (self.runtime / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleShortVersionString": PROBE.VERSION}, stream)
        self.game = self.base / "Original owned data"
        self.game.mkdir()
        (self.game / "VOL.0").write_bytes(b"owned payload")
        self.archive = self.base / "owned-not-official.dmg"
        self.archive.write_bytes(b"not an official runtime")

    def test_prepare_only_constructs_detect_and_isolates_config_and_environment(self):
        state = self.base / "state"
        state.mkdir()
        with patch.dict(os.environ, {"PYTHONPATH": "/untrusted", "DYLD_INSERT_LIBRARIES": "/untrusted"}):
            args, environment = PROBE.prepare_state(state, self.binary, self.game)
        self.assertEqual(args, ["/usr/bin/arch", "-arm64", str(self.binary),
                                "--config=" + str(state / "scummvm.ini"),
                                "--path=" + str(self.game), "--detect"])
        self.assertNotIn("--auto-detect", args)
        self.assertNotIn("--add", args)
        self.assertEqual(environment["SDL_VIDEODRIVER"], "dummy")
        self.assertEqual(environment["SDL_AUDIODRIVER"], "dummy")
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", environment)
        config = (state / "scummvm.ini").read_text()
        self.assertIn("fullscreen=false", config)
        self.assertIn("savepath=" + str(state / "saves"), config)
        self.assertIn("logfile=" + str(state / "logs/scummvm.log"), config)
        self.assertFalse((state / "logs/scummvm.log").exists())

    def test_bad_archive_never_mounts_or_executes_runtime(self):
        output = self.base / "bad-archive"
        with patch.object(PROBE, "run_owned", side_effect=AssertionError("must not execute")):
            result = PROBE.probe(self.runtime, self.archive, self.game, output)
        self.assertFalse(result["ok"])
        self.assertIn("SHA-256", result["error"])
        self.assertNotIn("detection_process", result)
        self.assertTrue(all(item["unchanged"] for item in result["integrity"].values()))

    def mock_probe(self, output, *, stdout=None, returncode=0, timed_out=False, mutate=False, limit=False):
        def run(args, **kwargs):
            result = {"args": args, "pid": 1000000000, "returncode": 0, "timed_out": False,
                      "stdout": '{"write_denied":true}', "stderr": ""}
            if kwargs["name"] == "detect":
                result.update(stdout=HEADER + row(self.game) if stdout is None else stdout,
                              returncode=returncode, timed_out=timed_out, output_limit_reached=limit)
                if mutate:
                    (self.game / "VOL.0").write_bytes(b"unexpected changed owned fixture")
            return result
        with patch.object(PROBE, "verify_factory", return_value={"bundle_matches_archive": True}), \
                patch.object(PROBE, "run_owned", side_effect=run), \
                patch.object(PROBE.os, "kill", side_effect=ProcessLookupError):
            return PROBE.probe(self.runtime, self.archive, self.game, output)

    def test_mock_detect_success_retains_all_original_input_hashes(self):
        result = self.mock_probe(self.base / "mock-success")
        self.assertTrue(result["ok"])
        self.assertFalse(result["game_started"])
        self.assertTrue(result["detection_process"]["pid_reaped"])
        self.assertTrue(all(item["unchanged"] for item in result["integrity"].values()))
        self.assertEqual(result["detected"]["path"], str(self.game))

    def test_exit_zero_empty_output_nonzero_and_timeout_never_succeed(self):
        for index, changes in enumerate(({"stdout": ""}, {"returncode": 2}, {"timed_out": True}, {"limit": True})):
            with self.subTest(changes=changes):
                result = self.mock_probe(self.base / ("failure-" + str(index)), **changes)
                self.assertFalse(result["ok"])

    def test_final_source_change_overrides_otherwise_successful_detection(self):
        result = self.mock_probe(self.base / "mutated", mutate=True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["integrity"]["game"]["unchanged"])
        self.assertTrue(result["integrity"]["runtime"]["unchanged"])

    def test_existing_output_and_dangling_link_are_preserved(self):
        existing = self.base / "foreign"
        existing.mkdir()
        (existing / "keep").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            PROBE.reserve_output(existing, [self.game])
        link = self.base / "dangling"
        target = self.base / "missing-target"
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            PROBE.reserve_output(link, [self.game])
        self.assertFalse(target.exists())
        self.assertEqual((existing / "keep").read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            PROBE.reserve_output(self.game / "evidence", [self.game])

    def test_macho_requires_arm64_and_checks_fat_file_bounds(self):
        self.assertEqual(PROBE.architectures(self.binary), ["arm64"])
        fat = self.base / "fat"
        fat.write_bytes(struct.pack(">II", 0xcafebabe, 1)
                        + struct.pack(">IIIII", 0x0100000c, 0, 4096, 4096, 12))
        with self.assertRaises(ValueError):
            PROBE.architectures(fat)

    def factory_fixture(self, name, *, mismatch=False, detach_failure=False):
        state = self.base / name
        state.mkdir()
        _, environment = PROBE.prepare_state(state, self.binary, self.game)
        official = state / "mount/Official.app"
        shutil.copytree(self.runtime, official)
        if mismatch:
            (official / "Contents/extra").write_bytes(b"original archive extra fixture")
        mounted = [False]
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if "attach" in args:
                mounted[0] = True
                output = plistlib.dumps({"system-entities": [{"mount-point": str(state / "mount")}]}).decode()
                return {"returncode": 0, "timed_out": False, "stdout": output}
            self.assertEqual(args, ["/usr/bin/hdiutil", "detach", str(state / "mount")])
            mounted[0] = detach_failure
            return {"returncode": int(detach_failure), "timed_out": False, "stdout": ""}
        # 自有归档替身只测试控制流；不冒充固定官方字节或真实挂载验证。
        with patch.object(PROBE, "ARCHIVE_BYTES", self.archive.stat().st_size), \
                patch.object(PROBE, "ARCHIVE_SHA256", PROBE.sha256(self.archive)), \
                patch.object(PROBE, "run_owned", side_effect=run), \
                patch.object(PROBE.os.path, "ismount", side_effect=lambda path: mounted[0]):
            try:
                result = PROBE.verify_factory(self.runtime, self.archive, state, environment, 1)
            except ValueError as error:
                result = {"error": str(error)}
        return result, calls, mounted[0]

    def test_factory_comparison_uses_readonly_mount_and_detaches_on_mismatch(self):
        good, calls, mounted = self.factory_fixture("factory-good")
        self.assertTrue(good["bundle_matches_archive"])
        self.assertIn("-readonly", calls[0])
        self.assertIn("-nobrowse", calls[0])
        self.assertIn("-noautoopen", calls[0])
        self.assertFalse(mounted)
        bad, calls, mounted = self.factory_fixture("factory-different", mismatch=True)
        self.assertIn("与固定官方归档不一致", bad["error"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1][1], "detach")
        self.assertFalse(mounted)

    def test_failed_detach_does_not_confirm_factory(self):
        result, calls, mounted = self.factory_fixture("factory-detach-failed", detach_failure=True)
        self.assertIn("未卸载", result["error"])
        self.assertNotIn("bundle_matches_archive", result)
        self.assertEqual(len(calls), 2)
        self.assertTrue(mounted)

    def test_owned_supervisor_enforces_timeout_and_output_limit(self):
        logs = self.base / "logs"
        logs.mkdir()
        environment = {"PATH": "/usr/bin:/bin"}
        timeout = PROBE.run_owned([sys.executable, "-I", "-S", "-c", "import time; time.sleep(2)"],
                                  env=environment, cwd=self.base, logs=logs, name="timeout", timeout=0.05)
        self.assertTrue(timeout["timed_out"])
        with self.assertRaises(ProcessLookupError):
            os.kill(timeout["pid"], 0)
        large = PROBE.run_owned([sys.executable, "-I", "-S", "-c", "import os; os.write(1,b'x'*2097152)"],
                                env=environment, cwd=self.base, logs=logs, name="limit", timeout=2)
        self.assertLessEqual(Path(large["stdout_file"]).stat().st_size, PROBE.OUTPUT_LIMIT)
        self.assertTrue(large["output_limit_reached"])
        self.assertTrue(PROBE.run_failed(large))

    def test_start_receipt_failure_still_kills_and_reaps_owned_process(self):
        logs = self.base / "callback-logs"
        logs.mkdir()
        started = []
        def fail_receipt(pid):
            started.append(pid)
            raise OSError("owned receipt unavailable")
        with self.assertRaisesRegex(OSError, "owned receipt unavailable"):
            PROBE.run_owned([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                            env={"PATH": "/usr/bin:/bin"}, cwd=self.base, logs=logs,
                            name="callback", timeout=1, on_start=fail_receipt)
        self.assertEqual(len(started), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(started[0], 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(started[0], 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(started[0], os.WNOHANG)

    def test_cli_failure_status_is_not_hidden(self):
        args = ["--runtime-app", str(self.runtime), "--archive", str(self.archive),
                "--game", str(self.game), "--output", str(self.base / "cli")]
        with patch.object(PROBE, "probe", return_value={"output": str(self.base / "cli"), "ok": False}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(PROBE.main(args), 1)


if __name__ == "__main__":
    unittest.main()
