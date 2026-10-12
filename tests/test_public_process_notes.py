# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Public installer sources omit internal task and shift labels."""
from pathlib import Path
import importlib.util
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("process_export", ROOT / "tools/release-check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def private_labels():
    return re.compile("|".join((
        "ta" + "sks/", "发布" + "轮", "夜" + "班", "hand" + "off",
        "评审" + r"\s*2026", "owner" + r"\s*(?:2026|定)",
        "this " + "round", "previous " + "ledger", "本" + "轮", "旧" + "台账",
        r"Round" + r"\s*[0-9]", "/Cla" + "ude/Pro" + "jects/",
        r"round" + r"\s+[AB][0-9]\b", "评审" + r"第\s*[0-9]",
        r"\b[ABCD]" + r"[0-9]\s*(?:要求|翻转|：|（|\()",
    )), re.IGNORECASE)


class ProcessNoteTests(unittest.TestCase):
    def test_bridge_off_does_not_promise_the_game_will_run(self):
        text = (ROOT / "lib/wine-wrap.sh").read_text()
        self.assertNotIn("游戏照常跑", text)

    def test_runtime_and_launcher_sources_do_not_contain_private_process_labels(self):
        pattern = private_labels()
        for name in ("tools/runtime-bundle.py", "bin/install.sh", "lib/wine-wrap.sh", "lib/runtimes.json"):
            with self.subTest(file=name):
                self.assertIsNone(pattern.search((ROOT / name).read_text()))

    def test_every_exported_file_omits_private_process_labels(self):
        entries, _ = release.snapshot(ROOT)
        files, _ = release.exclusions(ROOT)
        private_documents = {name for name in files if name.endswith(".md")}
        found = [name for name, (content, _) in entries.items()
                 if private_labels().search(name + "\n" + content.decode("utf8"))
                 or any(term in content.decode("utf8") for term in private_documents)]
        self.assertEqual(found, [], "Private process labels in exports: " + ", ".join(found))
