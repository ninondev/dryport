# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Steamworks DRM 包装（SteamStub）检测：主程序 PE 里有 `.bind` 节就是 Valve 的 DRM 包装器。

包装器的入口在 .bind 里，启动时要在同一个 Wine 前缀里找运行中的 Windows 版 Steam 客户端并取票据解密游戏代码；
steam_api 桥替换不了它（2026-09-13 a wrapped executable：S_API 报错出现在任何 steam_api.dll 加载之前，GameAssembly.dll 还没加载）。
本工具不绕过 DRM，只识别并把原因说清。只读 PE 头与节表，不改文件。

用法：python3 lib/steam_drm.py <exe>   → 打一行 JSON；带包装退出 3，没有退出 0，读不了退出 2。
"""
import json
import struct
import sys

MACHINES = {0x14C: "i386", 0x8664: "x86_64", 0xAA64: "arm64"}


def pe_sections(path):
    """返回 (machine, [节名...])；不是 PE 返回 (None, [])。"""
    with open(path, "rb") as fh:
        head = fh.read(0x40)
        if head[:2] != b"MZ":
            return None, []
        if len(head) < 0x40:
            raise ValueError("MZ 头不完整")
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        fh.seek(pe)
        coff = fh.read(24)
        if len(coff) < 24:
            raise ValueError("PE 头不完整")
        if coff[:4] != b"PE\0\0":
            return None, []
        machine, nsec, _, _, _, optsz, _ = struct.unpack_from("<HHIIIHH", coff, 4)
        if nsec > 96:
            raise ValueError("PE 节数超出 96 的检查上限")
        fh.seek(pe + 24 + optsz)
        table = fh.read(40 * nsec)
        if len(table) != 40 * nsec:
            raise ValueError("PE 节表不完整")
    names = []
    for i in range(nsec):
        raw = table[40 * i:40 * i + 8]
        if len(raw) < 8:
            break
        names.append(raw.rstrip(b"\0").decode("latin1"))
    return MACHINES.get(machine, hex(machine)), names


def inspect(path):
    machine, names = pe_sections(path)
    return {"path": str(path), "machine": machine, "sections": names, "steam_drm": ".bind" in names}


def has_steam_drm(path):
    try:
        return inspect(path)["steam_drm"]
    except OSError:
        return False


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    try:
        info = inspect(argv[1])
    except (OSError, ValueError) as exc:
        print(json.dumps({"path": argv[1], "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(info, ensure_ascii=False))
    return 3 if info["steam_drm"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
