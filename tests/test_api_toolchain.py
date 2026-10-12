# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""API 预检与实际子进程必须使用同一套工具；全部使用隔离样例。"""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import api
import uninstall_purge


class ApiToolchainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-api-toolchain-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "user tools"
        self.bin.mkdir()
        self.env = {"PATH": str(self.bin), "HOME": str(self.root), "TEST_KEEP": "yes"}

    def node(self, script="exit 0"):
        path = self.bin / "node"
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(0o755)
        return path

    def state(self):
        # machine_state 也读归档与冒烟目录；不 patch 就会扫到真实 $SN_GAMES
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(api, "RUNTIMES", self.root / "runtimes"), \
                patch.object(api, "steam_running", return_value=True), \
                patch.object(api, "debug_port_enabled", return_value=True), \
                patch.object(api, "SMOKE_DIR", self.root / "smoke"), \
                patch.object(uninstall_purge, "SN_GAMES", self.root / "games"):
            return api.machine_state([], {}, {"status": "empty", "error": None, "meta": {}})

    def test_environment_preserves_explicit_order_and_adds_finder_defaults_once(self):
        original = {**self.env, "PATH": str(self.bin) + ":/usr/bin:/opt/homebrew/bin"}
        result = api.child_environment(original)
        self.assertTrue(result["PATH"].startswith(original["PATH"] + ":"))
        self.assertEqual(result["PATH"].split(":"), list(dict.fromkeys(result["PATH"].split(":"))))
        self.assertEqual(result["TEST_KEEP"], "yes")
        self.assertEqual(api.child_environment(result), result)
        self.assertEqual(original["PATH"], str(self.bin) + ":/usr/bin:/opt/homebrew/bin")
        self.assertIn("/usr/local/bin", api.child_environment({})["PATH"].split(":"))

    def test_missing_node_cannot_claim_download_ready(self):
        with patch.object(api.shutil, "which", return_value=None):
            state = self.state()
        self.assertFalse(state["can_download"])
        self.assertIn("没有找到 Node.js", state["toolchain_error"])
        self.assertNotIn("打开 Steam", state["toolchain_error"])

    def test_unsupported_and_failed_node_remain_visible(self):
        for code, phrase in ((72, "WebSocket / fetch"), (1, "退出码 1")):
            with self.subTest(code=code):
                node = self.node("exit " + str(code))
                state = self.state()
                self.assertFalse(state["can_download"])
                self.assertIn(phrase, state["toolchain_error"])
                self.assertIn(str(node), state["toolchain_error"])

    def test_ready_node_keeps_explicit_path_and_does_not_create_runtime(self):
        # 本项检查路径与状态；新写脚本的首次执行曾接近探针截止，改用本机已有成功程序。
        # 实际 Node 的能力拒绝与真实超时/回收仍由后面的独立用例验证。
        node = self.bin / "node"
        node.symlink_to("/usr/bin/true")
        actual_run = subprocess.run
        returncodes = []

        def observe_run(*args, **kwargs):
            result = actual_run(*args, **kwargs)
            returncodes.append(result.returncode)
            return result

        with patch.object(api.subprocess, "run", wraps=observe_run) as run:
            state = self.state()
        self.assertTrue(state["can_download"], json.dumps({"state": state, "probe_returncodes": returncodes}, ensure_ascii=False))
        self.assertEqual(returncodes, [0])
        self.assertIsNone(state["toolchain_error"])
        self.assertEqual(run.call_args.args[0][0], str(node))
        self.assertEqual(run.call_args.kwargs["env"], api.child_environment(self.env))
        self.assertFalse((self.root / "runtimes").exists())

    def test_real_node_without_either_browser_api_is_rejected(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("本项使用已安装 Node 执行自有 JavaScript；其他缺工具用例仍运行")
        for capability in ("fetch", "WebSocket"):
            with self.subTest(capability=capability):
                code = "globalThis." + capability + "=undefined;eval(process.argv[1]);"
                self.node("exec " + shlex.quote(node) + " -e " + shlex.quote(code) + ' "$2"')
                state = self.state()
                self.assertFalse(state["can_download"])
                self.assertIn("WebSocket / fetch", state["toolchain_error"])

    def test_timeout_is_bounded_and_child_reaped(self):
        pidfile = self.root / "pid"
        self.node('echo $$ > "' + str(pidfile) + '"\nexec /bin/sleep 20')
        with patch.object(api, "NODE_PROBE_TIMEOUT", 1):
            state = self.state()
        self.assertFalse(state["can_download"])
        self.assertIn("超时", state["toolchain_error"])
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_node_execution_error_does_not_drop_snapshot(self):
        self.node()
        with patch.object(api.subprocess, "run", side_effect=OSError("fixture cannot exec")):
            state = self.state()
        self.assertFalse(state["can_download"])
        self.assertIn("无法启动", state["toolchain_error"])
        self.assertEqual(state["library_size"], 0)

    def test_install_and_stream_use_the_environment_checked_by_state(self):
        self.node()
        captured = []

        class Child:
            def __init__(self, argv, **kwargs):
                captured.append((argv, kwargs["env"]))
                self.stdout = iter([])

            def wait(self):
                return 0

        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(api, "steam_running", return_value=True), \
                patch.object(api.subprocess, "Popen", Child), \
                patch.object(api, "scan_installed", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_install("1"), 0)
            self.assertEqual(api._stream(["/fixture/cli", "survey"], "survey"), 0)
        expected = api.child_environment(self.env)
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(env == expected for _, env in captured))


if __name__ == "__main__":
    unittest.main()
