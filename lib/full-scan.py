#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""扫 appinfo.vdf: 引擎识别 + 反作弊识别 + 原生化可行性"""
import struct, sys, json
from collections import Counter, defaultdict

def load(path):
    d = open(path,'rb').read()
    magic = struct.unpack('<I', d[:4])[0]
    pos = 16 if magic >= 0x07564429 else 8
    out = []
    while pos < len(d)-8:
        appid = struct.unpack('<I', d[pos:pos+4])[0]
        if appid == 0: break
        size = struct.unpack('<I', d[pos+4:pos+8])[0]
        s,e = pos+8, pos+8+size
        if e > len(d): break
        out.append((appid, d[s:e])); pos = e
    return out

# ── 反作弊: 命中即禁止, 顺序不重要, 全部列出 ──
ANTICHEAT = {
    # 只用精确、不会撞词的特征。三字母缩写(EAC)之类一律不用:
    # "一个含 REACTOR 的游戏标题" 里的 REACTOR 就会撞上 EAC。
    'EasyAntiCheat': [b'EasyAntiCheat', b'easyanticheat', b'EasyAntiCheat_EOS',
                      b'start_protected_game.exe'],  # EAC 的标准启动包装器
    'BattlEye':      [b'BattlEye', b'BEService', b'BEClient', b'battleye.com'],
    'Vanguard':      [b'vgk.sys', b'vgc.exe', b'RiotVanguard', b'Riot Vanguard'],
    'nProtect':      [b'nProtect', b'GameGuard', b'GameMon'],
    'Denuvo-AC':     [b'Denuvo Anti-Cheat', b'denuvo_anti_cheat', b'AntiCheatProvider'],
    'FACEIT':        [b'FACEIT Anti', b'faceitac'],
    'Ricochet':      [b'Ricochet Anti'],
    'XIGNCODE':      [b'XIGNCODE', b'xhunter1.sys'],
    'mhyprot':       [b'mhyprot'],
    'ACE-Guard':     [b'AntiCheatExpert', b'ACE-BASE', b'anticheatexpert'],
    'PunkBuster':    [b'PunkBuster', b'pbsvc'],
    'VAC':           [b'VAC_Banned', b'ValveAntiCheat'],
}

# ── 引擎: 顺序即优先级, 先匹配先算 ──
ENGINES = [
    # (名字, 特征, 原生方案, 分档)
    ('Unity-IL2CPP', [b'GameAssembly.dll', b'global-metadata.dat', b'il2cpp_data'],
     '无原生路线, 游戏逻辑已编成 Windows 本机码', 'WINE'),
    ('Unity-Mono',   [b'Assembly-CSharp.dll', b'MonoBleedingEdge', b'mono-2.0-bdwgc'],
     '逐游戏移植: 同版本 arm64 Player 换壳, 有成功先例', 'MAYBE'),
    ('Unity',        [b'UnityPlayer.dll', b'UnityCrashHandler', b'unity default resources'],
     '先分辨 IL2CPP 还是 Mono', 'MAYBE'),
    ('Unreal',       [b'Shipping.exe', b'UE4Game', b'UnrealEngine', b'.utoc', b'pakchunk'],
     '无换 runner 路线', 'WINE'),
    ('RPGMaker-MVMZ',[b'nw.exe', b'rpg_core.js', b'rmmz_core.js', b'js/plugins.js'],
     'arm64 原生 NW.js 换壳', 'NATIVE'),
    ('RPGMaker-RGSS',[b'.rgss3a', b'.rgss2a', b'.rgssad', b'RGSS3', b'RGSS2', b'Game.rgss'],
     'mkxp-z, 已实测跑通', 'NATIVE'),
    ("Ren'Py",       [b'renpy', b'.rpa', b'.rpyc'],
     '官方 macOS SDK 换壳', 'NATIVE'),
    ('Godot',        [b'.pck', b'godot', b'project.binary'],
     '同版本原生 Godot 读 pck', 'NATIVE'),
    ('GameMaker',    [b'data.win', b'GameMaker', b'YoYo'],
     '视版本, 部分有 Mac runner', 'MAYBE'),
    ('WolfRPG',      [b'Game.dat', b'wolfrpg', b'DataWolfEditor'],
     '无成熟开源重实现', 'WINE'),
    ('KiriKiri',     [b'.xp3', b'krkr', b'TVP(KIRIKIRI)'],
     'KiriKiri2/Z 有开源重实现可试', 'MAYBE'),
    ('Flash/SWF',    [b'.swf'],
     'Ruffle 原生播放器', 'NATIVE'),
    ('AdobeAIR',     [b'Adobe AIR', b'.ane', b'application.xml'],
     'HARMAN AIR 的 Mac arm64 runtime', 'MAYBE'),
    ('Java',         [b'.jar', b'javaw.exe', b'jre/bin'],
     '原生 JVM 直接跑', 'NATIVE'),
    ('Electron',     [b'electron.exe', b'app.asar', b'chrome_100_percent.pak'],
     '原生 Electron 换壳', 'MAYBE'),
    ('Love2D',       [b'love.exe', b'.love', b'love2d'],
     '原生 LÖVE 直接跑 .love', 'NATIVE'),
    ('TyranoScript', [b'tyrano', b'tyranoscript'],
     '本质是 NW.js/HTML5', 'MAYBE'),
    ('SiglusEngine', [b'Siglus', b'SiglusEngine'],
     '无成熟重实现', 'WINE'),
    ('BGI/Ethornell',[b'BGI.exe', b'Ethornell', b'AdvHD.exe'],
     '无成熟重实现', 'WINE'),
    ('NScripter',    [b'nscript.dat', b'ONScripter'],
     'ONScripter 原生', 'NATIVE'),
    ('SRPGStudio',   [b'SRPG Studio', b'srpg_studio'],
     '无重实现', 'WINE'),
    ('Construct',    [b'c2runtime.js', b'c3runtime.js'],
     '本质 HTML5, 可换原生壳', 'MAYBE'),
]

