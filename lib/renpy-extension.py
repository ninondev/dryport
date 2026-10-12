# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""经 Ren'Py 官方 RPE 接口设置外置日志和首次窗口偏好，不替代游戏脚本。"""
import json
import os
import platform
import sys
import renpy

autorun = os.path.realpath(os.environ["SN_RENPY_AUTORUN"])
savedir = os.path.realpath(os.environ["SN_RENPY_SAVES"])
logdir = os.path.realpath(os.environ["SN_RENPY_LOGDIR"])
if (renpy.version_only != "8.1.3.23091805" or not renpy.macapp
        or renpy.game.args.command != "sn_native"
        or os.path.realpath(renpy.config.renpy_base) != autorun
        or os.path.realpath(renpy.game.args.savedir) != savedir):
    raise RuntimeError("原生入口与已准备的游戏不匹配。")

# 原游戏会用 logdir 创建 gundog.log；保留其原有 logging 配置。
renpy.config.logdir = logdir
renpy.arguments.register_command("sn_native", lambda: True, uses_display=True)
deferrals = 0


def initialized():
    global deferrals
    if renpy.game.post_init[-1] is not initialized:
        deferrals += 1
        if deferrals > 8:
            raise RuntimeError("游戏初始化回调未收束。")
        renpy.game.post_init.append(initialized)
        return
    if os.environ["SN_RENPY_FIRST_RUN"] == "1":
        renpy.game.preferences.fullscreen = False
    achievement = sys.modules.get("store.achievement")
    steam = getattr(achievement, "steam", None)
    api = getattr(achievement, "steamapi", None)
    steam_initialized = steam is not None and api is not None
    steam_appid = api.SteamUtils().GetAppID() if api is not None and steam_initialized else None
    with open(os.path.join(logdir, "initialized.json"), "x") as stream:
        json.dump({"protocol": 1, "pid": os.getpid(), "version": renpy.version_only,
                   "architecture": platform.machine(), "fullscreen": renpy.game.preferences.fullscreen,
                   "savedir": renpy.config.savedir, "interface_absent": renpy.game.interface is None,
                   "steam_enabled": renpy.config.enable_steam,
                   "early_script_version": renpy.config.early_script_version,
                   "steam_initialized": steam_initialized, "steam_appid": steam_appid}, stream)


def queue_initialized():
    renpy.game.post_init.append(initialized)


renpy.game.post_init.append(queue_initialized)
