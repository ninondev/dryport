#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""JSON API over the dryport toolchain, for the companion app (and for scripting).

Everything a GUI needs, as one line of JSON per call (or NDJSON while a job runs).
No passwords, ever: downloads go through the Steam client you are already logged into.

  api.py state                      machine + Steam status
  api.py snapshot                   library + state from one read-only snapshot
  api.py library [--limit N] [--q S] [--route R] [--only-playable] [--installed]
  api.py detail <appid>
  api.py install <appid>            NDJSON progress; runs `dryport install`
  api.py survey                    NDJSON; refresh the current account's library
  api.py name <appid>               plain-text display name (filesystem-safe)
  api.py archive <appid>            NDJSON; reversible: official shortcut removal, app moved into .uninstalled
  api.py restore <appid>            NDJSON; put the newest archived app back and re-add it through Steam
  api.py uninstall <appid> [--saves]  NDJSON; irreversible: archive, then delete our download dir and archives
  api.py uninstall-plan <appid>     JSON; what uninstall would delete, with sizes (saves listed, default off)
  api.py steam-debug [status|enable|disable]  JSON; Valve's own debug switch file: status is read-only, enable creates the empty flag (user confirmed), disable removes only that empty flag
  api.py clean [--go] [--deep]      NDJSON; sweeps this toolchain's leftovers
  api.py play <appid>               launch what is installed for this appid
  api.py reveal <appid>             open the install folder in Finder
  api.py forget <appid>             drop it from the registry (does not delete files)
