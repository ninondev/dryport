#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只读核对已实际验证的浏览器入口；文件名或 Electron 标签不能授予兼容资格。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

# 2026-09-12：Koi Farm 1.1.0，Steam depot 1518811 / 4627373275232663596。
# 只记录原件摘要，不分发游戏内容。菜单、移鱼、短时关闭保存及重开已分别实测。
# 全资源摘要约束生命周期适配范围；新增版本必须重新核对原处理器并验证保存。
PROFILES = {
    "1518810": {
        "profile": "koi-farm-1518810-v1", "content_root": "resources/app",
        "entry": "release.html", "lifecycle": "terminal-beforeunload",
        "entry_sha256": "a54837cec590e48d5cdf411d0d457ed611c87afcd459b455ae532d90ea6c0510",
        "resource_sha256": "20a596f45ff396cfc0042db928cc190336d4f1f1be117d742660e67e38cbc180",
        "resource_files": 403, "resource_bytes": 135399917,
        "depot": "1518811", "manifest": "4627373275232663596",
    },
}
MAX_ENTRIES = 4096
MAX_BYTES = 512 * 1024 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def resource_digest(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def inventory(game, relative_root):
    """逐级固定目录 fd，拒绝链接；读取有界且读取期间变化即停止。"""
    descriptor = os.open(game, DIRECTORY_FLAGS)
    try:
        for component in relative_root.split("/"):
            if component in ("", ".", "..") or "\\" in component:
                raise ValueError("原资源路径无效。")
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        records, counters = {}, [0, 0]

        def walk(parent, prefix, depth):
            if depth > 16:
                raise ValueError("原资源目录层级超过检查上限。")
            before = os.fstat(parent)
            names = sorted(os.listdir(parent))
            for name in names:
                counters[0] += 1
                if counters[0] > MAX_ENTRIES:
                    raise ValueError("原资源项数超过检查上限。")
                relative = prefix + name
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
                    try:
                        if identity(info) != identity(os.fstat(child)):
                            raise ValueError("读取期间原资源目录变化。")
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    # O_NONBLOCK 避免文件在 stat 后换成 FIFO 时挂住检查器。
                    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    try:
                        if identity(info) != identity(os.fstat(child)):
                            raise ValueError("读取期间原资源文件变化。")
                        if counters[1] + info.st_size > MAX_BYTES:
                            raise ValueError("原资源字节数超过检查上限。")
                        digest, size = hashlib.sha256(), 0
                        while block := os.read(child, min(1024 * 1024, info.st_size - size + 1)):
                            size += len(block)
                            if size > info.st_size:
                                raise ValueError("读取期间原资源文件增大。")
                            digest.update(block)
                        if size != info.st_size or identity(info) != identity(os.fstat(child)):
                            raise ValueError("读取期间原资源文件变化。")
                        counters[1] += size
                        records[relative] = {"bytes": size, "sha256": digest.hexdigest()}
                    finally:
                        os.close(child)
                else:
                    raise ValueError("原资源含链接或特殊文件，尚未支持：" + relative)
                if identity(info) != identity(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                    raise ValueError("读取期间原资源项被替换。")
            if names != sorted(os.listdir(parent)) or identity(before) != identity(os.fstat(parent)):
                raise ValueError("读取期间原资源目录变化。")

        walk(descriptor, "", 0)
        return dict(sorted(records.items()))
    finally:
        os.close(descriptor)


def inspect(game, appid=None):
    game = Path(game).resolve(strict=True)
    if appid is not None and (not isinstance(appid, str) or appid not in PROFILES):
        raise ValueError("此游戏没有已验证的 WebKit 入口；不能仅凭 Electron 标签启动。")
    candidates = [(appid, PROFILES[appid])] if appid is not None else list(PROFILES.items())
    for identifier, profile in candidates:
        try:
            records = inventory(game, profile["content_root"])
        except FileNotFoundError:
            continue
        if (records.get(profile["entry"], {}).get("sha256") == profile["entry_sha256"]
                and len(records) == profile["resource_files"]
                and sum(item["bytes"] for item in records.values()) == profile["resource_bytes"]
                and resource_digest(records) == profile["resource_sha256"]):
            return {**profile, "appid": identifier, "resource_inventory": records,
                    "validation": "菜单、操作及短时关闭保存已验证；音频、完整教程和长期性能待验证"}
    raise ValueError("游戏资源与已验证版本不符，已保留原件；需验证此版本后才能使用 WebKit 路线。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path)
    parser.add_argument("appid", nargs="?")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.game, args.appid), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(f"WebKit 入口未确认：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
