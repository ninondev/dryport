# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""steam-vtable-map.py 的合成 PE 与薄包装回归，不读取真实 DLL。"""
import importlib.util
from pathlib import Path
import shutil
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("steam_vtable_map", ROOT / "tools/steam-vtable-map.py")
vtable_map = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vtable_map)

OBJDUMP = shutil.which("x86_64-w64-mingw32-objdump") or shutil.which("objdump")


def synthetic_pe():
    """返回一个只有 .text 节、导出表和四个包装函数的最小 PE32+。"""
    section_rva = 0x1000
    raw_offset = 0x400
    section_size = 0x400
    data = bytearray(raw_offset + section_size)

    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"

    coff = 0x84
    struct.pack_into("<HHIIIHH", data, coff, 0x8664, 1, 0, 0, 0, 0xF0, 0x2022)
    opt = coff + 20
    struct.pack_into("<H", data, opt, 0x20B)

    export_rva = section_rva
    export_size = 0x200
    struct.pack_into("<II", data, opt + 112, export_rva, export_size)

    section = opt + 0xF0
    data[section:section + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", data, section + 8, section_size, section_rva, section_size, raw_offset)

    def offset(rva):
        return raw_offset + rva - section_rva

    exports = [
        ("SteamAPI_ISteamUser_BLoggedOn", bytes.fromhex("48 8b 01 48 ff 20"), 0x1200, (0, False, "rcx")),
        ("SteamAPI_ISteamUser_GetHSteamUser", bytes.fromhex("48 8b 01 48 ff 60 38"), 0x1240, (7, False, "rcx")),
        ("SteamAPI_ISteamUtils_GetAppID", bytes.fromhex("4c 8b 11 41 ff 62 58"), 0x1280, (11, False, "rcx")),
        (
            "SteamAPI_ISteamUser_GetSteamID",
            bytes.fromhex("48 8b 01 48 8d 54 24 20 48 ff 60 10"),
            0x12C0,
            (2, True, "rcx"),
        ),
    ]

    directory = offset(export_rva)
    name_rva = 0x10A0
    functions_rva = 0x1040
    names_rva = 0x1060
    ordinals_rva = 0x1080
    strings_rva = 0x1100
    struct.pack_into(
        "<IIHHIIIIIII",
        data,
        directory,
        0,
        0,
        0,
        0,
        name_rva,
        1,
        len(exports),
        len(exports),
        functions_rva,
        names_rva,
        ordinals_rva,
    )
    data[offset(name_rva):offset(name_rva) + 17] = b"steam_api64.dll\0"

    string_cursor = strings_rva
    for index, (name, code, code_rva, _) in enumerate(exports):
        struct.pack_into("<I", data, offset(functions_rva) + index * 4, code_rva)
        struct.pack_into("<I", data, offset(names_rva) + index * 4, string_cursor)
        struct.pack_into("<H", data, offset(ordinals_rva) + index * 2, index)
        encoded = name.encode("ascii") + b"\0"
        data[offset(string_cursor):offset(string_cursor) + len(encoded)] = encoded
        string_cursor += len(encoded)
        data[offset(code_rva):offset(code_rva) + len(code)] = code

    return bytes(data), raw_offset, section_rva, exports


@unittest.skipUnless(OBJDUMP, "需要 x86_64-w64-mingw32-objdump 才能反汇编合成包装")
class VtableMapTests(unittest.TestCase):
    def test_pe_exports_reads_the_synthetic_export_table(self):
        data, raw_offset, section_rva, exports = synthetic_pe()

        actual = vtable_map.pe_exports(data)

        self.assertEqual(set(actual), {name for name, _, _, _ in exports})
        for name, _, code_rva, _ in exports:
            self.assertEqual(actual[name], raw_offset + code_rva - section_rva)

    def test_classify_reads_slots_hidden_pointer_and_r10_form(self):
        _, _, _, exports = synthetic_pe()
        objdump = OBJDUMP

        for name, code, _, expected in exports:
            with self.subTest(name=name):
                instructions = vtable_map.disassemble([code], objdump)[0]
                self.assertEqual(vtable_map.classify(instructions), expected)


if __name__ == "__main__":
    unittest.main()
