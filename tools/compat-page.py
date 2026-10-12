#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""兼容性台账渲染：公开台账与公开摘要 → 英文和中文页面。

  tools/compat-page.py [--json 路径] [--out 目录]

每条必须有 date、evidence 与公开摘要引用，否则拒绝（页面不写）。name 只允许已获准点名的四款；其余条目无名，
页面的游戏列写「one title, not named / 一款，未点名」。tier 必须与 lib/route-tiers.json 一致（unity 路线
2017.3 以前的版本按其注记可为 experimental）。除本工具外没有任何东西写这两页。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIERS = ROOT / "lib/route-tiers.json"
RECORDS = ROOT / "docs/compatibility-evidence.json"
CONTEXT = ("game_build", "runtime_version", "macos_version", "tool_revision", "original_record", "hardware")
NAMED_TITLES = {"A Space for the Unbound", "Stories from Sol: The Gun-Dog", "Koi Farm", "Star Sky 3"}
EVIDENCE = {"real-machine", "mechanism"}

ROUTE_EN = {"unity": "Unity (Mono) retarget", "wine": "Wine + DXMT", "mkxp": "mkxp-z (RPG Maker XP/VX/VX Ace)",
            "nwjs": "NW.js", "webkit": "WebKit", "renpy-native": "Ren'Py", "godot": "Godot", "love": "LÖVE", "krkr": "KiriKiri"}
ROUTE_ZH = {"unity": "Unity（Mono）换原生播放器", "wine": "Wine + DXMT", "mkxp": "mkxp-z（RPG Maker XP/VX/VX Ace）",
            "nwjs": "NW.js", "webkit": "WebKit", "renpy-native": "Ren'Py", "godot": "Godot", "love": "LÖVE", "krkr": "吉里吉里"}

CHAIN_EN = ["draws a picture and can be operated", "saves inside the game",
            "a normal quit and relaunch loads that save", "a force-quit and relaunch loads the latest save",
            "original files and existing saves stay byte-identical"]
CHAIN_ZH = ["出画面且可操作", "游戏内存档", "正常退出重开能读档", "强退重开能读最新档", "原件与既有存档逐字节不变"]


