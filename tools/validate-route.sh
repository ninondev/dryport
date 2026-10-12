#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/paths.sh"
# validate-route.sh <appid> <depot> <gid> <显示名>
# 用已登录的 Steam 客户端把一个游戏的内容下到 steamapps/content，再跑 dryport 打包并启动验证。
# 前提：manifest-oracle 已经跑完（它会把 sDepotDownloadFileFilter 占住）。
set -eu
APPID="$1"; DEPOT="$2"; GID="$3"; NAME="$4"
SP="${TMPDIR:-/tmp}"; CDP="$SN_LIB/cdp.mjs"
S="$HOME/Library/Application Support/Steam"
CONTENT="$S/steamapps/content/app_${APPID}/depot_${DEPOT}"
say(){ printf '\n== %s\n' "$*"; }
cdp(){ node "$CDP" "$1"; }

say "确认下载过滤器是空的（不然会下 0 个文件）"
cdp "window.__spew.length=0; SteamClient.Console.ExecCommand('sDepotDownloadFileFilter \"\"'); 1" >/dev/null
sleep 2
say "开始下载 $NAME (appid $APPID depot $DEPOT)"
cdp "window.__spew.length=0; SteamClient.Console.ExecCommand('download_depot $APPID $DEPOT $GID'); 1" >/dev/null
for i in $(seq 1 240); do
  sleep 15
  out=$(cdp "window.__mo?window.__mo.join(''):(window.__spew||[]).map(s=>{try{return JSON.parse(s).spew}catch(e){return String(s)}}).join('')" 2>/dev/null | tail -c 200)
  sz=$(du -sm "$CONTENT" 2>/dev/null | cut -f1 || echo 0)
  printf '[%4ds] %sMB %s\n' "$((i*15))" "${sz:-0}" "$(echo "$out" | tr -d '\n' | tail -c 90)"
  echo "$out" | grep -qiE "Depot download complete|failed|error" && break
done
[ -d "$CONTENT" ] || { echo "内容目录没出现，放弃"; exit 1; }
say "下来的东西"
du -sh "$CONTENT"; ls "$CONTENT" | head -8
say "交给 dryport 打包"
STEAM_APPID="$APPID" "$HOME/Games/dryport" wrap "$CONTENT" "$NAME"
