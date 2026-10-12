#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""批量自动冒烟：从目录挑候选（按路线、体积升序、Steam 暂存区已有的优先），逐款调 tools/smoke-run.py，记预算台账，出报告。

  tools/smoke-batch.py --routes nwjs,godot,krkr,mkxp,love,wine,unknown,maybe --max-games 30 --max-download-gb 12
  tools/smoke-batch.py --appids 502230,1146510 --dwell 60          # 指定几款
  tools/smoke-batch.py --routes nwjs --dry-run                      # 只列候选，不跑

候选过滤（每条都有出处，见 docs/计划-2026-09-14.md）：
  risk == 可原生改包（联机的「只能原样跑(Wine)」闸会拒，不放行）；oslist 含 windows；路线在白名单；
  排除已装/已下载（installed.json 与磁盘扫描）、已归档、本机已有该 appid 状态（Wine/Ren'Py/WebKit/NW.js/LÖVE）、
  名字与 $SN_GAMES 现有目录或 .app 同名、planDepots 抛错（真下载也会抛）、.smoke 里已有结果（--retry 才重跑）。
预算：台账 $SN_GAMES/.smoke/ledger.json 记每款新下载字节（暂存区已有的记 0），超 --max-download-gb 停；
  磁盘可用 < 15 GiB 停；Steam 或调试口不可用、连续 3 次基础设施失败暂停整批。
整批用 caffeinate 防睡眠；一次只跑一款；每款结束由 smoke-run 复核残留。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_HOME, SN_RUNTIMES, SN_GAMES, STEAM   # noqa: E402

SMOKE_DIR = SN_GAMES / ".smoke"
LEDGER = SMOKE_DIR / "ledger.json"
REPORT = SMOKE_DIR / "smoke-report.md"
STAGING = Path(STEAM) / "Steam.AppBundle/Steam/Contents/MacOS/steamapps/content"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "dryport"
ROUTE_WHITELIST = {"nwjs", "mkxp", "godot", "krkr", "love", "unity", "wine", "maybe", "unknown"}
REPACK = "可原生改包"


def log(msg):
    print(f"[batch {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def safe_name(name):
    n = re.sub(r"[/:\x00]", " ", name).strip().strip(".")
    return re.sub(r"\s+", " ", n)


def sizes(language=None):
    ctx_file = SN_GAMES / ".dryport-catalog" / "current" / "windows-library.json"
    cmd = ["node", str(SN_HOME / "tools" / "smoke-sizes.mjs"), str(ctx_file)] + ([language] if language else [])
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
    result = {}
    for line in out.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        result[r["appid"]] = r
    return result


def staged_appids():
    if not STAGING.is_dir():
        return set()
    return {d.name[4:] for d in STAGING.iterdir() if d.name.startswith("app_") and d.is_dir()}


def smoke_results():
    out = {}
    if SMOKE_DIR.is_dir():
        for f in SMOKE_DIR.glob("*.json"):
            if f.name in ("ledger.json",):
                continue
            try:
                d = json.loads(f.read_text())
                out[str(d.get("appid") or f.stem)] = d.get("latest") or {}
            except Exception:
                pass
    return out


def load_ledger():
    if LEDGER.is_file():
        try:
            return json.loads(LEDGER.read_text())
        except Exception:
            pass
    return {"protocol": 1, "downloaded_bytes": 0, "runs": []}


def save_ledger(led):
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=1))
    os.replace(tmp, LEDGER)


def candidates(routes, size_map, retry=False, only_staged=False, max_size=None):
    import api
    import uninstall_purge
    ctx = api.catalog_context()
    rows = api.all_routes(ctx)
    reg = api.scan_installed(rows)
    archived = uninstall_purge.archived_appids()
    staged = staged_appids()
    done = smoke_results()
    existing = {p.name.lower() for p in SN_GAMES.iterdir()} if SN_GAMES.is_dir() else set()
    out, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for g in rows:
        appid = str(g["appid"])
        route = g.get("route")
        if route not in routes:
            continue
        if g.get("risk") != REPACK:
            skip(f"risk={g.get('risk')}"); continue
        oslist = g.get("oslist") or []
        if "windows" not in [str(x).lower() for x in (oslist if isinstance(oslist, list) else [oslist])]:
            skip("oslist 无 windows"); continue
        e = reg.get(appid) or {}
        if e.get("app") or e.get("folder"):
            skip("已装/已下载"); continue
        if appid in archived:
            skip("已归档"); continue
        if not retry and appid in done and done[appid].get("verdict"):
            skip("已有冒烟结果"); continue
        s = size_map.get(appid)
        if not s or "error" not in s and "size" not in s:
            skip("无体积资料"); continue
        if "error" in s:
            skip("选包会失败"); continue
        if max_size and s["size"] > max_size:
            skip("超过体积上限"); continue
        name = safe_name(g["name"])
        if name.lower() in existing or f"{name.lower()}.app" in existing:
            skip("与现有目录/app 同名"); continue
        plan = uninstall_purge.plan(appid, {})
        if plan["items"]:
            skip("本机已有该 appid 状态"); continue
        if (APP_SUPPORT / "nwjs" / appid).exists() or (APP_SUPPORT / "love" / appid).exists():
            skip("本机已有 NW.js/LÖVE 状态"); continue
        is_staged = appid in staged
        if only_staged and not is_staged:
            continue
        out.append({"appid": appid, "name": g["name"], "route": route, "engine": g.get("engine"), "size": s["size"],
                    "dl": s.get("dl") or 0, "staged": is_staged, "ownership": g.get("ownership"), "confidence": g.get("confidence")})
    out.sort(key=lambda c: (not c["staged"], c["size"]))
    return out, skipped


