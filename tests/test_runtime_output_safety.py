# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Every installer output must be confined before the first write."""
import json
import unittest

import test_runtime_bundle as fixtures


class RuntimeOutputSafetyTests(unittest.TestCase):
    setUp = fixtures.RuntimeBundleTests.setUp
    run_tool = fixtures.RuntimeBundleTests.run_tool
    pack = fixtures.RuntimeBundleTests.pack
    trust_manifest = fixtures.RuntimeBundleTests.trust_manifest

    def snapshot(self, directory):
        return {p.relative_to(directory).as_posix(): ("directory" if p.is_dir() else p.read_bytes())
                for p in directory.rglob("*")}

    def test_all_output_symlinks_are_refused_without_external_changes(self):
        self.pack()
        self.trust_manifest()
        for relative in ("build", "third-party-licenses", "third-party-licenses/toy",
                         "installed-runtimes.json"):
            with self.subTest(output=relative):
                dest = self.base / ("dest-" + relative.replace("/", "-"))
                outside = self.base / ("outside-" + relative.replace("/", "-"))
                dest.mkdir()
                link = dest / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith(".json"):
                    outside.write_text("{}")
                    before = outside.read_bytes()
                else:
                    outside.mkdir()
                    (outside / "keep").write_text("do not change")
                    before = self.snapshot(outside)
                link.symlink_to(outside)
                result = self.run_tool("install", "--from", str(self.out), "--only", "toy",
                                       "--replace", runtimes=dest)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("符号链接", result.stdout + result.stderr)
                self.assertTrue(link.is_symlink())
                after = outside.read_bytes() if outside.is_file() else self.snapshot(outside)
                self.assertEqual(after, before)
                self.assertFalse((dest / "toy").exists())
                self.assertEqual(list(dest.glob(".bundle-stage.*")), [])

    def test_in_root_parent_symlink_is_also_refused_before_writes(self):
        self.pack()
        self.trust_manifest()
        dest = self.base / "in-root-link"
        cache = dest / "elsewhere"
        cache.mkdir(parents=True)
        (dest / "build").symlink_to(cache)
        result = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(cache.iterdir()), [])

    def test_success_record_is_regular_and_leaves_no_temporary_files(self):
        self.pack()
        self.trust_manifest()
        dest = self.base / "record-dest"
        result = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = dest / "installed-runtimes.json"
        self.assertFalse(record.is_symlink())
        self.assertEqual(set(json.loads(record.read_text())), {"toy"})
        self.assertEqual(list(dest.glob(".bundle-part-*")), [])
