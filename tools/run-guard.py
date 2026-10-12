#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""受控起进程：把游戏（或任何试跑命令）作为自己的子进程组拉起，落 pid 记录，超时或本进程被中断时连子进程组一起收掉。

  tools/run-guard.py --name <标签> --max-seconds N [--cwd 目录] [--log 文件] -- <命令...>

为什么：2026-09-13 一个会话中途消失，它用 open -a 拉起的游戏成了 launchd 的孤儿跑了 25 分钟没人管（the runtime notes）。
open -a 把进程交给 launchd，会话没了它还在；这里的子进程在自己的进程组里，守护者收到 SIGTERM/SIGHUP/SIGINT 或超时就 killpg。
记录写在 $SN_GAMES/.running/<pid>.json：pid、进程组、标签、命令、开始时间、上限秒数、守护者 pid；结束即删。
`dryport running` 列出记录、指出失联（守护者已死或超时仍活着）的进程，`--kill-stale` 经确认才收。
退出码：子进程正常退出用它的退出码；被超时或信号收掉时 124；起不来 126。
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_GAMES

RUNNING = SN_GAMES / ".running"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--max-seconds", type=float, required=True)
    ap.add_argument("--cwd")
    ap.add_argument("--log", help="子进程 stdout/stderr 追加到这个文件；不给就继承")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.command[1:] if a.command and a.command[0] == "--" else a.command
    if not cmd:
        ap.error("-- 后面要有命令")
    RUNNING.mkdir(parents=True, exist_ok=True)
    out = open(a.log, "ab") if a.log else None
    try:
        proc = subprocess.Popen(cmd, cwd=a.cwd, stdout=out, stderr=subprocess.STDOUT if out else None, start_new_session=True)
    except OSError as error:
        print(f"起不来：{error}", file=sys.stderr); return 126
    record = RUNNING / f"{proc.pid}.json"
    started = time.time()
    record.write_text(json.dumps({"pid": proc.pid, "pgid": proc.pid, "name": a.name, "command": cmd, "cwd": a.cwd,
                                  "started": started, "max_seconds": a.max_seconds, "guard_pid": os.getpid()}, ensure_ascii=False) + "\n")
    reason = {"why": None}

    def stop(why):
        reason["why"] = why
        for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 3.0)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            except PermissionError as error:
                # macOS 实测（2026-09-13 entry-05）：外部先把游戏杀了之后，
                # killpg 这一组会返回 EPERM 而不是 ESRCH。原先只接 ProcessLookupError，
                # 异常一路逃出去，连 finally 里的 record.unlink() 都没跑到，
                # 于是 ~/Games/.running/ 里留下一条记录，`dryport running` 报假失联。
                # 信号发不出去就没有别的办法了，记下原因照常收尾。
                print(f"[run-guard] {a.name} 收不掉进程组 {proc.pid}：{error}", file=sys.stderr)
                return
            try:
                proc.wait(timeout=grace); return
            except subprocess.TimeoutExpired:
                continue

    def on_signal(signum, _frame):
        stop(f"signal {signum}")
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, on_signal)
    try:
        try:
            proc.wait(timeout=a.max_seconds)
        except subprocess.TimeoutExpired:
            stop(f"timeout {a.max_seconds}s")
    finally:
        # 收尾不能被任何异常打断：记录删不掉就会留下假的「失联」。
        if proc.poll() is None:
            try:
                stop(reason["why"] or "guard exiting")
            except Exception as error:
                print(f"[run-guard] {a.name} 收尾时出错：{error!r}", file=sys.stderr)
        try:
            record.unlink()
        except FileNotFoundError:
            pass
        if out:
            out.close()
    if reason["why"]:
        print(f"[run-guard] {a.name} 已收掉：{reason['why']}，跑了 {time.time() - started:.1f} 秒", file=sys.stderr)
        return 124
    return proc.returncode if proc.returncode is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
