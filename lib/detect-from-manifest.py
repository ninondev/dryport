#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""用 SteamDB 规则集从 depot manifest 识别引擎，不需要下载游戏本体。
用法: detect-from-manifest.py <manifest.txt> [...]
"""
import re, sys, os
from collections import Counter

RULES = os.path.expanduser("~/Games/runtimes/FileDetectionRuleSets/rules.ini")

def load_rules():
    """解析 SteamDB 的 rules.ini，返回 [(段名, 引擎名, 编译后正则)]"""
    out, section = [], None
    for line in open(RULES, encoding='utf8'):
        line = line.strip()
        if not line or line.startswith(';'): continue
        if line.startswith('['):
            section = line.strip('[]'); continue
        if '=' not in line: continue
        key, rest = line.split('=', 1)
        key = key.strip().rstrip('[]')
        # 行尾注释通常就是引擎的人类可读名
        pat, _, comment = rest.partition(';')
        pat, comment = pat.strip(), comment.strip()
        if not pat: continue
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            continue
        out.append((section, key, comment or key, rx))
    return out

def parse_manifest(path):
    """DepotDownloader 的 manifest 文本，取出文件路径列"""
    files = []
    for line in open(path, encoding='utf8', errors='replace'):
        line = line.rstrip('\n')
        if not line or line.startswith(('Content Manifest', 'Manifest', '  Total', 'Size')): continue
        # 格式大致是: <size> <chunks> <sha> <flags> <path>
        parts = line.split(None, 4)
        if len(parts) >= 5 and parts[0].isdigit():
            files.append(parts[4].replace('\\', '/'))
        elif line.startswith(('/', './')) or '/' in line:
            files.append(line.strip().replace('\\', '/'))
    return files

def detect(files, rules):
    hits = Counter()
    for f in files:
        for section, key, name, rx in rules:
            if rx.search(f):
                hits[(section, name)] += 1
    return hits

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    rules = load_rules()
    print(f"规则集: {len(rules)} 条\n")
    for mf in sys.argv[1:]:
        files = parse_manifest(mf)
        hits = detect(files, rules)
        print(f"{os.path.basename(mf)}  ({len(files)} 个文件)")
        if not hits:
            print("  未命中任何规则")
        for (sec, name), n in hits.most_common(8):
            print(f"  [{sec}] {name:<28} {n:>5} 个文件命中")
        print()
