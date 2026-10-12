#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP="$HERE/../.."
if ! /usr/bin/codesign --verify --strict "$APP"; then
  printf 'LÖVE 未启动：app 签名检查失败，请重新生成：%s\n' "$APP" >&2
  exit 1
fi
# Python 导入发生在游戏沙箱之前；只读取已验签的自有模块和标准库。
exec __SN_LOVE_PYTHON__ -E -s -S -B "$HERE/../Resources/love-launch.py" "$APP"
