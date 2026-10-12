#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
set -eu
APP=$(cd "$(dirname "$0")/../.." && pwd -P)
# 构建时写入实际解释器，Finder 不依赖终端 PATH，也不隐式索取 Command Line Tools。
PYTHON=__SN_NWJS_PYTHON__
[ -x "$PYTHON" ] || { printf '缺少构建时的 Python：%s。请修复安装环境后重新生成 app。\n' "$PYTHON" >&2; exit 1; }
exec "$PYTHON" "$APP/Contents/Resources/nwjs-launch.py" "$APP" "$@"
