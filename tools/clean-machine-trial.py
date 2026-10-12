#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""隔离环境检查：空 HOME、空 SN_RUNTIMES、空 SN_GAMES、PATH 不带 brew、C locale，
从源码快照走到 `dryport install <appid>`，每步记退出码、秒数与日志；结束把快捷方式归档掉，不在 owner 的 Steam 库里留东西。

  tools/clean-machine-trial.py --bundle <预编译运行时包目录> [--appid 2118420] [--out 证据目录] [--keep] [--snapshot 源码.tar.gz]

Steam 仍是本机已登录的那个（STEAM 显式指向真实路径，因为 HOME 是假的）。网络会用到：Node 固定版、反作弊库、Ren'Py 原厂包、Steam 客户端下载。
没有 --snapshot 就用 `git archive HEAD`（只含 git 跟踪的文件，与发布快照同口径）。--keep 不删试验目录（约 2 GB）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_HOME, STEAM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--appid", default="2118420")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--snapshot", type=Path)
    ap.add_argument("--with", dest="with_flags", default="--with-renpy", help="install.sh --runtime-only 的路线开关（默认 --with-renpy）")
    a = ap.parse_args(argv)
    trial = Path(tempfile.mkdtemp(prefix="sn-clean-trial-", dir=Path.home() / "Games/runtimes"))
    out = a.out or trial / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    src = trial / "src"; src.mkdir()
    if a.snapshot:
        with tarfile.open(a.snapshot) as tar:
            tar.extractall(src)
        roots = [p for p in src.iterdir() if p.is_dir()]
        src = roots[0] if len(roots) == 1 and not (src / "bin").exists() else src
    else:
        archive = subprocess.run(["git", "-C", str(SN_HOME), "archive", "HEAD"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
    for sub in ("home", "runtimes", "games"):
        (trial / sub).mkdir()
    env = {"HOME": str(trial / "home"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "SN_HOME": str(src),
           "SN_RUNTIMES": str(trial / "runtimes"), "SN_GAMES": str(trial / "games"), "STEAM": str(STEAM),
           "TMPDIR": str(trial / "home" / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"}
    (trial / "home/tmp").mkdir()
    report = {"trial": str(trial), "source": str(a.snapshot) if a.snapshot else "git archive HEAD", "appid": a.appid,
              "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "env": {k: v for k, v in env.items() if k != "STEAM"}, "steps": []}

    def step(name, cmd, expect=0, timeout=3600):
        t0 = time.monotonic()
        r = subprocess.run(cmd, env=env, cwd=src, capture_output=True, text=True, timeout=timeout)
        log = out / f"{len(report['steps']) + 1:02d}-{name}.log"
        log.write_text(r.stdout + ("\n--- stderr ---\n" + r.stderr if r.stderr else ""))
        gaps = [l for l in (r.stdout + r.stderr).splitlines() if any(k in l for k in ("尚缺", "错", "失败", "未完成", "error", "Error"))]
        row = {"step": name, "cmd": " ".join(map(str, cmd)), "rc": r.returncode, "expected_rc": expect,
               "seconds": round(time.monotonic() - t0, 1), "ok": r.returncode == expect, "gaps": gaps[:12], "log": log.name}
        report["steps"].append(row)
        print(f"[{'ok' if row['ok'] else 'NOT ok'}] {name}: rc={r.returncode} (expected {expect}) {row['seconds']}s")
        for g in gaps[:6]:
            print("     ", g[:140])
        return r

    try:
        step("check-empty", ["bash", "bin/install.sh", "--check"], expect=1)
        step("bundle-install", ["python3", "-B", "tools/runtime-bundle.py", "install", "--from", str(a.bundle.resolve())])
        step("install-base", ["bash", "bin/install.sh"])
        if a.with_flags:
            step("install-route", ["bash", "bin/install.sh", "--runtime-only", *a.with_flags.split()])
        step("check-ready", ["bash", "bin/install.sh", "--check"])
        step("game-install", ["bash", "bin/dryport", "install", a.appid], timeout=5400)
        apps = list((trial / "games").glob("*.app"))
        report["apps"] = [str(p) for p in apps]
        report["games_dir_bytes"] = sum(f.stat().st_size for f in (trial / "games").rglob("*") if f.is_file())
        report["runtimes_dir_bytes"] = sum(f.stat().st_size for f in (trial / "runtimes").rglob("*") if f.is_file())
        if apps:
            step("archive-again", ["bash", "bin/dryport", "archive", a.appid, "--yes"])
    finally:
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        report["all_ok"] = all(s["ok"] for s in report["steps"])
        (out / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        print(f"evidence: {out}  all_ok={report['all_ok']}")
        if not a.keep:
            shutil.rmtree(trial, ignore_errors=True)
            print("trial dir removed (--keep to keep)")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
