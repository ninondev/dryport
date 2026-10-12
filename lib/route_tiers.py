#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""路线分层：一条路线是「open（预览版承诺）」还是「experimental（实验）」。

数据只此一份（模块旁边的 route-tiers.json），CLI、api.py 与伴侣 app 三处共用，
改承诺只改 JSON。分层只是标注，不改变任何路线的实际行为。
"""
import argparse
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().with_name("route-tiers.json")
VERSION_RE = re.compile(r"(\d+)\.(\d+)")
CACHE = {}

EXPERIMENTAL_LINE = "      这条路线是实验：照样能装，但预览版不承诺能玩到底（真机全链条没凑齐两款）。"


def load(path=None):
    """读分层表并缓存。表缺失或损坏时按空表处理：分层只是标注，不能拖垮库浏览与安装。"""
    key = str(Path(path)) if path is not None else str(DATA)
    if key not in CACHE:
        try:
            CACHE[key] = json.loads(Path(key).read_text(encoding="utf8"))
        except (OSError, ValueError):
            CACHE[key] = {"protocol": 1, "labels": {}, "routes": {}}
    return CACHE[key]


def unity_version(text):
    """取文本里第一处 major.minor（Unity 5.6.3f1 → (5, 6)）；读不出（如「版本未知」）返回 None。"""
    m = VERSION_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def tier_for(route, engine=None):
    """这条路线此刻的分层。带 experimental_below 的路线（Unity）按引擎文本里的版本细分：
    低于分界算 experimental，达到或超过算 open，版本读不出就维持路线本身的分层。"""
    entry = load().get("routes", {}).get(route)
    if not isinstance(entry, dict):
        return None
    tier = entry.get("tier")
    below = entry.get("experimental_below")
    if below:
        version, threshold = unity_version(engine), unity_version(below)
        if version is not None and threshold is not None:
            return "experimental" if version < threshold else "open"
    return tier


def describe(route, engine=None):
    """JSON API 用的三个字段。注释只在结果仍是 open（承诺成立）时给出：
    已经标成实验的，标签本身就说明了情况，不再叠加历史注脚。"""
    tier = tier_for(route, engine)
    data = load()
    entry = data.get("routes", {}).get(route)
    note = entry.get("note") if isinstance(entry, dict) else None
    return {
        "tier": tier,
        "tier_label": data.get("labels", {}).get(tier) if tier else None,
        "tier_note": note if tier == "open" and note else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="查一条路线的分层：实验路线打印一行说明，其余一律不打印。")
    parser.add_argument("--route", required=True, help="路线 id（route-tiers.json 的 routes 键）")
    parser.add_argument("--engine", default=None, help="引擎识别行原文，Unity 按其中的版本细分")
    args = parser.parse_args(argv)
    try:
        if tier_for(args.route, args.engine) == "experimental":
            print(EXPERIMENTAL_LINE)
    except Exception:
        pass  # 分层只是标注：这里出任何错都不能影响安装流程的退出码
    return 0


if __name__ == "__main__":
    sys.exit(main())
