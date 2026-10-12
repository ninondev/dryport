#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""反作弊硬闸 — 查一个 Steam appid 能不能安全地在 Wine 下跑
用法: anticheat-gate.py <appid> [appid...]
退出码: 0=放行  1=拦截  2=无数据(需人工判断)
数据源: AreWeAntiCheatYet (众包, 含 Wine/Proton 实测状态)
"""
import json, os, sys

DB = os.path.expanduser("~/Games/runtimes/anticheat-db.json")
BLOCK = {'Broken', 'Denied'}          # 这两档一律拦
WARN  = {'Planned'}

def load():
    if not os.path.exists(DB):
        print("反作弊库缺失, 跑 update-anticheat-db.sh", file=sys.stderr); sys.exit(2)
    idx = {}
    for g in json.load(open(DB)):
        si = g.get('storeIds')
        if not isinstance(si, dict): continue
        v = si.get('steam')
        vals = v if isinstance(v, list) else ([v] if v is not None else [])
        for x in vals:
            try: idx[int(x)] = g
            except (TypeError, ValueError): pass
    return idx

def check(appid, idx):
    g = idx.get(appid)
    if not g:
        return 2, f"{appid}: 库中无记录(大概率无反作弊, 但未经证实)"
    ac = ', '.join(g.get('anticheats') or []) or '无'
    st = g['status']
    if st in BLOCK:
        return 1, f"{appid}: 拦截 — {g['name']} [{st}] 反作弊: {ac}"
    if st in WARN:
        return 2, f"{appid}: 存疑 — {g['name']} [{st}] 反作弊: {ac}"
    return 0, f"{appid}: 放行 — {g['name']} [{st}] 反作弊: {ac}"

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    idx = load()
    worst = 0
    for a in sys.argv[1:]:
        try: code, msg = check(int(a), idx)
        except ValueError: code, msg = 2, f"{a}: 不是合法 appid"
        print(("  ✗ " if code==1 else "  ? " if code==2 else "  ✓ ") + msg)
        worst = max(worst, code) if code != 2 or worst == 0 else worst
    sys.exit(worst)
