#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 基础环境按需准备；--check 与 --plan 不下载、不写文件、不启动游戏。
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../lib/paths.sh"
export PYTHONDONTWRITEBYTECODE=1
MODE=install
RUNTIME_ONLY=0
WITH_UNITY=0
WITH_NWJS=0
WITH_LOVE=0
WITH_RENPY=0
AC_URL=https://raw.githubusercontent.com/AreWeAntiCheatYet/AreWeAntiCheatYet/master/games.json
NWJS_VER=0.115.0
NWJS_SHA256=faa2d8cdda2d772f44bb74acceabdbf5553e861f1f3c1bbd719e45122ac518e4
HLSLCC_REV=3ea1fcd6bd0ac445bc078de0bf32f0950188577b
HLSLCC_SHA256=f07b9c04c1090216b0d434d3b87ad986b2231257ecb76f42de9dd4c12224f2a0
UVENV="$SN_RUNTIMES/unity-retarget/venv"
HLSLCC="$SN_RUNTIMES/unity-retarget/hlslcc-cli"
REQ="$SN_LIB/unity-retarget/requirements.txt"
ERRORS=0

say() { printf '%s\n' "$*"; }
ok() { printf '已就绪：%s\n' "$*"; }
missing() { printf '尚缺：%s\n' "$*"; }
error() { printf '未完成：%s\n' "$*" >&2; ERRORS=$((ERRORS + 1)); }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }
INSTALL_COMMAND=$(sn_quote_argument "$SN_HOME/bin/install.sh")
usage() {
  cat <<'HELP'
用法：./bin/install.sh [--check | --plan] [--runtime-only] [--with-unity] [--with-nwjs] [--with-love] [--with-renpy]
  默认           准备反作弊数据库，检查基础环境；不一次下载所有引擎
  --check        只读检查，不下载、不安装、不写 Steam；缺基础或所选路线依赖时退出 1
  --plan         展示来源、目录与会执行的动作，不联网、不修改文件
  --runtime-only 只处理选中路线依赖，须搭配 --with-*；可与 --check / --plan 合用
                 不检查 Node、Steam 下载端点、反作弊库或其他路线；就绪不代表能下载游戏
  --with-unity   准备 Unity Python 环境及 HLSLcc 着色器工具
  --with-nwjs    下载并校验 arm64 NW.js 0.115.0（MV/MZ 游戏路线）
  --with-love    下载并逐文件校验原厂 LÖVE 11.5（含 arm64）
  --with-renpy   缓存固定原厂 Ren'Py 8.1.3 SDK / Steam 扩展，准备 macOS 依赖

位置由 SN_HOME、SN_RUNTIMES、SN_GAMES 决定。已有可用运行时保留；异常文件不覆盖。
运行时要求：macOS Apple Silicon、Python 3.9+；编译工具按所选路线检查。
综合入口另需支持内建 WebSocket / fetch 的 Node.js、已登录 Steam 与可读下载端点。
Homebrew 用户缺基础工具时可运行：brew install python node
Unity 还需要 Python 3.10+、Xcode Command Line Tools、CMake；缺 CMake 时：brew install cmake
Ren'Py 自有启动壳需要 Xcode Command Line Tools；缺少时：xcode-select --install
Wine 单独构建：tools/build-wine.sh --help
HELP
}
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --plan) MODE=plan ;;
    --runtime-only) RUNTIME_ONLY=1 ;;
    --with-unity) WITH_UNITY=1 ;;
    --with-nwjs) WITH_NWJS=1 ;;
    --with-love) WITH_LOVE=1 ;;
    --with-renpy) WITH_RENPY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) die "未知参数：${arg}；运行 --help 查看用法" ;;
  esac
done
if [ "$RUNTIME_ONLY" = 1 ] && [ "$((WITH_UNITY + WITH_NWJS + WITH_LOVE + WITH_RENPY))" = 0 ]; then
  die '--runtime-only 须至少选择一个 --with-* 路线'
