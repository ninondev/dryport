#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# wine-wrap.sh — 把通过风险与 DRM 检查的 Windows 游戏生成独立 macOS .app
# 用法: wine-wrap.sh <游戏目录> <主 exe 相对路径> <显示名> [图标.png]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
require_repack_permission "${1:-}" || exit $?
# 用哪套 Wine：优先自编的 wine-cx（CrossOver 26.3 源码，带 winemac.drv 的 Metal 接口，
# 配 DXMT 能跑 DirectX 11；实测 A Space for the Unbound 出完整标题画面）。
# 退回 Wine Staging 的话没有 D3D11，只能跑 DX9 以下的老游戏。
if [ -n "${SN_WINE_ROOT:-}" ] && [ -x "$SN_WINE_ROOT/bin/wine" ]; then
  WINE_ROOT="$SN_WINE_ROOT"; WINE_KIND="SN_WINE_ROOT 指定（${SN_WINE_ROOT}）"   # 试新编的 Wine 用，不动 wine-cx 与已装游戏
elif [ -x "$RUNTIMES/wine-cx/bin/wine" ]; then
  WINE_ROOT="$RUNTIMES/wine-cx"; WINE_KIND="wine-cx（图形能力待验）"
elif [ -d "$RUNTIMES/Wine Staging.app" ]; then
  WINE_ROOT="$RUNTIMES/Wine Staging.app/Contents/Resources/wine"; WINE_KIND="Wine Staging(无 D3D11)"
else
  echo "错: 既没有 wine-cx 也没有 Wine Staging.app" >&2; exit 1
fi

GAME_DIR="${1:?用法: wine-wrap.sh <游戏目录> <主exe> <显示名> [图标]}"
EXE_REL="${2:?缺主 exe 相对路径}"
NAME="${3:?缺显示名}"
ICON="${4:-}"
TARGET="$SN_GAMES/$NAME.app"
# 引擎决定窗口化参数（SN_WINE_ENGINE 由 dryport 传，手工调用不传就只靠虚拟桌面）；
# 游戏带对应位数的 Steam API DLL 才有桥接能力；启动时仍需逐游戏明确开启。
ENGINE="${SN_WINE_ENGINE:-other}"
# 主程序位数决定接哪份桥：PE32 → 32 位 steam_api.dll（含按版本选择的 C++ 虚表），PE32+ → steam_api64.dll。读 PE 头的机器类型，不靠文件名。
ARCH=$(python3 - "$GAME_DIR/$EXE_REL" <<'PY'
import struct, sys
try:
    with open(sys.argv[1], "rb") as fh:
        data = fh.read(0x200)
    pe = struct.unpack_from("<I", data, 0x3C)[0] if data[:2] == b"MZ" and len(data) >= 0x40 else -1
    with open(sys.argv[1], "rb") as fh:
        fh.seek(pe); head = fh.read(6)
    machine = struct.unpack_from("<H", head, 4)[0] if head[:4] == b"PE\0\0" else 0
    print({0x14C: "i386", 0x8664: "x86_64"}.get(machine, "unknown"))
except Exception:
    print("unknown")
PY
)
BRIDGE=0; BRIDGE_DLL=""
case "$ARCH" in
  i386)   BRIDGE_DLL="steam_api.dll";;
  x86_64) BRIDGE_DLL="steam_api64.dll";;
  *)      echo "主程序的 PE 头读不出机器类型：${EXE_REL}（${ARCH}），按 64 位处理" >&2; ARCH="x86_64"; BRIDGE_DLL="steam_api64.dll";;
esac
# 32 位游戏的 D3D11 要靠 DXMT 的 i386 PE 才走 Metal；缺失时使用 Wine 自带实现。
if [ "$ARCH" = i386 ] && [ ! -f "$WINE_ROOT/lib/wine/i386-windows/winemetal.dll" ]; then
  echo "注意：wine-cx 里没有 32 位 DXMT（i386-windows/winemetal.dll），这款 32 位游戏的 DirectX 11 会走 Wine 自带实现，可玩性与性能需实测；补装：tools/install-dxmt.sh" >&2
fi
# Unreal 把它埋在 Engine/Binaries/ThirdParty/Steamworks/Steamv147/Win64/（6 层），所以要往深处找
if [ -n "$BRIDGE_DLL" ] && [ -n "$(find "$GAME_DIR" -maxdepth 8 -name "$BRIDGE_DLL" -type f 2>/dev/null | head -1)" ]; then BRIDGE=1; fi
# 32 位桥支持扁平 API 与按版本选择的 C++ 虚表。桥默认关闭，启动时按游戏明确选择。
# 打包时 SN_WINE_BRIDGE=0 可关闭配置中的桥接能力；未连接 Windows Steam 时游戏可能初始化失败。
if [ "$ARCH" = i386 ] && [ "$BRIDGE" = 1 ] && [ "${SN_WINE_BRIDGE:-}" = 0 ]; then
  BRIDGE=0
  echo "SN_WINE_BRIDGE=0：32 位主程序不接桥（未连接 Steam，游戏行为需实测）" >&2
