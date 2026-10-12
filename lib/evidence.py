# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""真机取证的公共件：列窗口、认新窗口、按窗口 id 截图、读 run-guard 记录、扫崩溃报告、看有没有人在用机器。

为什么单独成模块：2026-09-13 的 save-matrix / wine-trial / bench-game 各抄了一份「列窗口 + 截图」，
路径还写死了 ~/Games；自动冒烟（tools/smoke-run.py）再抄第四份就没人维护得动了。这里走 lib/paths.py。

判据上的三条规矩（the runtime notes「看不到窗口不等于没有窗口」）：
  - 枚举窗口用 CGWindowList（tools/window-list.swift），osascript 看不到别的应用的窗口；
  - 截图按窗口 id（screencapture -l），全屏截图会被前景窗口盖住、还会把用户别的窗口带进证据；
  - 判活用启动时记录的 pid / 进程组，不用进程名（启动壳 exec 之前 pgrep 会误判）。
只读；收进程由调用方经 run-guard 记录再做。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SN_HOME, SN_RUNTIMES, SN_GAMES   # noqa: E402

WINDOW_LIST = SN_RUNTIMES / "bin" / "window-list"
WINDOW_LIST_SRC = SN_HOME / "tools" / "window-list.swift"
RUNNING = SN_GAMES / ".running"
DIAG = Path.home() / "Library" / "Logs" / "DiagnosticReports"

# 自家的工具与桌面常驻件：它们的窗口永远不是被测游戏的窗口
OWN_OWNERS = {"claude", "terminal", "iterm2", "finder", "程序坞", "dock", "steam helper", "steam", "系统设置",
              "system settings", "notification center", "通知中心", "control center", "控制中心", "spotlight",
              "screencapture", "window server", "windowserver", "brave browser", "google chrome", "safari", "code",
              "cursor", "chatgpt", "codex", "frame-probe", "frame-probe-selftest", "keysend-selftest"}


def ensure_window_list():
    """源码比产物新就重编；系统 python3 没有 Quartz 模块，所以要这个 Swift 小工具。"""
    if WINDOW_LIST.is_file() and WINDOW_LIST_SRC.is_file() and WINDOW_LIST.stat().st_mtime >= WINDOW_LIST_SRC.stat().st_mtime:
        return True
    if not WINDOW_LIST_SRC.is_file():
        return WINDOW_LIST.is_file()
    WINDOW_LIST.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["swiftc", "-O", str(WINDOW_LIST_SRC), "-o", str(WINDOW_LIST)], capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and WINDOW_LIST.is_file()


def windows(needle=""):
    """[{id, pid, owner, title, layer, bounds{X,Y,Width,Height}}]，大窗口在前；工具缺失或出错返回 []。"""
    if not WINDOW_LIST.is_file():
        return []
    try:
        out = subprocess.run([str(WINDOW_LIST)] + ([needle] if needle else []), capture_output=True, text=True, timeout=15)
        return json.loads(out.stdout or "[]")
    except Exception:
        return []


def screen_size():
    try:
        out = subprocess.run([str(WINDOW_LIST), "--screen"], capture_output=True, text=True, timeout=15)
        d = json.loads(out.stdout or "{}")
        return int(d.get("width", 0)), int(d.get("height", 0))
    except Exception:
        return 0, 0


def new_windows(baseline_ids, min_w=200, min_h=150, exclude_owners=OWN_OWNERS):
    """启动前记下所有窗口 id，之后凡是新出现、够大、owner 不是自家工具的窗口都算候选。
    不按 owner 名猜游戏：Wine 下 owner 是 exe 名或 wine，原生路线才是游戏名。"""
    got = []
    for w in windows():
        if w["id"] in baseline_ids:
            continue
        b = w.get("bounds", {})
        if b.get("Width", 0) < min_w or b.get("Height", 0) < min_h:
            continue
        if (w.get("owner") or "").strip().lower() in exclude_owners:
            continue
        got.append(w)
    return got


def is_fullscreen(w, screen=None):
    sw, sh = screen or screen_size()
    b = w.get("bounds", {})
    return sw > 0 and b.get("Width", 0) >= sw and b.get("Height", 0) >= sh - 40


def shoot(window_id, dest):
    """按窗口 id 截图（-x 静音 -o 不带阴影）。返回文件存在与否。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", "-o", "-l", str(window_id), str(dest)], capture_output=True, timeout=30)
    return dest.is_file() and dest.stat().st_size > 0


def guard_records(label, since=0.0):
    """run-guard 落在 $SN_GAMES/.running 的记录里，标签相符且晚于 since 的（陈旧记录里的 pid 可能已被系统回收）。"""
    rows = []
    if not RUNNING.is_dir():
        return rows
    for f in sorted(RUNNING.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if rec.get("name") != label or float(rec.get("started", 0)) < since:
            continue
        rec["_file"] = str(f)
        rows.append(rec)
    return rows


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return True
    return bool(out) and not out.startswith("Z")


def reap_record(label, since=0.0):
    """只清自己这轮制造、且进程与守护者都已死的记录；别人的一律不动。返回说明文字列表。"""
    notes = []
    for rec in guard_records(label, since):
        alive = [rec.get(k) for k in ("pid", "guard_pid") if rec.get(k) and pid_alive(rec[k])]
        if alive:
            notes.append(f"记录 {Path(rec['_file']).name} 里还有活进程 {alive}，不删")
            continue
        try:
            Path(rec["_file"]).unlink()
            notes.append(f"清掉了残留记录 {Path(rec['_file']).name}（进程已全死）")
        except OSError as error:
            notes.append(f"删不掉记录 {rec['_file']}: {error}")
    return notes


def crash_reports(since, needles=()):
    """~/Library/Logs/DiagnosticReports 里 since 之后新出现、且首行 JSON 的 procPath/app_name 含任一 needle 的 .ips。
    ReportCrash 是异步的，调用方停掉进程后等几秒再扫。"""
    hits = []
    if not DIAG.is_dir():
        return hits
    for f in DIAG.glob("*.ips"):
        try:
            if f.stat().st_mtime < since:
                continue
            head = f.read_text(errors="replace").split("\n", 1)[0]
            meta = json.loads(head)
        except Exception:
            continue
        text = " ".join(str(meta.get(k, "")) for k in ("procPath", "app_name", "name", "procName")).lower()
        if not needles or any(n.lower() in text for n in needles):
            hits.append({"file": str(f), "proc": meta.get("app_name") or meta.get("procName") or meta.get("name")})
    return hits


def hid_idle_seconds():
    """有人在用机器吗：IOHIDSystem 的 HIDIdleTime（纳秒）。读不到返回 None（当作没人在用不合适，调用方自己决定）。"""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.split("=")[-1].strip()) / 1e9
            except ValueError:
                return None
    return None


def winedbg_running():
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    return [l.strip() for l in out.splitlines() if l.strip().lower().endswith("winedbg.exe")]


def stray_wine():
    """记录之外的 Wine 进程（lib/running.py 的判据）。"""
    import running
    return running.stray_wine_processes(running.records())


def file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def now_stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")
