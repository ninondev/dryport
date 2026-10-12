#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""卸载（不可逆）的账单与执行，以及归档（可逆）的枚举。归档必须可逆（restore 原样放回），
卸载只删本工具自己创建的东西，逐项列大小，存档单独一项默认不删，释放量按磁盘可用空间前后差报。

  plan <appid> [--json]            列出会删什么：下载目录、归档的 app、（可选）本工具管理的存档状态目录
  render <plan-json>               把 plan --json 的结果渲染成人看的行（CLI 确认前打印）
  apply <appid> [--saves] --yes    真删；每个路径删前重新核对身份，核不上就跳过并如实报告
  archives(appid)                  Python 接口：这个 appid 的合法归档，新的在前；restore 与界面的「恢复归档」按钮用

安全判据（每一项都要同时满足，否则只列出「跳过」不删）：
  - 路径是绝对路径、不是软链、在 SN_GAMES（或 WebKit 状态根）之内，不是 SN_HOME / SN_RUNTIMES / STEAM 或其祖先；
  - 下载目录必须带本工具写的 .dryport-appid 且内容等于 appid；
  - 归档容器必须是 SN_GAMES/.uninstalled/<appid>-* 且内含 .app 的 steam_appid.txt 等于 appid；
  - Ren'Py 状态目录必须是 SN_GAMES/.renpy-state/<appid>、Wine 状态目录必须是 SN_GAMES/.wine-state/<appid>，且 steam_appid.txt 等于 appid；
  - WebKit 状态目录必须带 kind=dryport-webkit-state 且 appid 匹配的身份文件。
Steam 自己的暂存与其它位置的存档一律不动。du 报的是上限（APFS 克隆会重复计），实际释放看 statvfs 前后差。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SN_HOME, SN_GAMES, SN_RUNTIMES, STEAM

WEBKIT_STATE_ROOT = Path(os.environ.get("SN_WEBKIT_STATE") or Path.home() / "Library/Application Support/dryport/webkit")


def _du_bytes(path):
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=120).stdout
        return int(out.split()[0]) * 1024
    except Exception:
        return 0


def _read(path):
    try:
        return path.read_text(encoding="utf8", errors="replace").strip()
    except OSError:
        return None


def _protected_roots():
    return [Path(p).resolve() for p in (SN_HOME, SN_RUNTIMES, STEAM)]


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_dir(path, root):
    """通用判据：绝对、非软链、目录、在 root 内、不是受保护根或其祖先。返回 None 表示通过，否则是原因。"""
    if not path.is_absolute():
        return "不是绝对路径"
    if path.is_symlink():
        return "是软链"
    if not path.is_dir():
        return "不是目录"
    if not _inside(path, root) or path.resolve() == root.resolve():
        return f"不在 {root} 之内"
    resolved = path.resolve()
    for r in _protected_roots():
        if resolved == r or r in resolved.parents or resolved in r.parents:
            return f"与受保护根 {r} 重叠"
    return None


def _app_marker(app):
    for rel in ("Contents/Resources/steam_appid.txt", "Contents/MacOS/steam_appid.txt"):
        m = app / rel
        if m.is_file() and not m.is_symlink():
            return _read(m)
    return None


def _archive_rows(only_appid=None):
    games = SN_GAMES.resolve()
    root = SN_GAMES / ".uninstalled"
    rows = []
    if not root.is_dir() or root.is_symlink():
        return rows
    for container in root.iterdir():
        appid, sep, _ = container.name.partition("-")
        if not sep or not appid.isdigit() or (only_appid and appid != only_appid) or _safe_dir(container, games):
            continue
        apps = [a for a in container.iterdir() if a.suffix == ".app" and a.is_dir() and not a.is_symlink()]
        if len(apps) != 1 or _app_marker(apps[0]) != appid:
            continue
        rows.append({"appid": appid, "path": str(container), "app": str(apps[0]), "name": apps[0].name,
                     "mtime": container.stat().st_mtime})
    return rows


def archives(appid):
    """这个 appid 的合法归档，新的在前：[{path(容器), app, name, mtime}]。判据与 plan 相同，不合判据的不列。"""
    rows = _archive_rows(str(appid))
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def archived_appids():
    """有合法归档的 appid 集合，一次扫描 .uninstalled（界面「已归档」类别用；rewrap 留下的旧 app 也算，装着的游戏由调用方排除）。"""
    return {r["appid"] for r in _archive_rows()}


