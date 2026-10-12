#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""起一个已装的 .app，等窗口出现，再用 tools/bench-game.py 采 N 秒（帧时间分位数、CPU、RSS、GPU、热），采完收掉。台阶 C 的固定场景数字用它。

  tools/bench-app.py <.app> --seconds 600 [--settle 20] [--label 名] [--key return]
固定场景默认是标题/主菜单（settle 秒后开始采）；--key 在采样前投一次按住的键（进第一个画面）。
输出 ~/Games/dryport-deliveries/<日期>/bench/<label>/bench.json，口径与 bench-game 相同：黑盒、本机本次、探针自身占少量 CPU。
"""
import argparse
import json
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_RUNTIMES, SN_GAMES   # noqa: E402
import evidence                            # noqa: E402

KEYSEND = SN_RUNTIMES / "bin" / "keysend"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("app"); ap.add_argument("--seconds", type=float, default=600); ap.add_argument("--settle", type=float, default=20)
    ap.add_argument("--label"); ap.add_argument("--key"); ap.add_argument("--window-wait", type=float, default=180)
    a = ap.parse_args(argv)
    app = Path(a.app).resolve()
    with open(app / "Contents/Info.plist", "rb") as f:
        exe = plistlib.load(f)["CFBundleExecutable"]
    label = a.label or app.stem
    out = SN_GAMES / "dryport-deliveries" / time.strftime("%Y-%m-%d") / "bench" / label.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    wl = app / "Contents/Resources/wine-launch.json"
    state = json.loads(wl.read_text()).get("state") if wl.is_file() else None
    evidence.ensure_window_list()
    base = {w["id"] for w in evidence.windows()}
    tag = f"bench-{label[:24]}"
    cmd = [sys.executable, str(ROOT / "tools/run-guard.py"), "--name", tag, "--max-seconds", str(a.window_wait + a.settle + a.seconds + 120),
           "--log", str(out / "launcher.log"), "--", str(app / "Contents/MacOS" / exe)]
    t0 = time.time()
    proc = subprocess.Popen(cmd, env={**os.environ, "SN_WINE_NO_NOTIFY": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    win = None
    while time.time() - t0 < a.window_wait and proc.poll() is None:
        fresh = [w for w in evidence.new_windows(base) if w["bounds"]["Width"] >= 500 and w["bounds"]["Height"] >= 360]
        if fresh:
            win = fresh[0]; break
        time.sleep(0.5)
    report = {"app": str(app), "label": label, "window_at": round(time.time() - t0, 1) if win else None, "window": win}
    if win:
        time.sleep(a.settle)
        if a.key and KEYSEND.is_file():
            subprocess.run([str(KEYSEND), "--pid", str(win["pid"]), "--key", a.key, "--hold-ms", "80"], capture_output=True, timeout=15)
            time.sleep(3)
        evidence.shoot(win["id"], out / "before.png")
        bench = [sys.executable, str(ROOT / "tools/bench-game.py"), "--window", str(win["id"]), "--pid", str(win["pid"]),
                 "--seconds", str(a.seconds), "--out", str(out)]
        if state:
            bench += ["--state", state]
        r = subprocess.run(bench, capture_output=True, text=True, timeout=a.seconds + 120)
        report["bench_rc"] = r.returncode
        report["bench_stderr"] = r.stderr[-500:]
        evidence.shoot(win["id"], out / "after.png")
    recs = evidence.guard_records(tag, t0 - 1)
    if recs:
        try:
            os.kill(int(recs[0]["pid"]), signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        proc.kill()
    if state and Path(state, "prefix").is_dir():
        ws = Path(json.loads(wl.read_text()).get("wine", SN_RUNTIMES / "wine-cx")) / "bin/wineserver"
        for flag in ("-k", "-w"):
            subprocess.run([str(ws), flag], env={**os.environ, "WINEPREFIX": str(Path(state, "prefix"))}, capture_output=True, timeout=60)
    evidence.reap_record(tag, t0 - 1)
    report["stray_wine"] = evidence.stray_wine()[:3]
    (out / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    b = out / "bench.json"
    if b.is_file():
        d = json.loads(b.read_text())
        fr = d.get("frames", {}); res = d.get("resources", {})
        print(json.dumps({"label": label, "window_at": report["window_at"], "seconds": d.get("wall_seconds"), "frames": fr, "resources": res,
                          "thermal_after": d.get("thermal_after")}, ensure_ascii=False))
    else:
        print(json.dumps({"label": label, "window_at": report["window_at"], "error": report.get("bench_stderr") or "no window"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
