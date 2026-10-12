# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""dryport 的路径层（Python 侧），与 lib/paths.sh 一一对应。

SN_HOME      本仓库（自动从本文件位置推出）
SN_RUNTIMES  第三方运行时与构建缓存，默认 ~/Games/runtimes，不进仓库
SN_GAMES     游戏本体、生成的 .app、以及从你自己的 Steam 库算出的路线表，含个人数据
"""
import os
from pathlib import Path

SN_HOME = Path(os.environ.get("SN_HOME") or Path(__file__).resolve().parent.parent)
SN_LIB = SN_HOME / "lib"
SN_RUNTIMES = Path(os.environ.get("SN_RUNTIMES") or Path.home() / "Games/runtimes")
SN_GAMES = Path(os.environ.get("SN_GAMES") or Path.home() / "Games")
STEAM = Path(os.environ.get("STEAM") or Path.home() / "Library/Application Support/Steam")
# 伴侣 app 的产物名（改名只改这一处；shell 侧的对应常量在 app/build.sh）。
DISPLAY_APP_NAME = "Dryport.app"
