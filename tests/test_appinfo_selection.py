# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""appinfo 选择解码与全量结果一致；非目标记录损坏不能绕过风险闸。"""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import risk


VERSIONS = (0x07564427, 0x07564428, 0x07564429)


def app(name):
    return {"appinfo": {"appid": 42, "common": {"name": name, "oslist": "windows",
                       "category": {"category_2": "1"}}, "token": 2 ** 40}}


def encode(tree, indexes=None):
    body = bytearray()
    for key, value in tree.items():
        kind = 0 if isinstance(value, dict) else 1 if isinstance(value, str) else 7 if value > 2 ** 31 else 2
        body.append(kind)
        if indexes is None:
            body.extend(key.encode() + b"\0")
        else:
            index = indexes.setdefault(key, len(indexes))
            body.extend(struct.pack("<I", index))
        if kind == 0:
            body.extend(encode(value, indexes))
        elif kind == 1:
            body.extend(value.encode() + b"\0")
        elif kind == 2:
            body.extend(struct.pack("<i", value))
        else:
            body.extend(struct.pack("<Q", value))
    return bytes(body) + b"\x08"


def cache(records, version=VERSIONS[0]):
    indexes = {} if version == VERSIONS[2] else None
    data = bytearray()
    for appid, tree in records:
        body = tree if isinstance(tree, bytes) else encode(tree, indexes)
        record = bytes(60 if version >= VERSIONS[1] else 40) + body
        data.extend(struct.pack("<II", appid, len(record)) + record)
    data.extend(bytes(4))
    header = struct.pack("<II", version, 0)
    if indexes is not None:
        header += struct.pack("<q", 16 + len(data))
        data.extend(struct.pack("<I", len(indexes)))
        data.extend(b"".join(key.encode() + b"\0" for key in indexes))
    return header + data


class AppinfoSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-appinfo-test-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "appinfo.vdf"

    def write(self, data):
        self.path.write_bytes(data)
        return self.path

    def test_all_supported_versions_keep_full_and_selected_trees_identical(self):
        for version in VERSIONS:
            with self.subTest(version=hex(version)):
                self.write(cache([(42, app("first")), (7, app("second")), (99, app("third"))], version))
                full = risk.load_appinfo(self.path)
                self.assertEqual(list(full), [42, 7, 99])
                self.assertEqual(full[42], app("first")["appinfo"])
                selected = risk.load_appinfo(self.path, appids=(value for value in (99, 42, 999)))
                self.assertEqual(selected, {key: value for key, value in full.items() if key in {99, 42}})
                self.assertEqual(list(selected), [42, 99])
                self.assertEqual(risk.load_appinfo(self.path, appids=set()), {})
                self.assertEqual(risk.load_appinfo(self.path, appids={999}), {})

    def test_only_requested_records_are_decoded(self):
        for version, parser_name in ((VERSIONS[0], "parse_bvdf"), (VERSIONS[2], "parse_bvdf_strtbl")):
            with self.subTest(version=hex(version)):
                self.write(cache([(42, app("first")), (7, app("second")), (99, app("third"))], version))
                parser = getattr(risk, parser_name)
                roots = []
                def observed(data, *args, **kwargs):
                    pos = kwargs.get("p", args[1] if parser_name.endswith("strtbl") and len(args) > 1
                                     else args[0] if parser_name == "parse_bvdf" and args else 0)
                    if pos == 0:
                        roots.append(len(data))
                    return parser(data, *args, **kwargs)
                with patch.object(risk, parser_name, side_effect=observed):
                    selected = risk.load_appinfo(self.path, appids={7, 1000})
                self.assertEqual(list(selected), [7])
                self.assertEqual(len(roots), 1)

    def test_duplicate_appids_keep_last_valid_record_like_full_loading(self):
        self.write(cache([(42, app("old")), (42, app("new")), (42, b"\x00unfinished")]))
        selected = risk.load_appinfo(self.path, appids={42})
        self.assertEqual(selected, risk.load_appinfo(self.path))
        self.assertEqual(selected[42]["common"]["name"], "new")

    def test_unselected_bad_record_boundaries_and_missing_terminator_still_fail(self):
        prefix = cache([(42, app("target"))])[:-4]
        broken_tails = {
            "no terminator": b"",
            "partial appid": b"\x02",
            "partial record header": struct.pack("<I", 99),
            "oversized record": struct.pack("<II", 99, 10000) + bytes(4),
            "undersized record": struct.pack("<II", 99, 40) + bytes(40) + bytes(4),
        }
        for label, tail in broken_tails.items():
            for wanted in ({42}, set(), None):
                with self.subTest(case=label, wanted=wanted):
                    self.write(prefix + tail)
                    with self.assertRaises(ValueError):
                        risk.load_appinfo(self.path, appids=wanted)

    def test_truncated_selected_node_never_returns_partial_single_player_evidence(self):
        node = b"\x00common\0\x00category\0\x01category_2\x001\0"
        self.write(cache([(42, node), (7, app("good"))]))
        selected = risk.load_appinfo(self.path, appids={42})
        self.assertEqual(selected, {})
        self.assertEqual(risk.assess(42, selected.get(42), {})["verdict"], risk.UNKNOWN)
        self.assertEqual(risk.load_appinfo(self.path, appids={7}), {7: app("good")["appinfo"]})

    def test_invalid_string_index_omits_only_undecodable_record(self):
        broken = b"\x00" + struct.pack("<I", 0xFFFFFFFF) + b"\x08\x08"
        self.write(cache([(42, broken), (7, app("good"))], VERSIONS[2]))
        self.assertEqual(risk.load_appinfo(self.path, appids={42}), {})
        self.assertEqual(risk.load_appinfo(self.path, appids={7}), {7: app("good")["appinfo"]})

    def test_corrupt_header_and_string_table_fail_even_for_empty_selection(self):
        valid = cache([(42, app("target"))], VERSIONS[2])
        table_offset = struct.unpack_from("<q", valid, 8)[0]
        bad_offset = valid[:8] + struct.pack("<q", len(valid)) + valid[16:]
        bad_count = valid[:table_offset] + struct.pack("<I", 0xFFFFFFFF) + valid[table_offset + 4:]
        cases = [b"", b"1234567", struct.pack("<II", VERSIONS[2], 0),
                 struct.pack("<II", 0xDEADBEEF, 0), bad_offset, bad_count, valid[:-1]]
        for data in cases:
            with self.subTest(size=len(data)):
                self.write(data)
                with self.assertRaises(ValueError):
                    risk.load_appinfo(self.path, appids=set())

    def test_v29_unselected_record_cannot_overlap_string_table(self):
        valid = cache([(42, app("target")), (7, app("other"))], VERSIONS[2])
        first_size = struct.unpack_from("<I", valid, 20)[0]
        second_pos = 16 + 8 + first_size
        broken = bytearray(valid)
        struct.pack_into("<I", broken, second_pos + 4, len(valid))
        self.write(broken)
        with self.assertRaises(ValueError):
            risk.load_appinfo(self.path, appids={42})

    def test_gate_reads_only_requested_appid_without_changing_receipt(self):
        for option in ("--gate", "--gate-json"):
            with self.subTest(option=option), patch.object(sys, "argv", ["risk.py", option, "42"]), \
                    patch.object(risk, "load_appinfo", return_value={42: app("fixture")["appinfo"]}) as loader, \
                    patch.object(risk, "load_ac", return_value={}), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(risk.main(), 0)
                loader.assert_called_once_with(appids={42})
                if option.endswith("json"):
                    self.assertEqual(json.loads(output.getvalue()), {"protocol": 1, "code": 0,
                                     **risk.assess(42, app("fixture")["appinfo"], {})})

    def test_invalid_gate_arguments_fail_before_loading_metadata(self):
        with patch.object(sys, "argv", ["risk.py", "--gate", "0"]), \
                patch.object(risk, "load_appinfo") as loader, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(risk.main(), 3)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
