#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""risk.py — 逐个游戏判定「改包会不会惹上封号风险」，给证据不给口号。

用法:
  risk.py <appid> [appid...]        单查，人话输出
  risk.py --all [--json out.json]   检查资源，出统计与清单
  risk.py --gate <appid>            退出码 0=可改包 1=联机不改包 2=反作弊拒绝 3=资料不足或检查失败

判据（按严重程度）:
  1. appinfo 里 category_8 = Valve Anti-Cheat enabled  → 一律不碰
  2. AreWeAntiCheatYet 里有第三方反作弊               → 一律不碰，不按兼容状态放行
  3. 有联机对战/合作（category 1/9/20/27/36/38/47/48/49）→ 不改包，服务端可能校验文件
  4. 以上都没有，且是单机                              → 可原生改包
改包动的是游戏自己的资源文件，所以任何会被服务端或反作弊校验文件的游戏都排除在改包之外。

**别拿 AreWeAntiCheatYet 的 status 当「反作弊严不严格」用（2026-09-12 实测，看清楚再动这里）**：
那个字段的语义是「这个反作弊在 Linux/Proton 下跑不跑得起来」，不是「改包安不安全」。
Supported / Running 意味着反作弊**正常在工作**，改包风险最高；Broken 反而是它跑不起来。
所以「只拒绝 Broken/Denied」会放行反作弊正在工作的联机游戏。
联机与反作弊分别检查；单机也不能绕过反作弊拒绝。
"""
import json, os, struct, sys
from collections import Counter
from paths import SN_RUNTIMES, STEAM

APPINFO = STEAM / "appcache/appinfo.vdf"
ACDB = SN_RUNTIMES / "anticheat-db.json"

VAC_CAT = 8
ONLINE_CATS = {1: "多人", 9: "合作", 20: "MMO", 27: "跨平台联机", 36: "在线对战",
               38: "在线合作", 47: "局域网对战", 48: "局域网合作", 49: "对战"}
SINGLE_CAT = 2

# ── binary VDF ────────────────────────────────────────────────────────────────
def _cstr(d, p):
    e = d.index(b"\x00", p)
    return d[p:e].decode("utf8", "replace"), e + 1

def parse_bvdf(d, p=0):
    out = {}
    while p < len(d):
        t = d[p]; p += 1
        if t == 0x08: return out, p
        k, p = _cstr(d, p)
        if t == 0x00:
            v, p = parse_bvdf(d, p)
        elif t == 0x01:
            v, p = _cstr(d, p)
        elif t == 0x02:
            v = struct.unpack_from("<i", d, p)[0]; p += 4
        elif t == 0x07:
            v = struct.unpack_from("<Q", d, p)[0]; p += 8
        else:
            raise ValueError(f"unknown vdf type {t:#x} at {p}")
        out[k] = v
    raise ValueError("appinfo 节点被截断，缺少结束标记")

def load_appinfo(path=APPINFO, *, appids=None):
    """按需解码元资料；仍扫描全部记录边界，损坏的尾部不能被选择读取掩盖。

    appids=None 保持全量接口；空集合只检查文件结构，不解码游戏节点。
    节点本身无法解码时沿用全量读取的行为：不返回该条资料，由风险闸判为未知。
    """
    wanted = None if appids is None else frozenset(appids)
    with open(path, "rb") as stream:
        d = stream.read()
    if len(d) < 8:
        raise ValueError("appinfo 文件头被截断")
    magic = struct.unpack_from("<I", d, 0)[0]
    if magic not in (0x07564427, 0x07564428, 0x07564429):
        raise ValueError(f"不支持的 appinfo 版本 {magic:#x}: {path}")
    pos = 16 if magic >= 0x07564429 else 8
    end = len(d)
    if magic >= 0x07564429:
        if len(d) < 16:
            raise ValueError("appinfo 文件头被截断")
        # v29+: a string table sits at the end, offset stored at bytes 8..16
        tbl_off = struct.unpack_from("<q", d, 8)[0]
        if tbl_off < 20 or tbl_off + 4 > len(d):
            raise ValueError("appinfo 字符串表位置无效")
        n = struct.unpack_from("<I", d, tbl_off)[0]
        if n > len(d) - tbl_off - 4:
            raise ValueError("appinfo 字符串表被截断")
        end = tbl_off
        strings = []; q = tbl_off + 4
        for _ in range(n):
            s, q = _cstr(d, q); strings.append(s)
    else:
        strings = None
    apps = {}
    terminated = False
    # size 后的定长元数据头；v28 起增加 binary SHA，v29 沿用。
    hdr = 4 + 4 + 8 + 20 + 4 + (20 if magic >= 0x07564428 else 0)
    while pos + 4 <= end:
        appid = struct.unpack_from("<I", d, pos)[0]
        if appid == 0:
            terminated = True
            break
        if pos + 8 > end:
            raise ValueError("appinfo 记录头被截断")
        size = struct.unpack_from("<I", d, pos + 4)[0]
        if pos + 8 + size > end:
            raise ValueError("appinfo 记录被截断")
        if size <= hdr:
            raise ValueError("appinfo 记录长度无效")
        if wanted is not None and appid not in wanted:
            pos += 8 + size
            continue
        body = d[pos + 8 + hdr: pos + 8 + size]
        try:
            tree, _ = (parse_bvdf_strtbl(body, strings) if strings is not None else parse_bvdf(body))
            # the whole app is wrapped in one outer node (usually named "appinfo")
            if len(tree) == 1:
                inner = next(iter(tree.values()))
                if isinstance(inner, dict) and ("common" in inner or "appid" in inner): tree = inner
            apps[appid] = tree
        except Exception:
            pass
        pos += 8 + size
    if not terminated:
        raise ValueError("appinfo 文件被截断，缺少结束标记")
    return apps

def parse_bvdf_strtbl(d, strings, p=0):
    """v29 stores keys as uint32 indexes into a trailing string table."""
    out = {}
    while p < len(d):
        t = d[p]; p += 1
        if t == 0x08: return out, p
        ki = struct.unpack_from("<I", d, p)[0]; p += 4
        if ki >= len(strings):
            raise ValueError("appinfo 字符串索引越界")
        k = strings[ki]
        if t == 0x00:
            v, p = parse_bvdf_strtbl(d, strings, p)
        elif t == 0x01:
            v, p = _cstr(d, p)
        elif t == 0x02:
            v = struct.unpack_from("<i", d, p)[0]; p += 4
        elif t == 0x07:
            v = struct.unpack_from("<Q", d, p)[0]; p += 8
        else:
            raise ValueError(f"unknown vdf type {t:#x}")
        out[k] = v
    raise ValueError("appinfo 节点被截断，缺少结束标记")

# ── anti-cheat database ───────────────────────────────────────────────────────
def load_ac(path=None):
    path = ACDB if path is None else path
    with open(path, encoding="utf-8") as stream:
        rows = json.load(stream)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"反作弊数据库为空或格式错误: {path}")
    idx = {}
    for g in rows:
        if not isinstance(g, dict) or not isinstance(g.get("anticheats"), list):
            raise ValueError(f"反作弊数据库记录格式错误: {path}")
        si = g.get("storeIds")
        if not isinstance(si, dict): continue
        v = si.get("steam"); vals = v if isinstance(v, list) else ([v] if v else [])
        for x in vals:
            try: idx[int(x)] = g
            except (TypeError, ValueError): pass
    if not idx:
        raise ValueError(f"反作弊数据库没有可用 Steam 记录: {path}")
    return idx

# ── verdict ───────────────────────────────────────────────────────────────────
REPACK, WINE_ONLY, REFUSE = "可原生改包", "只能原样跑(Wine)", "一律不碰"
UNKNOWN = "风险待核实"
EXIT_CODES = {REPACK: 0, WINE_ONLY: 1, REFUSE: 2, UNKNOWN: 3}

def app_platforms(app):
    """(平台集合, 证据)。证据：'oslist' = common.oslist 明说；'launch' = 启动项 config.oslist 并集；'depot' = 内容包 config.oslist 并集；
    'default' = type 是 game、有内容包，但哪儿都没标——Steam 自己的惯例是省略即 Windows 专属（2012 年前的老游戏常见；2026-09-13 普查
    本机 245 款家庭共享游戏里 172 款如此，等 Steam「加载」永远补不上）；'none' = 判不出。明说了却不含 windows 的照旧不算。"""
    app = app if isinstance(app, dict) else {}
    common = app.get("common") if isinstance(app.get("common"), dict) else {}
    raw = common.get("oslist")
    if isinstance(raw, str) and {p.strip().lower() for p in raw.split(",") if p.strip()}:
        return {p.strip().lower() for p in raw.split(",") if p.strip()}, "oslist"
    def union(entries):
        found = set()
        for entry in (entries.values() if isinstance(entries, dict) else ()):
            cfg = entry.get("config") if isinstance(entry, dict) else None
            o = cfg.get("oslist") if isinstance(cfg, dict) else None
            if isinstance(o, str):
                found |= {p.strip().lower() for p in o.split(",") if p.strip()}
        return found
    config = app.get("config") if isinstance(app.get("config"), dict) else {}
    found = union(config.get("launch"))
    if found:
        return found, "launch"
    depots = app.get("depots") if isinstance(app.get("depots"), dict) else {}
    found = union(depots)
    if found:
        return found, "depot"
    has_content = any(isinstance(d, dict) and isinstance(d.get("manifests"), dict) for d in depots.values())
    if str(common.get("type") or "").lower() == "game" and has_content:
        return {"windows"}, "default"
    return set(), "none"


def assess(appid, app, ac):
    common = (app or {}).get("common", {}) or {}
    if not isinstance(common, dict):
        common = {}
    name = common.get("name", f"app {appid}")
    category = common.get("category") or {}
    cats = {int(k[9:]) for k, v in category.items()
            if k.startswith("category_") and k[9:].isdigit() and str(v) == "1"} if isinstance(category, dict) else set()
    why = []
    verdict = REPACK
    if VAC_CAT in cats:
        verdict = REFUSE; why.append("Steam 商店标着 Valve Anti-Cheat enabled")
    g = ac.get(appid)
    if g:
        st = g.get("status", "?"); acs = ", ".join(g.get("anticheats") or []) or "未注明"
        verdict = REFUSE
        why.append(f"第三方反作弊 {acs}（状态 {st}），本工具不打包带反作弊的游戏")
    online = sorted(ONLINE_CATS[c] for c in cats & set(ONLINE_CATS))
    if online:
        if verdict == REPACK: verdict = WINE_ONLY
        why.append("带联机：" + "、".join(online))
    if verdict == REPACK and SINGLE_CAT not in cats:
        verdict = UNKNOWN
        why.append("Steam 本地资料缺少单机分类；请在已登录的 Steam 中刷新游戏资料后重试")
    # 这是 Windows 游戏包装入口的准备条件；单机分类不能替代平台证据，但平台证据可以来自 Steam 资料的启动项、内容包，
    # 或 Steam 自己「省略即 Windows」的惯例（见 app_platforms）。
    platforms, platform_evidence = app_platforms(app)
    if verdict != REFUSE:
        if "macos" in platforms:
            verdict = UNKNOWN
            why.append("Steam 已提供 macOS 版本，请直接从 Steam 安装原生版")
        elif "windows" not in platforms:
            verdict = UNKNOWN
            why.append("Steam 本地资料没有列出 Windows 版本，启动项与内容包也没标平台；本入口只准备 Windows 游戏")
        elif platform_evidence == "default":
            why.append("Steam 资料未标平台，按 Steam 惯例视为 Windows 专属（老游戏常见）")
        elif platform_evidence in ("launch", "depot"):
            why.append("平台来自 Steam 的启动项或内容包资料（游戏总表里没标）")
    if verdict == REPACK:
        why.append("本地资料标为单机，未发现反作弊或联机标记；这不是兼容性或账号安全保证")
    return dict(appid=appid, name=name, verdict=verdict, why=why,
                oslist=",".join(sorted(platforms)), platform_evidence=platform_evidence, type=common.get("type", ""),
                single=SINGLE_CAT in cats)

def main():
    args = sys.argv[1:]
    if not args: print(__doc__); return 2
    requested = None
    if args[0] in ("--gate", "--gate-json"):
        if len(args) != 2 or not args[1].isdigit() or int(args[1]) <= 0:
            print("用法: risk.py --gate <正整数 appid>", file=sys.stderr)
            return 3
        requested = {int(args[1])}
    try:
        apps = load_appinfo(appids=requested) if requested is not None else load_appinfo()
        ac = load_ac()
    except (OSError, ValueError, IndexError, struct.error) as exc:
        print(f"风险检查未完成: {exc}", file=sys.stderr)
        print("请启动并登录 Steam 刷新游戏资料，运行 tools/update-anticheat-db.sh 更新反作弊数据库后重试。", file=sys.stderr)
        return 3
    if args[0] in ("--gate", "--gate-json"):
        r = assess(int(args[1]), apps.get(int(args[1])), ac)
        code = EXIT_CODES[r["verdict"]]
        if args[0] == "--gate-json":
            print(json.dumps({"protocol": 1, "code": code, **r}, ensure_ascii=False))
            return code
        print(f"{r['name']}: {r['verdict']}")
        for w in r["why"]: print("  -", w)
        return code
    if args[0] == "--all":
        rows = []
        for appid, app in apps.items():
            c = (app.get("common") or {})
            if c.get("type", "").lower() != "game": continue
            os_ = c.get("oslist", "")
            if "windows" not in os_ or "macos" in os_: continue
            rows.append(assess(appid, app, ac))
        cnt = Counter(r["verdict"] for r in rows)
        print(f"appinfo 里 Windows 专属游戏 {len(rows)} 个")
        for k in (REPACK, WINE_ONLY, REFUSE, UNKNOWN): print(f"  {k}: {cnt.get(k,0)}")
        reasons = Counter(w.split("：")[0].split("（")[0] for r in rows if r["verdict"] != REPACK for w in r["why"])
        print("排除原因分布:", dict(reasons.most_common(6)))
        if "--json" in args:
            out = args[args.index("--json") + 1]
            json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
            print("清单写到", out)
        return 0
    for a in args:
        r = assess(int(a), apps.get(int(a)), ac)
        print(f"{r['appid']} {r['name']}  [{r['oslist'] or '平台未标'}]  → {r['verdict']}")
        for w in r["why"]: print("   -", w)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # 1 专用于已确认的联机游戏，不能让解析异常被显式联机开关误放行。
        print(f"风险检查未完成: {exc}", file=sys.stderr)
        sys.exit(3)