"""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SN_HOME, SN_LIB, SN_RUNTIMES, SN_GAMES, STEAM  # noqa: E402
from route_tiers import describe as tier_describe  # noqa: E402

GAMES = SN_GAMES
RUNTIMES = SN_RUNTIMES
LIB = SN_LIB
REGISTRY = GAMES / "installed.json"

ROUTE_LABEL = {                      # vocabulary comes from final-routes.json, built by route.py
    "renpy-native": "Ren'Py 原生（安装时校验版本）",
    "webkit": "WebKit 原生（安装时校验版本）",
    "steam": "Steam macOS 发行版",
    "unity": "Unity macOS", "nwjs": "NW.js 原生", "mkxp": "mkxp-z 原生",
    "renpy": "Ren'Py 候选（未接通）", "godot": "Godot 原生", "krkr": "krkrsdl2 原生", "vn": "视觉小说（引擎待确认）",
    "fna": "FNA/MonoGame 候选（未接通）",
    "wine": "Wine 兜底", "unknown": "待识别", "maybe": "待定，先看看",
    "love": "LÖVE 原包直读（版本与扩展待确认）",
    "refuse": "不碰（风险拦截）",
}
NATIVE_ROUTES = {"unity", "nwjs", "mkxp", "godot", "krkr", "love", "webkit", "renpy-native"}
BLOCKED_ROUTES = {"refuse"}
NODE_PROBE_TIMEOUT = 2


def child_environment(environment=None):
    """保留用户已经选定的工具顺序；Finder 缺少的目录只补到末尾。"""
    env = dict(os.environ if environment is None else environment)
    paths = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    for path in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if path not in paths:
            paths.append(path)
    # 系统没有 node 时补本工具自备的那份（与 lib/paths.sh 同一规矩），排在末尾：用户自己的工具优先
    if shutil.which("node", path=os.pathsep.join(paths)) is None:
        for candidate in sorted((RUNTIMES / "node").glob("*/bin/node")) if (RUNTIMES / "node").is_dir() else []:
            if os.access(candidate, os.X_OK):
                paths.append(str(candidate.parent)); break
    env["PATH"] = os.pathsep.join(paths)
    return env


def node_toolchain_error(env):
    """只运行本机能力探针，不连接 Steam；退出码 72 区分缺 API 与启动失败。"""
    node = shutil.which("node", path=env["PATH"])
    if node is None:
        return "没有找到 Node.js。运行 ./bin/install.sh 会自备一份固定版本（nodejs.org 官方包，校验后解到运行时目录）；也可自行安装 Node.js 22 或更新版本后刷新。"
    try:
        result = subprocess.run(
            [node, "-e", 'if(typeof WebSocket!=="function" || typeof fetch!=="function")process.exit(72)'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=NODE_PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"Node.js 检查超时：{node}。请确认该工具能正常运行后刷新。"
    except OSError:
        return f"无法启动 Node.js：{node}。请修复该工具或 PATH 后刷新。"
    if result.returncode == 72:
        return f"Node.js 缺少 WebSocket / fetch：{node}。请更新到 Node.js 22 或更新版本后刷新。"
    if result.returncode:
        return f"Node.js 检查失败（退出码 {result.returncode}）：{node}。请确认该工具能正常运行后刷新。"
    return None


def out(obj):
    # dumps 走 C 编码器，避免大库输出时逐字段写几十万次。
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def catalog_context():
    """一次请求固定一个完整目录版本；账号失配时不能回落到旧账号库表。"""
    store = GAMES / ".dryport-catalog"
    pointer = store / "current"
    if store.is_symlink() or pointer.exists() or pointer.is_symlink():
        from library_discovery import current_catalog_dir, DiscoveryError
        try:
            root = current_catalog_dir(GAMES)
        except DiscoveryError as error:
            return {"root": None, "status": "unavailable", "error": str(error), "meta": {}}
        if root is not None:
            return {"root": root, "status": "current", "error": None,
                    "meta": load_json(root / "catalog.json", {})}
    legacy = any((GAMES / name).is_file() for name in ("route-all.json", "final-routes.json"))
    return {"root": GAMES, "status": "legacy" if legacy else "empty", "error": None, "meta": {}}


def catalog_rows(name, context=None):
    context = context if context is not None else catalog_context()
    if context["status"] == "current":
        rows = json.loads((context["root"] / name).read_text(encoding="utf8"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) or "appid" not in row for row in rows):
            raise ValueError(f"游戏目录表损坏：{name}。请重新读取 Steam 库。")
        return rows
    return load_json(context["root"] / name, []) if context["root"] is not None else []


def windows_rows(context=None):
    context = context if context is not None else catalog_context()
    name = "windows-library.json" if context["meta"].get("protocol", 1) >= 2 else "owned-windows.json"
    return catalog_rows(name, context)


def all_routes(context=None):
    """合并同一快照的 appinfo 与文件清单证据；引擎识别不等于可玩验证。"""
    context = context if context is not None else catalog_context()
    first = {str(g["appid"]): g for g in catalog_rows("route-all.json", context)}
    second = {str(g["appid"]): g for g in catalog_rows("final-routes.json", context)}
    rows = []
    for appid in dict.fromkeys([*first, *second]):
        g = first.get(appid, second.get(appid))
        f = second.get(appid)
        if f:
            r = dict(f); r["source"] = "manifest"
            r.setdefault("route_name", g.get("route_name"))
        else:
            r = dict(g); r["source"] = "appinfo"
            r["evidence"] = g.get("why") or g.get("risk_why") or ""
            r["confidence"] = g.get("confidence") or "推测"
        r["appid"] = appid
        rows.append(r)
    return rows


def registry():
    return load_json(REGISTRY, {})


SMOKE_DIR = GAMES / ".smoke"


def smoke_latest():
    """tools/smoke-run.py 写回的自动冒烟结果，按 appid 取最新一次：{appid: {verdict, flags, at, evidence, ...}}。
    只读；目录每代重建、不能写，所以这些证据单独落在 $SN_GAMES/.smoke/<appid>.json。
    verdict=alive 只表示出窗口、活满观察期、无崩溃信号，不等于可玩，更不等于达到 Windows 水平。"""
    out = {}
    if not SMOKE_DIR.is_dir():
        return out
    for f in SMOKE_DIR.glob("*.json"):
        if f.name == "ledger.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        latest = d.get("latest") if isinstance(d, dict) else None
        appid = str(d.get("appid") or f.stem) if isinstance(d, dict) else f.stem
        if isinstance(latest, dict) and latest.get("verdict"):
            out[appid] = latest
    return out


def save_registry(r):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(r, ensure_ascii=False, indent=1))


def steam_running():
    return subprocess.run(["pgrep", "-x", "steam_osx"], capture_output=True).returncode == 0


def debug_port_enabled():
    # 开关文件存在不代表端点正在工作；只读探测，绝不替用户改 Steam。
    import urllib.request
    from urllib.parse import urlparse
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8080/json", timeout=0.4) as response:
            targets = json.loads(response.read(2 * 1024 * 1024))
        matches = [target for target in targets if target.get("title") == "SharedJSContext"]
        if len(matches) != 1:
            return False
        endpoint = urlparse(matches[0]["webSocketDebuggerUrl"])
        return (endpoint.scheme == "ws" and endpoint.hostname in ("localhost", "127.0.0.1", "::1")
                and endpoint.port == 8080)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def dir_size(p):
    try:
        return int(subprocess.run(["du", "-sk", str(p)], capture_output=True, text=True).stdout.split("\t")[0]) * 1024
    except Exception:
        return 0


def scan_installed(routes=None, *, persist=False):
    """Rebuild the registry from what is actually on disk: a game folder and/or a built .app."""
    reg = registry()
    routes = {str(g["appid"]): g for g in (all_routes() if routes is None else routes)}
    # 浏览与体检不写个人数据，也要容忍首次运行还没有游戏目录。
    for entry in reg.values():
        for key in ("app", "folder"):
            if entry.get(key) and not Path(entry[key]).is_dir():
                entry.pop(key)
    # folders downloaded by dryport (they carry steamapps/appmanifest_<appid>.acf)
    for d in GAMES.iterdir() if GAMES.is_dir() else []:
        if not d.is_dir() or d.name in ("runtimes", "patches"):
            continue
        found = []
        for acf in (d / "steamapps").glob("appmanifest_*.acf"):
            m = re.search(r'"name"\s+"([^"]*)"', acf.read_text(errors="replace"))
            found.append((acf.stem.split("_")[1], m.group(1) if m else None))
        # 走已登录客户端下载的目录没有 appmanifest，认 steam-download 留下的身份牌。
        marker = d / ".dryport-appid"
        if marker.exists():
            mid = marker.read_text(errors="replace").strip()
            if mid.isdigit() and mid not in [a for a, _ in found]:
                found.append((mid, None))
        for appid, acf_name in found:
            e = reg.setdefault(appid, {})
            e["appid"] = appid
            e["folder"] = str(d)
            g = routes.get(appid)
            # 库里的真名优先于 acf 与目录名（目录名可能是 app_<appid> 这种）。
            if g:
                e["name"] = g["name"]
                e["route"] = g.get("route", "unknown")
            elif acf_name:
                e.setdefault("name", acf_name)
    # built .app bundles: match by the steam_appid.txt we write into them
    for app in GAMES.glob("*.app"):
        # 新标记在资源目录，避免签名把 MacOS 下的文本当作可执行代码。
        # 旧包只读兼容；新标记存在时不允许旧标记覆盖它。
        f = app / "Contents/Resources/steam_appid.txt"
        if not f.exists() and not f.is_symlink():
            f = app / "Contents/MacOS/steam_appid.txt"
        if f.is_symlink() or not f.is_file():
            continue
        appid = f.read_text().strip()
        if not appid.isdigit():
            continue
        e = reg.setdefault(appid, {})
        e["appid"] = appid
        e["app"] = str(app)
        g = routes.get(appid)
        if g:
            e["name"] = g["name"]
            e.setdefault("route", g.get("route", "unknown"))
        else:
            e.setdefault("name", app.stem)
    if persist:
        save_registry(reg)
    return reg


def is_installed(entry):
    # 下载目录还不是可启动的 app；残留注册记录也不能当安装成功。
    return bool(entry and entry.get("app") and Path(entry["app"]).is_dir())


def read_library(context=None):
    routes = all_routes(context)
    reg = scan_installed(routes)
    known = {row["appid"] for row in routes}
    # 首次按 appid 安装不要求先生成全库路线表，已落盘的游戏仍须能在界面找到。
    for appid, entry in reg.items():
        if appid in known or not (entry.get("app") or entry.get("folder")):
            continue
        path = Path(entry.get("app") or entry["folder"])
        routes.append({"appid": appid, "name": entry.get("name") or path.stem,
                       "route": entry.get("route") or "unknown", "source": "local",
                       "confidence": "待核实", "evidence": "本机安装标记；尚无游戏路线表"})
    return routes, reg


def machine_state(routes, reg, context=None):
    context = context if context is not None else catalog_context()
    free = shutil.disk_usage(str(Path.home())).free
    running = steam_running()
    debug = running and debug_port_enabled()
    toolchain_error = node_toolchain_error(child_environment())
    def executable(path):
        return path.is_file() and os.access(path, os.X_OK)
    nwjs_app = os.environ.get("NWJS_APP")
    nwjs_root = Path(os.environ.get("NWJS_ROOT") or RUNTIMES / "nwjs")
    nwjs_bins = ([Path(nwjs_app) / "Contents/MacOS/nwjs"] if nwjs_app else
                 nwjs_root.glob("*/nwjs-v*-osx-arm64/nwjs.app/Contents/MacOS/nwjs"))
    krkr_app = Path(os.environ.get("KRKR_APP") or RUNTIMES / "krkrsdl2/build/krkrsdl2.app")
    unity_env = Path(os.environ.get("SN_VENV") or RUNTIMES / "unity-retarget/venv")
    counts, sources = {}, {}
    for g in routes:
        counts[g.get("route", "unknown")] = counts.get(g.get("route", "unknown"), 0) + 1
        sources[g["source"]] = sources.get(g["source"], 0) + 1
    return {
        "steam_running": running,
        "debug_port": debug,
        "can_download": running and debug and toolchain_error is None,
        "toolchain_error": toolchain_error,
        "free_bytes": free,
        "library_size": len(routes),
        "installed": sum(is_installed(entry) for entry in reg.values()),
        "archived": len({a for a in __import__("uninstall_purge").archived_appids() if not is_installed(reg.get(a))}),
        "route_counts": counts,
        "smoke_counts": _smoke_counts(),
        "confirmed": sources.get("manifest", 0),
        "guessed": sources.get("appinfo", 0),
        "local": sources.get("local", 0),
        "catalog_status": context["status"],
        "catalog_error": context["error"],
        "borrowed_count": context["meta"].get("excluded", {}).get("borrowed", 0),
        "metadata_missing": context["meta"].get("metadata_missing", 0),
        "risk_pending": context["meta"].get("risk_pending", 0),
        "catalog_warnings": context["meta"].get("warnings", []),
        "catalog_owned_games": context["meta"].get("games_owned_count", context["meta"].get("windows_owned_count")),
        "catalog_shared_games": context["meta"].get("games_shared_count", context["meta"].get("windows_shared_count")),
        "tools": {
            "wine": executable(RUNTIMES / "wine-cx/bin/wine"),
            "mkxpz": executable(RUNTIMES / "mkxp-build/Build/Products/Release/Z-universal.app/Contents/MacOS/Z-universal"),
            "nwjs": any(executable(path) for path in nwjs_bins),
            "krkrsdl2": executable(krkr_app / "Contents/MacOS/krkrsdl2"),
            "godot": any(executable(path) for path in (RUNTIMES / "godot").glob("*/tmpl/*_template.app/Contents/MacOS/*release*")),
            "unity_retarget": ((LIB / "unity-retarget/retarget.py").is_file()
                               and executable(unity_env / "bin/python")
                               and executable(RUNTIMES / "unity-retarget/hlslcc-cli")),
        },
    }


def _smoke_counts():
    c = {}
    for latest in smoke_latest().values():
        v = latest.get("verdict") or "?"
        c[v] = c.get(v, 0) + 1
    return c


def cmd_state():
    context = catalog_context()
    routes, reg = read_library(context)
    out(machine_state(routes, reg, context))


def cmd_snapshot():
    context = catalog_context()
    routes, reg = read_library(context)
    out({"library": library_page({}, routes, reg, context), "state": machine_state(routes, reg, context)})


def library(args):
    context = catalog_context()
    routes, reg = read_library(context)
    out(library_page(args, routes, reg, context))


def library_page(args, routes, reg, context=None):
    owned = {str(g["appid"]): g for g in windows_rows(context)}
    import uninstall_purge
    archived = uninstall_purge.archived_appids()   # 「已归档」= 有归档 app 且现在没装着
    smoke = smoke_latest()
    rows = []
    q = (args.get("q") or "").lower()
    for g in routes:
        appid = str(g["appid"])
        route = g.get("route", "unknown")
        if q and q not in g["name"].lower():
            continue
        if args.get("route") and route != args["route"]:
            continue
        if args.get("only_playable") and route in BLOCKED_ROUTES:
            continue
        inst = reg.get(appid)
        installed = is_installed(inst)
        if args.get("installed") and not installed:
            continue
        if args.get("archived") and (installed or appid not in archived):
            continue
        o = owned.get(appid, {})
        tier = tier_describe(route)
        rows.append({
            "appid": appid,
            "name": g["name"],
            "engine": g.get("engine"),
            "route": route,
            "route_label": ROUTE_LABEL.get(route, route),
            "native": route in NATIVE_ROUTES,
            "tier": tier["tier"], "tier_label": tier["tier_label"], "tier_note": tier["tier_note"],
            "confidence": g.get("confidence"),
            "evidence": g.get("evidence"),
            "platform_note": g.get("platform_note"),
            "source": g.get("source"),
            "risk": g.get("risk"),
            "download_bytes": o.get("dl", 0),
            "ownership": o.get("ownership", g.get("ownership")),
            "license_source": o.get("license_source", g.get("license_source")),
            "installed": installed,
            "archived": (not installed) and appid in archived,
            "app": (inst or {}).get("app"),
            "folder": (inst or {}).get("folder"),
            "smoke": smoke.get(appid),
        })
    rows.sort(key=lambda r: (not r["installed"], not r["native"], r["name"].lower()))
    limit = int(args.get("limit") or 0)
    return {"total": len(rows), "rows": rows[:limit] if limit else rows}


def cmd_detail(appid):
    context = catalog_context()
    routes, installed = read_library(context)
    reg = installed.get(appid, {})
    g = next((x for x in routes if str(x["appid"]) == appid), None)
    o = next((x for x in windows_rows(context) if str(x["appid"]) == appid), {})
    evidence_row = o
    if g and not evidence_row and g.get("metadata_status") == "missing":
        # 平台资料未齐仍能查看已有引擎证据；这些内容包不因此进入下载列表。
        evidence_row = next((x for x in context["meta"].get("apps", [])
                             if str(x.get("appid")) == appid), {})
    refresh_renpy_profile = bool(g and appid == "2118420" and g.get("route") in ("renpy", "renpy-native"))
    if g and evidence_row and g.get("route") != "steam" and (g.get("source") != "manifest" or refresh_renpy_profile):
        from library_discovery import refine_route
        from manifest_route_cache import ManifestRouteCache
        cache = ManifestRouteCache(STEAM, GAMES.resolve() / ".dryport-catalog/manifest-cache")
        g = refine_route(g, evidence_row, steam=STEAM, route_cache=cache)
    d = {"appid": appid, "found": bool(g)}
    if g:
        d.update({k: g.get(k) for k in ("name", "engine", "route", "route_name", "confidence", "evidence", "risk", "source", "manifest_note", "platform_note")})
        d["route_label"] = ROUTE_LABEL.get(g.get("route"), g.get("route"))
        d["native"] = g.get("route") in NATIVE_ROUTES
        d.update(tier_describe(g.get("route")))
    d["download_bytes"] = o.get("dl", 0)
    d.update({k: reg.get(k) for k in ("folder", "app")})
    d["smoke"] = smoke_latest().get(appid)
    try:
        import uninstall_purge
        d["archives"] = uninstall_purge.archives(appid)   # 界面「恢复归档」按钮的依据；新的在前
    except Exception as error:   # 归档列表出错不能拖垮详情
        d["archives"], d["archives_error"] = [], str(error)
    if reg.get("folder"):
        d["folder_bytes"] = dir_size(reg["folder"])
    if reg.get("app"):
        d["app_bytes"] = dir_size(reg["app"])
    if not g or g.get("route") != "steam":
        gate = subprocess.run([sys.executable, str(LIB / "risk.py"), "--gate", appid], capture_output=True, text=True)
        d["risk_gate"] = {"code": gate.returncode, "text": (gate.stdout + gate.stderr).strip()}
        if g and g.get("metadata_status") == "missing" and gate.returncode in (0, 1):
            # 风险进程与目录可能读取了不同时间的元资料；先刷新目录，不能拿单独通过替它补齐平台。
            d["risk_gate"] = {"code": 3, "text": g.get("platform_note") or
                              "Steam 平台资料尚未齐全，请等待库加载后刷新，再安装。"}
    out(d)


def cmd_install(appid):
    """Stream `dryport install` as NDJSON so a GUI can show live progress."""
    if not steam_running():
        out({"event": "error", "msg": "Steam 没开。下载走你已登录的 Steam 客户端，先把 Steam 打开。"})
        return 1
    t0 = time.time()
    out({"event": "start", "appid": appid})
    env = child_environment()
    p = subprocess.Popen([str(SN_HOME / "bin/dryport"), "install", appid], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    pct = re.compile(r"(\d{1,3})\s*%")
    for line in p.stdout:
        line = line.rstrip()
        ev = {"event": "log", "line": line, "t": round(time.time() - t0, 1)}
        m = pct.search(line)
        if m:
            ev["percent"] = int(m.group(1))
        out(ev)
    rc = p.wait()
    scan_installed(persist=True)
    out({"event": "done", "code": rc, "seconds": round(time.time() - t0, 1)})
    return rc


def _stream(argv, label):
    """把 dryport 的输出按行流成 NDJSON，跟 cmd_install 一个路子。"""
    t0 = time.time()
    out({"event": "start", "cmd": label})
    env = child_environment()
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    for line in p.stdout:
        out({"event": "log", "line": line.rstrip(), "t": round(time.time() - t0, 1)})
    rc = p.wait()
    if label != "survey":
        scan_installed(persist=label != "clean" or "--go" in argv)
    out({"event": "done", "code": rc, "seconds": round(time.time() - t0, 1)})
    return rc


def cmd_uninstall_plan(appid):
    """彻底卸载的账单：只读，JSON。"""
    import uninstall_purge
    print(json.dumps(uninstall_purge.plan(appid, scan_installed().get(str(appid), {})), ensure_ascii=False))
    return 0


def cmd_archive(appid):
    """归档（可逆）：经官方接口移除快捷方式，app 原样移入 .uninstalled。GUI 自己弹确认框，所以直接 --yes。"""
    return _stream([str(SN_HOME / "bin/dryport"), "archive", appid, "--yes"], "archive")


def cmd_restore(appid):
    """归档的反操作：最近一次归档的 app 原样放回并经官方接口重新加入 Steam 库。"""
    return _stream([str(SN_HOME / "bin/dryport"), "restore", appid, "--yes"], "restore")


def cmd_uninstall(appid, saves=False, keep=False):
    """卸载（不可逆）：归档后删掉本工具自己的下载目录与归档 app；saves 才连存档状态目录。keep 是旧用法，等于归档。"""
    if keep:
        return cmd_archive(appid)
    argv = [str(SN_HOME / "bin/dryport"), "uninstall", appid, "--yes"]
    if saves:
        argv.append("--saves")
    return _stream(argv, "uninstall")


STEAM_DEBUG_FLAG = STEAM / "Steam.AppBundle/Steam/Contents/MacOS/.cef-enable-remote-debugging"


def cmd_steam_debug(action="status"):
    """Valve 自己的调试开关文件。status 只读；enable 在用户确认后创建一个空文件，
    不改任何 Steam 二进制或配置内容；本机 8080 端点让本工具经已登录客户端下载与添加快捷方式。
    disable只删除本工具创建的那个空文件，删后须重启 Steam 端点才关闭。"""
    bundle = STEAM_DEBUG_FLAG.parent
    d = {"path": str(STEAM_DEBUG_FLAG), "bundle_present": bundle.is_dir(), "flag_exists": STEAM_DEBUG_FLAG.is_file(),
         "steam_running": steam_running(), "endpoint_ok": False}
    if d["steam_running"]:
        d["endpoint_ok"] = bool(debug_port_enabled())
    if action == "enable":
        if not bundle.is_dir():
            out({**d, "event": "error", "msg": f"没有找到 Steam 客户端包：{bundle}；先安装并打开一次 Steam"}); return 1
        if STEAM_DEBUG_FLAG.is_symlink() or (STEAM_DEBUG_FLAG.exists() and not STEAM_DEBUG_FLAG.is_file()):
            out({**d, "event": "error", "msg": f"开关路径已被别的东西占用，未改动：{STEAM_DEBUG_FLAG}"}); return 1
        created = False
        if not STEAM_DEBUG_FLAG.exists():
            STEAM_DEBUG_FLAG.touch(); created = True
        d.update({"flag_exists": True, "created": created, "restart_needed": not d["endpoint_ok"]})
    elif action == "disable":
        if STEAM_DEBUG_FLAG.is_symlink() or (STEAM_DEBUG_FLAG.exists() and not STEAM_DEBUG_FLAG.is_file()):
            out({**d, "event": "error", "msg": f"开关路径不是本工具创建的空文件，未改动：{STEAM_DEBUG_FLAG}"}); return 1
        removed = False
        if STEAM_DEBUG_FLAG.exists():
            if STEAM_DEBUG_FLAG.stat().st_size != 0:
                out({**d, "event": "error", "msg": f"开关文件不是空的（不是本工具所建），未改动：{STEAM_DEBUG_FLAG}"}); return 1
            STEAM_DEBUG_FLAG.unlink(); removed = True
        d.update({"flag_exists": False, "removed": removed, "restart_needed": removed})
    elif action != "status":
        out({"event": "error", "msg": f"steam-debug 只认 status / enable / disable，不认 {action}"}); return 2
    out(d)
    return 0


def cmd_clean(go=False, deep=False):
    """清残渣。不加 go 就是只算账。"""
    argv = [str(SN_HOME / "bin/dryport"), "clean"]
    if go:
        argv.append("--go")
    if deep:
        argv.append("--deep")
    return _stream(argv, "clean")


def cmd_play(appid):
    reg = scan_installed().get(appid)
    if not reg:
        out({"event": "error", "msg": "这个游戏还没装"})
        return 1
    if reg.get("app"):
        subprocess.Popen(["open", "-a", reg["app"]])
        out({"event": "launched", "how": "native", "path": reg["app"]})
        return 0
    wrap = RUNTIMES / "wine-wrap.sh"
    if reg.get("folder") and wrap.exists():
        out({"event": "error", "msg": "只下载了文件，还没打包。先 dryport wrap。"})
        return 1
    out({"event": "error", "msg": "没有可启动的东西"})
    return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip()); return 2
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "state":
        return cmd_state()
    if cmd == "snapshot":
        return cmd_snapshot()
    if cmd == "survey":
        if rest not in ([], ["--deep"]):
            raise ValueError("survey 只接受 --deep，用于识别全部本机文件清单")
        return _stream([str(SN_HOME / "bin/dryport"), "survey", *rest], "survey")
    if cmd == "library":
        args, i = {}, 0
        while i < len(rest):
            a = rest[i]
            if a == "--limit": i += 1; args["limit"] = rest[i]
            elif a == "--q": i += 1; args["q"] = rest[i]
            elif a == "--route": i += 1; args["route"] = rest[i]
            elif a == "--only-playable": args["only_playable"] = True
            elif a == "--installed": args["installed"] = True
            elif a == "--archived": args["archived"] = True
            i += 1
        return library(args)
    if cmd == "name":
        # 纯文本输出，给 shell 用。文件名安全：去掉路径分隔符与首尾空白/点。
        if not rest:
            return 2
        g = {str(x["appid"]): x for x in all_routes()}.get(rest[0])
        if not g:
            g = next((row for row in windows_rows()
                      if str(row["appid"]) == rest[0]), None)
        if not g:
            return 1
        n = re.sub(r"[/:\x00]", " ", g["name"]).strip().strip(".")
        n = re.sub(r"\s+", " ", n)
        if not n:
            return 1
        print(n)
        return 0
    if cmd == "clean":
        return cmd_clean(go="--go" in rest, deep="--deep" in rest)
    if cmd == "steam-debug":
        return cmd_steam_debug(rest[0] if rest else "status")
    if cmd == "uninstall-plan":
        return cmd_uninstall_plan(rest[0])
    if cmd in ("uninstall", "archive", "restore"):
        if not rest:
            out({"event": "error", "msg": "需要 appid"}); return 2
        if cmd == "archive":
            return cmd_archive(rest[0])
        if cmd == "restore":
            return cmd_restore(rest[0])
        return cmd_uninstall(rest[0], saves="--saves" in rest or "--purge-saves" in rest,
                             keep="--keep-download" in rest or "--keep" in rest)
    if cmd in ("detail", "install", "play", "reveal", "forget"):
        if not rest:
            out({"event": "error", "msg": "需要 appid"}); return 2
        appid = rest[0]
        if cmd == "detail": return cmd_detail(appid)
        if cmd == "install": return cmd_install(appid)
        if cmd == "play": return cmd_play(appid)
        if cmd == "reveal":
            r = scan_installed().get(appid, {})
            p = r.get("app") or r.get("folder")
            if p: subprocess.run(["open", "-R", p]); out({"event": "revealed", "path": p}); return 0
            out({"event": "error", "msg": "没装"}); return 1
        if cmd == "forget":
            r = registry(); r.pop(appid, None); save_registry(r); out({"event": "forgotten"}); return 0
    out({"event": "error", "msg": f"不认识的命令 {cmd}"})
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (OSError, ValueError) as error:
        print(f"未完成：{error}", file=sys.stderr)
        sys.exit(1)