apps = load(sys.argv[1])
eng_all = Counter(); eng_win = Counter()
ac_win = Counter(); ac_apps = defaultdict(list)
tier_win = Counter()
rows = []
total = winonly = 0
for appid, blob in apps:
    if b'.exe' not in blob: continue
    total += 1
    wo = (b'macos' not in blob and b'macOS' not in blob)
    if wo: winonly += 1
    # 反作弊
    acs = [n for n, sigs in ANTICHEAT.items() if any(s in blob for s in sigs)]
    # 引擎
    eng = tier = plan = None
    for name, sigs, p, t in ENGINES:
        if any(s in blob for s in sigs):
            eng, plan, tier = name, p, t; break
    eng = eng or '未识别'; tier = tier or 'UNKNOWN'
    eng_all[eng] += 1
    if wo:
        eng_win[eng] += 1
        tier_win[tier if not acs else 'BANNED'] += 1
        for a in acs:
            ac_win[a] += 1; ac_apps[a].append(appid)
        rows.append({'appid':appid,'eng':eng,'tier':tier,'ac':acs})
json.dump({'rows':rows}, open('/tmp/fullscan.json','w'))

print(f"含 .exe 的 app: {total}    Windows-only: {winonly}\n")
print("═══ 反作弊(Windows-only 中命中) ═══")
if ac_win:
    for a, n in ac_win.most_common():
        print(f"  {a:<16} {n:>5} 个    例: {', '.join(str(x) for x in ac_apps[a][:4])}")
    print(f"  合计 {sum(ac_win.values())} 处命中")
else:
    print("  零命中")
print()
print("═══ 引擎分布(Windows-only) ═══")
print(f"  {'引擎':<16}{'数量':>7}   {'原生方案'}")
print("  " + "─"*76)
TIER = {r[0]: r[3] for r in ENGINES}
for eng, n in eng_win.most_common():
    plan = next((r[2] for r in ENGINES if r[0]==eng), '需人工判断')
    mark = {'NATIVE':'可原生','MAYBE':'待验证','WINE':'走 Wine'}.get(TIER.get(eng,''), '未知')
    print(f"  {eng:<16}{n:>7}   [{mark}] {plan[:52]}")
print()
print("═══ 可行性汇总(Windows-only) ═══")
for t, label in [('NATIVE','可原生化'),('MAYBE','可能原生,需逐个验证'),('WINE','只能 Wine'),('UNKNOWN','引擎未识别'),('BANNED','带反作弊,禁止')]:
    print(f"  {label:<22}{tier_win.get(t,0):>6}")
