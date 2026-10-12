# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Independent private quotations must catch changes to both public copies."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("claims_tests", ROOT / "tests/test_compat_page.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class PrivateClaimsGuardTests(unittest.TestCase):
    def test_changing_both_public_copies_cannot_bypass_private_provenance(self):
        inventory = ROOT / "docs/internal/compatibility-claims.json"
        if not inventory.is_file():
            self.skipTest("Private quoted-claim inventory is absent from the public export")
        private = json.loads(inventory.read_text())
        original = ROOT / private["source"]
        if not original.is_file():
            self.skipTest("Private quotation source is absent from this checkout")
        data = json.loads((ROOT / "docs/compatibility.json").read_text())
        evidence = json.loads((ROOT / "docs/compatibility-evidence.json").read_text())
        measured = next(e for e in data["entries"] if "fps" in e)
        measured["fps"] += 1
        next(r for r in evidence["records"] if r["id"] == measured["id"])["claims"]["fps"] = measured["fps"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            (docs / "internal").mkdir(parents=True)
            (docs / "compatibility.json").write_text(json.dumps(data))
            (docs / "compatibility-evidence.json").write_text(json.dumps(evidence))
            (docs / "internal/compatibility-claims.json").write_text(inventory.read_text())
            (root / private["source"]).write_text(original.read_text())
            public = "test_every_chain_or_number_claim_traces_to_public_summary"
            private_check = "test_every_published_claim_traces_to_private_quotation"
            suite = unittest.TestSuite([checks.CompatLedgerTests(public)])
            if hasattr(checks.CompatLedgerTests, private_check):
                suite.addTest(checks.CompatLedgerTests(private_check))
            result = unittest.TestResult()
            with mock.patch.object(checks, "ROOT", root), mock.patch.object(checks, "JSON", docs / "compatibility.json"):
                suite.run(result)
            self.assertEqual(result.testsRun, 2, "Private provenance check must be present")
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0][0]._testMethodName, private_check)
