# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Reports cover every native route using the shared labels, with synthetic local data."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import api

spec = importlib.util.spec_from_file_location("library_report", ROOT / "lib/library-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class NativeReportTests(unittest.TestCase):
    def test_non_native_route_notes_reach_text_and_html(self):
        import collections
        import html
        expected = {
            "steam": "历史发行版未必兼容当前系统",
            "renpy": "自动安装尚未接通",
            "fna": "还没接通",
            "maybe": "还没有对应的原生运行时",
            "vn": "要看文件清单才能定",
            "wine": "工作副本",
            "unknown": "可玩性与性能待验证",
            "refuse": "风险",
        }
        rows = [dict(appid=str(i + 1), name="fixture " + route, route=route,
                     engine="synthetic", confidence="待核实", size=10_000_000)
                for i, route in enumerate(expected)]
        output = io.StringIO()
        with patch.object(report, "load", return_value=rows), patch.object(sys, "argv", ["library-report"]), contextlib.redirect_stdout(output):
            report.main()
        by = collections.defaultdict(list)
        for row in rows:
            by[row["route"]].append(row)
        with tempfile.TemporaryDirectory(prefix="sn-report-notes-") as tmp:
            page = Path(tmp) / "report.html"
            report.write_html(rows, by, page)
            text = page.read_text()
        for route, note in expected.items():
            with self.subTest(route=route):
                self.assertIn(note, output.getvalue())
                self.assertIn(html.escape(note), text)
                self.assertEqual(report.BLURB[route][0], api.ROUTE_LABEL[route])
        self.assertNotIn("不改游戏任何一个字节", output.getvalue())

    def rows(self):
        return [dict(appid=str(index + 1), name="fixture " + route, route=route,
                     engine="synthetic", confidence="确证", size=10_000_000)
                for index, route in enumerate(sorted(api.NATIVE_ROUTES))]

    def test_text_report_covers_every_native_route_without_crashing(self):
        output = io.StringIO()
        with patch.object(report, "load", return_value=self.rows()), patch.object(sys, "argv", ["library-report"]), contextlib.redirect_stdout(output):
            report.main()
        for route in api.NATIVE_ROUTES:
            self.assertIn(api.ROUTE_LABEL[route], output.getvalue())

    def test_html_report_covers_every_native_route_without_crashing(self):
        import collections
        rows = self.rows()
        by = collections.defaultdict(list)
        for row in rows:
            by[row["route"]].append(row)
        with tempfile.TemporaryDirectory(prefix="sn-report-") as tmp:
            output = Path(tmp) / "report.html"
            report.write_html(rows, by, output)
            text = output.read_text()
        import html
        for route in api.NATIVE_ROUTES:
            self.assertIn(html.escape(api.ROUTE_LABEL[route]), text)