fi
if [ "$MODE" = plan ]; then
  say "代码：$SN_HOME"
  say "运行时：$SN_RUNTIMES"
  say "游戏：$SN_GAMES"
  if [ "$RUNTIME_ONLY" = 1 ]; then
    say '计划范围：仅所选路线依赖；未执行检查，也未验证游戏下载条件。'
  else
    say "基础操作：已有反作弊库有效则沿用，缺失时从 $AC_URL 获取并校验 JSON。"
  fi
  if [ "$WITH_UNITY" = 1 ]; then
    say "Unity：用 $REQ 在 $UVENV 安装固定 Python 包版本。"
    say "HLSLcc：https://codeload.github.com/Unity-Technologies/HLSLcc/tar.gz/$HLSLCC_REV"
    say "HLSLcc SHA-256：${HLSLCC_SHA256}；本地编译到 ${HLSLCC}。"
  fi
  if [ "$WITH_NWJS" = 1 ]; then
    say "NW.js：https://dl.nwjs.io/v$NWJS_VER/nwjs-v$NWJS_VER-osx-arm64.zip"
    say "NW.js SHA-256：${NWJS_SHA256}；放到 $SN_RUNTIMES/nwjs/${NWJS_VER}。"
  fi
  if [ "$WITH_LOVE" = 1 ]; then
    python3 - "$SN_LIB" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from runtime_archive import LOVE_URL, LOVE_SHA256, LOVE_VERSION
from paths import SN_RUNTIMES
print('LÖVE：' + LOVE_URL)
print('LÖVE SHA-256：' + LOVE_SHA256 + '；放到 ' + str(SN_RUNTIMES / 'love' / LOVE_VERSION))
print('逐文件核对原厂 ZIP，保留原厂签名状态；不运行游戏。')
PY
  fi
  if [ "$WITH_RENPY" = 1 ]; then
    python3 - "$SN_LIB" <<'PY'
import importlib, sys
sys.path.insert(0, sys.argv[1])
runtime = importlib.import_module('renpy-runtime')
for kind in ('sdk', 'steam'):
    name, digest, size = runtime.archive_spec(kind)
    print('Ren\'Py：' + runtime.RELEASE_URL + name)
    print('SHA-256：' + digest + '；字节数：' + str(size) + '；缓存：' + str(runtime.cache_path(kind)))
print('已有正确归档先复制到持久缓存，缺失才下载；逐文件校验到 ' + str(runtime.DEFAULT_OUTPUT))
print('保留原厂字节和签名；首次只运行自有标准库验证，不启动游戏。打包需要 Xcode Command Line Tools。')
PY
  fi
  say '大型运行时独立准备；本入口不装 Steam、steamcmd 或 Rosetta，不修改 Steam 配置。'
  exit 0
fi

[ "$(uname -s)" = Darwin ] || die '只支持 macOS'
[ "$(uname -m)" = arm64 ] || die '请在 Apple Silicon 的原生 arm64 终端运行'
# 系统自带的 python3 是 CLT 的一部分：没装 CLT 时执行它会弹安装框而不是报错，
# 显式检测开发工具并给出准备命令（2026-09-13）。
# 只在确实能问到 xcode-select 时才判定；隔离环境里问不到就跳过，不拿工具缺失当 CLT 缺失。
if command -v xcode-select >/dev/null 2>&1 && ! xcode-select -p >/dev/null 2>&1; then
  die '缺 Xcode Command Line Tools。先运行 xcode-select --install，在弹出的窗口里装完（约几分钟）再重跑本命令。它提供 python3、git 与编译器，本工具多条路线都依赖它。'
