# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""run-guard 起的子进程组在超时或守护者被中断时必须被收掉；`dryport running` 只读列出并只经确认收失联的。全部用临时 SN_GAMES。"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tools/run-guard.py"
CLI = ROOT / "bin/dryport"


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False


class RunGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-run-guard-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_GAMES": str(self.base / "games"),
                    "SN_RUNTIMES": str(self.base / "rt"), "STEAM": str(self.base / "steam"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.running = self.base / "games/.running"

    def guard(self, *args, **kw):
        return subprocess.Popen([sys.executable, "-B", str(GUARD), *args], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)

    def wait_record(self):
        for _ in range(100):
            files = list(self.running.glob("*.json")) if self.running.exists() else []
            if files:
                return json.loads(files[0].read_text())
            time.sleep(0.05)
        self.fail("run-guard 没有落记录")

    def test_record_is_removed_even_when_the_group_cannot_be_signalled(self):
        """2026-09-13 entry-05 冒烟抓到：外部先把游戏杀了之后，守护者的 os.killpg
        在 macOS 上返回 EPERM 而不是 ESRCH。当时 stop() 只接 ProcessLookupError，
        异常一路逃出 main，连 finally 里的 record.unlink() 都没跑到，
        `dryport running` 因此报一条假的「失联」。这里把 killpg 换成永远抛 EPERM 来复现。"""
        shim = self.base / "shim.py"
        shim.write_text(
            "import os, runpy, sys\n"
            "def _deny(pgid, sig):\n"
            "    raise PermissionError(1, 'Operation not permitted')\n"
            "os.killpg = _deny\n"
            f"sys.argv = [{str(GUARD)!r}, '--name', 'eperm', '--max-seconds', '1',\n"
            "             '--', sys.executable, '-c', 'import time; time.sleep(30)']\n"
            f"runpy.run_path({str(GUARD)!r}, run_name='__main__')\n")
        r = subprocess.run([sys.executable, "-B", str(shim)], capture_output=True, text=True,
                           env=self.env, timeout=60)
        left = list(self.running.glob("*.json")) if self.running.exists() else []
        self.assertEqual(left, [], f"信号发不出去时留下了记录 {left}；stderr={r.stderr[-300:]}")
        self.assertIn("收不掉进程组", r.stderr)

    def test_timeout_kills_the_child_group_and_removes_the_record(self):
        p = self.guard("--name", "sleepy", "--max-seconds", "1", "--", "/bin/sh", "-c", "sleep 30 & sleep 30")
        rec = self.wait_record()
        self.assertEqual(rec["name"], "sleepy"); self.assertTrue(alive(rec["pid"]))
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 124, err)
        self.assertIn("timeout", err)
        self.assertFalse(alive(rec["pid"]))
        self.assertEqual(list(self.running.glob("*.json")), [])
        # 进程组里的孙进程（后台 sleep）也要死
        time.sleep(0.3)
        ps = subprocess.run(["pgrep", "-g", str(rec["pgid"])], capture_output=True, text=True).stdout.strip()
        self.assertEqual(ps, "")

    def test_sigterm_to_the_guard_takes_the_child_down(self):
        p = self.guard("--name", "term", "--max-seconds", "60", "--", "sleep", "60")
        rec = self.wait_record()
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 124, err)
        self.assertFalse(alive(rec["pid"]))
        self.assertEqual(list(self.running.glob("*.json")), [])

    def test_normal_exit_returns_the_child_code(self):
        p = self.guard("--name", "quick", "--max-seconds", "10", "--", "/bin/sh", "-c", "exit 7")
        p.communicate(timeout=20)
        self.assertEqual(p.returncode, 7)
        self.assertEqual(list(self.running.glob("*.json")) if self.running.exists() else [], [])

    def test_reused_pid_and_dead_records_are_not_reported_as_stale(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import running
        self.assertEqual((running.etime_seconds("1-02:03:04"), running.etime_seconds("05:07"), running.etime_seconds("00:00:09"), running.etime_seconds("")), (93784, 307, 9, None))
        fresh = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(lambda: fresh.poll() is None and fresh.kill())
        self.assertTrue(running.alive(fresh.pid, started=time.time()))                 # 记录时间与进程年龄相符：是它
        self.assertFalse(running.alive(fresh.pid, started=time.time() - 3600))         # 记录说一小时前起的，进程却刚起：pid 被复用
        done = subprocess.Popen(["true"]); done.wait()
        self.running.mkdir(parents=True, exist_ok=True)
        (self.running / f"{done.pid}.json").write_text(json.dumps({
            "pid": done.pid, "pgid": done.pid, "name": "gone", "command": ["true"], "cwd": None,
            "started": time.time() - 30, "max_seconds": 30, "guard_pid": done.pid}))
        (self.running / f"{fresh.pid}.json").write_text(json.dumps({
            "pid": fresh.pid, "pgid": fresh.pid, "name": "reused", "command": ["sleep", "60"], "cwd": None,
            "started": time.time() - 3600, "max_seconds": 30, "guard_pid": 999999}))
        r = subprocess.run([str(CLI), "running"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[已退出（残留记录，已清理）] gone", r.stdout)
        self.assertIn("[已退出（残留记录，已清理）] reused", r.stdout)                  # 复用的 pid 不算它的进程，更不算失联
        self.assertNotIn("失联", r.stdout); self.assertNotIn("--kill-stale", r.stdout)
        self.assertEqual(list(self.running.glob("*.json")), [], "两条残留记录都该被清掉")
        self.assertTrue(alive(fresh.pid), "别人的进程不能被碰")

    def test_stray_wine_processes_can_use_injected_ps_lines(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import running

        ps_lines = r"""
42002 00:01 C:\windows\system32\services.exe
42003 00:02 /bin/zsh
42004 00:03 .../Game.exe
42005 00:04 C:\windows\system32\child.exe
42006 00:05 .../Game.exe
42007 00:06 steambridge-helper
"""
        rows = [{"pid": 42000, "pgid": 42000, "guard_pid": 42001}]
        with patch.object(running, "_members", return_value=[42000, 42005]):
            strays = running.stray_wine_processes(rows, ps_lines=ps_lines)

        self.assertEqual([item["pid"] for item in strays], [42002, 42004, 42006])
        self.assertNotIn(42003, [item["pid"] for item in strays])
        self.assertNotIn(42005, [item["pid"] for item in strays])
        self.assertNotIn(42007, [item["pid"] for item in strays])

    def test_running_reports_wine_processes_outside_the_records(self):
        fake = self.base / "entry-07.exe"; fake.symlink_to("/bin/sleep")   # 复制 /bin/sleep 会被 Apple Silicon 的签名校验杀掉，符号链接不会
        stray = subprocess.Popen([str(fake), "60"], start_new_session=True)
        self.addCleanup(lambda: stray.poll() is None and stray.kill())
        try:
            r = subprocess.run([str(CLI), "running"], env=self.env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("没有 run-guard 记录", r.stdout)
            self.assertIn("不在记录里的 Wine 进程", r.stdout); self.assertIn(f"pid {stray.pid}", r.stdout)
            self.assertTrue(alive(stray.pid), "只列不收")
        finally:
            stray.kill(); stray.wait(timeout=10)

    def test_running_lists_live_and_stale_and_kills_stale_only_with_confirmation(self):
        p = self.guard("--name", "live", "--max-seconds", "60", "--", "sleep", "60")
        live = self.wait_record()
        orphan = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(lambda: orphan.poll() is None and orphan.kill())
        self.running.mkdir(parents=True, exist_ok=True)
        (self.running / f"{orphan.pid}.json").write_text(json.dumps({   # 刚起的进程、记录时间相符、守护者没了：失联
            "pid": orphan.pid, "pgid": orphan.pid, "name": "orphan", "command": ["sleep", "60"], "cwd": None,
            "started": time.time() - 2, "max_seconds": 30, "guard_pid": 999999}))
        try:
            r = subprocess.run([str(CLI), "running"], env=self.env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[在跑] live", r.stdout); self.assertIn("[失联] orphan", r.stdout); self.assertIn("--kill-stale", r.stdout)
            r = subprocess.run([str(CLI), "running", "--kill-stale"], env=self.env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("预演", r.stdout)
            self.assertTrue(alive(orphan.pid)); self.assertTrue(alive(live["pid"]))
            r = subprocess.run([str(CLI), "running", "--kill-stale", "--yes"], env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("orphan", r.stdout); self.assertIn("已收掉", r.stdout)
            self.assertIsNotNone(orphan.wait(timeout=10))            # 父进程是本测试，poll/wait 才能确认它死了（否则是僵尸）
            self.assertTrue(alive(live["pid"]))                       # 在跑的、守护者还在的不碰
            self.assertEqual([f.name for f in self.running.glob("*.json")], [f"{live['pid']}.json"])
        finally:
            p.send_signal(signal.SIGTERM); p.communicate(timeout=20)


if __name__ == "__main__":
    unittest.main()
