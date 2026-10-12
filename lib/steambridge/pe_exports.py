#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只读提取用户自有 PE DLL 的导出名字；不加载 DLL，也不读取机器码。"""
import argparse
import json
import mmap
import struct
import sys
from pathlib import Path


class InvalidPE(ValueError):
    pass


def read_exports(path: Path) -> list[str]:
    with path.open("rb") as stream:
        if path.stat().st_size == 0:
            raise InvalidPE("文件为空")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            def unpack(fmt, offset):
                size = struct.calcsize(fmt)
                if offset < 0 or offset + size > len(data):
                    raise InvalidPE("PE 结构超出文件边界")
                return struct.unpack_from(fmt, data, offset)

            if data[:2] != b"MZ":
                raise InvalidPE("不是 Windows PE 文件")
            pe, = unpack("<I", 0x3C)
            if data[pe:pe + 4] != b"PE\0\0":
                raise InvalidPE("PE 签名不完整")
            machine, sections, _, _, _, optional_size, flags = unpack("<HHIIIHH", pe + 4)
            optional = pe + 24
            magic, = unpack("<H", optional)
            if not flags & 0x2000 or (machine, magic) not in ((0x8664, 0x20B), (0x14C, 0x10B)):
                raise InvalidPE("需要 x86_64 的 PE32+ DLL（steam_api64.dll）或 i386 的 PE32 DLL（steam_api.dll）")
            pe32plus = magic == 0x20B
            if not 1 <= sections <= 96 or optional_size < (120 if pe32plus else 104):
                raise InvalidPE("PE 头中的节数或可选头大小无效")
            directory_count, = unpack("<I", optional + (108 if pe32plus else 92))
            if not directory_count:
                raise InvalidPE("DLL 没有导出目录")
            export_rva, export_size = unpack("<II", optional + (112 if pe32plus else 96))
            if not export_rva or export_size < 40:
                raise InvalidPE("DLL 没有完整的导出目录")
            header_size, = unpack("<I", optional + 60)
            section_ranges = []
            for i in range(sections):
                section = optional + optional_size + 40 * i
                _, virtual_size, rva, raw_size, raw_offset, _, _, _, _, _ = unpack(
                    "<8sIIIIIIHHI", section
                )
                section_ranges.append((rva, max(virtual_size, raw_size), raw_offset, raw_size))

            def file_span(rva, size=1):
                if rva < header_size and rva + size <= min(header_size, len(data)):
                    return rva, min(header_size, len(data))
                for start, virtual_size, raw, raw_size in section_ranges:
                    if start <= rva < start + virtual_size:
                        delta = rva - start
                        if delta + size <= raw_size and raw + delta + size <= len(data):
                            return raw + delta, min(raw + raw_size, len(data))
                        break
                raise InvalidPE("导出目录的 RVA 未指向文件中完整的数据")

            export_offset, _ = file_span(export_rva, 40)
            _, _, _, _, _, _, functions, names, functions_rva, names_rva, ordinals_rva = unpack(
                "<IIHHIIIIIII", export_offset
            )
            if not names or names > functions or names > len(data) // 4:
                raise InvalidPE("DLL 没有命名导出，或导出数量无效")
            names_offset, _ = file_span(names_rva, names * 4)
            ordinals_offset, _ = file_span(ordinals_rva, names * 2)
            functions_offset, _ = file_span(functions_rva, functions * 4)
            result = []
            for i in range(names):
                name_rva, = unpack("<I", names_offset + i * 4)
                ordinal, = unpack("<H", ordinals_offset + i * 2)
                if ordinal >= functions:
                    raise InvalidPE("导出序号超出地址表范围")
                address, = unpack("<I", functions_offset + ordinal * 4)
                if not address:
                    raise InvalidPE("命名导出指向空地址")
                start, limit = file_span(name_rva)
                end = data.find(b"\0", start, limit)
                if end < 0:
                    raise InvalidPE("导出名没有结束符")
                try:
                    name = data[start:end].decode("ascii")
                except UnicodeDecodeError as exc:
                    raise InvalidPE("导出名不是 ASCII") from exc
                if not name or any(ord(char) < 33 or ord(char) > 126 for char in name):
                    raise InvalidPE("导出名为空或含控制字符")
                result.append(name)
            if len(set(result)) != len(result):
                raise InvalidPE("导出名字重复")
            return sorted(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dll", type=Path, help="用户自有游戏里的原版 steam_api64.dll")
    parser.add_argument("--json", action="store_true", help="输出可用于 exports.json 的 JSON 数组")
    args = parser.parse_args()
    try:
        names = read_exports(args.dll)
    except (OSError, InvalidPE, ValueError) as exc:
        parser.exit(1, f"无法读取导出表：{args.dll}：{exc}\n")
    print(json.dumps(names, indent=2) if args.json else "\n".join(names))


if __name__ == "__main__":
    main()