fi
command -v python3 >/dev/null || die '缺 Python 3；安装后重新运行本命令'
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' || die '需要 Python 3.9 或更新版本'
ok "Python $(python3 -c 'import sys; print(sys.version.split()[0])')"
if [ "$RUNTIME_ONLY" = 0 ]; then
# 优先用系统已有的 node；没有就取本工具自备的那份。不再让用户先装 Homebrew：
# 使用已有工具安装依赖，不自动引入其他包管理器（2026-09-13）。
# 取法与 NW.js、LÖVE 一致：官方包、固定版本、校验 SHA-256 后才用。
# 优先用系统已有的 node；没有就看本工具自备的那份。不再让用户先装 Homebrew：
# 使用已有工具安装依赖，不自动引入其他包管理器（2026-09-13）。
# 取版本号只在需要时做，且失败不终止脚本（set -e 下 $() 失败会直接退出且吞掉 stderr）。
node_usable(){ [ -n "${1:-}" ] && [ -x "$1" ] && "$1" -e 'if(typeof WebSocket!=="function"||typeof fetch!=="function")process.exit(1)' >/dev/null 2>&1; }
if command -v node >/dev/null && node_usable "$(command -v node)"; then
  ok "Node.js $(node --version)，客户端下载所需 WebSocket / fetch 可用"
else
  NODE_VER=$(python3 -c "import sys; sys.path.insert(0, '$SN_LIB'); import runtime_archive as r; print(r.NODE_VERSION)" 2>/dev/null || true)
  SN_NODE=""
  [ -n "$NODE_VER" ] && SN_NODE="$SN_RUNTIMES/node/$NODE_VER/bin/node"
  if ! node_usable "$SN_NODE" && [ "$MODE" = install ] && [ -n "$NODE_VER" ]; then
    say "取本工具自备的 Node.js v${NODE_VER}（nodejs.org 官方包，SHA-256 核对后解到 $SN_RUNTIMES/node/${NODE_VER}）…"
    mkdir -p "$SN_RUNTIMES/logs"
    NODE_LOG=$(mktemp "$SN_RUNTIMES/logs/install-node.XXXXXX")
    if ! python3 "$SN_LIB/runtime_archive.py" install-node > "$NODE_LOG" 2>&1; then
      tail -5 "$NODE_LOG" >&2
    fi
  fi
  if node_usable "$SN_NODE"; then
    ok "Node.js $("$SN_NODE" --version)（本工具自备，在 $SN_RUNTIMES/node/${NODE_VER}）"
  else
    error "Node.js 缺失或没有内建 WebSocket / fetch。运行 ${INSTALL_COMMAND}（不带 --check）会自备一份：nodejs.org 官方包 v${NODE_VER:-22.11.0}，校验 SHA-256 后解到 $SN_RUNTIMES/node/${NODE_VER:-22.11.0}；也可自行安装当前版 Node.js 后重跑。"
  fi
