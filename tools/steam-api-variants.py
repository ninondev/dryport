#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""全库 steam_api64.dll 变体普查（口径：depot 清单里的 SHA-1，不下载、不联网），并按仓库现有虚表表算覆盖下限。

  tools/steam-api-variants.py [--json] [--known 目录]

  --known 目录   放着已取到的变体 DLL（<sha1>.dll）或 tools/steam-vtable-map.py 的输出 <sha1>.json；用来把变体映射到接口版本串，
                 与 lib/steambridge/vtables/*.json 比对得出「该变体的版本串是否都有表」。默认 ~/Games/.sdk-variants/dlls。
输出：Wine 路线带 steam_api64.dll 的游戏数、变体数、前 N 变体的占比、每个变体最小的已拥有游戏（用来取 DLL），以及覆盖下限。
逐游戏名字属个人库表，只在 --json 里给 appid，不给游戏名。
"""
import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_GAMES, STEAM

ROW = re.compile(r"^\s*([\d,]+)\s+\d+\s+([0-9a-fA-F]{40})\s+\d+\s+(.+?)\s*$")
DLL = re.compile(r"(^|[\\/])steam_api64\.dll$", re.I)
DLL32 = re.compile(r"(^|[\\/])steam_api\.dll$", re.I)   # --dll steam_api.dll：32 位桥的 ret N 表要靠这些 DLL 的机器码补版本
VTABLES = Path(__file__).resolve().parent.parent / "lib/steambridge/vtables"


def census(catalog=None, depotcache=None, dll=DLL):
    catalog = catalog or SN_GAMES / ".dryport-catalog/current"
    depotcache = depotcache or STEAM / "depotcache"
    routes = json.loads((catalog / "final-routes.json").read_text(encoding="utf8"))
    library = json.loads((catalog / "windows-library.json").read_text(encoding="utf8"))
    depots = {str(app["appid"]): [(d.get("depot"), d.get("gid")) for d in app.get("depots", []) if d.get("gid")] for app in library}
    variants = collections.defaultdict(list)     # sha1 -> [(depot_total_bytes, appid)]
    wine = no_manifest = with_dll = 0
    for app in routes:
        if app.get("route") != "wine":
            continue
        wine += 1
        total, sha, seen = 0, None, False
        for depot, gid in depots.get(str(app["appid"]), []):
            p = depotcache / f"{depot}_{gid}.manifest.txt"
            if not p.is_file():
                continue
            seen = True
            for line in open(p, encoding="utf8", errors="replace"):
                m = ROW.match(line)
                if not m:
                    continue
                total += int(m.group(1).replace(",", ""))
                if sha is None and dll.search(m.group(3)):
                    sha = m.group(2).lower()
        if not seen:
            no_manifest += 1
        if sha:
            with_dll += 1
            variants[sha].append((total, str(app["appid"])))
    return {"wine_games": wine, "without_local_manifest": no_manifest, "with_steam_api64": with_dll,
            "variants": {h: sorted(v) for h, v in variants.items()}}


def known_versions(known_dir):
    """<sha1>.json（steam-vtable-map 输出）→ 版本串集合；同家族列两个版本串时表属于新的那个。
    目录里只有 <sha1>.dll 没有 .json 的，先跑 tools/steam-vtable-map.py 把 .json 补出来（只读 DLL）。"""
    out = {}
    mapper = Path(__file__).resolve().parent / "steam-vtable-map.py"
    for dll in Path(known_dir).glob("*.dll"):
        target = dll.with_suffix(".json")
        if not target.exists() and mapper.exists():
            subprocess.run([sys.executable, "-B", str(mapper), str(dll), "--out", str(target)], capture_output=True, text=True, timeout=600)
    for p in Path(known_dir).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        vs = {sorted(v)[-1] for v in d.get("interface_versions", {}).values() if v}
        if vs:
            out[p.stem.lower()] = vs
    return out


def report(data, known, tables):
    variants = data["variants"]
    total = sum(len(v) for v in variants.values())
    ranked = sorted(variants.items(), key=lambda kv: -len(kv[1]))
    covered = sum(len(v) for h, v in variants.items() if h in known and known[h] <= tables)
    partial = sum(len(v) for h, v in variants.items() if h in known and not known[h] <= tables)
    unknown = total - covered - partial
    cum, top = 0, []
    for i, (h, rows) in enumerate(ranked[:16], 1):
        cum += len(rows)
        top.append({"rank": i, "sha1": h, "games": len(rows), "cumulative_share": round(cum / total, 4) if total else 0,
                    "smallest_owned_appid": rows[0][1], "smallest_depot_mb": round(rows[0][0] / 1e6),
                    "tables": "all" if (h in known and known[h] <= tables) else ("partial" if h in known else "unknown")})
    return {"wine_games": data["wine_games"], "without_local_manifest": data["without_local_manifest"],
            "with_steam_api64": total, "variants": len(variants), "top": top,
            "coverage": {"all_versions_have_tables": covered, "some_version_missing": partial, "variant_unknown": unknown,
                         "share_all": round(covered / total, 4) if total else 0}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--known", default=str(Path.home() / "Games/.sdk-variants/dlls"))
    ap.add_argument("--dll32", action="store_true", help="改查 32 位 steam_api.dll 的变体（给 argsizes-i386.json 补版本用）：只列变体与最小的已拥有游戏")
    a = ap.parse_args(argv)
    if a.dll32:
        data = census(dll=DLL32)
        rows = sorted(((len(v), h, min(v)) for h, v in data["variants"].items()), reverse=True)
        if a.json:
            print(json.dumps({"wine_games": data["wine_games"], "with_steam_api": data["with_steam_api64"], "variants": len(rows),
                              "top": [{"games": n, "sha1": h, "smallest_owned_appid": small[1], "smallest_depot_mb": round(small[0] / 1e6)} for n, h, small in rows]},
                             ensure_ascii=False, indent=1))
            return 0
        print(f"Wine 路线 {data['wine_games']} 款，带 32 位 steam_api.dll {data['with_steam_api64']} 款，变体 {len(rows)} 个（口径：清单里第一份 steam_api.dll 的 SHA-1）")
        for i, (n, h, small) in enumerate(rows[:20], 1):
            print(f"{i:2d} {n:4d} 款  最小的已拥有游戏 appid {small[1]}（{round(small[0] / 1e6)} MB）  {h[:12]}")
        return 0
    data = census()
    known = known_versions(a.known) if Path(a.known).is_dir() else {}
    tables = {p.stem for p in VTABLES.glob("*.json")}
    r = report(data, known, tables)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print(f"Wine 路线 {r['wine_games']} 款，无本地清单 {r['without_local_manifest']}，带 steam_api64.dll {r['with_steam_api64']}，变体 {r['variants']} 个")
    for t in r["top"]:
        print(f"{t['rank']:2d} {t['games']:4d} 款 累计 {t['cumulative_share']:6.1%}  表 {t['tables']:7s}  取 DLL 最小的已拥有游戏 appid {t['smallest_owned_appid']}（{t['smallest_depot_mb']} MB）  {t['sha1'][:12]}")
    c = r["coverage"]
    print(f"覆盖下限：{c['all_versions_have_tables']} 款（{c['share_all']:.1%}）的 DLL 内嵌版本串全部有表；{c['some_version_missing']} 款缺部分；{c['variant_unknown']} 款属未取到 DLL 的变体")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
