#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""reclassify.py — 把已经取到的 depot 文件清单重新分类，并与 appinfo 判定、封号风险判定合并成最终路线表。

分类只读 depotcache 里的 *.manifest.txt（manifest-oracle 解出来的），不发任何请求，
所以规则随时可以改了重跑，不用再要一次清单。

用法: reclassify.py [--json 输出文件]
"""
import json, os, re, sys, collections, hashlib

from paths import SN_GAMES, STEAM

DC = str(STEAM / "depotcache")
NAME = re.compile(r"^\s*[\d,]+\s+\d+\s+[0-9a-f]{40}\s+\d+\s+(.+?)\s*$")

# (引擎, 判据正则, 路线, 置信度)。顺序即优先级：反作弊最先，然后确证特征，最后弱特征。
RULES = [
    ("EasyAntiCheat",  r"easyanticheat|start_protected_game\.exe$",              "refuse", "确证"),
    ("BattlEye",       r"(^|/)be(service|client)|battleye",                      "refuse", "确证"),
    ("nProtect",       r"gameguard|npgmup\.des",                                 "refuse", "确证"),
    # 下面两条是 2026-09-13 检查资源 4557 份清单补的。判据都锚在目录或精确文件名上：
    # 只写 anticheatexpert 会误伤资源名，只写 pb 会误伤一大片（实测 vanguard、ricochet、inca
    # 这类词在普通游戏资源里遍地都是）。锚定后全库命中 ACE 2 个、PunkBuster 2 个，零误伤。
    ("Anti-Cheat Expert", r"(^|/)anticheatexpert/|(^|/)ace-(base|core)\.sys$",     "refuse", "确证"),
    ("PunkBuster",     r"(^|/)pb(svc|setup)[^/]*\.exe$|(^|/)punkbuster/",          "refuse", "确证"),
    ("Unity-IL2CPP",   r"(^|/)gameassembly\.dll$|global-metadata\.dat$",         "wine",   "确证"),
    ("Unity-Mono",     r"(^|/)assembly-csharp\.dll$|monobleedingedge/",          "unity",  "确证"),
    ("Unity",          r"(^|/)unityplayer\.dll$|unity default resources$|\.unity3d$", "unity", "确证"),
    ("Unity（后端待确认）", r"_data/globalgamemanagers(?:\.assets)?$",                "unity", "确证"),
    ("RPGMaker-MVMZ",  r"(^|/)nw\.dll$|rpg_core\.js$|rmmz_core\.js$",             "nwjs",   "确证"),
    ("RPGMaker-MVMZ 候选", r"\.rpgsave$",                                       "nwjs",   "推测"),
    ("RPGMaker-RGSS",  r"\.rgss[123]?a$|rgss[0-9]{3}[ej]?\.dll$|\.rvdata2?$|\.rxdata$", "mkxp", "确证"),
    ("Ren'Py",         r"(^|/)renpy/|\.rpa$|\.rpyc$",                            "renpy",  "确证"),
    # Godot 导出的 pck 一定在根目录且与可执行同名；别的引擎也用 .pck 当资源包（一款 Wine 游戏 就是），所以必须限死在根目录
    ("Godot",          r"(^|/)godot[^/]*\.(exe|dll|pck)$",                         "godot",  "确证"),
    ("KiriKiri",       r"\.xp3$|(^|/)(krkr|kirikiri)",                           "krkr",   "确证"),
    # 一款 SiglusEngine 视觉小说 靠这条从「Godot?」纠回来。siglusengine*.exe 这个名字不会出现在别的引擎里。
    ("SiglusEngine",   r"(^|/)siglusengine[^/]*\.exe$|(^|/)gameexe\.dat$",         "vn",     "确证"),
    ("Unreal",         r"-(win64|win32|wingdk)-shipping\.exe$|\.uasset$|\.upk$|\.utoc$|/engine/binaries/", "wine", "确证"),
    # 2026-09-13 逐个核过 17 个被判成 FNA 的游戏，只有 3 个真是 FNA。旧判据拿 `.xnb` 当确证太宽：
    # `.xnb` 只是 XNA 时代的内容格式，SFML.Net 游戏和 WinForms 加 Lua 游戏
    # 都带它。而这三类的结局完全不同：FNA 官方有 mac 构建可以换壳；MonoGame 的 WindowsDX 后端
    # 整套 SharpDX 绑死 DirectX，只能走 Wine；微软 XNA 早已停更且只有 Windows。
    ("FNA",            r"(^|/)fna\.dll$|(^|/)fna3d\.dll$",                        "fna",    "确证"),
    ("MonoGame DesktopGL", r"(^|/)monogame\.framework\.desktopgl\.dll$",           "fna",    "确证"),
    ("微软 XNA",        r"microsoft\.xna\.framework",                              "wine",   "确证"),
    ("Cocos2d-x",      r"(^|/)libcocos2d|cocos2d",                                 "maybe",  "确证"),
    ("LÖVE",           r"(^|/)love\.dll$|\.love$",                                "love",   "确证"),
    ("Java/libGDX",    r"(^|/)(javaw?\.exe|jvm\.dll)$|(^|/)jre/",                  "maybe",  "确证"),
    ("GameMaker",      r"(^|/)data\.win$",                                       "maybe",  "确证"),
    ("WolfRPG",        r"(^|/)data\.wolf$|guruguru",                             "maybe",  "确证"),
    ("NScripter",      r"(^|/)(nscript\.dat|arc\.nsa)|\.ns2$",                   "maybe",  "确证"),
    ("AdventureGameStudio", r"\.crm$|(^|/)acsetup\.cfg$",                        "maybe",  "确证"),
    ("ScummVM 候选",   r"(^|/)scummvm\.exe$",                                    "maybe", "推测"),
    ("DOSBox 候选",    r"(^|/)dosbox(?:-x)?\.exe$",                               "maybe", "推测"),
    ("Flash/AIR",      r"adobe air\.dll$|\.swf$",                                "wine",   "推测"),
]
# 存档后缀和通用 .pck 等弱线索不能抢在 RGSS、Unreal 等确证特征之前。
RULES = sorted(RULES, key=lambda rule: rule[3] != "确证")
COMPILED = [(n, re.compile(rx, re.I), rt, cf) for n, rx, rt, cf in RULES]
# 先排除所有规则都不可能命中的资源文件，最终结论仍由原正则给出。
# 规则变化时自动退回完整扫描；扩充快筛范围并验证等价后才更新指纹。快筛不决定路线。
_FILTER_RULE_HASH = "a469a45da6a845300a49173aeec86e3cdc18c7c0bb1fd3af26b0eb7bdfe98501"
_FILTER_RULES = tuple(COMPILED) if hashlib.sha256("\n".join(rx.pattern for _, rx, _, _ in COMPILED).encode()).hexdigest() == _FILTER_RULE_HASH else ()
_CANDIDATE_EXTENSIONS = frozenset((
    "dll", "dat", "exe", "unity3d", "js", "rpgsave", "rgssa", "rgss1a", "rgss2a", "rgss3a",
    "rvdata", "rvdata2", "rxdata", "rpa", "rpyc", "pck", "xp3", "uasset", "upk", "utoc",
    "xnb", "win", "wolf", "ns2", "crm", "cfg", "swf", "love", "sys",
))
_CANDIDATE_PATH = re.compile(
    r"easyanticheat|beservice|beclient|battleye|gameguard|npgmup\.des|monobleedingedge/|"
    r"unity default resources|_data/globalgamemanagers|renpy/|krkr|kirikiri|/engine/binaries/|microsoft\.xna\.framework|"
    r"monogame\.framework|cocos2d|jre/|guruguru|nscript\.dat|arc\.nsa|anticheatexpert|ace-base|ace-core|punkbuster|pbsvc|pbsetup")
ROUTE_NAME = {
    "webkit": "WebKit 原生（安装时校验版本）",
    "love": "LÖVE 原包直读（版本与扩展待确认）",
    "unity": "Unity macOS 播放器（架构依版本）+ 着色器重定向", "nwjs": "NW.js 原生换壳", "mkxp": "mkxp-z 原生",
    "renpy": "Ren'Py 候选（未接通）", "godot": "Godot 官方 mac 模板", "krkr": "krkrsdl2 原生",
    "fna": "FNA/MonoGame 原生（未接通）", "maybe": "待定，需逐个看", "wine": "Wine + DXMT 兜底",
    "vn": "视觉小说（引擎待确认）", "refuse": "不碰（风险拦截）", "unknown": "待识别",
}
NATIVE = {"unity", "nwjs", "mkxp", "godot", "krkr", "love", "webkit"}

def files_of(depot, gid):
    t = os.path.join(DC, f"{depot}_{gid}.manifest.txt")
    if not os.path.exists(t): return None
    out = []
    for l in open(t, encoding="latin1"):
        m = NAME.match(l)
        if m: out.append(m.group(1).replace("\\", "/").lower())
    return out

def runtime_combination(files, roots):
    """只有根目录游戏入口与成套运行时并存才给候选；不授予安装或平台兼容资格。"""
    helper_names = {"setup", "uninstall", "unins000", "python", "pythonw", "launcher",
                    "steamadapter", "help", "nw", "nwjs", "node", "chrome", "electron", "crashpad_handler"}
    entries = sorted(f[:-4] for f in roots if f.endswith(".exe") and f[:-4] not in helper_names)
    if not entries:
        return None
    matches = []
    if {"data.solarus", "libsolarus.dll", "luasteam.dll"} <= roots:
        matches.append(("Solarus 候选", "根目录 data.solarus + libsolarus.dll + luasteam.dll + 游戏 EXE；运行时与 Lua 扩展未接通"))
    if {"hlboot.dat", "libhl.dll", "heaps.hdll"} <= roots:
        matches.append(("HashLink/Heaps 候选", "根目录 hlboot.dat + libhl.dll + heaps.hdll + 游戏 EXE；字节码与 HDLL 兼容未接通"))
    if {"nw.pak", "icudtl.dat", "ffmpegsumo.dll", "libegl.dll", "libglesv2.dll", "d3dcompiler_47.dll"} <= roots:
        matches.append(("旧 NW.js 候选", "根目录 nw.pak + icudtl.dat + ffmpegsumo.dll + EGL/GLES + D3D 编译库 + 游戏 EXE；原容器入口未接通"))
    if {"requirements.txt", "steamadapter.py"} <= roots and any(entry + ".py" in roots for entry in entries):
        # PyInstaller 的 _internal 与旧根目录布局分别核对；不把任意依赖目录里的 pygame 算进来。
        binaries = {f.replace("\\", "/").lower() for f in files if f.lower().endswith((".dll", ".pyd"))}
        pygame = None
        for prefix in ("", "_internal/"):
            for filename in sorted(binaries):
                version = re.fullmatch(re.escape(prefix) + r"python(3[0-9]+)\.dll", filename)
                if not version:
                    continue
                for architecture in ("win_amd64", "win32"):
                    abi = "cp" + version.group(1) + "-" + architecture
                    required = {prefix + "pygame/" + module + "." + abi + ".pyd" for module in ("base", "display")}
                    if required <= binaries:
                        pygame = filename + " + pygame base/display " + abi
                        break
                if pygame:
                    break
            if pygame:
                break
        if pygame:
            matches.append(("Python/Pygame 候选", "根目录同名 .py/.exe + requirements.txt + SteamAdapter.py；" + pygame + "；依赖与原生扩展未接通"))
    if len(matches) > 1:
        return "多个引擎候选", "maybe", "推测", "、".join(name for name, _ in matches) + "；主入口待核实，运行时未接通"
    if matches:
        name, evidence = matches[0]
        return name, "maybe", "推测", evidence
    return None

def classify(files):
    candidates = files
    if tuple(COMPILED) == _FILTER_RULES:
        candidates = [f for f in files if not f.isascii() or "\n" in f
                      or f.rsplit(".", 1)[-1].lower() in _CANDIDATE_EXTENSIONS
                      or _CANDIDATE_PATH.search(f.lower())]
    for name, rx, route, conf in COMPILED:
        for f in candidates:
            if rx.search(f): return name, route, conf, f[:80]
    # 旧 NW.js 可以把应用放在 package.nw；限定根目录组合，避免把附带启动器当成游戏本体。
    roots = {f.lower() for f in files if "/" not in f and "\\" not in f}
    if "nw.pak" in roots:
        if "package.nw" in roots:
            return "NW.js", "nwjs", "确证", "根目录 nw.pak + package.nw"
        if {"package.json", "index.html"} <= roots:
            return "NW.js", "nwjs", "推测", "根目录 nw.pak + package.json + index.html；入口内容待确认"
    # Chromium 的单个文件也会出现在帮助页和启动器里；只认游戏根目录的完整组合。
    # 只保留组合所需的小集合，未知的大资源包不再复制一整份文件名表。
    paths = {f.replace("\\", "/").lower() for f in files
             if f.lower().endswith(("app.asar", "package.json", "rpg_rt.ldb", "rpg_rt.lmt", ".lmu"))}
    if {"resources.pak", "icudtl.dat", "v8_context_snapshot.bin"} <= roots:
        for package in ("resources/app.asar", "resources/app/package.json"):
            if package in paths:
                return "Electron 候选", "maybe", "推测", "根目录 Chromium 文件 + " + package + "；运行时与原生模块待确认"
    # 先支持已核对的根目录和 data/ 结构；不把嵌套工具、样例或同名 DLL 当成主游戏。
    for directory in ("", "data/"):
        if {directory + "rpg_rt.ldb", directory + "rpg_rt.lmt"} <= paths:
            maps = sorted(f for f in paths if re.fullmatch(re.escape(directory) + r"map[0-9]+\.lmu", f))
            if maps:
                return "RPG Maker 2000/2003（EasyRPG 候选）", "maybe", "推测", \
                    (directory or "根目录 ") + "RPG_RT.ldb + RPG_RT.lmt + " + maps[0].rsplit("/", 1)[-1] + "；运行时未接通"
    # MonoGame 分后端：WindowsDX 拉一整套 SharpDX 绑死 DirectX，换 mac 运行时无解；
    # DesktopGL 走 SDL 加 OpenGL，才有换壳余地。只有 monogame.framework.dll 而认不出后端的，
    # 给候选不给路线。（DesktopGL 的确证判据在上面的 RULES 里，这里只处理剩下两种。）
    lowered = {f.lower() for f in files}
    if any(f == "monogame.framework.dll" or f.endswith("/monogame.framework.dll") for f in lowered):
        if any("sharpdx" in f for f in lowered):
            return "MonoGame WindowsDX", "wine", "确证", "monogame.framework.dll 与 SharpDX 同包：DirectX 后端，换不动"
        return "MonoGame（后端待定）", "maybe", "推测", "有 monogame.framework.dll 但认不出 DesktopGL 还是 WindowsDX"

    # Godot 导出的包一定是 `游戏名.pck` 与同名 `游戏名.exe` 成对。
    # 2026-09-13 抽查发现，只看「根目录有个 .pck」会把 6 个游戏判错：
    # 一类策略游戏 的 gamedata.pck/music.pck/patch.pck、一款战术游戏 的 data.pck、
    # 一款 SiglusEngine 视觉小说 的 scenezh.pck（它其实是 SiglusEngine）。这些包要是真按 Godot 模板打包，出来就是坏的。
    # 配对之后 28 个候选里 22 个留下，全部是真 Godot。
    for pck in sorted(f for f in roots if f.endswith(".pck")):
        if pck[:-4] + ".exe" in roots:
            return "Godot?", "godot", "推测", "根目录 " + pck + " 与同名 exe 成对；运行时版本未核对"
    # 成套新信号也不能抢掉前面已识别的引擎、原包组合或风险拒绝。
    combination = runtime_combination(files, roots)
    if combination:
        return combination
    return "未识别", "unknown", "无", unknown_evidence(files)


def unknown_evidence(files):
    """认不出引擎时，把文件清单里能说明「下载后会遇到什么」的事实写进证据（2026-09-13 用户询问「待定的 1487 个是不是不可用」；
    这些游戏下载后走 Wine 兜底，用户在下载前该知道位数、DirectX 时代和主程序有没有）。没有可说的就只报文件数。"""
    lowered = [f.replace("\\", "/").lower() for f in files]
    hints = []
    api64 = any(f.endswith("steam_api64.dll") for f in lowered)
    api32 = any(f == "steam_api.dll" or f.endswith("/steam_api.dll") for f in lowered)
    if api64 and api32:
        hints.append("带 32 位与 64 位 steam_api")
    elif api64:
        hints.append("64 位（带 steam_api64.dll）")
    elif api32:
        hints.append("32 位（带 steam_api.dll）")
    if any(f.endswith("vulkan-1.dll") for f in lowered):
        hints.append("带 Vulkan 库")
    if any(re.search(r"(^|/)d3dcompiler_4[67]\.dll$", f) or re.search(r"(^|/)d3dx11_\d+\.dll$", f) for f in lowered):
        hints.append("DirectX 11 时代文件")
    elif any(re.search(r"(^|/)d3dx9_\d+\.dll$", f) or re.search(r"(^|/)(dxwebsetup|dxsetup)\.exe$", f) for f in lowered):
        hints.append("DirectX 9 时代文件")
    roots = [f for f in lowered if "/" not in f and f.endswith(".exe")
             and not re.search(r"unins|setup|redist|vcredist|dxsetup|crash|launcher|config|report", f)]
    if not roots:
        hints.append("根目录没有主程序 exe")
    elif len(roots) > 1:
        hints.append(f"根目录 {len(roots)} 个 exe")
    base = f"{len(files)} 个文件里没有已知特征"
    return base + ("；" + "、".join(hints) + "；下载后走 Wine 兜底，未验证" if hints else "")

def main():
    # 延迟导入，避免 discovery 复用本模块分类规则时形成导入环。
    import library_discovery as discovery
    directory = discovery.current_catalog_dir()
    if directory is not None:
        path = directory / "windows-library.json"
        if not path.exists():
            path = directory / "owned-windows.json"
        scope = "当前账号目录中的 Windows 专属游戏"
    else:
        path = SN_GAMES / "owned-windows.json"
        scope = "旧快照中的 Windows 专属游戏（尚未确认当前账号）"
    owned = {str(r["appid"]): r for r in json.loads(path.read_text())}
    out = []
    got = miss = 0
    for appid, r in owned.items():
        result = discovery.refine_route({**r, "appid": appid}, {**r, "appid": appid})
        got += result.get("files", 0) > 0
        miss += result.get("files", 0) == 0
        out.append(result)
    print(f"{scope} {len(out)} 个：有文件清单的 {got}，只有元资料线索的 {miss}")
    by = collections.Counter(o["route_name"] for o in out)
    for k, v in by.most_common(): print(f"  {k}: {v}")
    nat = [o for o in out if o["route"] in NATIVE]
    print(f"\n原生运行时候选 {len(nat)} 个（其中文件清单确证的 {sum(1 for o in nat if o['confidence']=='确证')} 个）；尚不代表可玩验证")
    print("引擎分布:", dict(collections.Counter(o["engine"] for o in nat).most_common(10)))
    if "--json" in sys.argv:
        p = sys.argv[sys.argv.index("--json") + 1]
        json.dump(sorted(out, key=lambda o: o["appid"]), open(p, "w"), ensure_ascii=False, indent=1)
        print("清单写到", p)
if __name__ == "__main__": main()
