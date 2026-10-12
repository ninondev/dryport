#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""library-report.py — 把当前账号可访问的游戏列成一张可读的表：走哪条路、有没有封号风险、多大、怎么装。
用法: library-report.py [--html 输出.html] [--top N]
数据来自当前账号的本地目录快照，全部是本机已有的判定结果，不发请求。
"""
import json, os, sys, html, collections
import api
from paths import SN_GAMES

ROOT = str(SN_GAMES)
NATIVE = api.NATIVE_ROUTES
ORDER = ["steam", "mkxp", "nwjs", "love", "renpy", "godot", "krkr", "unity", "fna", "maybe", "vn", "wine", "unknown", "refuse"]
BLURB = {route: (label, "引擎特征不代表可玩性，需逐游戏实测。" if route in NATIVE else "")
         for route, label in api.ROUTE_LABEL.items()}
NON_NATIVE_NOTES = {
    "steam": "在官方客户端中安装或游玩；历史发行版未必兼容当前系统。",
    "renpy": "此候选的自动安装尚未接通；支持的固定版本另走 Ren'Py 原生路线。",
    "fna": "候选路线还没接通。",
    "maybe": "引擎认出来了，还没有对应的原生运行时。",
    "vn": "视觉小说的引擎要看文件清单才能定。",
    "wine": "默认克隆保留原件；显式接桥会替换工作副本 DLL 并保留备份。可玩性需逐游戏实测。",
    "unknown": "读取本机文件清单继续识别；可玩性与性能待验证。",
    "refuse": "风险检查拦截，不打包；具体原因见游戏详情。",
}
for route, note in NON_NATIVE_NOTES.items():
    BLURB[route] = (api.ROUTE_LABEL[route], note)
ORDER += [route for route in api.ROUTE_LABEL if route not in ORDER]

def load():
    context = api.catalog_context()
    if context["error"]:
        raise ValueError(context["error"])
    if context["status"] == "empty":
        raise ValueError("还没有游戏目录。运行 dryport survey 读取当前账号的游戏库。")
    if context["status"] == "legacy":
        print("这是历史路线快照，账号归属未确认；运行 dryport survey 更新。")
    fr = api.all_routes(context)
    ow = {str(r["appid"]): r for r in api.windows_rows(context)}
    for r in fr:
        o = ow.get(str(r["appid"]), {})
        r["engine"] = r.get("engine") or "未识别"
        r["confidence"] = r.get("confidence") or "待核实"
        r["size"] = o.get("dl", 0); r["depot"] = o.get("depot"); r["gid"] = o.get("gid")
    return fr

def main():
    rows = load()
    by = collections.defaultdict(list)
    for r in rows: by[r["route"]].append(r)
    print(f"当前游戏目录 {len(rows)} 个\n")
    print(f"{'路线':30s} {'个数':>5s}  说明")
    for k in ORDER:
        if not by.get(k): continue
        name, note = BLURB.get(k, (k, ""))
        print(f"{name:28s} {len(by[k]):5d}  {note}")
    nat = [r for r in rows if r["route"] in NATIVE]
    cert = [r for r in nat if r["confidence"] == "确证"]
    print(f"\n原生路线候选 {len(nat)} 个，其中引擎特征确证 {len(cert)} 个；可玩性仍需逐个实测。")
    print("确证名单里体积最小的几个，最适合拿来先试：")
    for r in sorted(cert, key=lambda r: r["size"])[:12]:
        if r["size"] < 2e6: continue
        print(f"   {r['appid']:>8}  {r['name'][:38]:38s} {r['size']/1e6:7.1f}MB  {BLURB[r['route']][0]}")
    if "--html" in sys.argv:
        out = sys.argv[sys.argv.index("--html") + 1]
        write_html(rows, by, out); print("\n网页写到", out)

def write_html(rows, by, path):
    esc = html.escape
    def card(k):
        name, note = BLURB.get(k, (k, ""))
        items = sorted(by[k], key=lambda r: -r["size"])
        body = "".join(
            f"<tr><td class=n>{esc(r['name'])}</td><td class=e>{esc(r['engine'])}</td>"
            f"<td class=s>{r['size']/1e6:.0f}</td><td class=c>{esc(r['confidence'])}</td>"
            f"<td class=a>{r['appid']}</td></tr>" for r in items[:400])
        return (f"<details{' open' if k in NATIVE else ''}><summary><b>{esc(name)}</b>"
                f"<span class=cnt>{len(items)}</span><span class=note>{esc(note)}</span></summary>"
                f"<div class=scroll><table><thead><tr><th>游戏<th>引擎<th>MB<th>把握<th>appid</thead>{body}</table></div>"
                + (f"<p class=more>只列了体积最大的 400 个，共 {len(items)} 个</p>" if len(items) > 400 else "")
                + "</details>")
    nat = sum(len(by[k]) for k in NATIVE if k in by)
    cert = sorted((r for r in rows if r["route"] in NATIVE and r["confidence"] == "确证" and 2e6 < r["size"] < 1.5e9),
                  key=lambda r: r["size"])[:8]
    start = "".join(
        f"<li><b>{esc(r['name'])}</b> <span class=mut>{r['size']/1e6:.0f} MB · {esc(BLURB[r['route']][0])}</span>"
        f"<code>dryport install {r['appid']}</code></li>" for r in cert)
    doc = f"""<title>我的游戏在 Mac 上能怎么跑</title>