fi
python3 - "$GAME_DIR" "$EXE_REL" "$NAME" "$TARGET" <<'PY'
import os, pathlib, sys
game, exe, name, target = sys.argv[1:]
root = pathlib.Path(game).resolve(strict=True)
entry = pathlib.Path(exe)
if (not name or name in ('.', '..') or any(c in name for c in '/\\\0\r\n')
        or entry.is_absolute() or '..' in entry.parts or not exe or '\\' in exe
        or not (root / entry).is_file() or not (root / entry).resolve().is_relative_to(root)):
    raise SystemExit('显示名或主 exe 路径不合法；exe 必须是游戏目录内的文件。')
output = pathlib.Path(target).absolute()
if output.resolve().is_relative_to(root):
    raise SystemExit('输出不能位于原游戏目录内。')
if os.path.lexists(output):
    raise SystemExit(f'同名 app 已存在，已保留；请选择另一个显示名：{output}')
PY
# Steamworks DRM 包装（PE 节 .bind）：入口先要同一前缀里运行中的 Windows 版 Steam 客户端取票据解密，桥替代不了，本工具也不绕过 DRM。
# 检查器无法给出结果时也停止；不提供 DRM 拒绝的覆盖开关。
STEAM_DRM=0
if python3 -B "$SN_LIB/steam_drm.py" "$GAME_DIR/$EXE_REL" >/dev/null 2>&1; then :; else
  rc=$?
  if [ "$rc" = 3 ]; then
    echo "主程序 ${EXE_REL} 带 Steamworks DRM 包装（PE 节 .bind）。它启动时要在同一个 Wine 前缀里找运行中的 Windows 版 Steam 客户端并取票据解密，steam_api 桥替代不了；本工具不绕过 DRM，也不在前缀里装 Windows 版 Steam，所以这款不打包。" >&2
    exit 3
  else
    echo "could not inspect the main executable for DRM（steam_drm.py 退出 ${rc}）；停止打包，未生成 app。" >&2
    exit 4
  fi
fi
mkdir -p "$SN_GAMES"
STAGE=$(mktemp -d "$SN_GAMES/.wine-stage.XXXXXX")
OUT="$STAGE/$NAME.app"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"

# 游戏文件用 APFS 克隆, 不占额外空间
cp -Rc "$GAME_DIR" "$OUT/Contents/Resources/game"

# 桥不放进 app 克隆：WINEDLLOVERRIDES=steam_api,steam_api64=b 加 overlay 已经让按完整路径加载的游戏也拿到桥（probe32 实测），
# 克隆里保持游戏原件，这样 SN_WINE_BRIDGE=0 或 32 位不接桥时 Valve 的 DLL 原样加载。（09-13 曾就地替换过一轮，10:50 撤销。）

# Wine 位置记录为数据；app 不带越出签名封存边界的运行时链接。
echo "Wine 引擎: $WINE_KIND" >&2

