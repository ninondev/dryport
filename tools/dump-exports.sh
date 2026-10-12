#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 从用户自有 DLL 读取名字表；只需 Python 3 标准库，不执行或复制 DLL。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/paths.sh"
exec python3 -B "$SN_LIB/steambridge/pe_exports.py" "$@"
