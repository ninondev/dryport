#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""黑盒帧时间与资源采样：对一个已经在跑的窗口，用 frame-probe 订阅窗口合成帧算「有效新帧」帧时间，
同时每秒采样进程 CPU%、RSS、GPU 占用与热状态。引擎无关，不改游戏，不改系统。

  tools/bench-game.py --window <id> | --owner <窗口 owner 子串> [--pid <pid>] [--seconds 60] [--out 目录]

输出 JSON：帧时间 p50/p95/p99（毫秒）、有效帧率、卡顿次数（帧间隔 > 50 ms）、最长帧间隔，
CPU%/RSS 峰值与中位数，GPU Device Utilization 采样，pmset 热状态，前提口径与取数时间。
数字只描述本机这次采样；没有低配机不写低配结论。frame-probe 需要 SN_RUNTIMES/bin/frame-probe（见 tools/frame-probe.swift）。
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_RUNTIMES

BIN = SN_RUNTIMES / "bin"


def find_window(owner):
    rows = json.loads(subprocess.run([str(BIN / "window-list"), owner], capture_output=True, text=True, timeout=20).stdout or "[]")
    return rows[0] if rows else None


def gpu_utilization():
    """ioreg 里 AGXAccelerator 的 PerformanceStatistics.Device Utilization %；读不到返回 None。"""
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def thermal():
    try:
        out = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
        return {"cpu_speed_limit": int(m.group(1)) if m else None, "raw": out.strip()[:300]}
    except Exception:
        return {"cpu_speed_limit": None, "raw": ""}


def ps_sample(pid):
    try:
        out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.split()
        return {"cpu": float(out[0]), "rss_bytes": int(out[1]) * 1024} if len(out) == 2 else None
    except Exception:
        return None


def frame_stats(lines):
    ts = [json.loads(l)["t"] for l in lines if '"status": "complete"' in l]
    if len(ts) < 2:
        return {"frames": len(ts), "note": "有效新帧不足 2 帧，算不出帧时间"}
    gaps = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
    gaps_sorted = sorted(gaps)
    def pct(p):
        return gaps_sorted[min(len(gaps_sorted) - 1, int(round(p / 100 * (len(gaps_sorted) - 1))))]
    fps = (len(ts) - 1) / (ts[-1] - ts[0])
    out = {"frames": len(ts), "seconds": round(ts[-1] - ts[0], 3), "effective_fps": round(fps, 2),
           "frame_ms_p50": round(pct(50), 2), "frame_ms_p95": round(pct(95), 2), "frame_ms_p99": round(pct(99), 2),
           "frame_ms_max": round(max(gaps), 2), "stutters_over_50ms": sum(1 for g in gaps if g > 50)}
    # 窗口服务器只在画面变化时交付帧：静态主菜单 60 秒只有几十帧，effective_fps 会远低于 1000/p50。
    # 这时数字反映的是「画面多久变一次」，不是游戏能跑多少帧（2026-09-13 静态主菜单实测 1.05 fps 对 p50 16.7 ms）。
    if pct(50) > 0 and fps < 0.5 * (1000 / pct(50)):
        out["note"] = "疑似静态画面：有效帧率远低于 1000/p50，这组数不代表游戏帧率；要让画面持续变化（进游戏动起来）再采"
        out["static_suspect"] = True
    return out


def bridge_summary(state):
    """桥两侧日志的计数汇总（DLL 每 60 秒与 detach、helper 每 60 秒与 client gone 各写一次）：取各自最后两行 summary，
    外加 not forwarded / no C++ vtable 的计数。没有日志就是没接桥，报 None 不报错。"""
    out = {"helper": None, "dll": None, "not_forwarded": 0, "no_vtable": 0}
    helper = state / "logs/helper.log"
    if helper.is_file():
        lines = helper.read_text(errors="replace").splitlines()
        out["helper"] = [l for l in lines if "summary (" in l][-2:] or None
        out["not_forwarded"] += sum("not forwarded" in l for l in lines)
    for dll in sorted(state.glob("prefix/drive_c/users/*/AppData/Local/Temp/steambridge.log")):
        lines = dll.read_text(errors="replace").splitlines()
        out["dll"] = [l for l in lines if "summary (" in l][-2:] or None
        out["not_forwarded"] += sum("not forwarded" in l for l in lines)
        out["no_vtable"] += sum("no C++ vtable for" in l for l in lines)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", type=int, help="窗口 id（window-list 给）")
    ap.add_argument("--owner", help="窗口 owner 名子串，取最大的那个窗口")
    ap.add_argument("--pid", type=int, help="要采样 CPU/RSS 的进程；不给就不采")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--hz", type=int, default=240, help="frame-probe 采样上限")
    ap.add_argument("--out", type=Path, help="把 frames.jsonl 与 bench.json 写到这个目录")
    ap.add_argument("--state", type=Path, help="游戏的 .wine-state/<appid> 目录：采完把桥两侧日志末尾的 summary 行并进报告")
    a = ap.parse_args(argv)
    probe = BIN / "frame-probe"
    if not probe.is_file():
        sys.exit(f"缺少 {probe}：swiftc -O tools/frame-probe.swift -o {probe}")
    window = None
    if a.window is None:
        if not a.owner:
            sys.exit("要 --window 或 --owner")
        window = find_window(a.owner)
        if not window:
            sys.exit(f"没有 owner 含「{a.owner}」的窗口")
        a.window = window["id"]
    report = {"protocol": 1, "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "window": window or {"id": a.window},
              "seconds_requested": a.seconds, "pid": a.pid,
              "note": "黑盒：帧 = 窗口服务器交付的 complete 帧；探针本身占少量 CPU/GPU；只描述本机本次采样"}
    samples = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            s = {"t": round(time.monotonic(), 3), "gpu_util": gpu_utilization()}
            if a.pid:
                s["ps"] = ps_sample(a.pid)
            samples.append(s)
            stop.wait(1.0)
    th = threading.Thread(target=sampler, daemon=True)
    report["thermal_before"] = thermal()
    th.start()
    t0 = time.monotonic()
    r = subprocess.run([str(probe), str(a.window), str(a.seconds), str(a.hz)], capture_output=True, text=True, timeout=a.seconds + 60)
    stop.set(); th.join(timeout=3)
    report["thermal_after"] = thermal()
    report["probe_rc"] = r.returncode
    report["probe_stderr"] = r.stderr.strip()[:500]
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    done = next((json.loads(l) for l in lines if '"done"' in l), {})
    report["probe"] = done
    report["frames"] = frame_stats([l for l in lines if '"done"' not in l])
    cpu = [s["ps"]["cpu"] for s in samples if s.get("ps")]
    rss = [s["ps"]["rss_bytes"] for s in samples if s.get("ps")]
    gpu = [s["gpu_util"] for s in samples if s.get("gpu_util") is not None]
    report["resources"] = {"samples": len(samples),
                           "cpu_pct_median": round(statistics.median(cpu), 1) if cpu else None,
                           "cpu_pct_max": max(cpu) if cpu else None,
                           "rss_bytes_max": max(rss) if rss else None,
                           "gpu_util_median": statistics.median(gpu) if gpu else None,
                           "gpu_util_max": max(gpu) if gpu else None}
    report["wall_seconds"] = round(time.monotonic() - t0, 3)
    if a.state:
        report["bridge"] = bridge_summary(a.state)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "frames.jsonl").write_text("\n".join(lines) + "\n")
        (a.out / "samples.jsonl").write_text("\n".join(json.dumps(s) for s in samples) + "\n")
        (a.out / "bench.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