def render(p):
    """plan() 的结果渲染成人看的行（不带缩进，调用方自己加）。"""
    lines = []
    for i in p["items"]:
        lines.append(f"{'[默认]' if i['default'] else '[可选]'} {i['label']}：{i['path']}（账面 {i['bytes'] / 1e6:.0f} MB）")
    for x in p["skipped"]:
        lines.append(f"[不动] {x['path']}：{x['why']}")
    lines.append(f"默认合计账面 {p['bytes_default'] / 1e6:.0f} MB；存档 {p['bytes_saves'] / 1e6:.0f} MB（默认不删）。{p['note']}")
    return "\n".join(lines)


def plan(appid, installed=None):
    """installed: api.scan_installed() 的一条（可空）。返回 {items: [...], skipped: [...], ...}。"""
    appid = str(appid)
    games = SN_GAMES.resolve()
    items, skipped = [], []

    def add(kind, path, label, default=True, extra=None):
        entry = {"kind": kind, "path": str(path), "label": label, "bytes": _du_bytes(path), "default": default}
        if extra:
            entry.update(extra)
        items.append(entry)

    installed = installed or {}
    app = installed.get("app") or ""
    if app:
        p = Path(app)
        why = _safe_dir(p, games)
        if why or p.suffix != ".app" or _app_marker(p) != appid:
            skipped.append({"kind": "app", "path": app, "why": why or "标记不是这个 appid"})
        else:
            add("app", p, "已安装的 app（先经官方接口移除快捷方式并归档，再随归档删除）", extra={"archive_first": True})

    folder = installed.get("folder") or ""
    if folder:
        p = Path(folder)
        why = _safe_dir(p, games)
        marker = _read(p / ".dryport-appid") if not why else None
        if why or marker != appid:
            skipped.append({"kind": "download", "path": folder, "why": why or "没有本工具的 .dryport-appid 标记或 appid 不符"})
        else:
            add("download", p, "下载的游戏文件（Steam 自己的暂存不动）")

    archive_root = SN_GAMES / ".uninstalled"
    if archive_root.is_dir() and not archive_root.is_symlink():
        for container in sorted(archive_root.iterdir()):
            if not container.name.startswith(appid + "-"):
                continue
            why = _safe_dir(container, games)
            apps = [a for a in container.iterdir() if a.suffix == ".app"] if not why else []
            if why or len(apps) != 1 or _app_marker(apps[0]) != appid:
                skipped.append({"kind": "archive", "path": str(container), "why": why or "归档容器里不是这个 appid 的单个 app"})
            else:
                add("archive", container, f"归档的 app：{apps[0].name}")

    renpy = SN_GAMES / ".renpy-state" / appid
    if renpy.exists() or renpy.is_symlink():
        why = _safe_dir(renpy, games)
        if why or _read(renpy / "steam_appid.txt") != appid:
            skipped.append({"kind": "saves", "path": str(renpy), "why": why or "steam_appid.txt 不符"})
        else:
            add("saves", renpy, "Ren'Py 存档与状态目录（本工具创建）", default=False)

    wine = SN_GAMES / ".wine-state" / appid
    if wine.exists() or wine.is_symlink():
        why = _safe_dir(wine, games)
        if why or _read(wine / "steam_appid.txt") != appid:
            skipped.append({"kind": "saves", "path": str(wine), "why": why or "steam_appid.txt 不符"})
        else:
            add("saves", wine, "Wine prefix：存档、游戏设置与着色器缓存（本工具创建）", default=False)

    if WEBKIT_STATE_ROOT.is_dir() and not WEBKIT_STATE_ROOT.is_symlink():
        for d in sorted(WEBKIT_STATE_ROOT.iterdir()):
            if not d.is_dir() or d.is_symlink():
                continue
            ident = None
            for cand in list(d.glob(".*.json")) + list(d.glob("*.json")):   # 身份文件是隐藏的 .dryport-webkit.json
                try:
                    data = json.loads(cand.read_text(encoding="utf8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict) and data.get("kind") == "dryport-webkit-state":
                    ident = data
                    break
            if ident and str(ident.get("appid")) == appid:
                why = _safe_dir(d, WEBKIT_STATE_ROOT)
                if why:
                    skipped.append({"kind": "saves", "path": str(d), "why": why})
                else:
                    add("saves", d, "WebKit 存档与状态目录（本工具创建）", default=False)

    total_default = sum(i["bytes"] for i in items if i["default"])
    total_saves = sum(i["bytes"] for i in items if i["kind"] == "saves")
    return {"protocol": 1, "appid": appid, "items": items, "skipped": skipped,
            "bytes_default": total_default, "bytes_saves": total_saves,
            "note": "du 是上限（APFS 克隆重复计），实际释放按磁盘可用空间前后差报；其它位置的存档不动。"}


def apply(appid, saves=False, installed=None, archive_hook=None, log=print):
    """真删。archive_hook(app_path) 由调用方提供：走官方接口移除快捷方式并归档 app，返回归档后的容器路径。"""
    p = plan(appid, installed)
    games = SN_GAMES.resolve()
    before = shutil.disk_usage(str(SN_GAMES)).free
    deleted, refused = [], list(p["skipped"])
    # 1. 仍安装的 app 先归档（快捷方式必须经 Steam 官方接口删掉）
    for item in [i for i in p["items"] if i["kind"] == "app"]:
        if not archive_hook:
            refused.append({"kind": "app", "path": item["path"], "why": "没有归档钩子，未动"})
            continue
        container = archive_hook(item["path"])
        if not container:
            refused.append({"kind": "app", "path": item["path"], "why": "归档失败，未删除"})
            continue
        p = plan(appid, {k: v for k, v in (installed or {}).items() if k != "app"})   # 归档后重新算账
    for item in p["items"]:
        if item["kind"] == "app":
            continue
        if item["kind"] == "saves" and not saves:
            continue
        path = Path(item["path"])
        root = WEBKIT_STATE_ROOT if (item["kind"] == "saves" and _inside(path, WEBKIT_STATE_ROOT)) else games
        why = _safe_dir(path, root)
        if why:
            refused.append({"kind": item["kind"], "path": item["path"], "why": why}); continue
        # 删前再核一次身份
        if item["kind"] == "download" and _read(path / ".dryport-appid") != str(appid):
            refused.append({"kind": item["kind"], "path": item["path"], "why": "删前复核：标记不符"}); continue
        if item["kind"] == "archive":
            apps = [a for a in path.iterdir() if a.suffix == ".app"]
            if len(apps) != 1 or _app_marker(apps[0]) != str(appid):
                refused.append({"kind": item["kind"], "path": item["path"], "why": "删前复核：归档内容不符"}); continue
        if item["kind"] == "saves" and root == games and _read(path / "steam_appid.txt") != str(appid):
            refused.append({"kind": item["kind"], "path": item["path"], "why": "删前复核：steam_appid.txt 不符"}); continue
        try:
            shutil.rmtree(path)
            deleted.append(item)
            log(f"  已删除：{path}（账面 {item['bytes'] / 1e6:.0f} MB）")
        except OSError as error:
            refused.append({"kind": item["kind"], "path": item["path"], "why": f"删除失败：{error}"})
    after = shutil.disk_usage(str(SN_GAMES)).free
    return {"protocol": 1, "appid": str(appid), "deleted": deleted, "refused": refused,
            "freed_bytes": max(0, after - before), "free_before": before, "free_after": after,
            "ok": not any(r["kind"] in ("download", "archive", "saves") and "删除失败" in r["why"] for r in refused)}


def _installed_record(appid):
    import api
    return api.scan_installed().get(str(appid), {})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("appid"); a.add_argument("--json", action="store_true")
    b = sub.add_parser("apply"); b.add_argument("appid"); b.add_argument("--saves", action="store_true"); b.add_argument("--yes", action="store_true")
    c = sub.add_parser("render"); c.add_argument("plan_json")
    args = ap.parse_args(argv)
    if args.cmd == "render":
        print(render(json.loads(args.plan_json)))
        return 0
    rec = _installed_record(args.appid)
    if args.cmd == "plan":
        p = plan(args.appid, rec)
        print(json.dumps(p, ensure_ascii=False) if args.json else render(p))
        return 0
    if not args.yes:
        print("没有 --yes，只算账不删。"); return 2
    if rec.get("app"):
        print("仍有已安装的 app，请先 dryport archive <appid>（经官方接口删快捷方式并归档），或直接 dryport uninstall。"); return 3
    result = apply(args.appid, saves=args.saves, installed=rec)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