cat > "$OUT/Contents/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
# 由 wine-wrap.sh 生成。目标: 让 Wine 尽量不露痕迹, 而且绝不独占全屏。
# 配置读取也是可中断的启动阶段。只收本次已知的读取子进程；运行阶段会换成下面的完整收尾处理。
STARTUP_PID=""
startup_signal(){
  trap '' INT TERM HUP
  if [ -n "$STARTUP_PID" ]; then
    kill -TERM "$STARTUP_PID" >/dev/null 2>&1 || true
    wait "$STARTUP_PID" 2>/dev/null || true
  fi
  exit 143
}
trap startup_signal INT TERM HUP
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RES="$HERE/Resources"
export WINEDEBUG="${SN_WINEDEBUG:--all}"   # 默认关掉调试输出省 CPU；自动验证传 SN_WINEDEBUG=-all,+seh,+loaddll 看缺 DLL 与异常
# macOS 的 OpenGL 只给 forward-compatible 的 3.2+ core 上下文，winemac.drv 对没带 FORWARD_COMPATIBLE 位的 3.2+ 请求直接拒绝
# （ERROR_INVALID_VERSION_ARB）；CrossOver 自己留了这个开关（CW Hack 24834）把位补上。SDL2 请求 3.2 core 时不带这个位（一款 OpenGL 游戏 2026-09-14），
# 所以默认打开；要关设 SN_CX_FWD_COMPAT_GL_CTX=0。
export CX_FWD_COMPAT_GL_CTX="${SN_CX_FWD_COMPAT_GL_CTX:-1}"
# 实测：写成空值时 wineboot 仍会卡在 mscoree 的 DllRegisterServer 等 wine-mono 安装框，
# 必须写 =d（禁用）才跳得过去。
export WINEDLLOVERRIDES="mscoree,mshtml=d"
# 配置一次读全：wine 根、主 exe、工具链、appid、引擎、是否接桥、桥目录、显示名，然后是窗口化参数（NUL 分隔，路径与名字都是数据）
WARGS=()
{
  STARTUP_PID=$!
  IFS= read -r -d '' WINE_ROOT; IFS= read -r -d '' GAME_EXE; IFS= read -r -d '' SCRIPT_HOME
  IFS= read -r -d '' APPID; IFS= read -r -d '' ENGINE; IFS= read -r -d '' BRIDGE; IFS= read -r -d '' BRIDGE_DIR; IFS= read -r -d '' APP_NAME; IFS= read -r -d '' STATE
  IFS= read -r -d '' ARCH; IFS= read -r -d '' BRIDGE_DLL; IFS= read -r -d '' WORKDIR; IFS= read -r -d '' N_LAUNCH_ARGS
  while IFS= read -r -d '' _a; do WARGS+=("$_a"); done
} < <(/usr/bin/python3 -E -s -S -B - "$RES/wine-launch.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
arch = c.get("arch") or "x86_64"
launch = list(c.get("launch_args") or [])   # Steam 启动配置里的参数在前，引擎窗口化参数在后（后者覆盖前者）
fields = [c["wine"], c["exe"], c["toolchain"], str(c.get("appid") or ""), c.get("engine") or "other",
          "1" if c.get("bridge") else "0", c.get("bridge_dir") or "", c.get("name") or "game", c.get("state") or "",
          arch, c.get("bridge_dll") or ("steam_api.dll" if arch == "i386" else "steam_api64.dll"),
          c.get("workdir") or "", str(len(launch))] + launch + list(c.get("windowed_args") or [])
sys.stdout.write("\0".join(fields) + "\0")
PY
)
STARTUP_PID=""
[ -n "${WINE_ROOT:-}" ] && [ -n "${GAME_EXE:-}" ] && [ -n "${STATE:-}" ] || { echo "wine-launch.json 读不出来" >&2; exit 1; }
# prefix、存档、桥 overlay、日志与着色器缓存都放在 app 外的状态目录（与 Ren'Py 的 .renpy-state 同一套规矩）：
# app 本体保持签名封存不被写坏，归档/恢复不动它，卸载时它是单独一行「存档」默认不删。
export WINEPREFIX="$STATE/prefix"
mkdir -p "$STATE/logs" "$STATE/dxmt-cache" || { echo "建不了状态目录 ${STATE}" >&2; exit 1; }
[ -f "$STATE/steam_appid.txt" ] || printf '%s' "$APPID" > "$STATE/steam_appid.txt"
export WINE="$WINE_ROOT/bin/wine"
export PATH="$WINE_ROOT/bin:$PATH"
export DXVK_LOG_LEVEL=none
export MVK_CONFIG_LOG_LEVEL=1         # MoltenVK 只报错误, 不刷屏
export DXMT_SHADER_CACHE_PATH="$STATE/dxmt-cache"   # DXMT 着色器缓存，第二次启动更快