def fail(message):
    print(f"错: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate(entries, tiers, evidence):
    routes = tiers["routes"]
    records = {r["id"]: r for r in evidence["records"]}
    if len(records) != len(evidence["records"]) or {e.get("id") for e in entries} != set(records):
        fail("台账与公开摘要的条目清单不一致")
    ids = set()
    for e in entries:
        where = e.get("id", "<no id>")
        if not isinstance(e.get("id"), str) or not re.fullmatch(r"entry-[0-9]+", e["id"]):
            fail("条目 id 必须是 entry-数字 形式的匿名标识符")
        if not e.get("id") or e["id"] in ids:
            fail(f"条目 id 缺失或重复：{where}")
        ids.add(e["id"])
        if e.get("evidence_ref") != e["id"] or e["id"] not in records:
            fail(f"条目 {where} 缺少对应的公开摘要")
        record = records[e["id"]]
        for field in ("date", "route", "evidence"):
            if e.get(field) != record.get(field):
                fail(f"条目 {where}.{field} 与公开摘要不一致")
        claims = {k: e[k] for k in ("chain", "fps", "frame_p99_ms") if k in e}
        if claims != record["claims"]:
            fail(f"条目 {where} 的验证链或数字与公开摘要不一致")
        context = {**evidence.get("context", {}), **record.get("context", {})}
        for field in CONTEXT:
            if field not in e or field not in context or e[field] != context[field]:
                fail(f"条目 {where}.{field} 缺失或与记录环境不一致")
        if not e.get("date"):
            fail(f"条目 {where} 没有 date，拒绝")
        if e.get("evidence") not in EVIDENCE:
            fail(f"条目 {where} 没有 evidence（real-machine 或 mechanism），拒绝")
        if e.get("name") and e["name"] not in NAMED_TITLES:
            fail(f"条目 {where} 的 name 只允许点名四款之一：{e['name']!r}")
        route = e.get("route")
        if route not in routes:
            fail(f"条目 {where} 的 route 不在 lib/route-tiers.json：{route!r}")
        tier = e.get("tier")
        want = routes[route]["tier"]
        # unity 路线注记：2017.3 以前经 Rosetta 属实验，所以 unity 条目可以合理地标 experimental
        if tier != want and not (route == "unity" and tier == "experimental" and want == "open"):
            fail(f"条目 {where} 的 tier 是 {tier!r}，与 lib/route-tiers.json 的 {route}={want!r} 不一致")
        chain = e.get("chain", [])
        if not isinstance(chain, list) or not chain or any(not isinstance(s, int) or s < 1 or s > 5 for s in chain):
            fail(f"条目 {where} 的 chain 必须是 1..5 的非空列表")
        for field in ("fps", "frame_p99_ms"):
            if field in e and not isinstance(e[field], (int, float)):
                fail(f"条目 {where} 的 {field} 必须是数字")


def game_cell(e, zh):
    return e["name"] if e.get("name") else ("一款，未点名" if zh else "one title, not named")


def chain_cell(e):
    return "+".join(str(s) for s in e["chain"])


def rows(entries, zh):
    out = []
    for e in entries:
        game = game_cell(e, zh)
        perf = ""
        if "fps" in e:
            perf = f"{e['fps']:g} fps"
        if "frame_p99_ms" in e:
            perf += f"{', ' if perf else ''}p99 {e['frame_p99_ms']:g} ms"
        ref = f"[{e['evidence_ref']}](compatibility-evidence.json)"
        out.append([e["date"], game, e["engine"], e["hardware"], f"{e['evidence']} {ref}", chain_cell(e), perf, e.get("notes", "")])
    return out


def table(entries, zh):
    header = ["日期", "游戏", "引擎", "硬件", "摘要记录", "链", "帧率", "备注"] if zh else \
             ["Date", "Game", "Engine", "Hardware", "Summary record", "Chain", "Frames", "Notes"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows(entries, zh):
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(lines)


def render(data, tiers):
    entries = data["entries"]
    order = [r for r in tiers["routes"] if any(e["route"] == r for e in entries)]
    label_en = {"open": "promised for the preview", "experimental": "experimental: installable, not promised"}
    label_zh = tiers["labels"]
    pages = {}
    for zh, path in ((False, "compatibility.md"), (True, "compatibility.zh.md")):
        title = "# 兼容性台账" if zh else "# Compatibility ledger"
        intro = ("这是已有结果的选录，公开摘要可通过摘要记录列查阅。原始记录未公开。"
                 "版本与原始记录背景见摘要记录，null 表示未知。"
                 "无名条目写作「一款，未点名」。链列列出摘要报告通过的步骤编号，见下面图例。"
                 if zh else
                 "Selected recorded results, linked to public summaries in the Summary record column. "
                 "The summaries preserve historical results; original records are unpublished. "
                 "Version and original-record context is listed in the summary records; null means unknown. "
                 "Entries without a name say one title, not named. Chain lists the steps reported as passed; see the legend.")
        parts = [title, "", intro, ""]
        for route in order:
            route_entries = sorted((e for e in entries if e["route"] == route), key=lambda e: e["date"], reverse=True)
            tier = route_entries[0]["tier"]
            labels = label_zh if zh else label_en
            route_name = ROUTE_ZH[route] if zh else ROUTE_EN[route]
            parts.append(f"## {route_name} — {labels[tier]}")
            parts.append("")
            parts.append(table(route_entries, zh))
            parts.append("")
        chain = CHAIN_ZH if zh else CHAIN_EN
        parts.append(("## 图例" if zh else "## Legend"))
        parts.append("")
        for i, step in enumerate(chain, 1):
            parts.append(f"- {i}. {step}")
        if zh:
            parts.append("- 证据 real-machine：历史真游戏结果摘要，尚未与原始记录核对。")
            parts.append("- 证据 mechanism：用夹具或合成数据在测试里证明（未跑真游戏）。")
            parts.append("- 帧率列保留已记录的测量结果；未测量的条目该列为空。")
        else:
            parts.append("- Evidence real-machine: a historical real-game summary, awaiting comparison with original records.")
            parts.append("- Evidence mechanism: proven with fixtures or synthetic data in tests; no real game run.")
            parts.append("- The Frames column retains recorded measurements; entries without measurements leave it empty.")
        parts.append("")
        pages[path] = "\n".join(parts)
    return pages


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=str(ROOT / "docs/compatibility.json"))
    ap.add_argument("--out", default=str(ROOT / "docs"))
    a = ap.parse_args(argv)
    data = json.loads(Path(a.json).read_text(encoding="utf8"))
    tiers = json.loads(TIERS.read_text(encoding="utf8"))
    evidence = json.loads(RECORDS.read_text(encoding="utf8"))
    validate(data["entries"], tiers, evidence)
    out = Path(a.out)
    for name, text in render(data, tiers).items():
        (out / name).write_text(text, encoding="utf8")
        print(f"已写 {out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
