# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Preview opening follows measured results, limits, usage and planned comparison.
Retain route tiers, historical caveats and the existing Chinese section structure.
"""
import json
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ZH_HEADING = {"已验证什么": "What is verified", "工作原理": "How it works",
              "它会在你的机器与账号上碰什么": "What it touches on your machine and your account",
              "安装、更新、清理": "Install, update, clean up", "参与贡献": "Contribute",
              "许可证与第三方运行时": "Licence and third-party runtimes"}
EXPECTED_SECTIONS = ["What is verified", "How it works", "What it touches on your machine and your account",
                     "Install, update, clean up", "Contribute", "Licence and third-party runtimes"]

# Historical caveats retain the same normalized comparison; private link targets are omitted.
OLD_CAVEATS = [
    "Engine detection is not a promise that a game will run; the route ledger separates real-game tests from sample-only and unimplemented routes.",
    "Experimental routes still install, and the app and the installer label them; there is no promise that they play through.",
    "A promised route that has not passed the full check on two real games by 2026-09-30 moves to experimental too.",
    "On the Wine route, a game whose main executable carries the Steamworks DRM wrapper (a `.bind` PE section) is refused before packaging with an explanation: it needs a running Windows Steam client inside the same prefix, and this tool does not bypass DRM.",
    "Native routes do not run that executable; they print a notice and continue (details in [docs/LEGAL.md](docs/LEGAL.md)).",
    "They must not modify or bypass anti-cheat or DRM, or distribute modified games.",
    "These [working-copy requirements](docs/架构.md#游戏工作副本与恢复) do not mean every existing route has passed restore testing.",
    "Multiplayer, tickets, and item/economy surfaces are never forwarded.",
    "You do not need to choose these runtime flags.",
    "It does not check Steam login, the debugging endpoint or risk data; a ready runtime does not mean game downloads are ready.",
    "A clean Mac completing the whole setup without assistance has **not** been verified.",
    "The default scan reuses valid classification caches and reads local file manifests when needed; it does not download game content or fetch missing manifests.",
    "Engine identification does not establish playability.",
    "Installation, moving fish, normal exit and restoring that move in a new process have been tested; the full tutorial, audible sound, extended play and low-end hardware performance remain unverified.",
    "Other Electron games do not inherit this compatibility.",
    "One early story save has been restored in a new process; full-story coverage and other Ren'Py games remain unverified.",
    "Generated apps do not update themselves when this tool is upgraded; this is the upgrade path for them.",
    "A clean source snapshot does not make older commits safe to publish.",
]

KEYWORDS = re.compile(r"\b(not|never|unverified|experimental)\b", re.IGNORECASE)


def normalize(text):
    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1", text)   # 链接折成显示文本
    return re.sub(r"\s+", " ", text).strip()


class ReadmeStructureTests(unittest.TestCase):
    def readme(self, name):
        return (ROOT / name).read_text(encoding="utf8")

    def headings(self, name):
        return [line[3:].strip() for line in self.readme(name).splitlines() if line.startswith("## ")]

    def test_first_screen_is_the_approved_one(self):
        text = self.readme("README.md")
        opening = text.splitlines()[2]
        self.assertIn("The Unity route", opening)
        self.assertIn("without Wine", opening)
        for line in ("# dryport", "Unity D3D11 shaders retargeted to Metal",
                     "A Space for the Unbound", "60.0 fps, frame p99 19.44 ms",
                     "original records are unpublished", "Developer preview",
                     "Apple silicon Macs only", "Unity 5.x shader translation is not implemented",
                     "bash bin/install.sh --check", "./bin/dryport survey",
                     "./bin/dryport install <appid>", "## Comparison (coming)",
                     "CrossOver and Highball", "No comparison numbers are available yet"):
            self.assertIn(line, text)
        self.assertLess(text.index("60.0 fps"), text.index("## Limits"))
        self.assertLess(text.index("## Limits"), text.index("## How to try it"))
        self.assertLess(text.index("## How to try it"), text.index("## What comes next"))
        self.assertRegex(text, r"git clone https://github.com/[A-Za-z0-9-]+/dryport && cd dryport")
        self.assertNotIn("![", text, "No image or badge without a real recording")
        spec = importlib.util.spec_from_file_location("readme_export", ROOT / "tools/release-check.py")
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        files, _ = release.exclusions(ROOT)
        for name in files:
            self.assertNotIn("](" + name + ")", text)

    def test_readme_results_match_the_route_ledger(self):
        text = self.readme("README.md")
        entries = json.loads((ROOT / "docs/compatibility.json").read_text())["entries"]
        for entry in entries:
            rows = [line for line in text.splitlines() if line.startswith("| ") and entry["id"] in line]
            self.assertEqual(len(rows), 1, entry["id"])
            row = rows[0]
            self.assertIn("1 to 5" if entry["chain"] == [1, 2, 3, 4, 5] else "1 only", row)
            if entry.get("name"):
                self.assertIn(entry["name"], row)
            else:
                self.assertIn("Anonymous " + entry["id"], row)
            if "fps" in entry:
                self.assertIn(str(entry["fps"]) + " fps", row)
                self.assertIn("p99 " + str(entry["frame_p99_ms"]) + " ms", row)
            else:
                self.assertIn("Not recorded", row)

    def test_tier_cells_follow_route_tiers(self):
        tiers = json.loads((ROOT / "lib/route-tiers.json").read_text())["routes"]
        cases = [("README.md", {"RPG Maker XP": "mkxp", "RPG Maker MV": "nwjs", "Unity (Mono": "unity",
                                "Ren'Py (pinned": None, "Other eligible Windows": "wine"},
                  {"open": "promised", "experimental": "experimental"}),
                 ("README.zh.md", {"RPG Maker XP": "mkxp", "RPG Maker MV": "nwjs", "Unity（Mono": "unity",
                                   "Ren'Py（Gun-Dog": None, "其他符合条件": "wine"},
                  {"open": "承诺", "experimental": "实验"})]
        for name, rows, labels in cases:
            table = {}
            for line in self.readme(name).splitlines():
                if line.startswith("| ") and line.count("|") == 4 and "---" not in line and "Tier" not in line and "档位" not in line:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    table[normalize(cells[0])] = cells[2]
            for prefix, route in rows.items():
                row = next(k for k in table if k.startswith(prefix))
                want = "experimental" if route is None else tiers[route]["tier"]
                self.assertEqual(table[row], labels[want], f"{name}: {prefix} 行的档位格")

    def test_preview_sections_and_retained_chinese_sections(self):
        self.assertEqual(self.headings("README.md"), [
            "Limits", "How to try it", "How it works", "Comparison (coming)",
            "What comes next", "Contribute and license"])
        zh = [ZH_HEADING.get(h, h) for h in self.headings("README.zh.md")]
        self.assertEqual(zh, EXPECTED_SECTIONS)

    def test_old_readme_caveats_survive_the_restructure(self):
        corpus = normalize("\n".join(self.readme(n) for n in
                                     ("README.md", "docs/how-it-works.md", "docs/safety.md", "docs/usage.md")))
        for sentence in OLD_CAVEATS:
            self.assertTrue(KEYWORDS.search(sentence), f"清单里混进了不含告诫词的句子：{sentence[:50]}")
            self.assertIn(normalize(sentence), corpus, f"旧告诫句丢失：{sentence[:60]}…")

    def test_readmes_link_the_three_documents_and_the_ledger(self):
        for name, docs in (("README.md", ("docs/how-it-works.md", "docs/safety.md", "docs/usage.md",
                                          "docs/compatibility.md", "CONTRIBUTING.md", "docs/LEGAL.md")),
                           ("README.zh.md", ("docs/how-it-works.zh.md", "docs/safety.zh.md", "docs/usage.zh.md",
                                             "docs/compatibility.zh.md", "CONTRIBUTING.md", "docs/LEGAL.md"))):
            text = self.readme(name)
            for doc in docs:
                self.assertIn(f"]({doc})", text, f"{name} 缺链接 {doc}")
                self.assertTrue((ROOT / doc).is_file(), f"{doc} 不存在")


if __name__ == "__main__":
    unittest.main()