# steambridge：游戏带 steam_api64.dll 时桥 DLL 铺到 overlay、按 Wine builtin 加载；helper 在本机把调用转给已登录的 Steam。
# 桥和 helper 都从运行时目录取当前版本（桥更新后下次启动自动换），标记不对就不接桥，未连接 Steam，游戏行为需实测。
# 接不接是逐游戏的选择，默认不接——见下面 bridge_wanted 与 launcher 里的启动闸。
HELPER_PID=""
OVERLAY_READY=0
bridge_prepare(){
  [ "$BRIDGE" = 1 ] || return 1
  local sub="x86_64-windows"; [ "$ARCH" = i386 ] && sub="i386-windows"
  [ -n "$BRIDGE_DIR" ] && [ -f "$BRIDGE_DIR/$BRIDGE_DLL" ] && [ -x "$BRIDGE_DIR/steambridge-helper" ] || { echo "steambridge 运行时缺 ${BRIDGE_DLL}（${BRIDGE_DIR}），本次不带 Steam 功能" >&2; return 1; }
  /usr/bin/python3 -E -s -S -B - "$BRIDGE_DIR/$BRIDGE_DLL" <<'PY' || { echo "桥 DLL 缺 Wine builtin 标记，本次不带 Steam 功能" >&2; return 1; }
import sys
raise SystemExit(0 if open(sys.argv[1], "rb").read(0x50)[0x40:0x50] == b"Wine builtin DLL" else 1)
PY
  mkdir -p "$STATE/overlay/$sub" || return 1
  if ! cmp -s "$BRIDGE_DIR/$BRIDGE_DLL" "$STATE/overlay/$sub/$BRIDGE_DLL"; then
    cp "$BRIDGE_DIR/$BRIDGE_DLL" "$STATE/overlay/$sub/$BRIDGE_DLL" || return 1
  fi
  OVERLAY_READY=1
  return 0
}
# 桥默认不接。SN_WINE_BRIDGE=1 是显式手工开关（实验/测试用）；=0 一律关；
# 未设时只认「dryport bridge enable <appid>」写进状态目录的选择文件。旧 app 的旧壳不读它，按它们建成时的行为跑。
# 选择文件是符号链接或不是普通文件时按未开启处理，并在 stderr 提示一次，不顺着链接读。
bridge_wanted(){
  case "${SN_WINE_BRIDGE:-}" in
    0) return 1;;
    1) return 0;;
  esac
  local choice="$STATE/bridge-choice"
  if [ -L "$choice" ] || { [ -e "$choice" ] && [ ! -f "$choice" ]; }; then
    echo "steambridge：bridge-choice 不是普通文件（符号链接或被别的东西占用），按未开启处理：$choice" >&2
    return 1
  fi
  [ -f "$choice" ] && grep -qx enabled "$choice"
}
bridge_start(){
  local port token
  port=$(/usr/bin/python3 -E -s -S -B -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])') || return 1
  # 本次启动的随机令牌（系统随机源 32 字节，十六进制）同时进入 helper 与 Wine 环境。
  # helper 验证令牌持有者；不验证游戏进程身份，也未建立同用户隔离。
  token=$(/usr/bin/python3 -E -s -S -B -c 'import secrets; print(secrets.token_hex(32))') || return 1
  : > "$STATE/logs/helper.log"
  STEAMBRIDGE_TOKEN="$token" "$BRIDGE_DIR/steambridge-helper" "$APPID" "$port" >> "$STATE/logs/helper.log" 2>&1 &
  HELPER_PID=$!
  local i
  for i in $(seq 1 50); do
    kill -0 "$HELPER_PID" 2>/dev/null || { HELPER_PID=""; echo "steambridge helper 起不来，见 $STATE/logs/helper.log；本次不带 Steam 功能" >&2; return 1; }
    grep -q 'listening on 127.0.0.1:' "$STATE/logs/helper.log" 2>/dev/null && break
    sleep 0.1
  done
  grep -q 'listening on 127.0.0.1:' "$STATE/logs/helper.log" 2>/dev/null || { kill "$HELPER_PID" 2>/dev/null; HELPER_PID=""; echo "steambridge helper 5 秒内没就绪，本次不带 Steam 功能" >&2; return 1; }
  export WINEDLLPATH="$STATE/overlay"
  export WINEDLLOVERRIDES="steam_api,steam_api64=b;mscoree,mshtml,winemenubuilder.exe=d"
  export STEAMBRIDGE_PORT="$port" STEAMBRIDGE_TOKEN="$token" SteamAppId="$APPID" SteamGameId="$APPID"
  return 0
}

# 运行时目录里新增的内建 DLL（后装的 32 位 DXMT、升级后的 Wine）在旧 prefix 的 system32/syswow64 里没有登记副本，
# 加载器按名字导入时报 not found（entry-07 2026-09-13，c0000135 秒退）。wineboot -u 本该补登记，实测在这套 wine-cx 上卡死，
# 所以自己补：只补缺的名字，不覆盖已有副本；运行时目录没有比戳更新的文件就整个跳过（几十毫秒）。
sync_builtins(){
  [ -d "$WINEPREFIX/drive_c/windows" ] || return 0
  local stamp="$WINEPREFIX/.sn-builtins-stamp" pair src dst n=0 f have name machine
  if [ -f "$stamp" ] && [ -z "$(find "$WINE_ROOT/lib/wine" -type f -newer "$stamp" -print -quit 2>/dev/null)" ]; then return 0; fi
  # 同一位数在 prefix 任何目录登记过的名字不重复放（wbem\ 子目录、spool\、windows 根目录都有），按文件的 PE 机器类型分位数：
  # syswow64 缺的名字不能因为 system32 有就跳过（winemetal.dll 正是 64 位有、32 位没有）。wineboot 有意不登记的 8 个名字也不放。
  for pair in x86_64-windows:system32:8664 i386-windows:syswow64:14c; do
    src="$WINE_ROOT/lib/wine/${pair%%:*}"; machine="${pair##*:}"; dst="${pair#*:}"; dst="$WINEPREFIX/drive_c/windows/${dst%%:*}"
    [ -d "$src" ] && [ -d "$dst" ] || continue
    have=$(/usr/bin/python3 -E -s -S -B - "$WINEPREFIX/drive_c/windows" "$machine" <<'PY'
import os, struct, sys
root, want = sys.argv[1], int(sys.argv[2], 16)
for d, _, files in os.walk(root):
    for f in files:
        if not f.lower().endswith(".dll"): continue
        try:
            with open(os.path.join(d, f), "rb") as fh:
                head = fh.read(0x40)
                if head[:2] != b"MZ": continue
                fh.seek(struct.unpack_from("<I", head, 0x3C)[0]); pe = fh.read(6)
            if pe[:4] == b"PE\0\0" and struct.unpack_from("<H", pe, 4)[0] == want: print(f.lower())
        except (OSError, struct.error):
            pass
PY
)
    for f in "$src"/*.dll; do
      [ -f "$f" ] || continue
      name=$(printf '%s' "${f##*/}" | tr 'A-Z' 'a-z')
      case "$name" in comctl32_v6.dll|diasymreader.dll|fusion.dll|msado15.dll|mscorwks.dll|msdaps.dll|msdasql.dll|oledb32.dll) continue;; esac
      printf '%s\n' "$have" | grep -qx -- "$name" && continue
      [ -e "$dst/${f##*/}" ] || { cp "$f" "$dst/" 2>/dev/null && n=$((n+1)); }
    done
  done
  [ "$n" = 0 ] || echo "运行时新增 ${n} 个内建 DLL 已登记进 prefix（${WINEPREFIX}）" >&2
  touch "$stamp"
}

