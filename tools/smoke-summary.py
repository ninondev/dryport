#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""把 $SN_GAMES/.smoke/*.json 的最新结果汇总成 Markdown：按实际路线（下载后识别的引擎）分的通过率表 + 逐款表。写文档用；只读。

  tools/smoke-summary.py            打印两张表
  tools/smoke-summary.py --recent 20  只看最近 20 款（按时间）
口径：verdict=alive 只表示出窗口、活满观察期、活性信号 ≥ 2，不等于可玩。
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_GAMES   # noqa: E402


def load():
    rows = []
    for f in (SN_GAMES / ".smoke").glob("*.json"):
        if f.name == "ledger.json":
            continue
        try:
            d = json.loads(f.read_text()); l = d.get("latest") or {}
        except Exception:
            continue
        if not l.get("verdict"):
            continue
        r = {}
        try:
            r = json.loads((Path(l["evidence"]) / "result.json").read_text())
        except Exception:
            pass
        rows.append({"appid": d.get("appid"), "name": r.get("name") or "", "route": l.get("route"), "engine": l.get("engine"),
                     "detected": r.get("detected_engine") or "", "verdict": l["verdict"], "flags": l.get("flags") or [],
                     "window_at": r.get("window_at_seconds"), "at": l.get("at") or "", "runs": len(d.get("runs") or []),
                     "evidence": l.get("evidence") or ""})
    return rows


def actual_route(r):
    d = r["detected"]
    if r["route"] in ("nwjs", "mkxp", "godot", "krkr", "love"):
        return r["route"] + " 原生"
    if d.startswith("Unity 20") or d.startswith("Unity 5"):
        return "Unity 原生（换播放器）"
    if "IL2CPP" in d:
        return "Unity IL2CPP（Wine）"
    if "GameMaker" in d:
        return "GameMaker（Wine + 32 位桥）"
    if "AIR" in d:
        return "Adobe AIR（Wine）"
    if "NW.js" in d:
        return "NW.js 原生"
    if "版本未知" in d or "后端待定" in d:
        return "Unity 版本不明（Wine）"
    return "未识别（Wine）"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--recent", type=int); a = ap.parse_args(argv)
    rows = load()
    rows.sort(key=lambda r: r["at"])
    if a.recent:
        rows = rows[-a.recent:]
    total = collections.Counter(r["verdict"] for r in rows)
    print(f"{len(rows)} 款：" + "、".join(f"{k} {v}" for k, v in total.most_common()))
    print()
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[actual_route(r)][r["verdict"]] += 1
    print("| 实际路线 | 款数 | alive | 其它 |"); print("|---|---:|---:|---|")
    for k, v in sorted(by.items(), key=lambda kv: -sum(kv[1].values())):
        others = "、".join(f"{a_} {b}" for a_, b in sorted(v.items()) if a_ != "alive")
        print(f"| {k} | {sum(v.values())} | {v.get('alive', 0)} | {others} |")
    print()
    print("| appid | 游戏 | 目录路线 | 下载后识别 | verdict | 出窗口 s | flags | 跑过 |"); print("|---|---|---|---|---|---:|---|---:|")
    for r in sorted(rows, key=lambda r: (r["verdict"] != "alive", actual_route(r), r["name"])):
        print(f"| {r['appid']} | {r['name'][:36]} | {r['route']} | {r['detected'][:24]} | {r['verdict']} | {r['window_at'] if r['window_at'] is not None else ''} | {' '.join(r['flags'])[:40]} | {r['runs']} |")


if __name__ == "__main__":
    main()
