#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# fetch-icon.sh <steam_appid> <输出.icns>
# 取图三级回退: 本地 Steam 缓存 → 借 Steam 客户端网络栈 → 本机直连 CDN
# (本机直连 store.steampowered.com 在国内被墙, 所以客户端通道是主力)
set -uo pipefail
APPID="${1:?缺 appid}"; OUT="${2:?缺输出路径}"
. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
CACHE="$HOME/Library/Application Support/Steam/appcache/librarycache/$APPID"
TMP=$(mktemp -d); SRC=""

# 一级: 本地缓存里已有的大图
for n in library_capsule.jpg library_600x900.jpg header.jpg; do
  f=$(find "$CACHE" -name "$n" -size +8k 2>/dev/null | head -1)
  [ -n "$f" ] && { SRC="$f"; break; }
done

# 二级: 借 Steam 客户端下载
if [ -z "$SRC" ] && curl -s --max-time 3 http://localhost:8080/json/version >/dev/null 2>&1; then
  node "$SN_LIB/fetch-steam-image.mjs" "$APPID" "$TMP/dl.jpg" >/dev/null 2>&1 \
    && [ -s "$TMP/dl.jpg" ] && SRC="$TMP/dl.jpg"
fi

# 三级: 本机直连(通常被墙, 兜底用)
if [ -z "$SRC" ]; then
  for n in library_600x900.jpg header.jpg; do
    code=$(curl -s -o "$TMP/$n" -w "%{http_code}" --max-time 10 \
      "https://cdn.akamai.steamstatic.com/steam/apps/$APPID/$n" 2>/dev/null)
    [ "$code" = "200" ] && [ -s "$TMP/$n" ] && { SRC="$TMP/$n"; break; }
  done
fi
[ -n "$SRC" ] || { echo "取不到 $APPID 的图" >&2; exit 1; }

# 居中裁方, 否则 Dock 图标会被拉扁
W=$(sips -g pixelWidth "$SRC" 2>/dev/null | awk '/pixelWidth/{print $2}')
H=$(sips -g pixelHeight "$SRC" 2>/dev/null | awk '/pixelHeight/{print $2}')
S=$(( W < H ? W : H ))
sips -s format png -c "$S" "$S" "$SRC" --out "$TMP/sq.png" >/dev/null 2>&1 || cp "$SRC" "$TMP/sq.png"

# iconutil 要求完整尺寸集, 少一个就整体失败
SET="$TMP/icon.iconset"; mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -s format png -z $s $s "$TMP/sq.png" --out "$SET/icon_${s}x${s}.png" >/dev/null 2>&1
  sips -s format png -z $((s*2)) $((s*2)) "$TMP/sq.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$SET" -o "$OUT" 2>/dev/null && echo "$OUT"