if [ "${1:-}" = "--sn-check" ]; then
  # 自检：只解析配置、铺 overlay、给已有 prefix 补内建 DLL 登记，不起 Wine 也不起 helper。给测试与 doctor 用。
  # 这里的 overlay 铺设是诊断（证明桥机制完好）；bridge_wanted 才是真正启动时会不会接桥的决定。
  bridge_wanted; wanted=$?; [ "$wanted" = 0 ] && wanted=1 || wanted=0
  bridge_prepare >/dev/null 2>&1 || true
  sync_builtins || true
  helper_present=0; [ -n "$BRIDGE_DIR" ] && [ -x "$BRIDGE_DIR/steambridge-helper" ] && helper_present=1
  /usr/bin/python3 -E -s -S -B - "$WINE_ROOT" "$GAME_EXE" "$APPID" "$ENGINE" "$BRIDGE" "$OVERLAY_READY" "$helper_present" "$APP_NAME" "$STATE" "$ARCH" "$BRIDGE_DLL" "$WORKDIR" "$N_LAUNCH_ARGS" "$wanted" "${WARGS[@]}" <<'PY'
import json, sys
wine, exe, appid, engine, bridge, overlay, helper, name, state, arch, dll, workdir, n_launch, wanted, *wargs = sys.argv[1:]
n = int(n_launch or 0)
print(json.dumps({"wine": wine, "exe": exe, "appid": appid, "engine": engine, "bridge": bridge == "1",
                  "bridge_wanted": wanted == "1",
                  "overlay_ready": overlay == "1", "helper_present": helper == "1", "name": name, "state": state,
                  "arch": arch, "bridge_dll": dll, "workdir": workdir, "launch_args": wargs[:n], "windowed_args": wargs[n:]}, ensure_ascii=False))
PY
  exit 0
fi

# 收尾: 退出时把这个 prefix 里剩下的 wine 进程收干净。winedevice、services、
# explorer、没关掉的安装器弹窗全归 wineserver 管, 不杀就留在后台占内存。
# wineserver -k 只作用于 $WINEPREFIX, 不碰别的 Wine 实例, 也不碰系统。
# 所以下面不能用 exec; exec 会把这层壳换掉, trap 就不会跑了。
GAME_PID=""
cleanup(){
  # wineserver -k 只作用于本 prefix，不碰别的 Wine 实例，也不碰系统。
  # 个别 winedevice 会赖一下，隔一秒再来一刀；macOS 没有 /proc，只能靠 wineserver 自己收。
  "$WINE_ROOT/bin/wineserver" -k >/dev/null 2>&1 || true
  sleep 1
  "$WINE_ROOT/bin/wineserver" -k >/dev/null 2>&1 || true
  [ -z "$HELPER_PID" ] || kill "$HELPER_PID" >/dev/null 2>&1 || true
}
# bash 在前台子进程结束前不跑 trap：游戏必须放后台、这里 wait，信号一到先收游戏（run-guard 会整组收），再 cleanup。
# 2026-09-13 实测：前台跑游戏时 kill launcher 什么都不发生，wine、wineserver、helper 全留下。
on_signal(){
  trap '' INT TERM HUP
  if [ -n "$GAME_PID" ]; then
    kill -TERM "$GAME_PID" >/dev/null 2>&1 || true
    local i; for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$GAME_PID" >/dev/null 2>&1 || break; sleep 0.5; done
    kill -KILL "$GAME_PID" >/dev/null 2>&1 || true
    wait "$GAME_PID" 2>/dev/null || true
  fi
  cleanup
  trap - EXIT
  exit 143
}
trap on_signal INT TERM HUP
trap cleanup EXIT

