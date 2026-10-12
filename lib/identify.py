#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""用 SteamDB 规则集识别引擎/SDK/反作弊。
输入可以是: 游戏目录、DepotDownloader 的 manifest 文本、或文件清单。
用法: identify.py <路径>
"""
import re, sys, os
from collections import Counter

RULES = os.path.expanduser("~/Games/runtimes/FileDetectionRuleSets/rules.ini")

def load_rules():
    rules, section = [], None
    for line in open(RULES, encoding='utf8'):
        line = line.strip()
        if not line or line.startswith(';'): continue
        if line.startswith('['): section = line.strip('[]'); continue
        if '=' not in line: continue
        key, rest = line.split('=', 1)
        key = key.strip().rstrip('[]')
        pat, _, comment = rest.partition(';')
        pat, comment = pat.strip(), comment.strip()
        if not pat: continue
        try: rules.append((section, comment or key, re.compile(pat, re.I)))
        except re.error: pass
    return rules

def collect(path):
    """目录就遍历文件，文本就当清单解析"""
    if os.path.isdir(path):
        out = []
        for root, _, files in os.walk(path):
            for f in files:
                out.append(os.path.relpath(os.path.join(root, f), path).replace('\\','/'))
        return out
    out = []
    for line in open(path, encoding='utf8', errors='replace'):
        line = line.rstrip('\n')
        if not line.strip(): continue
        parts = line.split(None, 4)
        if len(parts) >= 5 and parts[0].isdigit():
            out.append(parts[4].replace('\\','/'))
        else:
            out.append(line.strip().replace('\\','/'))
    return out

# 原生运行时路由表: 引擎名 -> (方案, 档位)
ROUTE = {
    'Unity':        ('看 IL2CPP 还是 Mono; Mono 可试 arm64 Player 换壳', 'MAYBE'),
    'Unreal':       ('无换 runner 路线', 'WINE'),
    'RPGMaker':     ('mkxp-z, 已实测跑通', 'NATIVE'),
    'RenPy':        ("官方 macOS SDK 换壳", 'NATIVE'),
    "Ren'Py":       ("官方 macOS SDK 换壳", 'NATIVE'),
    'Godot':        ('同版本原生 Godot 读 pck', 'NATIVE'),
    'GameMaker':    ('视版本, 部分有 Mac runner', 'MAYBE'),
    'Love':         ('原生 LÖVE 直接跑 .love', 'NATIVE'),
    'Flash':        ('Ruffle 原生播放器', 'NATIVE'),
}

if __name__ == '__main__':
    if len(sys.argv) < 2: print(__doc__); sys.exit(2)
    rules = load_rules()
    files = collect(sys.argv[1])
    hits = Counter()
    for f in files:
        for sec, name, rx in rules:
            if rx.search(f): hits[(sec, name)] += 1

    engines   = [(n,c) for (s,n),c in hits.items() if s == 'Engine']
    anticheat = [(n,c) for (s,n),c in hits.items() if s == 'AntiCheat']
    sdks      = [(n,c) for (s,n),c in hits.items() if s == 'SDK']

    print(f"文件 {len(files)} 个, 规则 {len(rules)} 条\n")
    if anticheat:
        print("!! 反作弊命中 (在 Wine 下有封号风险):")
        for n,c in sorted(anticheat, key=lambda x:-x[1]): print(f"     {n}  ({c} 个文件)")
        print()
    print("引擎:")
    if engines:
        for n,c in sorted(engines, key=lambda x:-x[1])[:5]:
            plan, tier = ROUTE.get(n, ('无原生方案, 走 Wine', 'WINE'))
            mark = {'NATIVE':'可原生','MAYBE':'待验证','WINE':'走 Wine'}[tier]
            print(f"     {n:<22} [{mark}] {plan}")
    else:
        print("     未识别")
    if sdks:
        print("\nSDK: " + ", ".join(n for n,_ in sorted(sdks, key=lambda x:-x[1])[:6]))
    sys.exit(1 if anticheat else 0)
