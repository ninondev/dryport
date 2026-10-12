# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Check exported bytes against an optional private identifier inventory."""
import importlib.util
import json
import os
import re
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_inventory", ROOT / "tools/release-check.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def private_inventory():
    configured = os.environ.get("DRYPORT_PRIVATE_INVENTORY")
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError("Configured private identifier inventory is unavailable")
        return path
    return ROOT / "docs/internal/withheld-titles.txt"


class WithheldExportTests(unittest.TestCase):
    def test_explicit_missing_private_inventory_fails_instead_of_skipping(self):
        with patch.dict(os.environ, {"DRYPORT_PRIVATE_INVENTORY": str(ROOT / "missing-private-input")}):
            with self.assertRaises(FileNotFoundError):
                private_inventory()

    def test_explicit_private_inventory_is_selected_without_copying(self):
        with patch.dict(os.environ, {"DRYPORT_PRIVATE_INVENTORY": str(ROOT / "LICENSE")}):
            self.assertEqual(private_inventory(), ROOT / "LICENSE")

    def test_every_exported_file_withholds_private_identifiers(self):
        path = private_inventory()
        if not path.is_file():
            self.skipTest("Private identifier inventory is absent from the public export")
        data = json.loads(path.read_text())
        terms = data["additional_terms"] + data["additional_executables"] + [
            term for entry in data["entries"] for term in entry["terms"] + entry.get("executables", [])]
        self.assertEqual(len(data["entries"]), 9)
        self.assertTrue(terms)
        entries, _ = export.snapshot(ROOT)
        found = []
        for name, (content, _) in entries.items():
            text = (name + "\n" + content.decode("utf8")).casefold()
            for title in ("A Space for the Unbound", "Stories from Sol: The Gun-Dog", "Koi Farm", "Star Sky 3"):
                text = text.replace(title.casefold(), "")
            if (any(term.casefold() in text for term in terms)
                    or any(re.search(r"\b" + re.escape(term) + r"\b", content.decode("utf8"))
                           for term in data.get("case_sensitive_titles", []))):
                found.append(name)
        self.assertEqual(found, [], "Exported files contain private identifiers: " + ", ".join(found))

    def test_executable_identifiers_are_checked_in_exported_bytes(self):
        path = private_inventory()
        if not path.is_file():
            self.skipTest("Private identifier inventory is absent from the public export")
        data = json.loads(path.read_text())
        executable = next(term for entry in data["entries"] for term in entry.get("executables", []))
        with patch.object(export, "snapshot", return_value=({"fixture.txt": (executable.upper().encode(), 0)}, [])):
            with self.assertRaisesRegex(AssertionError, "fixture.txt"):
                self.test_every_exported_file_withholds_private_identifiers()

    def test_cli_does_not_name_the_private_inventory(self):
        self.assertNotIn("withheld-" + "titles.txt", (ROOT / "bin/dryport").read_text())

    def test_anonymous_notes_do_not_publish_distinctive_playthrough_details(self):
        entries = json.loads((ROOT / "docs/compatibility.json").read_text())["entries"]
        for entry in entries:
            if not entry.get("name"):
                with self.subTest(entry=entry["id"]):
                    self.assertNotRegex(entry["notes"], r"prologue|card shuffle|19:26|19:35|300 ms|distance 0\.0|across 3 runs")
