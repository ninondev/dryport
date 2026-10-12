#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/paths.sh"
# 把 DXMT v0.80 按 builtin 方式装进 wine-cx；不设 DllOverride。
# 64 位与 32 位两套 PE 都装：wine-cx 是新式 wow64，32 位游戏的 d3d11.dll 也经 winemetal.so 走 Metal。
# 2026-09-13 之前只装了 x86_64，entry-07（32 位 GameMaker）跑在 Wine 自带 d3d 上，单核 94% 而 GPU 14%（bench 实测，2026-09-13）。
set -u
SRC="$SN_RUNTIMES/dxmt/v0.80"
DST="${SN_WINE_ROOT:-$SN_RUNTIMES/wine-cx}/lib/wine"   # SN_WINE_ROOT=<另一套 wine 目录> 可给新编的 Wine 装（2026-09-14 wine-cx-wgl）
UNIX="$DST/x86_64-unix"
WIN="$DST/x86_64-windows"
WIN32="$DST/i386-windows"
[ -d "$UNIX" ] || { echo "FATAL: $UNIX 不存在"; exit 1; }
[ -d "$WIN" ]  || { echo "FATAL: $WIN 不存在"; exit 1; }

echo "=== 备份将被覆盖的 Wine 自带件 ==="
for f in d3d11.dll dxgi.dll d3d10core.dll; do
  for dir in "$WIN" "$WIN32"; do
    [ -d "$dir" ] || continue
    if [ -f "$dir/$f" ] && [ ! -f "$dir/$f.wine-orig" ]; then
      cp -p "$dir/$f" "$dir/$f.wine-orig"
      echo "备份 $dir/$f -> $f.wine-orig ($(stat -f%z "$dir/$f") bytes)"
    else
      echo "跳过备份 ${dir}/${f}（不存在或已备份）"
    fi
  done
done

echo "=== 安装 ==="
install_one() {  # $1=src $2=dstdir
  cp -p "$1" "$2/" && echo "OK $(basename "$1")  $(stat -f%z "$1") bytes  $1 -> $2/$(basename "$1")" || echo "FAIL $1"
}
install_one "$SRC/x86_64-unix/winemetal.so"   "$UNIX"
install_one "$SRC/x86_64-windows/winemetal.dll" "$WIN"
install_one "$SRC/x86_64-windows/d3d11.dll"     "$WIN"
install_one "$SRC/x86_64-windows/dxgi.dll"      "$WIN"
install_one "$SRC/x86_64-windows/d3d10core.dll" "$WIN"
if [ -d "$WIN32" ] && [ -d "$SRC/i386-windows" ]; then
  install_one "$SRC/i386-windows/winemetal.dll" "$WIN32"
  install_one "$SRC/i386-windows/d3d11.dll"     "$WIN32"
  install_one "$SRC/i386-windows/dxgi.dll"      "$WIN32"
  install_one "$SRC/i386-windows/d3d10core.dll" "$WIN32"
else
  echo "跳过 32 位：${WIN32} 或 ${SRC}/i386-windows 不存在"
fi
echo "=== 完成 ==="