# 首次建 prefix 走模板克隆：wineboot --init 要 30 到 115 秒，其中大头是往 system32/syswow64 复制 1.3 GB 内建 DLL 登记副本；
# 同一套 wine-cx 建出来的 prefix 内容彼此相同（用户名固定是 crossover，注册表只引用运行时目录，不引用 prefix 自己的路径），
# 所以第一次按老路建好后存成模板，之后每款游戏 cp -Rc（APFS 克隆，秒级、不占空间）。模板按 wine 二进制与 DXMT 文件的指纹分目录，
# 换了运行时自动用新模板。SN_WINE_PREFIX_TEMPLATE=0 回到每款都 wineboot 的老路（2026-09-14）。
prefix_template_dir(){
  local fp
  fp=$(/usr/bin/stat -f '%z:%m' "$WINE_ROOT/bin/wine" "$WINE_ROOT/lib/wine/x86_64-windows/winemetal.dll" "$WINE_ROOT/lib/wine/i386-windows/winemetal.dll" 2>/dev/null | /usr/bin/shasum -a 256 | cut -c1-16)
  [ -n "$fp" ] || return 1
  printf '%s' "$(dirname "$WINE_ROOT")/wine-prefix-template/$fp"
}
if [ ! -d "$WINEPREFIX" ]; then
  _tpl=""
  [ "${SN_WINE_PREFIX_TEMPLATE:-1}" = 1 ] && _tpl=$(prefix_template_dir) || _tpl=""
  if [ -n "$_tpl" ] && [ -d "$_tpl/prefix/drive_c/windows/system32" ] && [ -f "$_tpl/prefix/system.reg" ]; then
    _t0=$(date +%s)
    if /bin/cp -Rc "$_tpl/prefix" "$WINEPREFIX" 2>/dev/null; then
      echo "Wine prefix 从模板克隆（$(( $(date +%s) - _t0 )) 秒）：${_tpl}" >&2
    else
      rm -rf "$WINEPREFIX"; _tpl=""     # 克隆没成（比如不是 APFS）就按老路建
    fi
  fi
  if [ ! -d "$WINEPREFIX" ]; then
    # 首次要建 prefix，真机 30 到 115 秒才出窗口；给一条系统通知免得用户以为没反应（SN_WINE_NO_NOTIFY=1 关掉，测试用）
    [ -n "${SN_WINE_NO_NOTIFY:-}" ] || /usr/bin/osascript -e "display notification \"首次启动要初始化 Wine 环境，通常 1 到 2 分钟后出窗口\" with title \"${APP_NAME}\"" >/dev/null 2>&1 &
    "$WINE" wineboot --init >/dev/null 2>&1
    # 关掉 Wine 的窗口装饰, 用 macOS 自己的窗口
    "$WINE_ROOT/bin/wine" reg add 'HKCU\Software\Wine\X11 Driver' /v Decorated /d N /f >/dev/null 2>&1 || true
    # 字体平滑
    "$WINE_ROOT/bin/wine" reg add 'HKCU\Control Panel\Desktop' /v FontSmoothing /d 2 /f >/dev/null 2>&1 || true
    # 中日文字体映射也进模板（幂等脚本，见下）
    _cjk="$SCRIPT_HOME/tools/wine-cjk-fonts.sh"
    if [ -x "$_cjk" ] && [ ! -f "$WINEPREFIX/.sn-cjk-done" ]; then
      "$_cjk" "$WINEPREFIX" "$WINE_ROOT/bin/wine" >/dev/null 2>&1 && touch "$WINEPREFIX/.sn-cjk-done"
    fi
    # 存成模板：先克隆到临时目录再原子改名，两个 app 同时首启也不会互相写坏；已有模板不覆盖。
    # 注册表 system.reg / user.reg 要等 wineserver 退出才落盘（实测 wineboot 5 秒返回、8 秒后才有 .reg），所以先 wineserver -w。
    "$WINE_ROOT/bin/wineserver" -w >/dev/null 2>&1 || true
    if [ -n "$_tpl" ] && [ ! -d "$_tpl" ] && [ -f "$WINEPREFIX/system.reg" ]; then
      mkdir -p "$(dirname "$_tpl")" && _tmp=$(mktemp -d "$(dirname "$_tpl")/.tmp-XXXXXX") \
        && /bin/cp -Rc "$WINEPREFIX" "$_tmp/prefix" 2>/dev/null && /bin/mv "$_tmp" "$_tpl" 2>/dev/null \
        && echo "Wine prefix 模板已存：${_tpl}" >&2 || rm -rf "${_tmp:-}"
    fi
  fi
fi

