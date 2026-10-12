# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Unity 5.6 格式边界用自造数据验证，不包含游戏或原厂资源。"""
import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unityblob", ROOT / "lib/unity-retarget/unityblob.py")
blob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blob)


def integer(value):
    return struct.pack("<i", value)


def string(value):
    data = value.encode("utf8")
    return integer(len(data)) + data + b"\0" * (-len(data) % 4)


def legacy_tail():
    # 两个缓冲区紧邻，中间没有新版的 struct-count；采样器特意不等于纹理索引。
    return b"".join([
        integer(9), integer(2), integer(0), integer(0), integer(3), integer(4),
        integer(2),
        string(""), integer(0), integer(0),
        string("Unit"), integer(16), integer(1),
        string("value"), *(integer(n) for n in (0, 1, 4, 0, 0, 0)),
        integer(3),
        string("tex_a"), integer(0), integer(3), integer(0x102),
        string("tex_b"), integer(0), integer(0), integer(-252),
        string("Unit"), integer(1), integer(2), integer(0),
    ])


class Unity56BlobTests(unittest.TestCase):
    def test_legacy_constant_buffers_have_no_struct_count(self):
        parsed = blob.parse_tail(legacy_tail(), blob.V56)
        self.assertEqual([(c["name"], c["size"]) for c in parsed["cbs"]], [("", 0), ("Unit", 16)])
        self.assertEqual(parsed["cbs"][1]["vals"], [dict(name="value", type=0, rows=1,
                         cols=4, isMatrix=0, arraySize=0, offset=0)])
        self.assertEqual(parsed["channels"], [(0, 0), (3, 4)])

    def test_legacy_sampler_is_packed_with_dimension_and_signed(self):
        bindings = blob.parse_tail(legacy_tail(), blob.V56)["binds"]
        self.assertEqual(bindings[0], dict(kind="tex", name="tex_a", index=3, sampler=1, dim=2, ms=0))
        self.assertEqual(bindings[1], dict(kind="tex", name="tex_b", index=0, sampler=-1, dim=4, ms=0))
        self.assertEqual(bindings[2], dict(kind="cb", name="Unit", index=2, x=0))

    def test_legacy_tail_roundtrip_is_exact(self):
        raw = legacy_tail()
        self.assertEqual(blob.build_tail(blob.parse_tail(raw, blob.V56), blob.V56), raw)

    def test_modern_grammar_still_contains_struct_counts_and_sampler_word(self):
        legacy = legacy_tail()
        parsed = blob.parse_tail(legacy, blob.V56)
        for version in (blob.V2017, blob.V2018, blob.V2019):
            with self.subTest(version=version):
                modern = blob.build_tail(parsed, version)
                # 两个 struct-count 和两个独立 sampler 字，各增加 4 字节。
                self.assertEqual(len(modern), len(legacy) + 16)
                self.assertEqual(blob.parse_tail(modern, version), parsed)

    def test_legacy_and_modern_layouts_cannot_be_silently_interchanged(self):
        with self.assertRaises((ValueError, struct.error, NotImplementedError)):
            blob.parse_tail(legacy_tail(), blob.V2017)

    def test_truncated_or_trailing_legacy_tail_is_rejected(self):
        raw = legacy_tail()
        for damaged in (raw[:-1], raw + b"\0\0\0\0"):
            with self.subTest(size=len(damaged)), self.assertRaises((ValueError, struct.error)):
                blob.parse_tail(damaged, blob.V56)

    def test_eight_byte_table_can_reference_entries_out_of_order(self):
        def entry(program_type, code):
            return (integer(blob.V56) + integer(program_type) + integer(0) * 4
                    + integer(0) + integer(len(code)) + code + b"\0" * (-len(code) % 4) + legacy_tail())
        first, second = entry(15, b"sample vertex"), entry(17, b"sample fragment")
        # Unity 5.6 的物理顺序经常不同于 m_BlobIndex 顺序。
        raw = integer(2) + integer(20 + len(second)) + integer(len(first))
        raw += integer(20) + integer(len(second)) + second + first
        parsed = blob.parse_blob(raw)
        self.assertEqual(parsed.entry_size, 8)
        self.assertEqual([e["code"] for e in parsed], [b"sample vertex", b"sample fragment"])
        self.assertEqual(blob.parse_blob(blob.build_blob(parsed)), parsed)


if __name__ == "__main__":
    unittest.main()
