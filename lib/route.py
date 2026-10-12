#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""route.py — 不下载任何游戏本体，只用 Steam 本地 appinfo 缓存判断每个 Windows 专属游戏走哪条路。

线索全部来自 appinfo.vdf：云存档规则（root/path/pattern）、启动 exe 名、商店分类。
这些字段 Steam 本来就缓存在本机，读它们不产生任何网络请求，也碰不到游戏文件。

用法:
  route.py <appid> ...        单查
  route.py --all [--json 文件] 全库统计
"""
import json, os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk

# (引擎, 置信度, 路线, 判据) —— 顺序即优先级
# 置信度「确证」= 该特征只可能由这个引擎产生；「推测」= 强相关但可能撞车
SAVE_FP = [
    ('RPG Maker MV/MZ 候选', '推测', 'nwjs',  lambda r, p, pat, ex: pat.endswith('.rpgsave') or p.split('/')[0] == 'www'),
    ('RPG Maker VX Ace', '确证', 'mkxp', lambda r, p, pat, ex: pat.endswith('.rvdata2')),
    ('RPG Maker VX',     '确证', 'mkxp', lambda r, p, pat, ex: pat.endswith('.rvdata')),
    ('RPG Maker XP',     '确证', 'mkxp', lambda r, p, pat, ex: pat.endswith('.rxdata')),
    ("Ren'Py",           '确证', 'renpy',lambda r, p, pat, ex: 'renpy' in p),
    ('Godot',            '确证', 'godot',lambda r, p, pat, ex: 'godot' in p),
    # 云存档路径分不出 Mono 还是 IL2CPP：两者都把存档写在 %USERPROFILE%\AppData\LocalLow，
    # Easy Save 3 的 .es3 也是两边都用。而这两者结局相反，Mono 能换 mac 播放器，IL2CPP 只能走 Wine。
    # 2026-09-13 拿 3047 个有文件清单确证引擎的游戏当真值量过：这条判 Unity 的准确率 68.9%，
    # 286 个对不上的里 280 个是 IL2CPP。所以只给候选，不给原生路线名，等下载后由文件证据定夺。
    ('Unity（后端待定：IL2CPP 走 Wine，Mono 才能原生）', '推测', 'maybe',
     lambda r, p, pat, ex: pat.endswith('.es3') or r == 'winappdatalocallow'),
]
EXE_FP = [
    ('EasyAntiCheat',   '确证', 'refuse',re.compile(r'^start_protected_game\.exe$')),
    ('Unreal Engine',   '确证', 'wine',  re.compile(r'-(win64|win32|wingdk|winarm64)-(shipping|test|debuggame)\.exe$|-shipping\.exe$')),
    ('NW.js',          '推测', 'nwjs',  re.compile(r'^nw\.exe$')),
    ('GameMaker',       '推测', 'maybe', re.compile(r'^(runner|gamemaker)\.exe$')),
]
VN_TAG = 3799   # 题材标签只用于展示候选，不能确定引擎。
ROUTE_NAME = {'renpy-native': "Ren'Py 原生（安装时校验版本）",
              'vn': "视觉小说（引擎待确认）", 'nwjs': 'NW.js 原生换壳候选', 'mkxp': 'mkxp-z 原生候选', 'renpy': "Ren'Py 候选（未接通）",
              'godot': 'Godot 官方 mac 模板', 'unity': 'Unity macOS 播放器（架构依版本）+ 着色器重定向',
              'wine': 'Wine + DXMT 候选', 'fna': 'FNA/MonoGame 候选（未接通）',
              'maybe': '引擎候选（未接通）', 'refuse': '不碰（风险拦截）', 'unknown': '待识别',
              'steam': 'Steam 官方 macOS 版', 'love': 'LÖVE 原包直读（版本与扩展待确认）',
              'webkit': 'WebKit 原生（安装时校验版本）'}

def classify(app):
    c = app.get('common') or {}
    ufs = (app.get('ufs') or {}).get('savefiles') or {}
    ents = []
    for v in ufs.values():
        if isinstance(v, dict):
            ents.append(((v.get('root') or '').lower(),
                         (v.get('path') or '').replace('\\', '/').lower(),
                         (v.get('pattern') or '').lower()))
    exes = []
    for v in ((app.get('config') or {}).get('launch') or {}).values():
        if isinstance(v, dict) and v.get('executable'):
            exes.append(v['executable'].replace('\\', '/').split('/')[-1].lower())
    for name, conf, route, rx in EXE_FP:
        for e in exes:
            if rx.search(e): return name, conf, route, f'启动程序 {e}'
    for name, conf, route, fn in SAVE_FP:
        for r, p, pat in ents:
            if fn(r, p, pat, exes): return name, conf, route, f'云存档规则 {r or "-"}/{p or "-"} {pat}'
    if any(str(value) == str(VN_TAG) for value in (c.get('store_tags') or {}).values()):
        return '视觉小说（引擎待定）', '推测', 'vn', '商店标签 Visual Novel'
    return '未识别', '无', 'unknown', '本地 appinfo 里没有可判定的特征'

def main():
    args = sys.argv[1:]
    if not args: print(__doc__); return 2
    apps = risk.load_appinfo(); ac = risk.load_ac()
    def one(appid):
        app = apps.get(appid) or {}
        eng, conf, route, why = classify(app)
        rk = risk.assess(appid, app, ac)
        if rk['verdict'] == risk.REFUSE: route, why2 = 'refuse', rk['why'][0]
        elif rk['verdict'] == risk.UNKNOWN: route, why2 = 'unknown', rk['why'][0]
        elif rk['verdict'] == risk.WINE_ONLY and route not in ('refuse',): route, why2 = 'wine', rk['why'][0]
        else: why2 = ''
        return dict(appid=appid, name=rk['name'], engine=eng, confidence=conf,
                    route=route, route_name=ROUTE_NAME[route], why=why,
                    risk=rk['verdict'], risk_why=why2)
    if args[0] == '--all':
        out = []
        for appid, app in apps.items():
            c = app.get('common') or {}
            if c.get('type', '').lower() != 'game': continue
            o = c.get('oslist', '')
            if 'windows' not in o or 'macos' in o: continue
            out.append(one(appid))
        print(f'Windows 专属游戏 {len(out)} 个')
        by = collections.Counter(o['route_name'] for o in out)
        for k, v in by.most_common(): print(f'  {k}: {v}')
        native = [o for o in out if o['route'] in ('nwjs', 'mkxp', 'godot', 'unity')]
        cert = [o for o in native if o['confidence'] == '确证']
        print(f'\n原生运行时候选 {len(native)} 个，其中文件或元资料特征确证 {len(cert)} 个；尚不代表可玩验证')
        print('确证的引擎分布:', dict(collections.Counter(o["engine"] for o in cert).most_common()))
        if '--json' in args:
            p = args[args.index('--json') + 1]
            json.dump(out, open(p, 'w'), ensure_ascii=False, indent=1); print('清单写到', p)
        return 0
    for a in args:
        o = one(int(a))
        print(f"{o['appid']} {o['name']}\n   引擎 {o['engine']}（{o['confidence']}）依据: {o['why']}")
        print(f"   路线 {o['route_name']}   风险 {o['risk']}" + (f"（{o['risk_why']}）" if o['risk_why'] else ''))
    return 0

if __name__ == '__main__': sys.exit(main())