sync_builtins || true
cd "$RES/game"
# Steam 启动配置指定了工作目录时按它 cd，主程序改用绝对路径（Wine 认 unix 路径）
if [ -n "${WORKDIR:-}" ] && [ -d "$RES/game/$WORKDIR" ]; then
  GAME_EXE="$RES/game/${GAME_EXE#./}"
  cd "$RES/game/$WORKDIR"
fi
# 默认走 Wine 虚拟桌面。游戏在 Windows 一侧切「全屏」时只会填满这个窗口,
# 不会独占 mac 的显示器。2026-09-11 实测独占全屏把桌面、菜单栏和光标全盖死,
# 游戏卡住时只能强杀。虚拟桌面里游戏自己存的显示设置照常生效。
#   要原样的独占全屏: SN_WINE_DESKTOP=0
#   换虚拟桌面尺寸  : SN_WINE_DESKTOP_SIZE=1600x900
# 中日文字体：新 prefix 默认没有任何字体映射，中日文会显示成方框。跑一次就够（幂等）。
_cjk="$SCRIPT_HOME/tools/wine-cjk-fonts.sh"
if [ -x "$_cjk" ] && [ ! -f "$WINEPREFIX/.sn-cjk-done" ]; then
  "$_cjk" "$WINEPREFIX" "$WINE_ROOT/bin/wine" >/dev/null 2>&1 && touch "$WINEPREFIX/.sn-cjk-done"
fi

# 2026-09-11 实测：命令行 `explorer /desktop=名字,WxH 程序.exe` 在自编的 wine-cx 上不出窗口
# （explorer 起来了，0% CPU，程序压根没启动）。改成写注册表开默认虚拟桌面，实测出 1280x720 窗口，
# 游戏正常渲染。注册表只写这个 prefix，不影响别的。
if [ "${SN_WINE_DESKTOP:-1}" = "1" ]; then
  _sz="${SN_WINE_DESKTOP_SIZE:-1280x720}"
  _cur=$("$WINE" reg query 'HKCU\Software\Wine\Explorer\Desktops' /v Default 2>/dev/null | tr -d '\r' | awk '/Default/{print $NF}')
  if [ "$_cur" != "$_sz" ]; then
    _reg="${TMPDIR:-/tmp}/sn_desk_$$.reg"
    printf 'REGEDIT4\n\n[HKEY_CURRENT_USER\\Software\\Wine\\Explorer]\n"Desktop"="Default"\n\n[HKEY_CURRENT_USER\\Software\\Wine\\Explorer\\Desktops]\n"Default"="%s"\n' "$_sz" > "$_reg"
    "$WINE" regedit "$_reg" >/dev/null 2>&1; rm -f "$_reg"
  fi
else
  "$WINE" reg delete 'HKCU\Software\Wine\Explorer' /v Desktop /f >/dev/null 2>&1 || true
fi
# 默认不接桥：不铺 overlay、不设 override、不起 helper，副本保留游戏自带的 Steam API。
# 没有 Windows Steam 时初始化可能失败，游戏行为需实测。用 bridge enable 或 SN_WINE_BRIDGE=1 明确选择。
if bridge_wanted && bridge_prepare; then bridge_start || true; fi
# 有 run-guard 就交给它：落 pid 记录，会话或壳被中断时整组收掉（2026-09-13）；上限一天。
# 放后台再 wait，trap 才能在游戏还在跑时响应（见上）。
if [ -f "$SCRIPT_HOME/tools/run-guard.py" ]; then
  /usr/bin/python3 -B "$SCRIPT_HOME/tools/run-guard.py" --name "$APP_NAME" --max-seconds 86400 -- "$WINE" "$GAME_EXE" "${WARGS[@]}" "$@" &
else
  "$WINE" "$GAME_EXE" "${WARGS[@]}" "$@" &
fi
GAME_PID=$!
wait "$GAME_PID"; rc=$?
GAME_PID=""
exit $rc
LAUNCHER

DESK_NAME=$(printf '%s' "$NAME" | tr -cd '[:alnum:]' | cut -c1-24)
[ -n "$DESK_NAME" ] || DESK_NAME=game
chmod +x "$OUT/Contents/MacOS/launcher"

STATE_KEY="$STEAM_APPID"; [ -n "$STATE_KEY" ] || STATE_KEY=$(printf '%s' "$NAME" | tr -cd '[:alnum:]._-' | cut -c1-48); [ -n "$STATE_KEY" ] || STATE_KEY=game
python3 - "$OUT" "$NAME" "$EXE_REL" "$SN_HOME" "$STEAM_APPID" "$ICON" "$WINE_ROOT" "$ENGINE" "$BRIDGE" "$SN_RUNTIMES/steambridge" "$SN_GAMES/.wine-state/$STATE_KEY" "$ARCH" "$BRIDGE_DLL" "$STEAM_DRM" "${SN_WINE_LAUNCH_ARGS:-}" "${SN_WINE_ARGS:-}" "${SN_WINE_WORKDIR:-}" <<'PY'
import hashlib, json, pathlib, plistlib, re, sys
app, name, exe, home, appid, icon, wine, engine, bridge, bridge_dir, state, arch, bridge_dll, drm, launch_json, launch_text, workdir = sys.argv[1:]
# Steam 启动配置的参数：dryport 传 JSON 列表（SN_WINE_LAUNCH_ARGS）；手工调用可用 SN_WINE_ARGS 传一串，按 Windows 写法切（引号成组）
if launch_json.strip():
    launch_args = [str(a) for a in json.loads(launch_json)]
