#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""给 Wine 侧的 steam_api64.dll 盖 "Wine builtin DLL" 标记，并可单独核对。

Wine 的加载器只把 DOS 头之后（文件偏移 0x40）带这 32 字节签名的 PE 当作 builtin；外置桥靠
`WINEDLLOVERRIDES=steam_api64=b` 加 WINEDLLPATH 指向 overlay 目录加载，没有标记时 overlay 里放什么都不会被加载
（2026-09-13 真机实测：Steamworks.NET 报 DllNotFoundException，helper 连接 0 次）。有 wine-cx 的 winebuild 就用它
（`winebuild --builtin`），没有就按同一格式自己写；两条路的结果都用 check() 核对。

用法：mark_builtin.py <dll> [--winebuild <path>]   盖章并核对
      mark_builtin.py --check <dll>                 只核对，缺标记退出 1
"""
import argparse
import subprocess
import sys
from pathlib import Path

SIGNATURE = b"Wine builtin DLL".ljust(32, b"\0")
OFFSET = 0x40                                     # sizeof(IMAGE_DOS_HEADER)


def check(path):
    data = Path(path).read_bytes()
    if len(data) < 0x80 or data[:2] != b"MZ":
        return False
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    return e_lfanew >= OFFSET + len(SIGNATURE) and data[OFFSET:OFFSET + 16] == SIGNATURE[:16]


def mark(path, winebuild=None):
    path = Path(path)
    data = bytearray(path.read_bytes())
    if data[:2] != b"MZ":
        raise SystemExit(f"{path}: 不是 PE 文件")
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if e_lfanew < OFFSET + len(SIGNATURE):
        raise SystemExit(f"{path}: DOS 头与 PE 头之间只有 {e_lfanew - OFFSET} 字节，放不下 32 字节签名")
    if winebuild and Path(winebuild).is_file():
        subprocess.run([str(winebuild), "--builtin", str(path)], check=True, capture_output=True, timeout=30)
    else:
        data[OFFSET:OFFSET + len(SIGNATURE)] = SIGNATURE   # 覆盖的是 DOS stub，PE 头与节表不动
        path.write_bytes(bytes(data))
    if not check(path):
        raise SystemExit(f"{path}: 盖章后核对失败")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dll", type=Path)
    ap.add_argument("--check", action="store_true", help="只核对")
    ap.add_argument("--winebuild", type=Path, help="wine-cx 的 winebuild；不给或不存在则自己写签名")
    args = ap.parse_args(argv)
    if args.check:
        ok = check(args.dll)
        print(f"{args.dll}: {'有' if ok else '缺'} Wine builtin 标记")
        return 0 if ok else 1
    mark(args.dll, args.winebuild)
    print(f"{args.dll}: 已盖 Wine builtin 标记并核对")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
