#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""带真实 Steam 的桥集成运行（机制验证）：起 helper，在临时 Wine prefix 里跑 sbtest.exe 的 RegisterCallback 与
ManualDispatch 两种模式，只读身份与成就并断言策略检查；测完停自己的 helper、wineserver 与 prefix。

  tools/sbtest-run.py [--appid 1201270] [--out 目录] [--keep-prefix]

临时 prefix 建一次就 1.4 GB（wineboot 给每个内建 DLL 留登记副本），取完 DLL 侧日志默认就删；要留下来翻加 --keep-prefix
（2026-09-13：20 次运行的 prefix 悄悄占了 29 GB，盘到 96%）。

产物目录：helper.log、sbtest-register.log、sbtest-manual.log、steambridge.log（DLL 侧）、evidence.json。
前提：$SN_RUNTIMES/steambridge 已由 lib/steambridge/build.sh 构建，$SN_RUNTIMES/wine-cx 存在，Steam 已登录并在跑。
不碰游戏目录，不改 Steam；appid 默认 1201270（Unbound，owner 拥有，成就只读）。
"""
import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_RUNTIMES

BR = SN_RUNTIMES / "steambridge"
WINE = SN_RUNTIMES / "wine-cx/bin/wine"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--appid", default="1201270")
    ap.add_argument("--out", type=Path, help="证据目录（默认在 SN_RUNTIMES 下建临时目录）")
    ap.add_argument("--keep-prefix", action="store_true", help="保留临时 Wine prefix（默认取完日志就删，约 1.4 GB）")
    a = ap.parse_args(argv)
    for need in (BR / "steam_api64.dll", BR / "steambridge-helper", BR / "sbtest.exe", WINE):
        if not need.exists():
            sys.exit(f"缺少 {need}；先跑 lib/steambridge/build.sh，wine-cx 需已存在")
    out = a.out or Path(tempfile.mkdtemp(prefix="sn-sbtest-", dir=SN_RUNTIMES))
    out.mkdir(parents=True, exist_ok=True)
    report = {"appid": a.appid, "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
              "dll_sha256": sha(BR / "steam_api64.dll"), "helper_sha256": sha(BR / "steambridge-helper"),
              "sbtest_sha256": sha(BR / "sbtest.exe"),
              "dll32_sha256": sha(BR / "steam_api.dll") if (BR / "steam_api.dll").exists() else None}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    # helper 只服务带同一令牌的连接；令牌同时进 helper 与 Wine 进程环境。
    token = secrets.token_hex(32)
    prefix = out / "prefix"
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all", WINEARCH="win64",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d",
               STEAMBRIDGE_PORT=str(port), STEAMBRIDGE_TOKEN=token, SteamAppId=a.appid, SteamGameId=a.appid)
    helper = None
    rc = 1
    try:
        hl = open(out / "helper.log", "wb")
        helper = subprocess.Popen([str(BR / "steambridge-helper"), a.appid, str(port)], stdout=hl,
                                  stderr=subprocess.STDOUT, start_new_session=True, cwd=out,
                                  env=dict(os.environ, STEAMBRIDGE_TOKEN=token))
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if helper.poll() is not None:
                raise SystemExit(f"helper 提前退出 rc={helper.returncode}，见 {out / 'helper.log'}")
            if "listening on 127.0.0.1:" in (out / "helper.log").read_text(errors="replace"):
                break
            time.sleep(0.05)
        else:
            raise SystemExit("helper 10 秒内没就绪")
        report["helper_started"] = True
        t0 = time.monotonic()
        r = subprocess.run([str(WINE), "wineboot", "--init"], env=env, cwd=out, capture_output=True, text=True, timeout=300)
        report["wineboot"] = {"rc": r.returncode, "seconds": round(time.monotonic() - t0, 3)}
        for mode in ("register", "manual"):
            t1 = time.monotonic()
            args = [str(WINE), str(BR / "sbtest.exe")] + (["manual"] if mode == "manual" else [])
            r = subprocess.run(args, env=env, cwd=BR, capture_output=True, text=True, timeout=300)
            (out / f"sbtest-{mode}.log").write_text(r.stdout + r.stderr)
            report["sbtest_" + mode] = {"rc": r.returncode, "seconds": round(time.monotonic() - t1, 3),
                                        "pass": r.stdout.count("PASS:"), "fail": r.stdout.count("FAIL:")}
            print(f"=== sbtest {mode} ===\n" + r.stdout)
        report["sbtest"] = {"rc": max(report["sbtest_register"]["rc"], report["sbtest_manual"]["rc"]),
                            "fail": report["sbtest_register"]["fail"] + report["sbtest_manual"]["fail"],
                            "pass": report["sbtest_register"]["pass"] + report["sbtest_manual"]["pass"]}
        if (BR / "sbtest32.exe").exists() and (BR / "steam_api.dll").exists():
            # 32 位桥（扁平 API）：同一个 helper，wine 的 wow64 跑 32 位客户端
            for mode in ("register", "manual"):
                t1 = time.monotonic()
                args = [str(WINE), str(BR / "sbtest32.exe")] + (["manual"] if mode == "manual" else [])
                r = subprocess.run(args, env=env, cwd=BR, capture_output=True, text=True, timeout=300)
                (out / f"sbtest32-{mode}.log").write_text(r.stdout + r.stderr)
                report["sbtest32_" + mode] = {"rc": r.returncode, "seconds": round(time.monotonic() - t1, 3),
                                              "pass": r.stdout.count("PASS:"), "fail": r.stdout.count("FAIL:")}
                print(f"=== sbtest32 {mode} ===\n" + r.stdout)
            report["sbtest"]["rc"] = max(report["sbtest"]["rc"], report["sbtest32_register"]["rc"], report["sbtest32_manual"]["rc"])
            report["sbtest"]["fail"] += report["sbtest32_register"]["fail"] + report["sbtest32_manual"]["fail"]
            report["sbtest"]["pass"] += report["sbtest32_register"]["pass"] + report["sbtest32_manual"]["pass"]
    finally:
        subprocess.run([str(WINE.parent / "wineserver"), "-k"], env=env, timeout=10)
        subprocess.run([str(WINE.parent / "wineserver"), "-w"], env=env, timeout=15)
        if helper and helper.poll() is None:
            os.killpg(helper.pid, signal.SIGTERM)
            try:
                helper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(helper.pid, signal.SIGKILL)
        report["helper_rc"] = helper.returncode if helper else None
        log = (out / "helper.log").read_text(errors="replace") if (out / "helper.log").exists() else ""
        report["helper_refused_lines"] = sum(1 for l in log.splitlines() if "refused" in l)
        report["helper_interface_lines"] = [l for l in log.splitlines() if "interface" in l or "callback" in l or "forwarding" in l or "FilterText" in l][:60]
        cands = list(prefix.glob("drive_c/users/*/AppData/Local/Temp/steambridge.log")) + list(prefix.glob("drive_c/users/*/**/steambridge.log"))
        if cands:
            (out / "steambridge.log").write_bytes(cands[0].read_bytes())
            text = cands[0].read_text(errors="replace")
            report["dll_log_inert_lines"] = [l for l in text.splitlines() if "inert" in l or "allback" in l or "ManualDispatch" in l or "FilterText" in l][:60]
            report["dll_not_forwarded_lines"] = [l for l in text.splitlines() if "not forwarded" in l or "stub" in l][:60]
        if not a.keep_prefix:
            import shutil
            shutil.rmtree(prefix, ignore_errors=True)   # 日志已抄到 out/，prefix 本身只是可重建的 1.4 GB
        report["ok"] = bool(report.get("sbtest", {}).get("rc") == 0 and report["sbtest"]["fail"] == 0 and report["helper_refused_lines"] == 0)
        (out / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k not in ("helper_interface_lines", "dll_log_inert_lines", "dll_not_forwarded_lines")}, indent=1, ensure_ascii=False))
        print("helper lines:", *report["helper_interface_lines"], sep="\n  ")
        print("dll lines:", *report.get("dll_log_inert_lines", []), sep="\n  ")
        print("evidence:", out)
        rc = 0 if report["ok"] else 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
