# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Steamworks DRM 包装（.bind 节）识别只读 PE 节表，不猜文件名，也不改文件。"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import steam_drm  # noqa: E402


def build_pe(machine, sections, optsz=0):
    data = bytearray(0x40 + 24 + optsz + 40 * len(sections))
    data[:2] = b"MZ"; struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\0\0"; struct.pack_into("<HHIIIHH", data, 0x44, machine, len(sections), 0, 0, 0, optsz, 0)
    base = 0x40 + 24 + optsz
    for i, name in enumerate(sections):
        data[base + 40 * i:base + 40 * i + 8] = name.encode().ljust(8, b"\0")
    return bytes(data)


class SteamDrmTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-drm-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, name, data):
        p = self.dir / name; p.write_bytes(data); return p

    def test_bind_section_means_steam_drm_and_other_layouts_do_not(self):
        wrapped = self.write("WrappedFixture.exe", build_pe(0x14C, [".text", ".rdata", ".data", ".rsrc", ".reloc", ".bind"]))
        info = steam_drm.inspect(wrapped)
        self.assertTrue(info["steam_drm"]); self.assertEqual(info["machine"], "i386"); self.assertIn(".bind", info["sections"])
        plain = self.write("FixtureGame.exe", build_pe(0x8664, [".text", ".rdata", ".data", ".pdata", "_RDATA", ".rsrc", ".reloc"], optsz=240))
        info = steam_drm.inspect(plain)
        self.assertFalse(info["steam_drm"]); self.assertEqual(info["machine"], "x86_64"); self.assertEqual(len(info["sections"]), 7)
        self.assertTrue(steam_drm.has_steam_drm(wrapped)); self.assertFalse(steam_drm.has_steam_drm(plain))

    def test_non_pe_and_missing_files_are_not_drm(self):
        text = self.write("readme.exe", b"not a portable executable at all, just bytes")
        self.assertEqual(steam_drm.inspect(text), {"path": str(text), "machine": None, "sections": [], "steam_drm": False})
        self.assertFalse(steam_drm.has_steam_drm(self.dir / "missing.exe"))
        truncated = self.write("short.exe", build_pe(0x14C, [".text", ".bind"])[:0x50])
        with self.assertRaises(ValueError):
            steam_drm.inspect(truncated)

    def test_incomplete_or_oversized_pe_tables_are_inspection_errors(self):
        oversized = bytearray(build_pe(0x14C, [".text"]))
        struct.pack_into("<H", oversized, 0x46, 97)
        for index, data in enumerate((build_pe(0x14C, [".text", ".bind"])[:0x50],
                                      build_pe(0x14C, [".text", ".bind"])[:-1], bytes(oversized))):
            with self.subTest(layout=index):
                path = self.write(f"invalid-{index}.exe", data)
                result = subprocess.run([sys.executable, "-B", str(ROOT / "lib/steam_drm.py"), str(path)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("error", json.loads(result.stdout))
                self.assertEqual(path.read_bytes(), data)

    def test_cli_exit_codes_carry_the_verdict(self):
        wrapped = self.write("w.exe", build_pe(0x14C, [".text", ".bind"]))
        plain = self.write("p.exe", build_pe(0x14C, [".text"]))
        run = lambda p: subprocess.run([sys.executable, "-B", str(ROOT / "lib/steam_drm.py"), str(p)], capture_output=True, text=True, timeout=20)
        r = run(wrapped); self.assertEqual(r.returncode, 3); self.assertTrue(json.loads(r.stdout)["steam_drm"])
        r = run(plain); self.assertEqual(r.returncode, 0); self.assertFalse(json.loads(r.stdout)["steam_drm"])
        r = run(self.dir / "none.exe"); self.assertEqual(r.returncode, 2); self.assertIn("error", json.loads(r.stdout))
        self.assertEqual(wrapped.read_bytes(), build_pe(0x14C, [".text", ".bind"]), "检查不能改文件")


if __name__ == "__main__":
    unittest.main()
