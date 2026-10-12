#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""隔离合成库的启动基准；不读写用户游戏、注册表或 Steam 配置。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from paths import SN_HOME


def fixture(root, count=5251):
    games = root / "games"
    games.mkdir()
    kinds = ["unity", "mkxp", "wine", "unknown", "refuse", "godot", "nwjs", "renpy", "krkr", "maybe", "fna"]
    rows = [dict(appid=str(100000 + i), name=f"Synthetic Game 游戏 {i:05d}",
                 route=kinds[i % len(kinds)], engine="fixture", confidence="推测",
                 evidence="模拟清单证据，仅用于启动性能测量") for i in range(count)]
    for name, data in (("route-all.json", rows), ("final-routes.json", rows[:4852]),
                       ("owned-windows.json", [dict(appid=r["appid"], dl=67108864) for r in rows])):
        (games / name).write_text(json.dumps(data, ensure_ascii=False))
    return {**os.environ, "SN_HOME": str(SN_HOME), "SN_GAMES": str(games),
            "SN_RUNTIMES": str(root / "runtimes"), "STEAM": str(root / "steam"),
            "PYTHONPATH": str(SN_HOME / "lib"), "PYTHONDONTWRITEBYTECODE": "1"}


def measure(script, commands, env):
    started = time.perf_counter()
    size = 0
    for command in commands:
        result = subprocess.run([sys.executable, str(script), command], env=env,
                                capture_output=True, check=True)
        json.loads(result.stdout)
        size += len(result.stdout)
    return (time.perf_counter() - started) * 1000, size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, help="修改前的 api.py 副本")
    parser.add_argument("--samples", type=int, default=15)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("样本数至少为 3")
    candidates = [("current", SN_HOME / "lib/api.py", ["snapshot"])]
    if args.baseline:
        candidates.insert(0, ("baseline", args.baseline, ["library", "state"]))
    with tempfile.TemporaryDirectory(prefix="sn-api-bench-") as tmp:
        env = fixture(Path(tmp))
        values = {name: [] for name, _, _ in candidates}
        sizes = {}
        for i in range(args.samples + 3):
            # 交替顺序，降低文件缓存和其他后台任务的先后偏差。
            for name, script, commands in candidates[::1 if i % 2 else -1]:
                elapsed, sizes[name] = measure(script, commands, env)
                if i >= 3:
                    values[name].append(round(elapsed, 3))
        def sysctl(key):
            return subprocess.check_output(["sysctl", "-n", key], text=True).strip()
        print(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                          "machine": platform.machine(), "chip": sysctl("machdep.cpu.brand_string"),
                          "memory_bytes": int(sysctl("hw.memsize")), "python": sys.version.split()[0],
                          "rows": 5251, "confirmed": 4852, "samples": args.samples,
                          "conditions": "fresh Python process, warm filesystem, synthetic data, no game launch",
                          "source_sha256": {name: hashlib.sha256(script.read_bytes()).hexdigest()
                                            for name, script, _ in candidates},
                          "results": {name: {"median_ms": round(statistics.median(v), 3),
                                             "min_ms": min(v), "max_ms": max(v),
                                             "payload_bytes": sizes[name], "samples_ms": v}
                                      for name, v in values.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