def write_report(led):
    """报告按 $SN_GAMES/.smoke/*.json 的最新结果出（每款一份，smoke-run 自己写），台账只提供预算数字；批次中断也不丢。"""
    by_app = {r["appid"]: r for r in led.get("runs", [])}
    runs = []
    for appid, latest in sorted(smoke_results().items(), key=lambda kv: kv[1].get("at") or ""):
        r = dict(by_app.get(appid) or {})
        r.update({"appid": appid, **{k: latest.get(k) for k in ("verdict", "flags", "at", "route", "engine", "evidence", "git")}})
        try:
            res = json.loads((Path(latest["evidence"]) / "result.json").read_text())
            r.setdefault("name", res.get("name")); r["detected_engine"] = res.get("detected_engine")
            r["window_at_seconds"] = res.get("window_at_seconds"); r["seconds_total"] = res.get("seconds_total")
        except Exception:
            pass
        runs.append(r)
    lines = ["# 自动冒烟报告", "",
             f"取数时间 {time.strftime('%Y-%m-%d %H:%M %Z')}；口径：verdict=alive 只表示出窗口、活满观察期、无崩溃信号，不等于可玩，更不等于达到 Windows 水平。",
             f"累计新下载 {led.get('downloaded_bytes', 0) / 1e9:.2f} GB（暂存区已有的记 0）。", ""]
    by = {}
    for r in runs:
        key = (r.get("route") or "?", r.get("engine") or "?")
        d = by.setdefault(key, {"n": 0, "alive": 0, "verdicts": {}})
        d["n"] += 1
        v = r.get("verdict") or "?"
        d["verdicts"][v] = d["verdicts"].get(v, 0) + 1
        if v == "alive":
            d["alive"] += 1
    lines += ["| 目录路线 | 引擎 | 款数 | alive | 其它 verdict |", "|---|---|---:|---:|---|"]
    for (route, engine), d in sorted(by.items()):
        others = ", ".join(f"{k} {v}" for k, v in sorted(d["verdicts"].items()) if k != "alive")
        lines.append(f"| {route} | {engine} | {d['n']} | {d['alive']} | {others} |")
    lines += ["", "| appid | 游戏 | 目录路线 | 引擎 | 实际引擎 | verdict | flags | 出窗口 s | 用时 s | 新下载 MB | 证据 |", "|---|---|---|---|---|---|---|---:|---:|---:|---|"]
    for r in runs:
        lines.append(f"| {r.get('appid')} | {r.get('name')} | {r.get('route')} | {r.get('engine')} | {r.get('detected_engine') or ''} | "
                     f"{r.get('verdict')} | {' '.join(r.get('flags') or [])} | {r.get('window_at_seconds') or ''} | {r.get('seconds_total') or ''} | "
                     f"{(r.get('new_download_bytes') or 0) / 1e6:.0f} | {r.get('evidence') or ''} |")
    REPORT.write_text("\n".join(lines) + "\n")


