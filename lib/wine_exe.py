#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""挑 Wine 兜底路线的主程序 exe：先照 Steam 的启动配置（appinfo 里 config.launch 的 Windows 默认入口，含参数与工作目录），
没有或对不上文件时按引擎布局与排除规则选，选不准就把候选列出来让人定。

  wine_exe.py <游戏目录> [--appid N] [--json]      打印相对路径（--json 连参数、工作目录、候选与理由一起）；没有候选退出 1

规则（按先后）：
  0. --appid 给了且本地 appinfo 里有 config.launch：取第一条 oslist 含 windows（或没写）、type 为 default/none/空、无 betakey 的入口；
     executable 按 Windows 大小写不敏感解析到目录里的文件；.bat/.cmd 入口取脚本里第一条 .exe 命令（GOG 老游戏的 run.bat）。
     2026-09-14 冒烟：一类 ScummVM 游戏 的 run.bat 是 `scummvm.exe --fullscreen lsl1`，不带参数起 scummvm.exe 只有空启动器；
  1. 根目录的 exe 优先；排除安装器/运行库/崩溃处理器/卸载器（vcredist、dxsetup、UnityCrashHandler、unins…）。
  2. Unity：与 <X>_Data 同名的 <X>.exe。Unreal：根目录的引导 exe（Steam 也是从它启动），没有再取 Binaries/Win64/*-Win64-Shipping.exe。
  3. 只剩一个候选就它；多个时取与目录名最像的，都不像就取最大的，并在 reason 里说明是猜的。
"""
import argparse
import json
import re
import sys
from pathlib import Path, PureWindowsPath

EXCLUDE = re.compile(r"(crash|report|vcredist|vc_redist|dxsetup|dxwebsetup|directx|dotnet|ndp\d|unins|uninstall|setup|install|"
                     r"redist|easyanticheat|battleye|launcher_helper|updater|patcher|benchmark|config(?:tool)?|tools?\.exe$|"
                     r"physx|oalinst|steamerrorreporter|cef|helper\.exe$)", re.I)


def candidates(root):
    root = Path(root)
    rows = []
    for p in root.rglob("*.exe"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if len(rel.parts) > 4 or any(part.lower() in ("steamapps", "_commonredist", "redist", "redistributables", "__installer") for part in rel.parts):
            continue
        rows.append(rel)
    return rows


def pick(root):
    root = Path(root).resolve()
    rows = candidates(root)
    if not rows:
        return None, [], "目录里没有 exe"
    keep = [r for r in rows if not EXCLUDE.search(r.name)]
    if not keep:
        keep = rows
    # Unity：<X>_Data 旁的 <X>.exe
    for r in keep:
        if len(r.parts) == 1 and (root / (r.stem + "_Data")).is_dir():
            return r, keep, "Unity 布局：与 _Data 同名的 exe"
    # Unreal：根目录引导 exe；否则 Shipping exe
    shipping = [r for r in keep if re.search(r"-win64-shipping\.exe$", r.name, re.I)]
    top = [r for r in keep if len(r.parts) == 1]
    if shipping:
        # 引导 exe 与 Shipping exe 名字未必相同（FixtureGame.exe 对 Fixture-Win64-Shipping.exe）：
        # 根目录只有一个候选就是它；多个时取与 Shipping 名互为前缀的
        ship_stem = shipping[0].name.lower().split("-win64-shipping")[0]
        boot = top if len(top) == 1 else [r for r in top if r.stem.lower().startswith(ship_stem) or ship_stem.startswith(r.stem.lower())]
        if boot:
            return boot[0], keep, "Unreal 布局：根目录引导 exe"
        return shipping[0], keep, "Unreal 布局：没有引导 exe，取 Shipping exe"
    if len(top) == 1:
        return top[0], keep, "根目录唯一的 exe"
    pool = top or keep
    if len(pool) == 1:
        return pool[0], keep, "唯一的候选"
    dirname = re.sub(r"[^a-z0-9]", "", root.name.lower())
    def similarity(r):
        stem = re.sub(r"[^a-z0-9]", "", r.stem.lower())
        return len(set(stem) & set(dirname)) / max(1, len(set(stem) | set(dirname))) if stem else 0
    pool = sorted(pool, key=lambda r: (-similarity(r), -(root / r).stat().st_size, str(r)))
    best = pool[0]
    reason = "多个候选，取与目录名最像的（猜的，可用 SN_WINE_EXE 指定）" if similarity(best) > 0.5 else "多个候选，取最大的（猜的，可用 SN_WINE_EXE 指定）"
    return best, keep, reason


def split_windows_args(text):
    """Windows 命令行的粗切：双引号成组，其余按空白；不处理反斜杠转义（Steam 的 arguments 里没见过）。"""
    return [q if q else bare for q, bare in re.findall(r'"([^"]*)"|(\S+)', text or "")]


def resolve_rel(root, rel):
    """把 Windows 写法的相对路径落到目录里的文件：分隔符两种都认、逐段大小写不敏感（唯一匹配才算）；越界、绝对路径、找不到都返回 None。"""
    root = Path(root)
    text = str(rel or "").strip().strip('"')
    if not text or PureWindowsPath(text).is_absolute() or text.startswith(("/", "\\\\")):
        return None
    parts = [x for x in text.replace("\\", "/").split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        return None
    cur = _walk(root, parts)
    if cur is None or not cur.is_file() or cur.is_symlink():
        return None
    try:
        cur.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return cur


def batch_command(bat):
    """.bat/.cmd 里第一条起 .exe 的命令：(exe 写法, 参数串)；没有返回 None。只认最简单的写法（可带 @、start、引号），别的交回启发式。"""
    try:
        lines = Path(bat).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip().lstrip("@").strip()
        if not line or line.lower().startswith(("::", "rem ", "rem\t", "echo", "exit", "pause", "cd ", "set ", "title ")):
            continue
        m = re.match(r'^(?:start\s+(?:""\s+)?(?:/\w+\s+)*)?"?([^"\s]+\.exe)"?\s*(.*)$', line, re.I)
        if m:
            return m.group(1), m.group(2).strip()
    return None


def steam_launch(root, appid, apps=None):
    """Steam 启动配置里的 Windows 默认入口：{exe: 相对 posix 路径, args: [...], workdir: 相对 posix 或 "", reason} 或 None。
    apps 不给就从本地 appinfo 只解码这一款；读不到、没有 config.launch、文件对不上都返回 None（交回启发式）。"""
    if not appid:
        return None
    root = Path(root)
    if apps is None:
        try:
            import contextlib, io
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            with contextlib.redirect_stdout(io.StringIO()):   # 测试夹具里的假 risk.py 导入时会往 stdout 打回执，别混进 --json 输出
                import risk
            apps = risk.load_appinfo(appids={int(appid)})
        except Exception:
            return None
    app = apps.get(int(appid)) or apps.get(str(appid)) or {}
    launch = (app.get("config") or {}).get("launch") or {}
    if not isinstance(launch, dict):
        return None
    entries = sorted(launch.items(), key=lambda kv: (0, int(kv[0])) if str(kv[0]).isdigit() else (1, str(kv[0])))
    for key, v in entries:
        if not isinstance(v, dict) or not v.get("executable"):
            continue
        cfg = v.get("config") or {}
        oslist = str(cfg.get("oslist") or "")
        if oslist and "windows" not in oslist:
            continue
        if cfg.get("betakey") or str(v.get("type") or "").lower() not in ("", "default", "none"):
            continue
        exe = resolve_rel(root, v["executable"])
        if exe is None:
            continue
        args = split_windows_args(str(v.get("arguments") or ""))
        workdir = ""
        if v.get("workingdir"):
            wd = resolve_dir(root, v["workingdir"])
            if wd is not None and wd != root:
                workdir = wd.relative_to(root).as_posix()
        reason = f"Steam 启动配置第 {key} 条"
        if exe.suffix.lower() in (".bat", ".cmd"):
            bc = batch_command(exe)
            if not bc:
                continue
            base = (root / workdir) if workdir else root
            inner = resolve_rel(base, bc[0]) or resolve_rel(exe.parent, bc[0])
            if inner is None or inner.suffix.lower() != ".exe":
                continue
            args = split_windows_args(bc[1]) + args
            reason += f"（{exe.relative_to(root).as_posix()} 里起的 exe）"
            exe = inner
        elif exe.suffix.lower() != ".exe":
            continue
        return {"exe": exe.relative_to(root).as_posix(), "args": args, "workdir": workdir, "reason": reason}
    return None


def resolve_dir(root, rel):
    root = Path(root)
    text = str(rel or "").strip().strip('"')
    if not text or PureWindowsPath(text).is_absolute():
        return None
    parts = [x for x in text.replace("\\", "/").split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        return None
    cur = _walk(root, parts)
    return cur if cur is not None and cur.is_dir() else None


def _walk(root, parts):
    """逐段落到真实目录项：先要完全同名，再退到大小写不敏感的唯一匹配。用目录列表而不是 exists()，这样在大小写不敏感的卷上记下的也是真实拼写。"""
    cur = Path(root)
    for part in parts:
        if not cur.is_dir():
            return None
        names = {c.name: c for c in cur.iterdir()}
        if part in names:
            cur = names[part]
            continue
        found = [c for n, c in names.items() if n.lower() == part.lower()]
        if len(found) != 1:
            return None
        cur = found[0]
    return cur


def choose(root, appid=None, apps=None):
    """先 Steam 启动配置，再启发式。返回 {exe, args, workdir, reason, source, candidates}；exe 为 None 表示没有候选。"""
    hit = steam_launch(root, appid, apps=apps)
    if hit:
        return {**hit, "source": "steam", "candidates": []}
    exe, keep, reason = pick(root)
    return {"exe": exe.as_posix() if exe else None, "args": [], "workdir": "", "reason": reason, "source": "heuristic",
            "candidates": [r.as_posix() for r in keep]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("game")
    ap.add_argument("--appid", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not Path(a.game).is_dir():
        sys.exit(f"目录不存在：{a.game}")
    r = choose(a.game, a.appid.strip() or None)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    elif r["exe"]:
        print(r["exe"])
    else:
        print(r["reason"], file=sys.stderr)
    return 0 if r["exe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
