#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 只为已核实原包构建自有宿主；风险闸异常或拒绝时不进入构建。
set -eu
. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
[ "$#" -ge 3 ] || { echo "用法：webkit-wrap.sh <游戏目录> <显示名> <输出.app> [steam appid]" >&2; exit 1; }
require_repack_permission "$1" "${4:-}" || exit $?
exec python3 "$SN_LIB/webkit-build.py" "$1" "$2" "$3" "$STEAM_APPID"
