#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/paths.sh"
# wine-cjk-fonts.sh <prefix> [wine 可执行]
# 把 macOS 自带的中日韩字体挂进 Wine prefix，并把 Windows 常用字体名替换过去。
# 不复制字体文件（软链），不改系统，只动这个 prefix。
set -eu
PREFIX="${1:?用法: wine-cjk-fonts.sh <prefix> [wine]}"
WINE="${2:-$SN_RUNTIMES/wine-cx/bin/wine}"
FDIR="$PREFIX/drive_c/windows/Fonts"
mkdir -p "$FDIR"
n=0
for f in \
  "/System/Library/Fonts/PingFang.ttc" \
  "/System/Library/Fonts/Hiragino Sans GB.ttc" \
  "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc" \
  "/System/Library/Fonts/Supplemental/Songti.ttc" \
  "/System/Library/Fonts/Supplemental/Arial Unicode.ttf" \
  "/System/Library/Fonts/AppleSDGothicNeo.ttc" \
  "/System/Library/Fonts/Supplemental/Apple Symbols.ttf" ; do
  [ -f "$f" ] || continue
  b=$(basename "$f"); [ -e "$FDIR/$b" ] || ln -s "$f" "$FDIR/$b"; n=$((n+1))
done
# macOS 的日文黑体文件名是日文的，兜一层
for f in /System/Library/Fonts/*.ttc; do
  case "$(basename "$f")" in
    *ヒラギノ*|*Hiragino*) b=$(basename "$f"); [ -e "$FDIR/$b" ] || { ln -s "$f" "$FDIR/$b"; n=$((n+1)); } ;;
  esac
done
echo "挂了 $n 个字体文件进 $FDIR"

# Windows 里最常被游戏写死的中日字体名 → 本机实际有的字体。
# 实测两条：(1) `wine regedit <file.reg>` 导入这个键会静默失败，必须用 `wine reg add` 逐条写；
#          (2) Wine 只认 CoreText 枚举出来的 macOS 字体，往 prefix 的 Fonts 目录塞字体、
#              甚至装进 ~/Library/Fonts，wineboot -u 之后都扫不到（实测 725 条字体列表纹丝不动）。
# 所以只能映射到 macOS 自带字体的实际 face 名。Hiragino Sans W4 覆盖日文全部、中文绝大部分；
# 简体独有字与韩文仍有缺口（见 各游戏的字体检查）。
TARGET="${SN_CJK_FONT:-Hiragino Sans W4}"
for f in "MS Gothic" "MS PGothic" "MS UI Gothic" "MS Mincho" "MS PMincho" \
         "Meiryo" "Meiryo UI" "Yu Gothic" "Yu Gothic UI" \
         "SimSun" "NSimSun" "SimHei" "Microsoft YaHei" "Microsoft YaHei UI" \
         "Microsoft JhengHei" "MingLiU" "PMingLiU" "DFKai-SB" \
         "Malgun Gothic" "Gulim" "Batang" "Tahoma" "Segoe UI" \
         "MS Shell Dlg" "MS Shell Dlg 2"; do
  WINEDEBUG=-all "$WINE" reg add 'HKCU\Software\Wine\Fonts\Replacements' /v "$f" /d "$TARGET" /f >/dev/null 2>&1
done
echo "字体替换表已写入注册表（只影响这个 prefix），目标字体 $TARGET"