fi
fi
FREE=$(python3 - "$SN_RUNTIMES" <<'PY'
import pathlib, shutil, sys
p = pathlib.Path(sys.argv[1])
while not p.exists(): p = p.parent
print(shutil.disk_usage(p).free // (1024**3))
PY
)
say "运行时所在卷可用空间：$FREE GiB"
if [ "$MODE" = install ] && [ "$FREE" -lt 2 ]; then die '安装准备至少需要 2 GiB 可用空间；不会自动删文件'; fi

# 仅查客户端下载端点的可读状态；不写调试开关，不发任何控制命令。
if [ "$RUNTIME_ONLY" = 0 ]; then
if [ -f "$STEAM/appcache/appinfo.vdf" ]; then ok 'Steam 游戏元数据存在'
else error "Steam 元数据不存在：$STEAM/appcache/appinfo.vdf；启动 Steam 并登录后重跑"; fi
if python3 - <<'PY'
import json, urllib.request
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8080/json', timeout=2) as r: targets = json.load(r)
    raise SystemExit(not any(t.get('title') == 'SharedJSContext' and t.get('webSocketDebuggerUrl') for t in targets))
except (OSError, ValueError): raise SystemExit(1)
PY
then ok 'Steam 客户端下载端点可读（不代表游戏所有权或下载已验证）'
else error 'Steam 客户端下载端点不可读；当前无法一键下载。保持 Steam 已登录；没开过本机连接的机器运行 ./bin/dryport steam-debug --enable（经确认创建 Valve 的调试开关文件，需重启 Steam）'; fi
fi

LOCK=""
cleanup() { [ -z "$LOCK" ] || rmdir "$LOCK" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "$MODE" = install ]; then
  mkdir -p "$SN_RUNTIMES"
  LOCK_CANDIDATE="$SN_RUNTIMES/.dryport-install.lock"
  mkdir "$LOCK_CANDIDATE" 2>/dev/null || die "另一个安装占用了 ${LOCK_CANDIDATE}；先确认它已退出"
  LOCK="$LOCK_CANDIDATE"
fi
validate_db() {
  python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f: rows = json.load(f)
    valid = isinstance(rows, list) and bool(rows) and all(
        isinstance(r, dict) and isinstance(r.get('name'), str)
        and isinstance(r.get('anticheats'), list) and isinstance(r.get('storeIds'), dict) for r in rows)
    if not valid: raise ValueError('数据结构不符')
    print(len(rows))
except (OSError, ValueError, TypeError) as e:
    print(str(e), file=sys.stderr); raise SystemExit(1)
PY
}
if [ "$RUNTIME_ONLY" = 0 ]; then
DB="$SN_RUNTIMES/anticheat-db.json"
if [ -f "$DB" ] && COUNT=$(validate_db "$DB" 2>/dev/null); then
  ok "反作弊数据库 $COUNT 条，沿用已有数据；可用 tools/update-anticheat-db.sh 更新"
elif [ -e "$DB" ]; then
  error "反作弊数据库损坏，保留现场：${DB}；需用 tools/update-anticheat-db.sh 重新获取"
elif [ "$MODE" = install ]; then
  TMP_DB=$(mktemp "$SN_RUNTIMES/anticheat-db.download.XXXXXX")
  if curl --proto '=https' --silent --show-error --fail --location --retry 2 --connect-timeout 15 --max-time 90 --output "$TMP_DB" "$AC_URL" \
      && COUNT=$(validate_db "$TMP_DB"); then
    if ln "$TMP_DB" "$DB"; then rm "$TMP_DB"; ok "反作弊数据库 $COUNT 条，JSON 结构已校验"
    else error "数据库路径已被其他操作创建，保留下载文件：$TMP_DB"; fi
  else error "反作弊库获取失败，保留下载现场：$TMP_DB"; fi
else
  error '缺反作弊数据库；运行 ./bin/install.sh 获取'
fi
fi

fetch_pinned() {
  local url="$1" sha="$2" dst="$3" actual
  if [ ! -f "$dst" ]; then
    curl --proto '=https' --tlsv1.2 --fail --location --retry 3 --connect-timeout 20 --output "$dst.part" "$url" || return 1
    actual=$(shasum -a 256 "$dst.part" | awk '{print $1}')
    [ "$actual" = "$sha" ] || { say "下载 SHA-256 不符：$dst.part，预期 ${sha}，实际 $actual" >&2; return 1; }
    mv "$dst.part" "$dst"
  fi
  actual=$(shasum -a 256 "$dst" | awk '{print $1}')
  [ "$actual" = "$sha" ] || { say "缓存 SHA-256 不符，保留：$dst" >&2; return 1; }
}
valid_arm64() { [ -x "$1" ] && file -b "$1" | grep -q arm64; }
valid_unity_python() {
  [ -x "$UVENV/bin/python" ] && "$UVENV/bin/python" - "$REQ" <<'PY'
import importlib.metadata, pathlib, sys
try:
    for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        name, version = line.split('==')
        if importlib.metadata.version(name) != version: raise ValueError(name)
    import UnityPy, lz4, dnfile, icoextract
except (ImportError, ValueError): raise SystemExit(1)
PY
}
select_unity_python() {
  # 只为新建 Unity venv 选择解释器；不重排其他命令使用的 PATH。
  python3 - <<'PY'
import os, subprocess, time
deadline = time.monotonic() + 8
seen = set()
for directory in os.environ.get('PATH', '').split(os.pathsep):
    candidate = os.path.abspath(os.path.join(directory or os.curdir, 'python3'))
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        continue
    identity = os.path.realpath(candidate)
    if identity in seen:
        continue
    seen.add(identity)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    try:
        result = subprocess.run(
            [candidate, '-I', '-S', '-c', 'import sys; raise SystemExit(sys.version_info < (3, 10))'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=min(2, remaining))
    except (OSError, subprocess.TimeoutExpired):
        continue
    if result.returncode == 0:
        print(candidate)
        raise SystemExit(0)
raise SystemExit('Unity 新建环境需要 PATH 中可运行的 Python 3.10 或更新版本；请安装或修复后重试。已有文件已保留。')
PY
}
install_unity() (
  set -e
  mkdir -p "$SN_RUNTIMES/unity-retarget" "$SN_RUNTIMES/build"
  if ! valid_unity_python >/dev/null 2>&1; then
    if { [ -e "$UVENV" ] || [ -L "$UVENV" ]; } && [ ! -f "$UVENV/.dryport-created" ]; then
      die "现有 Python 环境不符合 requirements.txt，保留：${UVENV}；请使用新的 SN_RUNTIMES"
    fi
    UNITY_PYTHON=$(select_unity_python) || exit 1
    mkdir -p "$UVENV"
    touch "$UVENV/.dryport-created"
    "$UNITY_PYTHON" -m venv "$UVENV"
    "$UVENV/bin/python" -m pip --isolated install --index-url https://pypi.org/simple --disable-pip-version-check -r "$REQ"
    valid_unity_python || die 'Unity Python 依赖安装后校验失败'
  fi
  if ! valid_arm64 "$HLSLCC"; then
    [ ! -e "$HLSLCC" ] || die "已有着色器工具不是可执行 arm64，保留：$HLSLCC"
    command -v cmake >/dev/null || die '缺 CMake；brew install cmake 后重跑'
    xcrun --find clang++ >/dev/null || die '缺 Xcode Command Line Tools'
    BUILD=$(mktemp -d "$SN_RUNTIMES/build/hlslcc.XXXXXX")
    ARCHIVE="$SN_RUNTIMES/build/hlslcc-$HLSLCC_REV.tar.gz"
    fetch_pinned "https://codeload.github.com/Unity-Technologies/HLSLcc/tar.gz/$HLSLCC_REV" "$HLSLCC_SHA256" "$ARCHIVE"
    tar -xzf "$ARCHIVE" -C "$BUILD"
    SRC="$BUILD/HLSLcc-$HLSLCC_REV"
    cmake -S "$SRC" -B "$BUILD/objects" -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      -DCMAKE_OSX_ARCHITECTURES=arm64 -DHLSLCC_LIBRARY_SHARED=OFF
    cmake --build "$BUILD/objects" --parallel 2
    clang++ -arch arm64 -std=c++11 -O2 -I "$SRC/include" -I "$SRC/src/internal_includes" \
      "$SN_LIB/unity-retarget/hlslcc-cli.cpp" "$BUILD/objects/libhlslcc.a" -o "$BUILD/hlslcc-cli"
    valid_arm64 "$BUILD/hlslcc-cli" || die 'HLSLcc 编译结果不是 arm64'
    ln "$BUILD/hlslcc-cli" "$HLSLCC" || die 'HLSLcc 路径已被其他操作创建；保留构建现场'
    printf '%s\n' "revision=$HLSLCC_REV sha256=$HLSLCC_SHA256" > "$SN_RUNTIMES/unity-retarget/hlslcc-source.txt"
  fi
)
if [ "$RUNTIME_ONLY" = 0 ] || [ "$WITH_UNITY" = 1 ]; then
if [ "$WITH_UNITY" = 1 ] && [ "$MODE" = install ]; then
  # 用独立 shell 保留 set -e；if 包住整个函数会让内部命令失败仍继续执行。
  mkdir -p "$SN_RUNTIMES/logs"
  UNITY_LOG=$(mktemp "$SN_RUNTIMES/logs/install-unity.XXXXXX")
  say "准备 Unity 依赖，日志：$UNITY_LOG"
  set +e
  (set -e; install_unity) > "$UNITY_LOG" 2>&1
  RC=$?
  set -e
  if [ "$RC" != 0 ]; then
    tail -20 "$UNITY_LOG" >&2
    error "Unity 准备失败（退出 ${RC}），完整日志：$UNITY_LOG"
  fi
fi
if valid_unity_python >/dev/null 2>&1 && valid_arm64 "$HLSLCC"; then
  ok 'Unity Python 固定版本与 arm64 着色器工具齐备；播放器按游戏版本获取，兼容性另验'
else
  missing "Unity 依赖未齐；运行 $INSTALL_COMMAND --runtime-only --with-unity"
  [ "$WITH_UNITY" = 0 ] || error '所选 Unity 路线依赖未齐'
fi
fi

if [ "$RUNTIME_ONLY" = 0 ] || [ "$WITH_NWJS" = 1 ]; then
NWAPP="${NWJS_APP:-}"
NW_INVALID_OVERRIDE=0
shopt -s nullglob
if [ -z "$NWAPP" ]; then
  NW_CANDIDATES=("$SN_RUNTIMES"/nwjs/*/nwjs-v*-osx-arm64/nwjs.app)
  if [ "${#NW_CANDIDATES[@]}" -gt 0 ]; then
    NWAPP=$(printf '%s\n' "${NW_CANDIDATES[@]}" | sort -V | tail -1)
  fi
fi
if [ -n "$NWAPP" ] && ! valid_arm64 "$NWAPP/Contents/MacOS/nwjs"; then
  missing "当前选中的 NW.js 没有 arm64 主程序：$NWAPP"
  if [ -n "${NWJS_APP:-}" ]; then
    NW_INVALID_OVERRIDE=1
    error 'NWJS_APP 指定了不可用的运行时；修正该变量或 unset NWJS_APP 后重跑'
  fi
  NWAPP=""
fi
install_nwjs() (
  set -e
  local target="$SN_RUNTIMES/nwjs/$NWJS_VER"
  [ ! -e "$target" ] || die "NW.js 目标目录已存在但不可用，保留：${target}；请使用新的 SN_RUNTIMES"
  mkdir -p "$SN_RUNTIMES/nwjs" "$SN_RUNTIMES/build"
  local stage archive
  stage=$(mktemp -d "$SN_RUNTIMES/nwjs/.download.XXXXXX")
  archive="$SN_RUNTIMES/build/nwjs-v$NWJS_VER-osx-arm64.zip"
  fetch_pinned "https://dl.nwjs.io/v$NWJS_VER/nwjs-v$NWJS_VER-osx-arm64.zip" "$NWJS_SHA256" "$archive"
  unzip -q "$archive" -d "$stage"
  valid_arm64 "$stage/nwjs-v$NWJS_VER-osx-arm64/nwjs.app/Contents/MacOS/nwjs" || die 'NW.js 解压后缺 arm64 主程序'
  # 解压目录整体落位；中断只留下下载现场，不会把半个版本当作安装。
  python3 - "$stage" "$target" <<'PY'
import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True)
renamex = libc.renamex_np
renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
if renamex(os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2]), 4):
    err = ctypes.get_errno(); raise OSError(err, os.strerror(err), sys.argv[2])
PY
)
if [ -z "$NWAPP" ] && [ "$WITH_NWJS" = 1 ] && [ "$NW_INVALID_OVERRIDE" = 0 ] && [ "$MODE" = install ]; then
  mkdir -p "$SN_RUNTIMES/logs"
  NWJS_LOG=$(mktemp "$SN_RUNTIMES/logs/install-nwjs.XXXXXX")
  say "准备 NW.js，日志：$NWJS_LOG"
  set +e
  (set -e; install_nwjs) > "$NWJS_LOG" 2>&1
  RC=$?
  set -e
  if [ "$RC" != 0 ]; then
    tail -20 "$NWJS_LOG" >&2
    error "NW.js 安装失败（退出 ${RC}），完整日志：$NWJS_LOG"
  fi
  if valid_arm64 "$SN_RUNTIMES/nwjs/$NWJS_VER/nwjs-v$NWJS_VER-osx-arm64/nwjs.app/Contents/MacOS/nwjs"; then
    NWAPP="$SN_RUNTIMES/nwjs/$NWJS_VER/nwjs-v$NWJS_VER-osx-arm64/nwjs.app"
  fi
fi
if [ -n "$NWAPP" ]; then
  ok "NW.js arm64 运行时：${NWAPP}（游戏插件与成就兼容性另验）"
else
  missing "NW.js 未安装；运行 $INSTALL_COMMAND --runtime-only --with-nwjs"
  [ "$WITH_NWJS" = 0 ] || error '所选 NW.js 路线依赖未齐'
fi
fi

say ''
if [ "$RUNTIME_ONLY" = 0 ]; then say '其他路线与使用边界：'
else say '所选路线检查：'; fi
if [ "$WITH_LOVE" = 1 ]; then
  LOVE_ACTION=check-love
  [ "$MODE" != install ] || LOVE_ACTION=install-love
  if LOVE_RUNTIME=$(python3 "$SN_LIB/runtime_archive.py" "$LOVE_ACTION") \
      && [[ "$LOVE_RUNTIME" = /* && "$LOVE_RUNTIME" != *$'\n'* && "$LOVE_RUNTIME" != *$'\r'* ]]; then
    ok "LÖVE 11.5 原厂运行时逐文件一致：${LOVE_RUNTIME}；游戏兼容性另验"
  else
    error "所选 LÖVE 路线依赖未齐或校验回执无效；运行 $INSTALL_COMMAND --runtime-only --with-love，异常文件保留"
  fi
fi
if [ "$WITH_RENPY" = 1 ]; then
  if ! /usr/bin/xcrun --find clang >/dev/null 2>&1; then
    error "所选 Ren'Py 路线缺少自有启动壳编译器；运行 xcode-select --install 后重试"
  else
    RENPY_ACTION=check
    [ "$MODE" != install ] || RENPY_ACTION=install
    if RENPY_RESULT=$(python3 "$SN_LIB/renpy-runtime.py" "$RENPY_ACTION"); then
      if RENPY_SUMMARY=$(python3 -c 'import json,sys
try:
    r=json.load(sys.stdin)
    if not isinstance(r,dict) or not all(isinstance(r.get(k),str) and r[k].strip() for k in ("version","package")) or not r["package"].startswith("/"):
        raise ValueError("缺少有效 version / package")
    print(r["version"] + "：" + r["package"])
except (ValueError,TypeError) as e:
    raise SystemExit("Ren\x27Py 校验回执无效：" + str(e))' <<<"$RENPY_RESULT"); then
        ok "Ren'Py ${RENPY_SUMMARY}；固定原厂归档与运行依赖已核对，游戏版本另验"
      else
        error "Ren'Py 校验回执无效；依赖就绪尚未确认，已有文件保留"
      fi
    else
      error "所选 Ren'Py 路线依赖未齐；运行 $INSTALL_COMMAND --runtime-only --with-renpy，异常文件保留"
    fi
  fi
fi
if [ "$RUNTIME_ONLY" = 0 ]; then
MKXP="$SN_RUNTIMES/mkxp-build/Build/Products/Release/Z-universal.app/Contents/MacOS/Z-universal"
if valid_arm64 "$MKXP"; then ok 'mkxp-z arm64 主程序存在'
else missing 'mkxp-z：有项目发布的预编译运行时包时用 tools/runtime-bundle.py install --from <目录或 URL>；自己编译见 https://github.com/mkxp-z/mkxp-z、docs/记录-2026-09-10.md 与 tools/patch-mkxp-deps.sh'; fi
KRKR="$SN_RUNTIMES/krkrsdl2/build/krkrsdl2.app/Contents/MacOS/krkrsdl2"
if valid_arm64 "$KRKR"; then ok 'krkrsdl2 arm64 主程序存在'
else missing 'krkrsdl2：有预编译运行时包时用 tools/runtime-bundle.py install --from <目录或 URL>；自己编译见 https://github.com/krkrsdl2/krkrsdl2 与 docs/进度/进度-krkr.md'; fi
say 'Godot：无需预下载全部模板；lib/godot-wrap.sh 按游戏版本从 Godot 官方发布获取。'
say "Ren'Py：--with-renpy 只准备固定原厂依赖；游戏适配仍按已验证版本检查。FNA 尚未接通自动安装。"
WINE="$SN_RUNTIMES/wine-cx"
if [ -x "$WINE/bin/wine" ] && [ -f "$WINE/lib/wine/x86_64-unix/winemac.so" ] \
  && file -b "$WINE/bin/wine" | grep -q x86_64 \
  && file -b "$WINE/lib/wine/x86_64-unix/winemac.so" | grep -q x86_64 \
  && nm -g "$WINE/lib/wine/x86_64-unix/winemac.so" 2>/dev/null | awk '$NF=="_macdrv_functions" && $(NF-1)!="U" {n++} END {exit n!=1}'; then
  ok 'Wine 的 macdrv_functions 导出存在；此处没有运行 Wine 或游戏'
  if [ -f "$WINE/lib/wine/x86_64-unix/winemetal.so" ] && [ -f "$WINE/lib/wine/x86_64-windows/winemetal.dll" ]; then
    say 'DXMT 文件存在；DirectX 11 与启动稳定性须按真实游戏验证。'
  else missing 'DXMT 未安装，不能宣称 Wine 的 DirectX 11 路线可用；见 tools/install-dxmt.sh 与 docs/进度/进度-wine-cx.md'; fi
else
  missing '带 Metal 接口的 Wine；有预编译运行时包时用 tools/runtime-bundle.py install --from <目录或 URL>（分钟级），否则 tools/build-wine.sh --check 后按 --help 本地构建（小时级）。Wine Staging 不满足此能力。'
fi
say 'GUI：app/build.sh 单独构建伴侣 app；本入口尚不交付可双击安装器。'
fi
say ''
if [ "$ERRORS" -gt 0 ]; then
  if [ "$RUNTIME_ONLY" = 1 ]; then say '所选路线依赖未齐。补齐上述项后重跑相同命令。'
  else say '尚不能完成所选安装/下载流程。补齐上述项后重跑相同命令。'; fi
  exit 1
fi
if [ "$RUNTIME_ONLY" = 1 ]; then
  say '所选路线依赖已就绪；未检查 Steam 下载条件，也未验证游戏兼容性。'
  say "检查下载基础环境：$INSTALL_COMMAND --check"
  exit 0
fi
say '基础环境及所选依赖已就绪；其他路线状态见上方，未验证任意游戏兼容性。'
# --check 只体检、不装东西，并列出下一步准备命令（2026-09-13）。
say ''
say '下一步，按你要做的事选一条：'
printf '  装全部基础环境与反作弊数据库：%s\n' "$INSTALL_COMMAND"
printf '  只装某一路线的运行时        ：%s --runtime-only --with-unity\n' "$INSTALL_COMMAND"
printf '  查一个游戏能不能安全跑  ：%s check <appid>\n' "$(sn_quote_argument "$SN_HOME/bin/dryport")"
printf '  一条龙装一个游戏        ：%s install <appid>\n' "$(sn_quote_argument "$SN_HOME/bin/dryport")"
say '反作弊数据库由上面第一条取；在它跑完之前，check 与 install 会因风险资料不全而停下。'
