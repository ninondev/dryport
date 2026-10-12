# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""回调结构从 macOS 4 字节打包到 Win64 自然对齐的转换（lib/steambridge/cbconv.h）：用自造字节验证，不含 SDK 头文件。"""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "lib/steambridge"
CC = shutil.which("cc")

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "cbconv.h"
int main(int argc, char **argv) {
    int id = atoi(argv[1]); unsigned char src[512], dst[512]; int n = 0;
    memset(dst, 0xa5, sizeof dst);
    for (const char *p = argv[2]; p[0] && p[1]; p += 2) { unsigned v; sscanf(p, "%2x", &v); src[n++] = (unsigned char)v; }
    int out = argc > 3 ? sb_cb_to_pack4(id, src, n, dst, sizeof dst) : sb_cb_convert(id, src, n, dst, sizeof dst);
    printf("%d", out);
    for (int i = 0; i < out; i++) printf(" %02x", dst[i]);
    printf("\n");
    return 0;
}
'''


@unittest.skipUnless(CC, "需要 cc 编译测试驱动")
class CallbackConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="sn-cbconv-")
        base = Path(cls.tmp.name)
        (base / "driver.c").write_text(DRIVER)
        subprocess.run([CC, "-std=c11", "-Wall", "-Werror", "-I", str(BRIDGE), "-o", str(base / "driver"), str(base / "driver.c")],
                       check=True, capture_output=True, timeout=60)
        cls.driver = base / "driver"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def convert(self, cb_id, src, *, to_pack4=False):
        args = [str(self.driver), str(cb_id), src.hex()]
        if to_pack4:
            args.append("pack4")
        out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout.split()
        return int(out[0]), bytes.fromhex("".join(out[1:]))

    def test_user_stats_received_grows_from_20_to_24_with_zero_gap(self):
        mac = struct.pack("<QIQ", 0x0100000000125676, 1, 76561197960265728)   # pack(4): 20 bytes, no gap after EResult
        n, win = self.convert(1101, mac)
        self.assertEqual(n, 24)
        self.assertEqual(win, struct.pack("<QIIQ", 0x0100000000125676, 1, 0, 76561197960265728))

    def test_user_stats_received_shrinks_back_to_pack4(self):
        mac = struct.pack("<QIQ", 0x0100000000125676, 1, 76561197960265728)
        win = mac[:12] + b"\xa5" * 4 + mac[12:]
        self.assertEqual(self.convert(1101, win, to_pack4=True), (20, mac))

    def test_user_achievement_stored_pads_to_152(self):
        name = b"ACH_1".ljust(128, b"\0")
        mac = struct.pack("<QB", 0x125676, 0) + name + b"\xa5" * 3 + struct.pack("<II", 3, 10)   # 8 + 1 + 128 = 137 -> pack(4) aligns the uint32 at 140 -> 148
        self.assertEqual(len(mac), 148)
        n, win = self.convert(1103, mac)
        self.assertEqual(n, 152)
        self.assertEqual(win[:137], mac[:137])
        self.assertEqual(win[137:140], b"\0" * 3)
        self.assertEqual(win[140:148], struct.pack("<II", 3, 10))
        self.assertEqual(win[148:], b"\0" * 4)
        self.assertEqual(self.convert(1103, mac + b"\xa5" * 4, to_pack4=True),
                         (148, mac[:137] + b"\0" * 3 + mac[140:]))

    def test_same_layout_padding_is_zeroed_in_both_directions(self):
        source = struct.pack("<BB", 1, 0) + b"\xa5" * 2 + struct.pack("<II", 1201270, 4242)
        expected = source[:2] + b"\0" * 2 + source[4:]
        for to_pack4 in (False, True):
            with self.subTest(to_pack4=to_pack4):
                self.assertEqual(self.convert(331, source, to_pack4=to_pack4), (12, expected))

    def test_tail_padding_is_zeroed_and_removed_on_reverse_conversion(self):
        mac = struct.pack("<QI", 77, 1)
        self.assertEqual(self.convert(1102, mac), (16, mac + b"\0" * 4))
        self.assertEqual(self.convert(1102, mac + b"\xa5" * 4, to_pack4=True), (12, mac))

    def test_same_layout_structs_pass_through(self):
        mac = struct.pack("<BBxxII", 1, 0, 1201270, 4242)   # GameOverlayActivated_t: two bytes, pad to 4, two uint32; identical on both sides
        self.assertEqual(self.convert(331, mac), (12, mac))
        mac = struct.pack("<QII", 77, 1101, 20)           # SteamAPICallCompleted_t: 16 bytes both sides
        self.assertEqual(self.convert(703, mac), (16, mac))

    def test_empty_struct_is_one_zero_byte(self):
        self.assertEqual(self.convert(101, b"\0"), (1, b"\0"))

    def test_unknown_or_mismatched_input_is_refused_not_guessed(self):
        self.assertEqual(self.convert(9999, b"\0" * 8)[0], -1)
        self.assertEqual(self.convert(1101, b"\0" * 24)[0], -3)   # already 24 bytes: not a macOS layout
        self.assertEqual(self.convert(1102, b"\0" * 8)[0], -3)    # too short


if __name__ == "__main__":
    unittest.main()
