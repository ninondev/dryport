# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""固定 ScummVM 实验的写入约束；不能作为任意 macOS 应用的透明偏好重定向。"""
import importlib
from pathlib import Path

SHARED = importlib.import_module("love-runtime")


def preferences_guard(state, protected):
    # 只禁止访问这两个 cfprefsd 服务，不能保证读隔离或 synchronize 返回失败。
    # 实测既有宿主值仍可被读到，写后同步可返回 true，但新进程未见该写入。
    # 这不是把 NS/CF 偏好透明搬到 HOME；原域是否保持必须在外部回读。
    # 仅用于核心设置和存档通过外置 ini/savepath 管理的受限 ScummVM 实验。
    command = SHARED.sandbox(Path(state), [Path(path) for path in protected])
    command[-1] += ('(deny network*)\n'
                    '(deny mach-lookup (global-name "com.apple.cfprefsd.agent") '
                    '(global-name "com.apple.cfprefsd.daemon"))\n')
    return command


def environment(state, headless=True):
    state = Path(state)
    result = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C",
              "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
              "CFFIXED_USER_HOME": str(state / "home")}
    if headless:
        result.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    return result
