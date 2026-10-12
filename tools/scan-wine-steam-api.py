#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""数 Wine 路线里的游戏各自带哪种 Steam 接入层，统计桥接口的支持范围。

只读当前目录（SN_GAMES/.dryport-catalog/current）和 Steam depotcache 里已有的文本清单，不下载、不联网。
默认只打印计数；`--detail <文件>` 才把逐游戏结果（含游戏名，属个人库表）写到指定文件，别写进仓库。

口径：
  steam_api64 / steam_api32   清单里有 steam_api64.dll / steam_api.dll
  steamworks_net / facepunch  按文件名可见的托管封装；Steamworks.NET 编进 IL2CPP 的看不出来，所以是下限
  native_sdk_only             带 steam_api 但没有托管封装，只能是 C++ SDK 或引擎自带原生扩展
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_GAMES, STEAM

ROW = re.compile(r"^\s*[\d,]+\s+\d+\s+[0-9a-fA-F]{40}\s+\d+\s+(.+?)\s*$")   # 大小列带千位逗号
MARKERS = {
    "steamworks_net": re.compile(r"steamworks\.net\.dll|com\.rlabrecque\.steamworks\.net", re.I),
    "facepunch": re.compile(r"facepunch\.steamworks", re.I),
    "steam_api64": re.compile(r"(^|/)steam_api64\.dll$", re.I | re.M),
    "steam_api32": re.compile(r"(^|/)steam_api\.dll$", re.I | re.M),
}


def manifest_files(path):
    with open(path, encoding="utf8", errors="replace") as fh:
        return [m.group(1).replace("\\", "/") for m in map(ROW.match, fh) if m]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", type=Path, help="逐游戏结果写到这个文件（含个人库表，别进仓库）")
    args = ap.parse_args()
    catalog = SN_GAMES / ".dryport-catalog/current"
    routes = json.loads((catalog / "final-routes.json").read_text(encoding="utf8"))
    library = json.loads((catalog / "windows-library.json").read_text(encoding="utf8"))
    depots = {app["appid"]: [(d.get("depot"), d.get("gid")) for d in app.get("depots", []) if d.get("gid")] for app in library}
    totals, by_engine, detail = collections.Counter(), collections.defaultdict(collections.Counter), []
    scanned = missing = 0
    for app in routes:
        if app.get("route") != "wine":
            continue
        files, found = [], False
        for depot, gid in depots.get(app["appid"], []):
            path = STEAM / "depotcache" / f"{depot}_{gid}.manifest.txt"
            if path.is_file():
                found = True
                files += manifest_files(path)
        if not found:
            missing += 1
            continue
        scanned += 1
        joined = "\n".join(files)
        hit = {k: bool(rx.search(joined)) for k, rx in MARKERS.items()}
        hit["any_steam_api"] = hit["steam_api64"] or hit["steam_api32"]
        hit["managed_wrapper"] = hit["steamworks_net"] or hit["facepunch"]
        hit["native_sdk_only"] = hit["any_steam_api"] and not hit["managed_wrapper"]
        hit["no_steam_api"] = not hit["any_steam_api"]
        for k, v in hit.items():
            if v:
                totals[k] += 1
                by_engine[app.get("engine")][k] += 1
        detail.append(dict(appid=app["appid"], name=app["name"], engine=app.get("engine"), files=len(files), **hit))
    out = dict(catalog=catalog.resolve().name, wine_route_entries=scanned + missing, scanned=scanned, missing_manifest=missing,
               totals=dict(totals), by_engine={k: dict(v) for k, v in sorted(by_engine.items(), key=lambda kv: -sum(kv[1].values()))})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.detail:
        args.detail.write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf8")
        print(f"逐游戏结果已写到 {args.detail}", file=sys.stderr)


if __name__ == "__main__":
    main()