else:
    launch_args = [q if q else bare for q, bare in re.findall(r'"([^"]*)"|(\S+)', launch_text)]
workdir = workdir.strip().strip("/")
if workdir and (".." in workdir.split("/") or not (pathlib.Path(app) / "Contents/Resources/game" / workdir).is_dir()):
    raise SystemExit(f"工作目录必须是游戏目录内已有的相对路径：{workdir}")
# 窗口化参数按引擎分：给 UE 传 -screen-width 它会忽略并全屏，两套不通用（tools/wine-trial.py 实测）
windowed = {"unity-il2cpp": ["-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "720"],
            "unity-mono-legacy": ["-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "720"],
            "unreal": ["-windowed", "-ResX=1280", "-ResY=720"]}.get(engine, [])
app = pathlib.Path(app)
# DOSBox 0.74 在 Wine 虚拟桌面里全屏就是一整块黑（一款 DOSBox 游戏 2026-09-14：fullscreen=true 时 2560x1440 全黑，output=surface 也救不了；
# 只改 fullscreen=false 就出标题画面）。DOSBox 没有 -windowed 开关，只能多传一份 conf（后传的覆盖前面的）；文件放进克隆里的工作目录，
# 因为 -conf 的相对路径按当前目录解析。
if pathlib.Path(exe).name.lower().startswith("dosbox"):
    conf_dir = app / "Contents/Resources/game" / workdir if workdir else app / "Contents/Resources/game"
    (conf_dir / "sn-windowed.conf").write_text("[sdl]\nfullscreen=false\n")
    windowed = windowed + ["-conf", "sn-windowed.conf"]
metadata = dict(CFBundleExecutable='launcher', CFBundleIdentifier='local.winewrap.' + hashlib.sha256(name.encode()).hexdigest()[:24],
                CFBundleName=name, CFBundleDisplayName=name, CFBundlePackageType='APPL', CFBundleShortVersionString='1.0',
                LSMinimumSystemVersion='11.0', NSHighResolutionCapable=True, LSApplicationCategoryType='public.app-category.games')
if icon: metadata['CFBundleIconFile'] = 'icon'
(app / 'Contents/Info.plist').write_bytes(plistlib.dumps(metadata))
(app / 'Contents/Resources/wine-launch.json').write_text(json.dumps(dict(
    exe='./' + exe, toolchain=home, wine=str(pathlib.Path(wine).resolve(strict=True)), name=name, appid=appid,
    engine=engine, windowed_args=windowed, launch_args=launch_args, workdir=workdir, bridge=bridge == '1', bridge_dir=bridge_dir,
    state=state, arch=arch, bridge_dll=bridge_dll, steam_drm=drm == '1'), ensure_ascii=False))
(app / 'Contents/Resources/steam_appid.txt').write_text(appid)
PY

# 图标: 传 .icns 直接用; 传纯数字当 steam appid, 走取图脚本
if [ -n "$ICON" ]; then
  if [ -f "$ICON" ] && [[ "$ICON" == *.icns ]]; then
    cp "$ICON" "$OUT/Contents/Resources/icon.icns"
  elif [[ "$ICON" =~ ^[0-9]+$ ]]; then
    "$SN_LIB/fetch-icon.sh" "$ICON" "$OUT/Contents/Resources/icon.icns" >/dev/null 2>&1 || true
  elif [ -f "$ICON" ]; then
    "$SN_LIB/fetch-icon.sh" 0 /dev/null >/dev/null 2>&1 || true
  fi
fi

/usr/bin/codesign --force -s - "$OUT"
/usr/bin/codesign --verify --strict "$OUT"
python3 - "$SN_LIB" "$OUT" "$TARGET" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from runtime_archive import publish
publish(sys.argv[2], sys.argv[3])
PY
OUT="$TARGET"
rmdir "$STAGE" 2>/dev/null || true   # 发布后 stage 目录空了就收掉（此前每次成功打包都留一个空的 .wine-stage.XXXXXX）

# 构建不运行 Wine，也不触碰其他 prefix 的进程。
echo "$OUT"
echo "首次启动走 Wine 虚拟桌面 ${DESK_NAME} 1280x720(不独占全屏); 要原样全屏设 SN_WINE_DESKTOP=0" >&2
