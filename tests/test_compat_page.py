# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public ledger claims must match public summaries and the committed generated pages."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/compat-page.py"
JSON = ROOT / "docs/compatibility.json"



class CompatLedgerTests(unittest.TestCase):
    def entries(self):
        return json.loads(JSON.read_text(encoding="utf8"))["entries"]

    def run_tool(self, json_path, out_dir):
        return subprocess.run([sys.executable, "-B", str(TOOL), "--json", str(json_path), "--out", str(out_dir)],
                              capture_output=True, text=True, timeout=60)

    def test_committed_pages_match_the_tool_output(self):
        with tempfile.TemporaryDirectory(prefix="sn-compat-") as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            result = self.run_tool(JSON, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("compatibility.md", "compatibility.zh.md"):
                self.assertEqual((out / name).read_text(encoding="utf8"),
                                 (ROOT / "docs" / name).read_text(encoding="utf8"),
                                 f"{name} 与工具输出不一致；重跑 tools/compat-page.py 后提交")

    def test_every_chain_or_number_claim_traces_to_public_summary(self):
        source = json.loads((ROOT / "docs/compatibility-evidence.json").read_text())
        by_id = {e["id"]: e for e in self.entries()}
        records = {r["id"]: r for r in source["records"]}
        self.assertEqual(set(by_id), set(records))
        covered = set()
        for entry_id, record in records.items():
            for field, expected in record["claims"].items():
                self.assertEqual(by_id[entry_id].get(field), expected)
                covered.add((entry_id, field))
        for entry in self.entries():
            for field in ("chain", "fps", "frame_p99_ms"):
                if entry.get(field) is not None:
                    self.assertIn((entry["id"], field), covered)

    def test_every_published_claim_traces_to_private_quotation(self):
        inventory = ROOT / "docs/internal/compatibility-claims.json"
        if not inventory.is_file():
            self.skipTest("Private quoted-claim inventory is absent from the public export")
        source = json.loads(inventory.read_text())
        original = ROOT / source["source"]
        if not original.is_file():
            self.skipTest("Private quotation source is absent from this checkout")
        lines = original.read_text().splitlines()
        by_id = {e["id"]: e for e in self.entries()}
        covered = set()
        for claim in source["claims"]:
            entry_id, field = claim["id"], claim["field"]
            self.assertTrue(claim["quote"])
            self.assertTrue(any(claim["quote"] in line for line in lines),
                            f"Quoted source line is missing for {entry_id}.{field}")
            self.assertIn(entry_id, by_id)
            self.assertEqual(by_id[entry_id].get(field), claim["expected"],
                             f"{entry_id}.{field} differs from its quoted claim")
            covered.add((entry_id, field))
        for entry in self.entries():
            for field in ("chain", "fps", "frame_p99_ms"):
                if entry.get(field) is not None:
                    self.assertIn((entry["id"], field), covered,
                                  "Published claim has no quoted private source")

    def test_only_named_titles_carry_a_name_and_pages_say_not_named(self):
        named = {"A Space for the Unbound", "Stories from Sol: The Gun-Dog", "Koi Farm", "Star Sky 3"}
        for e in self.entries():
            if e.get("name"):
                self.assertIn(e["name"], named)
        unnamed = [e for e in self.entries() if not e.get("name")]
        self.assertTrue(unnamed)
        for name in ("compatibility.md", "compatibility.zh.md"):
            text = (ROOT / "docs" / name).read_text(encoding="utf8")
            self.assertIn("one title, not named" if "zh" not in name else "一款，未点名", text)

    def test_tool_refuses_entries_without_date_or_evidence_or_with_bad_names(self):
        base = json.loads(JSON.read_text(encoding="utf8"))
        cases = []
        no_date = copy.deepcopy(base); del no_date["entries"][0]["date"]; cases.append(no_date)
        no_evidence = copy.deepcopy(base); del no_evidence["entries"][0]["evidence"]; cases.append(no_evidence)
        bad_name = copy.deepcopy(base); bad_name["entries"][0]["name"] = "Someone Else's Game"; cases.append(bad_name)
        bad_tier = copy.deepcopy(base)
        wine = next(e for e in bad_tier["entries"] if e["route"] == "wine")
        want = json.loads((ROOT / "lib/route-tiers.json").read_text(encoding="utf8"))["routes"]["wine"]["tier"]
        wine["tier"] = "open" if want == "experimental" else "experimental"   # 与 route-tiers 相反
        cases.append(bad_tier)
        for i, data in enumerate(cases):
            with tempfile.TemporaryDirectory(prefix="sn-compat-bad-") as tmp:
                bad = Path(tmp) / "compatibility.json"
                bad.write_text(json.dumps(data, ensure_ascii=False))
                out = Path(tmp) / "out"
                out.mkdir()
                result = self.run_tool(bad, out)
                self.assertNotEqual(result.returncode, 0, f"case {i} 应被拒绝")
                self.assertEqual(list(out.iterdir()), [], "拒绝时一个页面都不能写")


if __name__ == "__main__":
    unittest.main()
