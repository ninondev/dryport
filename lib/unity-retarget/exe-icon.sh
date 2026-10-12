#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# exe-icon.sh <game.exe> <out.icns>  — 从 Windows exe 的资源段抽最大图标转成 icns
# icoextract (MIT) 抽出 .ico, sips 会自动挑里面最大的一张, iconutil 打成 icns。
set -eu
EXE="${1:?缺 exe}"; OUT="${2:?缺输出 icns}"
PY="${SN_VENV:-${SN_RUNTIMES:-$HOME/Games/runtimes}/unity-retarget/venv}/bin"
[ -x "$PY/icoextract" ] || { echo "venv 里没有 icoextract" >&2; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
"$PY/icoextract" "$EXE" "$TMP/i.ico" >/dev/null 2>&1 || { echo "exe 里没有图标资源" >&2; exit 1; }
sips -s format png "$TMP/i.ico" --out "$TMP/i.png" >/dev/null 2>&1 || { echo "ico 转 png 失败" >&2; exit 1; }
W=$(sips -g pixelWidth "$TMP/i.png" | awk '/pixelWidth/{print $2}')
H=$(sips -g pixelHeight "$TMP/i.png" | awk '/pixelHeight/{print $2}')
S=$(( W < H ? W : H ))
sips -s format png -c "$S" "$S" "$TMP/i.png" --out "$TMP/sq.png" >/dev/null 2>&1 || cp "$TMP/i.png" "$TMP/sq.png"
SET="$TMP/icon.iconset"; mkdir -p "$SET"
for s in 16 32 128 256 512; do          # iconutil 要求完整尺寸集, 少一个整体失败
  sips -s format png -z $s $s "$TMP/sq.png" --out "$SET/icon_${s}x${s}.png" >/dev/null 2>&1
  sips -s format png -z $((s*2)) $((s*2)) "$TMP/sq.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$SET" -o "$OUT" && echo "$OUT"
