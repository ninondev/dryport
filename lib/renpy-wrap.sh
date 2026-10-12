#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 固定版本的启动身份必须与登记身份相同，不能把其它候选交给固定 builder。
set -eu
. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
[ "$#" -ge 3 ] && [ "$#" -le 4 ] || { echo "用法：renpy-wrap.sh <游戏目录> <显示名> <输出.app> [steam appid]" >&2; exit 1; }
require_repack_permission "$1" "${4:-}" || exit $?
[ "$STEAM_APPID" = 2118420 ] || { echo "此游戏尚无已验证的 Ren’Py 安装入口，原文件已保留。" >&2; exit 1; }
exec python3 "$SN_LIB/renpy-build.py" "$1" "$2" "$3"
