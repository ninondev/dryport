# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Original records are unpublished; API availability is a separate state."""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_exports", ROOT / "tools/release-check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def misleading_record_wording():
    records = r"(?:original|internal|private)\s+records?"
    missing = "unavail" + r"able|not\s+rerun"
    return re.compile(records + r"[^\n.]{0,60}(?:" + missing + ")|(?:" + missing + r")\s+" + records,
                      re.IGNORECASE)


class UnpublishedRecordTests(unittest.TestCase):
    def test_all_exports_describe_original_records_as_unpublished(self):
        entries, _ = release.snapshot(ROOT)
        pattern = misleading_record_wording()
        bad = [name for name, (data, _) in entries.items()
               if not name.endswith(tuple(release.DOC_IMAGES)) and pattern.search(data.decode("utf8"))]
        self.assertEqual(bad, [])

    def test_wording_guard_distinguishes_records_from_api_status(self):
        pattern = misleading_record_wording()
        for text in ("Original " + "records are unavail" + "able", "unavail" + "able internal " + "records",
                     "Original " + "records were not " + "rerun"):
            self.assertIsNotNone(pattern.search(text))
        self.assertIsNone(pattern.search("API unavailable; original records are unpublished"))
