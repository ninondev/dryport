#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只读匹配已验证的完整 Ren’Py 原包，不执行游戏脚本。"""
import argparse
import importlib
import json
from pathlib import Path
import sys

PROFILES = {
    "2118420": {
        "profile": "gun-dog-2118420-v1",
        "depot": "2118421", "manifest": "7969527995990251058",
    },
}


def inspect(game, appid):
    if appid not in PROFILES:
        raise ValueError("此游戏尚无已验证的 Ren’Py 安装入口，原文件已保留。")
    builder = importlib.import_module("renpy-build")
    return {**PROFILES[appid], **builder.profile(builder.SHARED.inventory(Path(game)), appid)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path)
    parser.add_argument("appid")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.game, args.appid), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print("Ren’Py 原包未匹配：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
