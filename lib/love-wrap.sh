#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 原包直读；版本和扩展只做静态检查。
set -eu
. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
[ "$#" -ge 3 ] || { echo "用法：love-wrap.sh <游戏目录> <显示名> <输出.app> [steam appid]" >&2; exit 1; }
require_repack_permission "$1" "${4:-}" || exit $?
exec python3 "$SN_LIB/love-build.py" "$1" "$2" "$3" "$STEAM_APPID"
