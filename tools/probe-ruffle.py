#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Ruffle 跑 Flash/AIR 游戏主 SWF 的技术实验（不进产品、不改游戏原件）：run-guard 起 Ruffle → 等窗口 → 观察 N 秒 → 三张截图 + 纯色判据 → 收掉。

  tools/probe-ruffle.py <appid> <swf 路径> [--seconds 40] [--out 目录]
Ruffle 原厂二进制在 $SN_RUNTIMES/ruffle/Ruffle.app（GitHub Releases 取，SHA 记在证据里）。stderr 进 launcher.log（AIR API 报错在这里看）。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_RUNTIMES, SN_GAMES   # noqa: E402
import evidence                            # noqa: E402

RUFFLE = SN_RUNTIMES / "ruffle" / "Ruffle.app" / "Contents" / "MacOS" / "ruffle"
IMAGE_STATS = SN_RUNTIMES / "bin" / "image-stats"


def stats(png):
    try:
        return json.loads(subprocess.run([str(IMAGE_STATS), str(png)], capture_output=True, text=True, timeout=60).stdout)
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("appid"); ap.add_argument("swf")
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    swf = Path(a.swf)
    out = Path(a.out) if a.out else SN_GAMES / "dryport-deliveries" / time.strftime("%Y-%m-%d") / "ruffle" / f"{a.appid}-{swf.stem}"
    out.mkdir(parents=True, exist_ok=True)
    evidence.ensure_window_list()
    base = {w["id"] for w in evidence.windows()}
    label = f"ruffle-{a.appid}"
    cmd = [sys.executable, str(ROOT / "tools" / "run-guard.py"), "--name", label, "--max-seconds", str(a.seconds + 60),
           "--log", str(out / "launcher.log"), "--", str(RUFFLE), "--width", "1280", "--height", "720", "--graphics", "metal",
           "--filesystem-access-mode", "allow", "--base", swf.parent.as_uri() + "/", str(swf)]   # 游戏目录只读，允许它读自己的资源，不弹确认框
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(swf.parent))
    win = None
    while time.time() - t0 < 60:
        fresh = [w for w in evidence.new_windows(base) if "ruffle" in (w.get("owner") or "").lower() or w["pid"] in {proc.pid}]
        fresh = fresh or evidence.new_windows(base)
        if fresh:
            win = fresh[0]; break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    result = {"appid": a.appid, "swf": str(swf), "ruffle": str(RUFFLE), "window_at": round(time.time() - t0, 1) if win else None, "shots": []}
    if win:
        for wait, tag in ((5, "t05"), (20, "t20"), (a.seconds, "end")):
            while time.time() - t0 - result["window_at"] < wait and proc.poll() is None:
                time.sleep(0.5)
            dest = out / f"{tag}.png"
            if evidence.shoot(win["id"], dest):
                result["shots"].append({"tag": tag, "stats": stats(dest)})
    result["exit_code_before_stop"] = proc.poll()
    try:
        recs = evidence.guard_records(label, t0 - 1)
        if recs:
            import os, signal
            os.kill(int(recs[0]["pid"]), signal.SIGTERM)
        proc.wait(timeout=20)
    except Exception:
        proc.kill()
    evidence.reap_record(label, t0 - 1)
    blank = [s for s in result["shots"] if s["stats"] and (s["stats"]["colors"] < 30 or s["stats"]["dominant_fraction"] >= 0.9)]
    result["verdict"] = ("no_window" if not win else "blank" if result["shots"] and len(blank) == len(result["shots"]) else "picture")
    log = out / "launcher.log"
    if log.is_file():
        text = log.read_text(errors="replace")
        result["log_errors"] = [l for l in text.splitlines() if "ERROR" in l or "error" in l.lower()][:12]
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({k: result.get(k) for k in ("appid", "verdict", "window_at", "exit_code_before_stop", "log_errors")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
