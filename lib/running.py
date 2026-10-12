# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""$SN_GAMES/.running 里的进程记录：谁在跑、哪个失联（守护者没了或超时仍活着）。只读；收进程由调用方经确认再做。"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from paths import SN_GAMES

RUNNING = SN_GAMES / ".running"


def etime_seconds(text):
    """ps 的 etime 字段（[[dd-]hh:]mm:ss）换算成秒；解析不了返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = text.split(":")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.insert(0, 0)
    return days * 86400 + nums[0] * 3600 + nums[1] * 60 + nums[2]


def alive(pid, started=None):
    """活着且不是僵尸，而且真是记录里那个进程。os.kill(pid, 0) 对僵尸也成功：被收掉但父进程还没 wait 的进程会被误判
    为活着，所以再问一次 ps；pid 会被系统回收复用，所以给了记录时间就再比一次进程已运行时长：守护者起子进程的同时落记录，
    两者相差超过 2 分钟的就是别的进程（2026-09-13：强杀后记录残留，pid 被复用就成了假失联）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        out = subprocess.run(["ps", "-o", "stat=,etime=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.split()
    except Exception:
        return True
    if not out or out[0].startswith("Z"):
        return False
    if started is not None and len(out) > 1:
        et = etime_seconds(out[1])
        if et is not None and abs(et - (time.time() - float(started))) > 120:
            return False
    return True


def records():
    rows = []
    if not RUNNING.is_dir():
        return rows
    for p in sorted(RUNNING.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        r["_file"] = str(p)
        r["alive"] = alive(int(r["pid"]), r.get("started"))
        r["guard_alive"] = alive(int(r["guard_pid"]), r.get("started")) if r.get("guard_pid") else False
        r["age_seconds"] = round(time.time() - float(r.get("started", 0)), 1)
        r["overtime"] = r["age_seconds"] > float(r.get("max_seconds", 0)) + 15
        # 失联：进程还活着但守护者没了，或超过上限 15 秒守护者还没收；进程已死的记录只是残留文件
        r["stale"] = r["alive"] and (not r["guard_alive"] or r["overtime"])
        r["gone"] = not r["alive"] and not r["guard_alive"]   # 守护者被 SIGKILL 时 finally 到不了，记录就留下了
        rows.append(r)
    return rows


def stray_wine_processes(rows=None, ps_lines=None):
    """记录之外的 Wine 进程：按 `ps -o comm=`（可执行文件本身）判，Wine 里的 Windows 进程 comm 就是 `C:\\windows\\system32\\services.exe`
    这种，游戏是 `...\\Game.exe`，再加 wineserver / wine-preloader；shell 的 comm 是 /bin/zsh，命令行参数里写什么都不算
    （2026-09-13：按整条命令行匹配时，`grep "C:\\windows"` 那条 shell 自己被列成了 Wine 进程）。
    排除任何 run-guard 记录覆盖的进程组。只列，不收；收进程由调用方经确认再做。"""
    known = set()
    for r in rows or []:
        known.add(int(r["pid"]))
        if r.get("guard_pid"):
            known.add(int(r["guard_pid"]))
        for pid in _members(int(r.get("pgid") or r["pid"])):
            known.add(pid)
    if ps_lines is None:
        try:
            ps_lines = subprocess.run(["ps", "-axo", "pid=,etime=,comm="], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
    strays = []
    for line in ps_lines.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, etime, comm = parts
        low = comm.lower()
        looks_wine = low.startswith("c:\\") or low.endswith(".exe") or low.endswith("/wineserver") or low.endswith("wine-preloader") or low.endswith("wine64-preloader")
        if not looks_wine or low.endswith("steambridge-helper"):
            continue
        if int(pid) in known or int(pid) == os.getpid():
            continue
        try:
            command = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            command = comm
        strays.append({"pid": int(pid), "etime": etime, "command": (command or comm)[:120]})
    return strays


def prune_gone(rows):
    """删掉进程与守护者都不在了的残留记录文件（本工具自己写的小 JSON），返回删了几条。"""
    n = 0
    for r in rows:
        if r.get("gone"):
            try:
                Path(r["_file"]).unlink()
                n += 1
            except OSError:
                pass
    return n


def _members(pgid):
    out = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out if x.isdigit()]


def _signal_group(pgid, leader, sig):
    """先整组 killpg；不是父进程时 macOS 对别的会话的进程组可能报 EPERM，就按 pgrep 列出的成员逐个 kill。"""
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        pass
    sent = False
    for pid in set(_members(pgid)) | {leader}:
        try:
            os.kill(pid, sig); sent = True
        except (ProcessLookupError, PermissionError):
            continue
    return sent


def kill_record(r):
    """收掉一条失联记录的进程组，先 TERM 后 KILL；返回 (成功, 说明)。"""
    pid = int(r["pid"]); pgid = int(r.get("pgid") or pid)
    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 3.0)):
        if not _signal_group(pgid, pid, sig) and not alive(pid):
            break
        deadline = time.time() + grace
        while time.time() < deadline:
            if not alive(pid):
                break
            time.sleep(0.1)
        if not alive(pid):
            break
    ok = not alive(pid)
    if ok:
        try:
            Path(r["_file"]).unlink()
        except FileNotFoundError:
            pass
    return ok, ("已收掉" if ok else "还活着")
