#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""无窗口验证 wine-cx 里的 D3D11 走的是谁：DXMT（winemetal）还是 Wine 自带实现。

把 tools/d3d11-probe.c 编成 PE32 与 PE32+（mingw），在临时 prefix 里各跑一次，打印 feature level、适配器名与 winemetal.dll
是否加载。32 位那份就是 2026-09-13 给 wine-cx 补装 DXMT i386 PE 后的机制验证，不用起游戏。

  tools/d3d11-probe.py [--out 目录] [--keep]

不碰游戏，不改 wine-cx；测完 wineserver -k，删临时 prefix（--keep 保留）。产物：probe64.log、probe32.log、evidence.json。
退出码：两种位数都建出硬件设备且加载了 winemetal.dll 为 0，否则 1（缺 i686 编译器时只算 64 位）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_RUNTIMES

HERE = Path(__file__).resolve().parent
WINE = SN_RUNTIMES / "wine-cx/bin/wine"
WINESERVER = SN_RUNTIMES / "wine-cx/bin/wineserver"
COMPILERS = {"x86_64": "x86_64-w64-mingw32-gcc", "i386": "i686-w64-mingw32-gcc"}


def parse(text):
    info = {"raw": text}
    for line in text.splitlines():
        if "D3D11CreateDevice" in line:
            for tok in line.split():
                if "=" in tok:
                    k, v = tok.split("=", 1); info[k] = v
        elif line.startswith("winemetal.dll loaded="):
            info["winemetal"] = line.strip().endswith("=1")
        elif line.startswith("adapter="):
            info["adapter"] = line.split("adapter=", 1)[1].split(" vendor=")[0]
    fl = int(info.get("feature_level", "0x0"), 16)
    info["dxmt"] = bool(info.get("winemetal")) and fl >= 0xB000
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    if not WINE.exists():
        sys.exit(f"缺少 {WINE}")
    out = a.out or Path(tempfile.mkdtemp(prefix="sn-d3d11-probe-", dir=SN_RUNTIMES))
    out.mkdir(parents=True, exist_ok=True)
    prefix = out / "prefix"
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all", WINEARCH="win64",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d")
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "wine": str(WINE), "results": {}}
    ok = True
    try:
        for arch, cc in COMPILERS.items():
            if not shutil.which(cc):
                report["results"][arch] = {"skipped": f"没有 {cc}"}
                print(f"{arch}: 跳过，没有 {cc}")
                continue
            exe = out / f"probe-{arch}.exe"
            build = subprocess.run([cc, "-O1", "-o", str(exe), str(HERE / "d3d11-probe.c"), "-ld3d11", "-ldxgi", "-static-libgcc"],
                                   capture_output=True, text=True)
            if build.returncode:
                report["results"][arch] = {"build_failed": build.stderr[-2000:]}
                print(f"{arch}: 编译失败\n{build.stderr}"); ok = False
                continue
            t0 = time.monotonic()
            run = subprocess.run([str(WINE), str(exe)], env=env, capture_output=True, text=True, timeout=300, cwd=out)
            (out / f"probe{'64' if arch == 'x86_64' else '32'}.log").write_text(run.stdout + run.stderr)
            info = parse(run.stdout); info["rc"] = run.returncode; info["seconds"] = round(time.monotonic() - t0, 1)
            report["results"][arch] = info
            print(f"{arch}: rc={run.returncode} hr={info.get('hr')} feature_level={info.get('feature_level')} "
                  f"winemetal={info.get('winemetal')} adapter={info.get('adapter')!r} dxmt={info['dxmt']} ({info['seconds']} s)")
            ok = ok and info["dxmt"]
    finally:
        subprocess.run([str(WINESERVER), "-k"], env=env, capture_output=True, timeout=60)
        time.sleep(1)
        if not a.keep:
            shutil.rmtree(prefix, ignore_errors=True)
    report["ok"] = ok
    (out / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"evidence: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
