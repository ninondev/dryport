#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""按当前判据重判已有冒烟结果（不重跑）：读每款最新 result.json 的截图统计、帧数、flags，重算 verdict，写回 result.json 与 .smoke/<appid>.json。
判据改动后跑一次，让报告口径一致；每次重判记进 run["rescored"]。只改 verdict / flags / liveness，其它字段不动。
  tools/smoke-rescore.py [--dry-run]
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_GAMES, SN_RUNTIMES   # noqa: E402

spec = importlib.util.spec_from_file_location("smoke_run", ROOT / "tools" / "smoke-run.py")
smoke = importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)
SMOKE_DIR = SN_GAMES / ".smoke"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args(argv)
    changed = 0
    for f in sorted(SMOKE_DIR.glob("*.json")):
        if f.name == "ledger.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        latest = d.get("latest") or {}
        ev = latest.get("evidence")
        if not ev or not (Path(ev) / "result.json").is_file():
            continue
        r = json.loads((Path(ev) / "result.json").read_text())
        old = r.get("verdict")
        if not old or old.startswith("install_failed") or old in ("launch_failed", "no_window", "crashed", "exited_early", "aborted_user_active"):
            continue
        shots = r.get("shots") or []
        for s_ in shots:
            png = Path(s_.get("file", ""))
            if png.is_file() and (s_.get("stats") is None or "detail_fraction" not in (s_.get("stats") or {})):
                s_["stats"] = smoke.image_stats(png)
        flags = set(r.get("flags") or []) - {"blank"}
        signals = smoke.liveness_signals(r.get("frames"), shots, flags)
        if shots and all(smoke.looks_blank(s_.get("stats")) for s_ in shots):
            new = "blank_window"; flags.add("blank")
        elif r.get("phase") in ("window", "fullscreen") and r.get("dwell_result") in ("alive", None) and not r.get("crash_signals"):
            new = "alive" if len(signals) >= 2 else "static_window"
        else:
            new = old
        if new != old or sorted(flags) != sorted(r.get("flags") or []):
            changed += 1
            print(f"{r.get('appid'):>8} {str(r.get('name'))[:30]:30} {old:14} -> {new:14} signals={signals} flags={sorted(flags)}")
            if not a.dry_run:
                r["verdict"], r["flags"], r["liveness"] = new, sorted(flags), signals
                r.setdefault("rescored", []).append({"from": old, "to": new, "rule": "2026-09-14 两信号"})
                (Path(ev) / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
                latest["verdict"], latest["flags"] = new, sorted(flags)
                for run in d.get("runs", []):
                    if run.get("evidence") == ev:
                        run["verdict"], run["flags"] = new, sorted(flags)
                tmp = f.with_suffix(".json.tmp"); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1)); os.replace(tmp, f)
    print(f"改动 {changed} 款")


if __name__ == "__main__":
    main()
