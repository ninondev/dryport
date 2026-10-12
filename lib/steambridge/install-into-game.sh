#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 手工接入已生成的 Wine 工作副本：使用运行时产物，副本原版 DLL 保留为 .orig。
set -euo pipefail
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in /*) SELF="$LINK" ;; *) SELF="$(dirname "$SELF")/$LINK" ;; esac
done
GAME_ROOT_INPUT="${SN_GAMES:-$HOME/Games}"
source "$(cd "$(dirname "$SELF")/.." && pwd)/paths.sh"
if [ "$#" -ne 2 ] || [ ! -d "$1" ]; then
  echo "用法：$0 <游戏目录> <appid>" >&2
  exit 2
fi
G="$(cd "$1" && pwd -P)"; APPID="$2"
if ! python3 -B - "$APPID" <<'PY'
import sys
value = sys.argv[1]
sys.exit(0 if value.isascii() and value.isdecimal() and 0 < int(value) <= 0xFFFFFFFF else 1)
PY
then
  echo "appid 必须是 1 至 4294967295 的整数：$APPID" >&2
  exit 2
fi
. "$SN_LIB/repack-gate.sh"
require_repack_permission "$G" "$APPID" || exit $?
SN_GAMES=$(python3 - "$SN_GAMES" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve(strict=True)
if '\n' in str(root) or '\r' in str(root):
    sys.exit('Configured games root contains a line break')
if not root.is_dir():
    sys.exit('Configured games root is not a directory')
print(root)
PY
)
SN_RUNTIMES=$(python3 - "$SN_RUNTIMES" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve(strict=True)
if '\n' in str(root) or '\r' in str(root):
    sys.exit('Configured runtime root contains a line break')
if not root.is_dir():
    sys.exit('Configured runtime root is not a directory')
print(root)
PY
)
if ! python3 -B - "$1" "$SN_GAMES" "$APPID" "$GAME_ROOT_INPUT" "$G" <<'PY'
import json, os, pathlib, plistlib, sys
try:
    supplied_game = pathlib.Path(os.path.abspath(sys.argv[1]))
    root = pathlib.Path(sys.argv[2])
    requested_root = pathlib.Path(os.path.abspath(sys.argv[4]))
    try:
        relative = supplied_game.relative_to(requested_root)
    except ValueError:
        relative = supplied_game.relative_to(root)
    game = root / relative
    if len(relative.parts) != 4 or not relative.parts[0].endswith('.app') or relative.parts[1:] != ('Contents', 'Resources', 'game'):
        raise ValueError('位置不是生成的 Wine app 内的 game 目录')
    current = root
    for part in ('', *relative.parts):
        if part:
            current = current / part
        if current.is_symlink() or not current.is_dir():
            raise ValueError('工作路径含链接或非目录')
    if str(game) != sys.argv[5]:
        raise ValueError('实际工作目录与核对的路径不一致')
    contents = game.parent.parent
    plist = contents / 'Info.plist'
    launch = game.parent / 'wine-launch.json'
    for path in (plist, launch):
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError('生成元数据不是独立普通文件')
    metadata = plistlib.loads(plist.read_bytes())
    if not str(metadata.get('CFBundleIdentifier', '')).startswith('local.winewrap.') or metadata.get('CFBundleExecutable') != 'launcher':
        raise ValueError('不是 Wine 工作副本的 app 元数据')
    if str(json.loads(launch.read_text())['appid']) != sys.argv[3]:
        raise ValueError('工作副本 appid 不一致')
except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException) as error:
    print(f'拒绝：需要 SN_GAMES 下已生成的独立 Wine 工作副本：{error}', file=sys.stderr)
    sys.exit(1)
PY
then
  exit 3
fi
OUT="$SN_RUNTIMES/steambridge"
if [ ! -s "$OUT/steam_api64.dll" ] || [ ! -x "$OUT/steambridge-helper" ]; then
  echo "缺少 steambridge 运行时：${OUT}。请先运行 $SN_LIB/steambridge/build.sh。" >&2
  exit 1
fi
DLLS=()
while IFS= read -r -d '' candidate; do DLLS+=("$candidate"); done < <(find "$G" -type f -name steam_api64.dll -print0)
if [ "${#DLLS[@]}" -ne 1 ]; then
  echo "游戏目录中发现 ${#DLLS[@]} 份 steam_api64.dll；需要恰好一份。未改动游戏：$G" >&2
  exit 1
fi
D="${DLLS[0]}"
if ! python3 -B - "$D" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('DLL 含链接或多个硬链接')
except (OSError, ValueError) as error:
    print(f'拒绝：DLL 不是独立工作副本：{error}', file=sys.stderr)
    sys.exit(1)
PY
then
  exit 3
fi
if [ -L "$D.orig" ] || { [ -e "$D.orig" ] && [ ! -f "$D.orig" ]; }; then
  echo "原件备份路径不是普通文件：$D.orig。未改动游戏。" >&2
  exit 1
fi

LOG="$(mktemp "$OUT/helper.log.XXXXXXXX")"
PID=""; TEMP_DLL=""; INSTALLED=0
cleanup() {
  [ -z "$TEMP_DLL" ] || rm -f -- "$TEMP_DLL"
  if [ "$INSTALLED" -eq 0 ] && [ -n "$PID" ]; then kill "$PID" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# 不终止别的游戏正在使用的 helper；端口被占用时本次启动会明确失败。
# helper 只服务持有同一令牌的连接；这不验证进程身份。helper 退出即作废。
TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
nohup env STEAMBRIDGE_TOKEN="$TOKEN" "$OUT/steambridge-helper" "$APPID" > "$LOG" 2>&1 &
PID=$!
READY=0
for ((i=0; i<50; i++)); do
  kill -0 "$PID" 2>/dev/null || break
  if grep -q 'listening on 127.0.0.1:' "$LOG"; then READY=1; break; fi
  sleep 0.1
done
if [ "$READY" -ne 1 ]; then
  echo "helper 未开始监听，游戏未改动。详情：$LOG" >&2
  cat "$LOG" >&2
  exit 1
fi
[ -f "$D.orig" ] || cp -p "$D" "$D.orig"
TEMP_DLL="$(mktemp "$(dirname "$D")/.steambridge.XXXXXXXX")"
cp -p "$OUT/steam_api64.dll" "$TEMP_DLL"
cmp -s "$OUT/steam_api64.dll" "$TEMP_DLL"
mv -f "$TEMP_DLL" "$D"
TEMP_DLL=""
INSTALLED=1
echo "已安装：${D}。原件保留在 $D.orig。"
echo "helper 已监听，进程：${PID}。游戏连接后仍须由 Steam 确认登录与所有权。"
echo "启动游戏时必须在其环境里带 STEAMBRIDGE_TOKEN=${TOKEN}（helper 只服务带此令牌的连接；helper 退出后令牌作废）。"
echo "日志：$LOG 与 Wine 里的 %TEMP%\\steambridge.log"