def infra_ok():
    import api
    return api.steam_running() and api.debug_port_enabled()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--routes", default="nwjs,godot,krkr,mkxp,love,wine,unknown,maybe")
    ap.add_argument("--appids", help="逗号分隔，指定就不按目录挑")
    ap.add_argument("--max-games", type=int, default=30)
    ap.add_argument("--max-download-gb", type=float, default=12)
    ap.add_argument("--max-size-mb", type=float, default=3000, help="单款安装体积上限")
    ap.add_argument("--dwell", type=float, default=60)
    ap.add_argument("--only-staged", action="store_true")
    ap.add_argument("--retry", action="store_true", help="已有结果的也重跑")
    ap.add_argument("--input", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--language", help="选包语言，默认按 depot-plan 的规则（english 回退）")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VAL", help="原样传给每款 smoke-run（对照实验用）")
    a = ap.parse_args(argv)
    routes = {r.strip() for r in a.routes.split(",") if r.strip()}
    if a.appids:
        routes = set(ROUTE_WHITELIST)      # 指定 appid 时不按路线过滤（11:23 重跑 8 款 Unity 时被默认路线表挡掉过）
    bad = routes - ROUTE_WHITELIST
    if bad:
        sys.exit(f"路线不在白名单：{bad}（白名单 {sorted(ROUTE_WHITELIST)}）")
    size_map = sizes(a.language)
    cands, skipped = candidates(routes, size_map, retry=a.retry, only_staged=a.only_staged, max_size=a.max_size_mb * 1e6)
    if a.appids:
        want = [x.strip() for x in a.appids.split(",") if x.strip()]
        by = {c["appid"]: c for c in cands}
        missing = [w for w in want if w not in by]
        if missing:
            log(f"指定的 appid 不在候选里（被过滤或不在目录）：{missing}")
        cands = [by[w] for w in want if w in by]
    log(f"候选 {len(cands)} 款；被过滤：{dict(sorted(skipped.items(), key=lambda x: -x[1])[:12])}")
    for c in cands[:a.max_games]:
        log(f"  {c['appid']:>8} {c['size'] / 1e6:7.0f} MB {'暂存' if c['staged'] else '   '} {c['route']:8} {str(c['engine'])[:22]:22} {c['name'][:40]}")
    if a.dry_run:
        return 0
    led = load_ledger()
    caff = subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])
    infra_fail = 0
    ran = 0
    try:
        for c in cands:
            if ran >= a.max_games:
                break
            if (SMOKE_DIR / "STOP").exists():
                log("发现 .smoke/STOP，跑完上一款后停（删掉它再启动即续跑）"); break
            free = os.statvfs(str(SN_GAMES)).f_bavail * os.statvfs(str(SN_GAMES)).f_frsize
            if free < 15 * 2**30:
                log(f"磁盘可用 {free / 2**30:.1f} GiB < 15 GiB，停"); break
            new_bytes = 0 if c["staged"] else c["dl"]
            if led["downloaded_bytes"] + new_bytes > a.max_download_gb * 1e9:
                log(f"预算：已用 {led['downloaded_bytes'] / 1e9:.2f} GB + {new_bytes / 1e9:.2f} GB 超 {a.max_download_gb} GB，跳过 {c['name']}"); continue
            if not infra_ok():
                log("Steam 或调试口不可用，等 60 秒再看"); time.sleep(60)
                if not infra_ok():
                    log("仍不可用，停整批"); break
            log(f"=== {ran + 1}/{min(a.max_games, len(cands))} {c['appid']} {c['name']} ({c['route']}/{c['engine']}, {c['size'] / 1e6:.0f} MB, {'暂存' if c['staged'] else '新下载'}) ===")
            cmd = [sys.executable, "-B", str(SN_HOME / "tools" / "smoke-run.py"), c["appid"], "--dwell", str(a.dwell)]
            if a.input:
                cmd.append("--input")
            for kv in a.env:
                cmd += ["--env", kv]
            t0 = time.time()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600)
                out = r.stdout
            except subprocess.TimeoutExpired as error:
                out = (error.stdout or "") + "\n[batch] smoke-run 超时 3600 秒"
                r = None
            last = None
            for line in reversed(out.splitlines()):
                if line.startswith("{"):
                    try:
                        last = json.loads(line); break
                    except ValueError:
                        pass
            entry = {"appid": c["appid"], "name": c["name"], "route": c["route"], "engine": c["engine"], "staged": c["staged"],
                     "size": c["size"], "new_download_bytes": new_bytes, "at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                     "batch_seconds": round(time.time() - t0, 1), "rc": r.returncode if r else 124}
            if last:
                entry.update({k: last.get(k) for k in ("verdict", "flags", "window_at_seconds", "seconds_total", "evidence")})
                try:
                    res = json.loads((Path(last["evidence"]) / "result.json").read_text())
                    entry["detected_engine"] = res.get("detected_engine")
                    entry["disk_delta_bytes"] = res.get("disk_delta_bytes")
                except Exception:
                    pass
            else:
                entry["verdict"] = "batch_error"
                entry["stderr"] = (r.stderr if r else "")[-800:]
                log(f"smoke-run 没给结果：{(r.stderr if r else '')[-300:]}")
            v = entry.get("verdict") or ""
            if v.startswith("install_failed:download") or v == "batch_error":
                infra_fail += 1
            else:
                infra_fail = 0
            if v not in ("install_failed:download", "batch_error"):
                led["downloaded_bytes"] += new_bytes
            led["runs"].append(entry)
            save_ledger(led)
            write_report(led)
            ran += 1
            log(f"→ {entry.get('verdict')} {entry.get('flags')}  累计新下载 {led['downloaded_bytes'] / 1e9:.2f} GB")
            if infra_fail >= 3:
                log("连续 3 次基础设施失败，暂停整批"); break
    finally:
        try:
            caff.terminate()
        except Exception:
            pass
    write_report(led)
    log(f"本批 {ran} 款，报告 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