<style>
:root{{--bg:#fbfaf8;--fg:#1b1a18;--mut:#6b6862;--line:#e4e0d8;--accent:#7a4a1e}}
@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16150f;--fg:#ece8df;--mut:#9a958a;--line:#2e2c24;--accent:#d8a35e}}}}
:root[data-theme=dark]{{--bg:#16150f;--fg:#ece8df;--mut:#9a958a;--line:#2e2c24;--accent:#d8a35e}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",sans-serif;margin:0;padding:32px 20px 80px;overflow-x:hidden}}
.wrap{{max-width:980px;margin:0 auto}}
h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}}
.sub{{color:var(--mut);margin:0 0 28px}}
.big{{display:flex;gap:28px;flex-wrap:wrap;margin:0 0 30px;padding:18px 20px;border:1px solid var(--line);border-radius:12px}}
.big{{gap:22px}}
.big div span{{display:block;font-size:30px;font-weight:600;color:var(--accent);line-height:1.1;font-variant-numeric:tabular-nums}}
.big div small{{color:var(--mut)}}
details{{border-top:1px solid var(--line);padding:10px 0}}
summary{{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:12px}}
summary::-webkit-details-marker{{display:none}}
.cnt{{color:var(--accent);font-variant-numeric:tabular-nums}}
.note{{color:var(--mut);font-size:13px}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{{width:100%;min-width:520px;border-collapse:collapse;margin:10px 0 4px;font-size:13.5px}}
.n{{max-width:22em}}
th{{text-align:left;color:var(--mut);font-weight:500;padding:4px 8px 4px 0;border-bottom:1px solid var(--line)}}
td{{padding:3px 8px 3px 0;border-bottom:1px solid var(--line)}}
.s,.a{{text-align:right;font-variant-numeric:tabular-nums;color:var(--mut)}}
.e,.c{{color:var(--mut)}}
.more{{color:var(--mut);font-size:13px}}
h2{{font-size:18px;margin:34px 0 2px;letter-spacing:-.01em}}
.mut{{color:var(--mut);font-size:13px}}
ol.start{{list-style:none;padding:0;margin:14px 0 0}}
ol.start li{{padding:10px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}}
ol.start code{{margin-left:auto;white-space:nowrap}}
.foot{{color:var(--mut);font-size:13px;margin-top:32px;border-top:1px solid var(--line);padding-top:14px}}
code{{background:color-mix(in srgb,var(--fg) 8%,transparent);padding:1px 5px;border-radius:4px}}
</style>
<div class=wrap>
<h1>我的游戏在 Mac 上能怎么跑</h1>
<p class=sub>路线来自本机 Steam 元数据与已有文件清单。引擎识别结果不代表该游戏已通过可玩性验证；旧快照的账号归属需通过 survey 更新。</p>
<div class=big>
  <div><span>{len(rows)}</span><small>可访问的游戏</small></div>
  <div><span>{nat}</span><small>原生路线候选</small></div>
  <div><span>{len(by.get('refuse',[]))}</span><small>有封号风险，不碰</small></div>
</div>
<h2>从这里开始</h2>
<p class=sub>已有引擎特征、体积较小的候选游戏。</p>
<ol class=start>{start}</ol>
<h2>全部路线</h2>
{''.join(card(k) for k in ORDER if by.get(k))}
<p class=foot>装一个游戏：<code>dryport install &lt;appid&gt;</code>。「把握」一列里，确证表示文件清单里有只可能属于该引擎的特征；推测表示线索强但会撞车，下载后才能定。</p>
</div>"""
    open(path, "w").write(doc)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"未生成报告：{error}", file=sys.stderr)
        sys.exit(1)
