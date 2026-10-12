# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public ledger metadata cannot reintroduce named IDs or private process references."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_compat_page", ROOT / "tools/compat-page.py")
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)
export_spec = importlib.util.spec_from_file_location("metadata_export", ROOT / "tools/release-check.py")
compat_export = importlib.util.module_from_spec(export_spec)
export_spec.loader.exec_module(compat_export)


class PublicMetadataTests(unittest.TestCase):
    def test_named_ids_are_refused_even_when_the_summary_matches(self):
        entries = json.loads((ROOT / "docs/compatibility.json").read_text())["entries"]
        records = json.loads((ROOT / "docs/compatibility-evidence.json").read_text())
        tiers = json.loads((ROOT / "lib/route-tiers.json").read_text())
        old = entries[0]["id"]
        entries[0]["id"] = entries[0]["evidence_ref"] = "a-withheld-title"
        next(r for r in records["records"] if r["id"] == old)["id"] = "a-withheld-title"
        with self.assertRaises(SystemExit) as error:
            compat.validate(entries, tiers, records)
        self.assertEqual(error.exception.code, 1)

    def test_public_data_ids_and_process_references_are_clean(self):
        data = json.loads((ROOT / "docs/compatibility.json").read_text())
        for entry in data["entries"]:
            self.assertRegex(entry["id"], r"^entry-[0-9]+$")
        for name in ("docs/compatibility.json", "lib/route-tiers.json"):
            text = (ROOT / name).read_text()
            files, prefixes = compat_export.exclusions(ROOT)
            for term in sorted(files) + list(prefixes) + ["hand" + "off", "maintainer"]:
                with self.subTest(file=name, term=term):
                    self.assertNotIn(term, text)
