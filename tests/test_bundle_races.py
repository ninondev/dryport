# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Controlled directory substitutions and archive link chains must fail closed."""
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import bundle_safety as safety


class BundleRaceTests(unittest.TestCase):
    def test_parent_moved_during_write_is_not_published_outside(self):
        with tempfile.TemporaryDirectory(prefix="sn-parent-move-") as tmp:
            base = Path(tmp).resolve()
            root, outside = base / "root", base / "outside"
            root.mkdir(); outside.mkdir()
            (outside / "victim").mkdir()
            (outside / "victim/file").write_bytes(b"outside original")
            with self.assertRaises((OSError, ValueError)):
                with safety.atomic_output(root, root / "child/file") as output:
                    output.write(b"new bytes")
                    (root / "child").rename(outside / "moved")
                    (root / "child").symlink_to(outside / "victim", target_is_directory=True)
            self.assertEqual((outside / "victim/file").read_bytes(), b"outside original")
            self.assertEqual(list((outside / "moved").iterdir()), [])

    def test_root_ancestor_replaced_before_open_cannot_redirect_output(self):
        with tempfile.TemporaryDirectory(prefix="sn-root-race-") as tmp:
            base = Path(tmp).resolve()
            ancestor, outside = base / "parent", base / "outside"
            root = ancestor / "root"
            root.mkdir(parents=True)
            (outside / "root").mkdir(parents=True)
            original_open = os.open
            changed = False
            def swap(path, flags, *args, **kwargs):
                nonlocal changed
                if not changed and (str(path) == str(root) or str(path) == ancestor.name):
                    changed = True
                    ancestor.rename(base / "kept-parent")
                    ancestor.symlink_to(outside, target_is_directory=True)
                return original_open(path, flags, *args, **kwargs)
            with patch.object(safety.os, "open", side_effect=swap):
                with self.assertRaises((OSError, ValueError)):
                    with safety.atomic_output(root, root / "new/file") as output:
                        output.write(b"must stay confined")
            self.assertTrue(changed)
            self.assertEqual(list((outside / "root").iterdir()), [])

    def test_parent_replaced_after_mkdir_is_not_followed(self):
        with tempfile.TemporaryDirectory(prefix="sn-parent-race-") as tmp:
            base = Path(tmp).resolve()
            root, outside = base / "root", base / "outside"
            root.mkdir(); outside.mkdir()
            original_mkdir = os.mkdir
            def swap(path, *args, **kwargs):
                result = original_mkdir(path, *args, **kwargs)
                if str(path) == "child":
                    (root / "child").rmdir()
                    (root / "child").symlink_to(outside, target_is_directory=True)
                return result
            with patch.object(safety.os, "mkdir", side_effect=swap):
                with self.assertRaises((OSError, ValueError)):
                    with safety.atomic_output(root, root / "child/file") as output:
                        output.write(b"must stay confined")
            self.assertEqual(list(outside.iterdir()), [])

    def check_links(self, links):
        with tempfile.TemporaryDirectory(prefix="sn-link-chain-") as tmp:
            archive = Path(tmp) / "fixture.tar"
            with tarfile.open(archive, "w") as package:
                for name, target in links:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE; member.linkname = target
                    package.addfile(member)
            return safety.archive_violation(archive)

    def test_chained_links_are_resolved_before_dotdot(self):
        violation = self.check_links((("toy/a/b", ".."), ("toy/c", "a/b/../.."), ("toy/e", "c/..")))
        self.assertIsNotNone(violation)

    def test_contained_framework_links_are_allowed_and_cycles_refused(self):
        self.assertIsNone(self.check_links((("toy/Versions/Current", "A"),
                                            ("toy/Resources", "Versions/Current/Resources"))))
        self.assertIsNotNone(self.check_links((("toy/a", "b"), ("toy/b", "a"))))
