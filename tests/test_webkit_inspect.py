# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""已验资源的摘要边界；自有样例不成为公开兼容配方。"""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
inspector = importlib.import_module("webkit-inspect")


class WebKitInspectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-webkit-inspect-")
        self.addCleanup(temporary.cleanup)
        self.game = Path(temporary.name) / "game"
        self.resources = self.game / "resources/app"
        self.resources.mkdir(parents=True)
        (self.resources / "release.html").write_text("<title>Own fixture</title>")
        (self.resources / "asset.dat").write_bytes(b"sample")
        self.records = inspector.inventory(self.game, "resources/app")
        self.profile = {**inspector.PROFILES["1518810"],
                        "entry_sha256": self.records["release.html"]["sha256"],
                        "resource_sha256": inspector.resource_digest(self.records),
                        "resource_files": len(self.records),
                        "resource_bytes": sum(x["bytes"] for x in self.records.values())}

    def inspect_fixture(self):
        with patch.dict(inspector.PROFILES, {"1518810": self.profile}, clear=True):
            return inspector.inspect(self.game, "1518810")

    def test_known_full_resources_match_and_inputs_are_unchanged(self):
        result = self.inspect_fixture()
        self.assertEqual(result["resource_inventory"], self.records)
        self.assertEqual(result["appid"], "1518810")
        self.assertEqual(inspector.inventory(self.game, "resources/app"), self.records)

    def test_matching_filename_or_appid_does_not_enable_arbitrary_html(self):
        with self.assertRaises(ValueError):
            inspector.inspect(self.game, "1518810")

    def test_wrong_appid_cannot_reuse_a_matching_profile(self):
        for value in ("1518811", "01518810", 1518810, True, "", "../1518810"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                inspector.inspect(self.game, value)

    def test_readonly_detection_can_identify_the_exact_profile_without_id_marker(self):
        with patch.dict(inspector.PROFILES, {"1518810": self.profile}, clear=True):
            self.assertEqual(inspector.inspect(self.game)["appid"], "1518810")
        self.assertFalse((self.game / ".dryport-appid").exists())

    def test_changed_asset_with_same_size_is_rejected_even_when_entry_matches(self):
        (self.resources / "asset.dat").write_bytes(b"change")
        with self.assertRaises(ValueError):
            self.inspect_fixture()

    def test_missing_and_extra_resources_are_rejected(self):
        asset = self.resources / "asset.dat"
        asset.unlink()
        with self.assertRaises(ValueError):
            self.inspect_fixture()
        asset.write_bytes(b"sample")
        (self.resources / "injected.js").write_text("throw Error('not executed');")
        with self.assertRaises(ValueError):
            self.inspect_fixture()

    def test_links_to_both_internal_and_external_targets_are_rejected(self):
        link = self.resources / "link.dat"
        for target in ("asset.dat", "/etc/hosts"):
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                self.inspect_fixture()
            link.unlink()

    def test_parent_resource_symlink_is_not_followed(self):
        actual = self.game / "resources/actual"
        self.resources.rename(actual)
        self.resources.symlink_to("actual", target_is_directory=True)
        with self.assertRaises(OSError):
            self.inspect_fixture()

    def test_special_file_does_not_block(self):
        os.mkfifo(self.resources / "fifo")
        with self.assertRaises(ValueError):
            self.inspect_fixture()

    def test_limits_are_enforced_before_unbounded_reads(self):
        with patch.object(inspector, "MAX_ENTRIES", 1), self.assertRaises(ValueError):
            self.inspect_fixture()
        with patch.object(inspector, "MAX_BYTES", 1), self.assertRaises(ValueError):
            self.inspect_fixture()

    def test_mutation_during_read_is_rejected(self):
        read = os.read
        changed = False

        def mutate(fd, size):
            nonlocal changed
            block = read(fd, size)
            if not changed and block:
                changed = True
                (self.resources / "asset.dat").write_bytes(b"changed after read")
            return block

        with patch.object(inspector.os, "read", side_effect=mutate), self.assertRaises(ValueError):
            self.inspect_fixture()

    def test_entry_code_is_not_executed(self):
        marker = self.game / "unexpected"
        (self.resources / "window.js").write_text(f"require('fs').writeFileSync({str(marker)!r}, 'bad');")
        with self.assertRaises(ValueError):
            self.inspect_fixture()
        self.assertFalse(marker.exists())

    def test_resource_digest_is_independent_of_directory_iteration_order(self):
        self.assertEqual(inspector.resource_digest(self.records),
                         inspector.resource_digest(dict(reversed(list(self.records.items())))))


if __name__ == "__main__":
    unittest.main()
