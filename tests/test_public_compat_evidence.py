# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public compatibility claims require public summaries and explicit missing context."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PublicEvidenceTests(unittest.TestCase):
    def test_links_are_labeled_as_summary_records(self):
        self.assertIn("Summary record", (ROOT / "docs/compatibility.md").read_text())
        self.assertIn("摘要记录", (ROOT / "docs/compatibility.zh.md").read_text())

    def test_per_game_context_can_be_recorded_and_mismatches_are_refused(self):
        spec = importlib.util.spec_from_file_location("per_game_compat", ROOT / "tools/compat-page.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        data = self.data()
        evidence = json.loads((ROOT / "docs/compatibility-evidence.json").read_text())
        tiers = json.loads((ROOT / "lib/route-tiers.json").read_text())
        entry = data["entries"][0]
        record = next(r for r in evidence["records"] if r["id"] == entry["id"])
        record["context"] = {**evidence["context"], "game_build": "fixture build", "runtime_version": "fixture runtime",
                             "macos_version": "fixture OS", "tool_revision": "fixture revision",
                             "original_record": "fixture record"}
        entry.update(record["context"])
        tool.validate(data["entries"], tiers, evidence)
        pages = tool.render(data, tiers)
        self.assertIn("null means unknown", pages["compatibility.md"])
        entry["runtime_version"] = "different runtime"
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            tool.validate(data["entries"], tiers, evidence)
        entry["runtime_version"] = record["context"]["runtime_version"]
        del entry["game_build"]
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            tool.validate(data["entries"], tiers, evidence)

    def data(self):
        return json.loads((ROOT / "docs/compatibility.json").read_text())

    def test_public_records_and_context_exist_for_every_entry(self):
        evidence = ROOT / "docs/compatibility-evidence.json"
        self.assertTrue(evidence.is_file(), "Public claims need a public source")
        records = {r["id"]: r for r in json.loads(evidence.read_text())["records"]}
        for entry in self.data()["entries"]:
            self.assertIn(entry["evidence_ref"], records)
            for field in ("game_build", "runtime_version", "macos_version", "tool_revision", "original_record"):
                self.assertIn(field, entry)

    def test_generator_refuses_a_changed_claim_or_missing_record_or_dropped_entry(self):
        base = self.data()
        changed = copy.deepcopy(base)
        changed["entries"][0]["chain"] = [1]
        missing = copy.deepcopy(base)
        missing["entries"][0]["evidence_ref"] = "missing-record"
        dropped = copy.deepcopy(base)
        dropped["entries"].pop()
        for index, data in enumerate((changed, missing, dropped)):
            with self.subTest(case=index), tempfile.TemporaryDirectory(prefix="sn-public-evidence-") as tmp:
                source = Path(tmp) / "input.json"
                source.write_text(json.dumps(data))
                out = Path(tmp) / "out"
                out.mkdir()
                result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/compat-page.py"),
                                         "--json", str(source), "--out", str(out)],
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(out.iterdir()), [], "Rejected claims must not produce pages")
