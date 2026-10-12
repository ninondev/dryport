# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""ingame-save 的纯逻辑与文件操作测试：不碰真游戏、不投键、不截图、不碰真机 ~/Games。

工具文件名带连字符，用 importlib 按路径加载；加载前把 SN_GAMES/SN_RUNTIMES 指到临时目录——
工具与 lib/paths、lib/evidence 在 import 时就算路径，任何一处模块级代码都绝不能摸到真数据。
SN_HOME 留真仓库：表文件 ingame-save-games.json 从那里读。
"""
import importlib.util
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]

_TMP = Path(tempfile.mkdtemp(prefix="sn-ingame-save-import-"))
os.environ["SN_GAMES"] = str(_TMP / "games")
os.environ["SN_RUNTIMES"] = str(_TMP / "runtimes")
os.environ["SN_HOME"] = str(ROOT)

sys.path.insert(0, str(ROOT / "lib"))
import evidence  # noqa: E402  # 纯当 import 安全的金丝雀：库的模块级代码摸了真数据，这里就会炸

_SPEC = importlib.util.spec_from_file_location("ingame_save", ROOT / "tools" / "ingame-save.py")
TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TOOL)

try:
    from PIL import Image
except ImportError:
    Image = None


class ParseKeysTests(unittest.TestCase):
    def test_bare_key_uses_default_hold(self):
        self.assertEqual(TOOL.parse_keys("return"),
                         [{"key": "return", "hold_ms": 80, "repeat": 1, "mods": []}])

    def test_hold_suffix(self):
        self.assertEqual(TOOL.parse_keys("z@300"),
                         [{"key": "z", "hold_ms": 300, "repeat": 1, "mods": []}])

    def test_repeat_suffix(self):
        self.assertEqual(TOOL.parse_keys("down*3"),
                         [{"key": "down", "hold_ms": 80, "repeat": 3, "mods": []}])

    def test_modifier_single_and_stacked(self):
        self.assertEqual(TOOL.parse_keys("cmd+q")[0]["mods"], ["cmd"])
        self.assertEqual(TOOL.parse_keys("cmd+q")[0]["key"], "q")
        self.assertEqual(TOOL.parse_keys("shift+ctrl+z")[0]["mods"], ["shift", "ctrl"])

    def test_wait_token(self):
        self.assertEqual(TOOL.parse_keys("wait:1.5"), [{"wait": 1.5}])

    def test_hold_and_repeat_combine(self):
        st = TOOL.parse_keys("z@300*2")[0]
        self.assertEqual((st["key"], st["hold_ms"], st["repeat"]), ("z", 300, 2))

    def test_sequence_keeps_order_and_forms(self):
        steps = TOOL.parse_keys("z, wait:0.5, cmd+q, return*2")
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["key"], "z")
        self.assertEqual(steps[1], {"wait": 0.5})
        self.assertEqual(steps[2]["mods"], ["cmd"])
        self.assertEqual(steps[3]["repeat"], 2)

    def test_bad_tokens_raise_and_name_the_token(self):
        for bad in ("f12", "wait:x", "wait:", "wait:-1", "cmd+f12", "z@", "z*0", "z@0", "soft+q", "key with space"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                TOOL.parse_keys(bad)
            self.assertIn(bad, str(cm.exception), f"报错要点名坏 token：{bad}")

    def test_empty_token_raises(self):
        with self.assertRaises(ValueError):
            TOOL.parse_keys("")


class ParseClickTests(unittest.TestCase):
    def test_click_defaults_to_80ms_hold(self):
        self.assertEqual(TOOL.parse_keys("click:120:40"),
                         [{"click": (120, 40), "hold_ms": 80}])

    def test_click_with_hold_suffix(self):
        self.assertEqual(TOOL.parse_keys("click:0:0@120"),
                         [{"click": (0, 0), "hold_ms": 120}])

    def test_click_mixes_with_keys_and_wait_in_order(self):
        steps = TOOL.parse_keys("wait:0.5, z, click:10:20@100")
        self.assertEqual(steps[0], {"wait": 0.5})
        self.assertEqual(steps[1]["key"], "z")
        self.assertEqual(steps[2], {"click": (10, 20), "hold_ms": 100})

    def test_bad_click_tokens_raise_and_name_the_token(self):
        for bad in ("click:", "click:1", "click:a:b", "click:1:2@0",
                    "click:1:2:x", "click:1:2:3", "click: 1:2"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                TOOL.parse_keys(bad)
            self.assertIn(bad, str(cm.exception), f"报错要点名坏 token：{bad}")


class ToScreenTests(unittest.TestCase):
    def test_window_origin_plus_in_window_offset(self):
        self.assertEqual(TOOL.to_screen({"X": 100, "Y": 50}, 10, 20), (110, 70))

    def test_window_offscreen_origin_adds_negatively(self):
        # 窗口左上角可以在屏幕外（bounds 为负），换算照常成立
        self.assertEqual(TOOL.to_screen({"X": -30, "Y": 0}, 10, 10), (-20, 10))

    def test_bounds_numbers_may_be_strings_or_floats(self):
        # window-list 落进 JSON 后数值形态不保证，int() 一律收
        self.assertEqual(TOOL.to_screen({"X": "100", "Y": 50.5}, 1, 1), (101, 51))

    def test_missing_or_unparseable_bounds_raise_valueerror(self):
        with self.assertRaises(ValueError):
            TOOL.to_screen(None, 1, 1)
        with self.assertRaises(ValueError):
            TOOL.to_screen({"Width": 100, "Height": 80}, 1, 1)


class SendStepsClickTests(unittest.TestCase):
    def send_click(self, windows, state):
        with patch.object(TOOL, "windows", return_value=windows), \
             patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.run.return_value = MagicMock(returncode=0)
            notes = TOOL.send_steps(4242, [{"click": (10, 20), "hold_ms": 80}], state)
            cmd = fake_sub.run.call_args.args[0]
        return notes, cmd

    def test_click_refreshes_bounds_by_window_id_before_sending(self):
        # 状态里记的 bounds 可能已过时（窗口被拖过），投之前要按 window_id 重取
        notes, cmd = self.send_click([{"id": 99, "bounds": {"X": 0, "Y": 0}},
                                      {"id": 7, "bounds": {"X": 100, "Y": 60}}],
                                     {"window_id": 7, "bounds": {"X": 1, "Y": 1}})
        self.assertEqual(cmd[cmd.index("--pid") + 1], "4242")
        self.assertEqual(cmd[cmd.index("--click") + 1], "110,80")
        self.assertEqual(cmd[cmd.index("--hold-ms") + 1], "80")
        self.assertEqual(len(notes), 1)

    def test_click_falls_back_to_recorded_bounds_when_window_list_misses(self):
        _, cmd = self.send_click([], {"window_id": 7, "bounds": {"X": 1, "Y": 2}})
        self.assertEqual(cmd[cmd.index("--click") + 1], "11,22")

    def test_click_without_any_bounds_refuses(self):
        with patch.object(TOOL, "windows", return_value=[]):
            with self.assertRaises(ValueError):
                TOOL.send_steps(4242, [{"click": (1, 1), "hold_ms": 80}], {"window_id": 7})
            with self.assertRaises(ValueError):
                TOOL.send_steps(4242, [{"click": (1, 1), "hold_ms": 80}], None)

    def test_click_keysend_failure_raises_keysenderror(self):
        with patch.object(TOOL, "windows", return_value=[]), \
             patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.run.return_value = MagicMock(returncode=1, stderr="boom")
            with self.assertRaises(TOOL.KeysendError):
                TOOL.send_steps(4242, [{"click": (1, 1), "hold_ms": 80}],
                                {"window_id": 7, "bounds": {"X": 0, "Y": 0}})


@unittest.skipUnless(Image, "没装 Pillow")
class ThumbDistTests(unittest.TestCase):
    def make(self, color):
        tmp = tempfile.TemporaryDirectory(prefix="sn-thumb-")
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "i.png"
        Image.new("L", (256, 144), color).save(p)
        return p

    def test_identical_images_give_zero(self):
        self.assertEqual(TOOL.thumb_dist(self.make(30), self.make(30)), 0.0)

    def test_black_vs_white_gives_255(self):
        self.assertEqual(TOOL.thumb_dist(self.make(0), self.make(255)), 255.0)


class LoadLatestVerdictTests(unittest.TestCase):
    def test_reading_the_second_save_passes_both_checks(self):
        status, detail = TOOL.load_latest_verdict(5, 20, 30, 40)
        self.assertEqual(status, "pass")
        self.assertIn("强杀前那份", detail)

    def test_reading_the_first_save_fails_even_close_to_a_saved_screen(self):
        # 只比标题近不够：离第一份更近说明读回的是旧档，不是强杀前那份
        status, _ = TOOL.load_latest_verdict(20, 5, 30, 40)
        self.assertEqual(status, "fail")

    def test_still_on_title_fails(self):
        status, _ = TOOL.load_latest_verdict(30, 40, 10, 40)
        self.assertEqual(status, "fail")

    def test_ties_do_not_pass(self):
        status, _ = TOOL.load_latest_verdict(30, 30, 30, 40)
        self.assertEqual(status, "fail")

    def test_lookalike_saves_are_unknown_with_the_note(self):
        status, detail = TOOL.load_latest_verdict(5, 20, 30, 7.9)
        self.assertEqual((status, detail), ("unknown", "两次存档画面几乎一样，分不出读到的是哪一次"))

    def test_threshold_boundary_is_strict(self):
        # d(一存,二存) 恰好 8 不算「几乎一样」，照常判
        status, _ = TOOL.load_latest_verdict(5, 20, 30, 8)
        self.assertEqual(status, "pass")


class ManifestTests(unittest.TestCase):
    def test_manifest_diff_sorts_into_new_changed_removed(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-manifest-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "a.txt").write_text("1")
        (root / "sub").mkdir()
        (root / "sub" / "b.txt").write_text("2")
        (root / "old.txt").write_text("3")
        before = TOOL.manifest(root)
        self.assertEqual(sorted(before), ["a.txt", "old.txt", "sub/b.txt"])
        (root / "a.txt").write_text("changed")
        (root / "old.txt").unlink()
        (root / "new.txt").write_text("4")
        added, changed, removed = TOOL.manifest_diff(before, TOOL.manifest(root))
        self.assertEqual((added, changed, removed), (["new.txt"], ["a.txt"], ["old.txt"]))

    def test_manifest_of_missing_dir_is_empty(self):
        self.assertEqual(TOOL.manifest(Path("/nonexistent/definitely-not-here")), {})


class SlotSplitTests(unittest.TestCase):
    BEFORE = {"Player.log": "h-log", "slot1.sav": "h-1", "settings.json": "h-set"}

    def test_untouched_old_saves_pass(self):
        after = {"Player.log": "h-log", "slot1.sav": "h-1", "settings.json": "h-set", "slot2.sav": "h-2"}
        touched, extra = TOOL.split_old_vs_slots(self.BEFORE, after, {"slot2.sav"})
        self.assertEqual((touched, extra), ([], []))

    def test_changed_preexisting_is_reported_separately_from_slots(self):
        after = {"Player.log": "h-log2", "slot1.sav": "h-1x", "settings.json": "h-set"}
        touched, extra = TOOL.split_old_vs_slots(self.BEFORE, after, {"slot1.sav"})
        self.assertEqual(touched, ["Player.log"])  # 槽外旧文件被改 → changed_preexisting → unknown
        self.assertEqual(extra, [])

    def test_removed_old_save_counts_as_changed(self):
        after = {"slot1.sav": "h-1", "settings.json": "h-set"}
        touched, _ = TOOL.split_old_vs_slots(self.BEFORE, after, set())
        self.assertEqual(touched, ["Player.log"])

    def test_slot_files_are_exempt(self):
        after = {"Player.log": "h-log", "slot1.sav": "h-1x", "settings.json": "h-set"}
        touched, _ = TOOL.split_old_vs_slots(self.BEFORE, after, {"slot1.sav"})
        self.assertEqual(touched, [])

    def test_new_non_slot_file_is_reported_as_extra(self):
        after = {"Player.log": "h-log", "slot1.sav": "h-1", "settings.json": "h-set", "mystery.bin": "h-m"}
        touched, extra = TOOL.split_old_vs_slots(self.BEFORE, after, set())
        self.assertEqual((touched, extra), ([], ["mystery.bin"]))


class RestoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-restore-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.evd = self.base / "evd"
        self.evd.mkdir()
        self.saves = self.base / "Library" / "saves"
        (self.saves).mkdir(parents=True)
        (self.saves / "slot1.sav").write_text("v1")
        (self.saves / "sub").mkdir()
        (self.saves / "sub" / "prefs.json").write_text("{}")
        self.backup = self.evd / "saves-before"
        shutil.copytree(self.saves, self.backup, symlinks=True)
        self.before = TOOL.manifest(self.saves)

    def dirty(self):
        (self.saves / "slot1.sav").write_text("v2")
        (self.saves / "new-slot.sav").write_text("fresh")

    def test_restore_puts_backup_back_and_archives_post_test_state(self):
        self.dirty()
        TOOL.restore_saves(self.saves, self.backup, self.evd, "120000", True)
        self.assertEqual(TOOL.manifest(self.saves), self.before)  # 放回的就是备份
        self.assertEqual((self.evd / "saves-after" / "slot1.sav").read_text(), "v2")  # 测后盘面进了证据目录
        self.assertTrue((self.evd / "saves-after" / "new-slot.sav").exists())
        self.assertEqual([p.name for p in self.base.glob("**/*.ingame-test-*")], [])  # 同卷 rename，无需旁置
        # 什么都没被删：备份、放回的存档、测后存档三处都在
        self.assertTrue(self.backup.is_dir() and self.saves.is_dir() and (self.evd / "saves-after").is_dir())

    def test_restore_across_volumes_moves_original_aside_instead_of_deleting(self):
        self.dirty()
        real_rename = os.rename

        def fake_rename(src, dst):
            if Path(dst).name == "saves-after":
                raise OSError(18, "模拟跨卷 EXDEV")
            return real_rename(src, dst)

        with patch("os.rename", fake_rename):
            TOOL.restore_saves(self.saves, self.backup, self.evd, "120000", True)
        aside = self.base / "Library" / "saves.ingame-test-120000"
        self.assertTrue(aside.is_dir(), "跨卷时原目录该整个旁置，不许删")
        self.assertEqual((aside / "slot1.sav").read_text(), "v2")
        self.assertEqual(TOOL.manifest(self.saves), self.before)
        self.assertTrue((self.evd / "saves-after" / "new-slot.sav").exists())

    def test_created_saves_are_only_archived_not_restored(self):
        TOOL.restore_saves(self.saves, self.backup, self.evd, "120000", False)
        self.assertTrue((self.evd / "saves-after" / "slot1.sav").exists())
        self.assertFalse(self.saves.exists(), "原来没有存档目录，不该凭空造一个回去")


class TableValidationTests(unittest.TestCase):
    def test_shipped_entries_only_missing_the_key_fields(self):
        if not (ROOT / "tools/ingame-save-games.json").is_file():
            self.skipTest("Internal game recipes are not part of the public source export")
        inventory = ROOT / "docs/internal/withheld-titles.txt"
        if not inventory.is_file():
            self.skipTest("Private recipe-key inventory is absent from the public source export")
        # 表是随实测逐步补的数据：只要求每条最多缺键序列两项，别的字段必须齐（补上键序列的条目不算失败）
        table = TOOL.load_table()
        required = json.loads(inventory.read_text())["required_recipe_keys"]
        for name in required:
            self.assertIn(name, table, f"{name} 应在表里")
        for name, entry in table.items():
            self.assertLessEqual(set(TOOL.entry_missing(entry)), {"save_keys", "load_keys"},
                                 f"{name} 只许缺键序列两个字段")
            self.assertIn(entry.get("click_mode") or "pid", TOOL.CLICK_MODES,
                          f"{name} 的 click_mode 不认识")

    def test_every_missing_field_is_named(self):
        self.assertEqual(TOOL.entry_missing({}), ["appid", "app", "saves", "save_keys", "load_keys"])

    def test_optional_fields_do_not_count_as_missing(self):
        full = {"appid": "1", "app": "X.app", "saves": "~/s", "save_keys": "z",
                "load_keys": "z", "advance_keys": None, "quit_keys": None}
        self.assertEqual(TOOL.entry_missing(full), [])


class KillGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-killguard-")
        self.addCleanup(tmp.cleanup)
        self.running = Path(tmp.name) / "running"
        self.running.mkdir(parents=True)

    def record(self, pgid=424242, name="ingamesave-x", started=None):
        (self.running / f"{pgid}.json").write_text(json.dumps(
            {"pid": pgid, "pgid": pgid, "guard_pid": pgid + 1, "name": name,
             "started": time.time() if started is None else started, "max_seconds": 600}))

    def test_refuses_own_process_group(self):
        with patch.object(TOOL, "RUNNING", self.running), self.assertRaises(TOOL.KillRefused) as cm:
            TOOL.resolve_kill_pgids({"label": "ingamesave-x", "started": time.time(), "pgid": os.getpgid(0)})
        self.assertIn("拒绝", str(cm.exception))

    def test_refuses_stale_record(self):
        """陈旧记录：上轮崩溃没删的守护记录，pgid 可能已被系统回收给无辜进程，按它杀就是乱杀。"""
        self.record(pgid=424242, started=time.time() - 3600)
        state = {"label": "ingamesave-x", "started": time.time() - 10, "pgid": 424242}
        with patch.object(TOOL, "RUNNING", self.running), self.assertRaises(TOOL.KillRefused):
            TOOL.resolve_kill_pgids(state)

    def test_refuses_when_pgid_missing_or_no_record(self):
        with self.assertRaises(TOOL.KillRefused):
            TOOL.resolve_kill_pgids({"label": "ingamesave-x", "started": time.time(), "pgid": None})
        with patch.object(TOOL, "RUNNING", self.running), self.assertRaises(TOOL.KillRefused):
            TOOL.resolve_kill_pgids({"label": "ingamesave-x", "started": time.time(), "pgid": 424242})

    def test_refuses_record_of_another_label(self):
        self.record(pgid=424242, name="someone-else", started=time.time())
        state = {"label": "ingamesave-x", "started": time.time() - 10, "pgid": 424242}
        with patch.object(TOOL, "RUNNING", self.running), self.assertRaises(TOOL.KillRefused):
            TOOL.resolve_kill_pgids(state)

    def test_accepts_fresh_matching_record_and_sends_sigkill(self):
        self.record(pgid=424242, started=time.time() - 5)
        state = {"label": "ingamesave-x", "started": time.time() - 10, "pgid": 424242}
        with patch.object(TOOL, "RUNNING", self.running), patch("os.killpg") as kp:
            self.assertEqual(TOOL.resolve_kill_pgids(state), [424242])
            TOOL.hard_kill_groups(state)
            kp.assert_called_once_with(424242, signal.SIGKILL)


class StatePgidsTests(unittest.TestCase):
    def test_new_state_keeps_outer_first_and_dedupes(self):
        self.assertEqual(TOOL.state_pgids({"pgid": 100, "pgids": [100, 200, "200", 0]}), [100, 200])

    def test_old_state_with_only_pgid_still_works(self):
        # 升级前 up 写的状态文件只有 pgid，down 得照样能收拾残局
        self.assertEqual(TOOL.state_pgids({"pgid": 100}), [100])
        self.assertEqual(TOOL.state_pgids({"pgids": [200], "pgid": 100}), [200, 100])
        self.assertEqual(TOOL.state_pgids({}), [])


class DescendantRecordsTests(unittest.TestCase):
    # 10=外层守护，100=启动壳（外层组），20=内层守护，200=游戏本体（内层组），50=别人家的
    TABLE = [(1, 0), (10, 1), (100, 10), (20, 100), (200, 20), (50, 1)]

    def test_nested_guards_are_descendants(self):
        # Wine 启动壳自起的第二层 run-guard（guard_pid 20）父链一路连到外层守护 10，是亲的
        recs = [{"name": "game", "guard_pid": 20}, {"name": "deeper", "guard_pid": 200}]
        self.assertEqual(TOOL.descendant_records(self.TABLE, 10, recs), recs)

    def test_unrelated_records_are_not_descendants(self):
        recs = [{"name": "other", "guard_pid": 50}]
        self.assertEqual(TOOL.descendant_records(self.TABLE, 10, recs), [])

    def test_stale_record_with_dead_guard_is_not_descendant(self):
        # guard_pid 不在进程表里 = 那层守护已死（上轮崩溃的陈账，pgid 可能已被回收），无从认亲
        recs = [{"name": "stale", "guard_pid": 999}]
        self.assertEqual(TOOL.descendant_records(self.TABLE, 10, recs), [])

    def test_outer_record_itself_is_not_a_descendant(self):
        # 外层记录的 guard_pid 就是守护 pid 本尊，不算「后代」，由调用方单加上
        recs = [{"name": "outer", "guard_pid": 10}]
        self.assertEqual(TOOL.descendant_records(self.TABLE, 10, recs), [])

    def test_unusable_records_are_skipped_not_fatal(self):
        recs = [{"name": "a", "guard_pid": None}, {"name": "b"}, {"name": "c", "guard_pid": 20}]
        self.assertEqual(TOOL.descendant_records(self.TABLE, 10, recs), [recs[2]])


class WindowOfGroupsTests(unittest.TestCase):
    def match(self, windows, pgids, pgid_of):
        def fake_getpgid(pid):
            if int(pid) not in pgid_of:
                raise ProcessLookupError(int(pid))
            return pgid_of[int(pid)]

        with patch.object(TOOL, "windows", return_value=windows), \
             patch("os.getpgid", fake_getpgid):
            return TOOL.window_of_groups(pgids)

    def test_window_in_any_recorded_group_matches(self):
        wins = [{"id": 1, "pid": 101}, {"id": 2, "pid": 202}]
        self.assertEqual(self.match(wins, [5], {101: 5, 202: 7}), wins[0])
        # Wine：窗口属主常常不在外层组里，内层组也要比对得上
        self.assertEqual(self.match(wins, [9, 7], {101: 5, 202: 7}), wins[1])

    def test_no_window_in_any_group_gives_none(self):
        self.assertIsNone(self.match([{"id": 1, "pid": 101}], [9, 10], {101: 5}))

    def test_window_whose_pid_is_gone_is_skipped(self):
        wins = [{"id": 1, "pid": 101}, {"id": 2, "pid": 202}]
        self.assertEqual(self.match(wins, [7], {202: 7}), wins[1])


class MultiGroupKillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-multikill-")
        self.addCleanup(tmp.cleanup)
        self.running = Path(tmp.name) / "running"
        self.running.mkdir(parents=True)

    def record(self, pgid, guard_pid, name="game", started=None):
        (self.running / f"{pgid}.json").write_text(json.dumps(
            {"pid": pgid, "pgid": pgid, "guard_pid": guard_pid, "name": name,
             "started": time.time() if started is None else started, "max_seconds": 600}))

    def state(self, outer=100, inner=200, started=None):
        return {"label": "ingamesave-x", "guard_pid": 10,
                "started": time.time() - 30 if started is None else started,
                "pgid": outer, "pgids": [outer, inner]}

    @staticmethod
    def table():
        # 内层守护（pid 20）由启动壳拉起，启动壳（pid 100，外层组）的父链连到外层守护 10
        return [(1, 0), (10, 1), (100, 10), (20, 100), (200, 20)]

    def test_force_kill_hits_inner_group_first_then_outer(self):
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table", return_value=self.table()), \
             patch("os.killpg") as kp:
            self.assertEqual(TOOL.hard_kill_groups(self.state()), [200, 100])
            self.assertEqual([c.args for c in kp.call_args_list],
                             [(200, signal.SIGKILL), (100, signal.SIGKILL)])

    def test_force_kill_step_reports_inner_first(self):
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table", return_value=self.table()), \
             patch("os.killpg"), \
             patch.object(TOOL, "group_gone", side_effect=[False, True]) as gg:
            ok, note = TOOL.force_kill_step(self.state())
        self.assertTrue(ok)
        self.assertIn("内层在前", note)
        self.assertEqual(gg.call_args_list[1].args[0], [200, 100])  # 等消失也按内层在前的次序

    def test_refuses_when_inner_group_is_the_harness_own(self):
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        st = self.state(inner=os.getpgid(0))
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table", return_value=self.table()), \
             patch("os.killpg") as kp:
            with self.assertRaises(TOOL.KillRefused):
                TOOL.hard_kill_groups(st)
        kp.assert_not_called()

    def test_refuses_stale_inner_record(self):
        # 内层记录比本会话旧：上轮崩溃的残留，pgid 可能已被系统回收，一组不过就一组都不杀
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20, started=time.time() - 3600)
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table", return_value=self.table()), \
             patch("os.killpg") as kp:
            with self.assertRaises(TOOL.KillRefused):
                TOOL.hard_kill_groups(self.state())
        kp.assert_not_called()

    def test_refuses_inner_record_that_no_longer_descends(self):
        # 记录虽是本会话的，但那层守护已死（认不了亲）——pgid 迟早被回收，杀不得
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table", return_value=[(1, 0), (20, 1)]), \
             patch("os.killpg") as kp:
            with self.assertRaises(TOOL.KillRefused):
                TOOL.hard_kill_groups(self.state())
        kp.assert_not_called()

    def test_old_state_with_only_outer_pgid_still_kills(self):
        self.record(100, 10, name="ingamesave-x")
        st = {"label": "ingamesave-x", "guard_pid": 10, "started": time.time() - 30, "pgid": 100}
        with patch.object(TOOL, "RUNNING", self.running), patch("os.killpg") as kp:
            self.assertEqual(TOOL.hard_kill_groups(st), [100])
            kp.assert_called_once_with(100, signal.SIGKILL)


class ProcessRowsTests(unittest.TestCase):
    def test_parses_pid_pgid_command_and_skips_junk(self):
        # Wine 把 argv 改写成 Z:\ 开头的 Windows 命令行，第三列里什么空格都可能有，只能 split(None, 2)
        out = ("  50912 50912 Z:\\Games\\A B.app\\game\\FixtureGame.exe\n"
               "badline\n"
               "  12 x y z\n"
               "\n"
               "  7 8 cmd  with  spaces\n")
        with patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.run.return_value = MagicMock(stdout=out)
            rows = TOOL.process_rows()
        self.assertEqual(rows, [(50912, 50912, "Z:\\Games\\A B.app\\game\\FixtureGame.exe"),
                                (7, 8, "cmd  with  spaces")])

    def test_ps_failure_returns_empty(self):
        # 整个模块被打成 mock 时 TimeoutExpired 也成了 mock，先换回真类才能当异常抛
        with patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.TimeoutExpired = subprocess.TimeoutExpired
            fake_sub.run.side_effect = OSError("no ps")
            self.assertEqual(TOOL.process_rows(), [])
            fake_sub.run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=10)
            self.assertEqual(TOOL.process_rows(), [])


class AppWindowsPathTests(unittest.TestCase):
    def test_example(self):
        # 结尾带反斜杠：不带的话 A B.app 会连 A B.app2 一起认进来
        self.assertEqual(TOOL.app_windows_path(Path("/Users/example/Games/A B.app")),
                         "Z:\\Users\\example\\Games\\A B.app\\")


class AppProcessPgidsTests(unittest.TestCase):
    # own_pgid 一律给 999：这些行里没有一组是「本工具自己」
    APP = Path("/Users/example/Games/A B.app")

    @staticmethod
    def cmd(app_part):
        return f"Z:\\Users\\example\\Games\\{app_part}\\Contents\\Resources\\game\\Binaries\\Win64\\G.exe"

    def test_match_is_case_insensitive(self):
        rows = [(1, 10, self.cmd("a b.app").upper())]
        self.assertEqual(TOOL.app_process_pgids(rows, self.APP, 999), [10])

    def test_dedup_keeps_first_seen_order(self):
        rows = [(1, 10, self.cmd("A B.app")), (2, 20, self.cmd("A B.app")),
                (3, 10, self.cmd("A B.app")), (4, 30, self.cmd("A B.app"))]
        self.assertEqual(TOOL.app_process_pgids(rows, self.APP, 999), [10, 20, 30])

    def test_own_pgid_and_low_pgids_are_skipped(self):
        rows = [(1, 999, self.cmd("A B.app")), (2, 1, self.cmd("A B.app")),
                (3, 0, self.cmd("A B.app")), (4, 40, self.cmd("A B.app"))]
        self.assertEqual(TOOL.app_process_pgids(rows, self.APP, 999), [40])

    def test_lookalike_app_and_wine_system_processes_do_not_match(self):
        rows = [(1, 10, self.cmd("A B.app2")),
                (2, 20, "C:\\windows\\system32\\services.exe"),
                (3, 30, "C:\\windows\\system32\\winedevice.exe")]
        self.assertEqual(TOOL.app_process_pgids(rows, self.APP, 999), [])

    def test_resolved_symlink_path_also_matches(self):
        # 拉起认的是符号链接路径，Wine 命令行里写的是解析后的实路径——两头都得认
        tmp = tempfile.TemporaryDirectory(prefix="sn-apppgid-")
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()  # macOS 的临时目录在 /var 下，/var 本身是链接，先解析掉
        real = base / "Real.app"
        real.mkdir()
        link = base / "Link.app"
        link.symlink_to(real)
        rows = [(1, 10, TOOL.app_windows_path(real) + "Binaries\\Win64\\G.exe")]
        self.assertEqual(TOOL.app_process_pgids(rows, link, 999), [10])


class LaunchPgidsAppDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-launchpgids-")
        self.addCleanup(tmp.cleanup)
        self.running = Path(tmp.name) / "running"
        self.running.mkdir(parents=True)

    def test_appends_app_pgids_after_record_pgids_without_duplicates(self):
        # 内层记录认到 200；进程表里 app 野组有 200 和 400——撞号的 200 不重复，400 补在后面
        (self.running / "200.json").write_text(json.dumps(
            {"pid": 200, "pgid": 200, "guard_pid": 20, "name": "game",
             "started": time.time(), "max_seconds": 600}))
        app = Path("/Games/A B.app")
        win = TOOL.app_windows_path(app)
        rows = [(1, 200, win + "Binaries\\Win64\\G.exe"),
                (2, 400, win + "Binaries\\Win64\\H.exe")]
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "process_table",
                          return_value=[(1, 0), (10, 1), (100, 10), (20, 100), (200, 20)]), \
             patch.object(TOOL, "process_rows", return_value=rows), \
             patch("os.getpgid", return_value=999):
            got = TOOL.launch_pgids({"pgid": 100, "guard_pid": 10}, time.time() - 5, app)
        self.assertEqual(got, [100, 200, 400])


class WineAppKillTests(unittest.TestCase):
    """带 app_dir 的强杀：野组按命令行认、记过但已死的静默跳过、杀完补 wineserver -k。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-winekill-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.running = self.base / "running"
        self.running.mkdir(parents=True)
        self.appdir = self.base / "A B.app"
        (self.appdir / "Contents" / "Resources").mkdir(parents=True)

    def record(self, pgid, guard_pid, name="game", started=None):
        (self.running / f"{pgid}.json").write_text(json.dumps(
            {"pid": pgid, "pgid": pgid, "guard_pid": guard_pid, "name": name,
             "started": time.time() if started is None else started, "max_seconds": 600}))

    @staticmethod
    def table():
        # 同 MultiGroupKillTests：外层 100（guard 10）、内层 200（guard 20）
        return [(1, 0), (10, 1), (100, 10), (20, 100), (200, 20)]

    def state(self, **kw):
        st = {"label": "ingamesave-x", "guard_pid": 10, "started": time.time() - 30,
              "pgid": 100, "pgids": [100, 200],
              "app_dir": str(self.appdir), "app_pgids": []}
        st.update(kw)
        return st

    def app_rows(self, *pgids):
        win = TOOL.app_windows_path(self.appdir)
        return [(p, p, win + "Binaries\\Win64\\G.exe") for p in pgids]

    def patch_all(self, rows):
        return [patch.object(TOOL, "RUNNING", self.running),
                patch.object(TOOL, "process_table", return_value=self.table()),
                patch.object(TOOL, "process_rows", return_value=rows)]

    def test_late_app_processes_killed_first_then_wineserver_for_the_prefix(self):
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        (self.appdir / "Contents" / "Resources" / "wine-launch.json").write_text(json.dumps(
            {"wine": str(self.base / "wine"), "state": str(self.base / "state")}))
        st = self.state(pgids=[100, 200, 300], app_pgids=[300])
        patches = self.patch_all(self.app_rows(300, 400))
        with patches[0], patches[1], patches[2], \
             patch.object(TOOL, "subprocess") as fake_sub, patch("os.killpg") as kp:
            self.assertEqual(TOOL.hard_kill_groups(st), [400, 300, 200, 100])
            self.assertEqual([c.args for c in kp.call_args_list],
                             [(400, signal.SIGKILL), (300, signal.SIGKILL),
                              (200, signal.SIGKILL), (100, signal.SIGKILL)])
        self.assertEqual(fake_sub.run.call_count, 1)
        args, kwargs = fake_sub.run.call_args
        self.assertEqual(args[0], [str(self.base / "wine" / "bin" / "wineserver"), "-k"])
        self.assertEqual(kwargs["env"]["WINEPREFIX"], str(self.base / "state" / "prefix"))

    def test_recorded_app_pgid_missing_from_table_is_skipped_not_refused(self):
        # 300 只在 app_pgids 里记过、此刻进程表已不见：组死了或 pgid 被回收，静默跳过，不炸
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        st = self.state(pgids=[100, 200, 300], app_pgids=[300])
        patches = self.patch_all([])
        with patches[0], patches[1], patches[2], patch("os.killpg") as kp:
            self.assertEqual(TOOL.hard_kill_groups(st), [200, 100])
            self.assertEqual([c.args for c in kp.call_args_list],
                             [(200, signal.SIGKILL), (100, signal.SIGKILL)])

    def test_unknown_pgid_without_record_or_app_evidence_refuses(self):
        # 500 没有守护记录、也不在 app_pgids/进程表里——没有任何证据证明它是本会话的，拒绝
        self.record(100, 10, name="ingamesave-x")
        st = self.state(pgids=[100, 500], app_pgids=[])
        patches = self.patch_all([])
        with patches[0], patches[1], patches[2], patch("os.killpg") as kp:
            with self.assertRaises(TOOL.KillRefused):
                TOOL.hard_kill_groups(st)
        kp.assert_not_called()

    def test_app_without_wine_launch_json_runs_no_wineserver(self):
        self.record(100, 10, name="ingamesave-x")
        self.record(200, 20)
        patches = self.patch_all([])
        with patches[0], patches[1], patches[2], \
             patch.object(TOOL, "subprocess") as fake_sub, patch("os.killpg"):
            self.assertEqual(TOOL.hard_kill_groups(self.state()), [200, 100])
        fake_sub.run.assert_not_called()


class WineServerOfTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-winesrv-")
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "Y.app"
        (self.app / "Contents" / "Resources").mkdir(parents=True)
        self.json_path = self.app / "Contents" / "Resources" / "wine-launch.json"

    def test_valid_json_gives_wineserver_and_prefix(self):
        self.json_path.write_text(json.dumps({"wine": "/opt/wine", "state": "/opt/state"}))
        self.assertEqual(TOOL.wine_server_of(self.app),
                         (Path("/opt/wine") / "bin" / "wineserver", Path("/opt/state") / "prefix"))

    def test_missing_file_is_none(self):
        self.assertIsNone(TOOL.wine_server_of(self.app))

    def test_bad_json_is_none(self):
        self.json_path.write_text("{oops")
        self.assertIsNone(TOOL.wine_server_of(self.app))

    def test_missing_or_empty_key_is_none(self):
        self.json_path.write_text(json.dumps({"wine": "/opt/wine"}))
        self.assertIsNone(TOOL.wine_server_of(self.app))
        self.json_path.write_text(json.dumps({"wine": "/opt/wine", "state": ""}))
        self.assertIsNone(TOOL.wine_server_of(self.app))


CLOUD_MSG = "这个 app 带存档云同步，但启动壳不认 SN_SAVEBRIDGE=0；先更新它的启动壳，免得测试存档传进云端。"


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-cloudsync-")
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "X.app"
        (self.app / "Contents" / "MacOS").mkdir(parents=True)

    def launcher(self, text):
        if text is not None:
            (self.app / "Contents" / "MacOS" / "launcher").write_text(text)

    def bridge(self):
        (self.app / "Contents" / "MacOS" / "savebridge").write_text("#!/bin/sh\n")

    def entry(self):
        return {"appid": "1", "app": "X.app", "saves": "s", "save_keys": "z", "load_keys": "z"}

    def test_app_without_bridge_is_fine(self):
        self.launcher("#!/bin/sh\nexec game\n")
        self.assertIsNone(TOOL.cloud_sync_problem(self.app))

    def test_bridge_with_old_launcher_is_refused_with_exact_message(self):
        self.bridge()
        self.launcher("#!/bin/sh\n./savebridge before\nexec game\n./savebridge after\n")
        self.assertEqual(TOOL.cloud_sync_problem(self.app), CLOUD_MSG)

    def test_bridge_without_launcher_at_all_is_refused(self):
        self.bridge()
        self.launcher(None)
        self.assertEqual(TOOL.cloud_sync_problem(self.app), CLOUD_MSG)

    def test_bridge_with_new_launcher_passes(self):
        self.bridge()
        self.launcher('#!/bin/sh\n[ "$SN_SAVEBRIDGE" = "0" ] || ./savebridge before\nexec game\n')
        self.assertIsNone(TOOL.cloud_sync_problem(self.app))

    def test_preflight_names_the_cloud_problem_and_clears_for_new_launcher(self):
        self.bridge()
        self.launcher("#!/bin/sh\nexec game\n")
        with patch.object(TOOL, "SN_GAMES", self.app.parent), patch.object(TOOL, "RUNNING", self.app.parent / "none-running"), \
                patch.object(TOOL, "KEYSEND", self.app.parent / "no-keysend"):
            self.assertIn(CLOUD_MSG, TOOL.preflight_problems(self.entry()))
        self.launcher("#!/bin/sh\nSN_SAVEBRIDGE=0 exec game\n")
        with patch.object(TOOL, "SN_GAMES", self.app.parent), patch.object(TOOL, "RUNNING", self.app.parent / "none-running"), \
                patch.object(TOOL, "KEYSEND", self.app.parent / "no-keysend"):
            self.assertNotIn(CLOUD_MSG, TOOL.preflight_problems(self.entry()))

    def test_up_refuses_before_touching_anything(self):
        self.bridge()
        self.launcher("#!/bin/sh\nexec game\n")
        with patch.object(TOOL, "SN_GAMES", self.app.parent), \
             patch.object(TOOL, "load_table", return_value={"x": self.entry()}), \
             patch.object(TOOL, "do_launch") as launch:
            rc = TOOL.cmd_up("x")
        self.assertEqual(rc, 2)
        launch.assert_not_called()


class LaunchEnvTests(unittest.TestCase):
    def test_env_is_os_environ_copy_with_savebridge_off(self):
        env = TOOL.launch_env()
        self.assertEqual(env["SN_SAVEBRIDGE"], "0")
        self.assertIsNot(env, os.environ)
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))

    def test_building_env_leaves_os_environ_untouched(self):
        before = dict(os.environ)
        TOOL.launch_env()
        self.assertEqual(dict(os.environ), before)

    def test_launch_guarded_hands_env_to_run_guard(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-launchenv-")
        self.addCleanup(tmp.cleanup)
        app = Path(tmp.name) / "Y.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "game"}))
        (app / "Contents" / "MacOS" / "game").write_text("#!/bin/sh\n")
        rec = {"pgid": 424242, "guard_pid": 424243, "name": "lbl", "started": time.time()}
        with patch.object(TOOL, "SN_GAMES", Path(tmp.name)), \
             patch.object(TOOL, "subprocess") as fake_sub, \
             patch.object(TOOL, "guard_records", return_value=[rec]):
            fake_sub.Popen.return_value = MagicMock()
            TOOL.launch_guarded({"app": "Y.app"}, "lbl", Path(tmp.name) / "evd", 60)
        env = fake_sub.Popen.call_args.kwargs["env"]
        self.assertEqual(env["SN_SAVEBRIDGE"], "0")
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


class NeedsFocusTests(unittest.TestCase):
    def send(self, state, steps=None):
        with patch.object(TOOL, "subprocess") as fake_sub, patch.object(TOOL.time, "sleep"):
            fake_sub.run.return_value = MagicMock(returncode=0)
            notes = TOOL.send_steps(4242, steps or TOOL.parse_keys("z, return"), state)
            calls = [c.args[0] for c in fake_sub.run.call_args_list]
        return notes, calls

    def test_focus_precedes_every_key_step(self):
        # 合成键只落进前台应用：配了 needs_focus，每步投键前都得先 osascript 置前
        notes, calls = self.send({"needs_focus": True})
        self.assertEqual(len(calls), 4)  # 两步，每步先置前再 keysend
        for i in (0, 2):
            self.assertEqual(calls[i][0], "osascript")
            self.assertIn("unix id is 4242", calls[i][2])
            self.assertIn("frontmost", calls[i][2])
        self.assertEqual(calls[1][calls[1].index("--pid") + 1], "4242")
        self.assertEqual(notes.count("已置前"), 2)  # 置过焦要记进结果里

    def test_no_focus_by_default(self):
        _, calls = self.send({})
        self.assertEqual(calls[0][0], str(TOOL.KEYSEND))  # 没配 needs_focus 就不多这道手

    def test_failed_focus_aborts_before_any_key_is_sent(self):
        # 置前失败还照投，键就会打进前台那家不知谁的窗口——宁可整段不投
        with patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.run.return_value = MagicMock(returncode=1, stderr="nope")
            with self.assertRaises(TOOL.KeysendError):
                TOOL.send_steps(4242, TOOL.parse_keys("z"), {"needs_focus": True})
        self.assertEqual(fake_sub.run.call_count, 1)


class ClickModeHidTests(unittest.TestCase):
    def send_click(self, state):
        wins = [{"id": 7, "bounds": {"X": 100, "Y": 60}}]
        with patch.object(TOOL, "windows", return_value=wins), \
             patch.object(TOOL, "subprocess") as fake_sub, \
             patch.object(TOOL.time, "sleep"):
            fake_sub.run.return_value = MagicMock(returncode=0)
            notes = TOOL.send_steps(4242, [{"click": (10, 20), "hold_ms": 80}], state)
            calls = [c.args[0] for c in fake_sub.run.call_args_list]
        return notes, calls

    def test_hid_click_focuses_first_then_clicks_through_hid(self):
        # NW.js/Chromium 的窗口对 --click 不理不睬：hid 没有 --pid 可指，认的是前台焦点，
        # 所以必须先置前，再走 --click-hid，屏幕坐标换算照旧
        notes, calls = self.send_click({"window_id": 7, "click_mode": "hid"})
        self.assertEqual(calls[0][0], "osascript")
        self.assertIn("unix id is 4242", calls[0][2])
        cmd = calls[1]
        self.assertEqual(cmd[0], str(TOOL.KEYSEND))
        self.assertNotIn("--pid", cmd)
        self.assertEqual(cmd[cmd.index("--click-hid") + 1], "110,80")
        self.assertEqual(cmd[cmd.index("--hold-ms") + 1], "80")
        self.assertIn("已置前", notes)

    def test_pid_click_stays_the_default_without_focus(self):
        _, calls = self.send_click({"window_id": 7})
        self.assertEqual(calls[0][0], str(TOOL.KEYSEND))
        self.assertIn("--pid", calls[0])
        self.assertIn("--click", calls[0])
        self.assertNotIn("--click-hid", calls[0])


class CaffeinateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sn-caffeinate-")
        self.addCleanup(temporary.cleanup)
        evidence = patch.object(TOOL, "EVID_ROOT", Path(temporary.name))
        evidence.start()
        self.addCleanup(evidence.stop)

    def test_command_pins_display_awake_to_this_process(self):
        # -d 只压显示睡眠，-u 顺手声明一次用户活动；-w 指着本进程，本工具一退 caffeinate 就跟着退，不用收尸
        with patch.object(TOOL, "subprocess") as fake_sub:
            self.assertTrue(TOOL.keep_display_awake())
            argv = fake_sub.Popen.call_args.args[0]
        self.assertEqual(argv, ["caffeinate", "-d", "-u", "-w", str(os.getpid())])

    def test_missing_caffeinate_is_reported_not_fatal(self):
        with patch.object(TOOL, "subprocess") as fake_sub:
            fake_sub.Popen.side_effect = OSError("no caffeinate")
            self.assertFalse(TOOL.keep_display_awake())

    def test_up_starts_caffeinate_before_launching(self):
        entry = {"appid": "1", "app": "X.app", "saves": "s", "save_keys": "z", "load_keys": "z"}
        state = {"label": "ingamesave-x-1", "pgid": 4242, "pgids": [4242], "guard_pid": 4243}
        win = {"id": 9, "pid": 4244}
        with patch.object(TOOL, "load_table", return_value={"x": entry}), \
             patch.object(TOOL, "read_state", return_value=None), \
             patch.object(TOOL, "already_running", return_value=[]), \
             patch.object(TOOL, "keep_display_awake") as caffeinate, \
             patch.object(TOOL, "do_launch", return_value=(state, None, win)), \
             patch.object(TOOL, "shoot", return_value=True):
            rc = TOOL.cmd_up("x")
        self.assertEqual(rc, 0)
        # 先压本进程（等窗口、截图），拉起之后再钉到外层守护上顶整局
        self.assertEqual(caffeinate.call_args_list, [call(), call(4243, detach=True)])


class KeepAwakeDetachTests(unittest.TestCase):
    def test_detach_pins_caffeinate_to_the_guard_pid_in_its_own_session(self):
        # 钉在外层守护上：它退了游戏必然收场，caffeinate 跟着退，整局显示器都醒着
        with patch.object(TOOL, "subprocess") as fake_sub:
            self.assertTrue(TOOL.keep_display_awake(4242, detach=True))
            argv = fake_sub.Popen.call_args.args[0]
            start_new_session = fake_sub.Popen.call_args.kwargs["start_new_session"]
        self.assertEqual(argv[-2:], ["-w", "4242"])
        self.assertEqual(argv[:2], ["caffeinate", "-d"])
        self.assertTrue(start_new_session)

    def test_default_call_pins_to_this_process_without_new_session(self):
        with patch.object(TOOL, "subprocess") as fake_sub:
            self.assertTrue(TOOL.keep_display_awake())
            argv = fake_sub.Popen.call_args.args[0]
            start_new_session = fake_sub.Popen.call_args.kwargs["start_new_session"]
        self.assertEqual(argv, ["caffeinate", "-d", "-u", "-w", str(os.getpid())])
        self.assertFalse(start_new_session)


class UpRefusesBusyAppTests(unittest.TestCase):
    def test_up_refuses_the_exact_line_and_never_launches(self):
        entry = {"appid": "1", "app": "X.app", "saves": "s", "save_keys": "z", "load_keys": "z"}
        with patch.object(TOOL, "load_table", return_value={"x": entry}), \
             patch.object(TOOL, "read_state", return_value=None), \
             patch.object(TOOL, "already_running", return_value=[777]), \
             patch.object(TOOL, "do_launch") as launch, \
             patch("builtins.print") as pr:
            rc = TOOL.cmd_up("x")
        self.assertEqual(rc, 2)
        launch.assert_not_called()
        self.assertIn("拒绝 up x：这个游戏已有进程在跑（pgid 777），先收掉再来",
                      [str(c.args[0]) for c in pr.call_args_list])


class PreflightBusyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-preflight-busy-")
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "X.app"
        (self.app / "Contents" / "MacOS").mkdir(parents=True)
        (self.app / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleExecutable": "game"}))

    def test_busy_app_is_named_as_a_preflight_problem(self):
        entry = {"appid": "1", "app": "X.app", "saves": "s", "save_keys": "z", "load_keys": "z"}
        with patch.object(TOOL, "SN_GAMES", self.app.parent), \
             patch.object(TOOL, "RUNNING", self.app.parent / "none-running"), \
             patch.object(TOOL, "KEYSEND", self.app.parent / "no-keysend"), \
             patch.object(TOOL, "already_running", return_value=[777]) as busy:
            problems = TOOL.preflight_problems(entry)
        busy.assert_called_once_with(entry)
        self.assertIn("这个游戏已有进程在跑（pgid 777），先收掉再来", problems)


class WakeDisplayOrderTests(unittest.TestCase):
    def test_shot_wakes_display_before_shooting(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-wakeorder-")
        self.addCleanup(tmp.cleanup)
        state = {"window_id": 9, "evidence_dir": str(Path(tmp.name))}
        order = []
        with patch.object(TOOL, "read_state", return_value=state), \
             patch.object(TOOL, "windows", return_value=[]), \
             patch.object(TOOL, "wake_display", side_effect=lambda: order.append("wake")) as wake, \
             patch.object(TOOL, "shoot",
                          side_effect=lambda *a, **k: order.append("shoot") or True) as sh:
            rc = TOOL.cmd_shot("x")
        self.assertEqual(rc, 0)
        wake.assert_called_once_with()
        sh.assert_called_once()
        self.assertEqual(order, ["wake", "shoot"])  # 先叫醒再拍，顺序不能反


class InstalledFolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-instfold-")
        self.addCleanup(tmp.cleanup)
        self.games = Path(tmp.name)
        self.installed = self.games / "installed.json"

    def patch_games(self):
        return patch.object(TOOL, "SN_GAMES", self.games)

    def test_table_folder_wins_over_installed_json(self):
        self.installed.write_text(json.dumps({"9": {"folder": "from-installed"}}))
        with self.patch_games():
            self.assertEqual(TOOL.installed_folder({"appid": "9", "folder": "from-table"}),
                             self.games / "from-table")

    def test_table_folder_accepts_absolute_and_home_forms(self):
        with self.patch_games():
            self.assertEqual(TOOL.installed_folder({"appid": "9", "folder": "/opt/game"}), Path("/opt/game"))
            self.assertEqual(TOOL.installed_folder({"appid": "9", "folder": "~/g"}), Path.home() / "g")

    def test_no_table_folder_falls_back_to_installed_json(self):
        self.installed.write_text(json.dumps({"9": {"folder": "app_9"}}))
        with self.patch_games():
            self.assertEqual(TOOL.installed_folder({"appid": "9"}), self.games / "app_9")
            self.assertEqual(TOOL.installed_folder({"appid": "9", "folder": None}), self.games / "app_9")

    def test_no_folder_anywhere_is_none(self):
        with self.patch_games():
            self.assertIsNone(TOOL.installed_folder({"appid": "9", "folder": None}))
            self.assertIsNone(TOOL.installed_folder({}))

    def test_installed_record_without_folder_field_is_none(self):
        # entry-03 的实况：appid 登记了却没 folder 字段，表里补个 folder 就能绕开
        self.installed.write_text(json.dumps({"9": {"name": "x"}}))
        with self.patch_games():
            self.assertIsNone(TOOL.installed_folder({"appid": "9"}))

    def test_broken_installed_json_is_none(self):
        self.installed.write_text("{oops")
        with self.patch_games():
            self.assertIsNone(TOOL.installed_folder({"appid": "9"}))


class ReapInnerRecordsTests(unittest.TestCase):
    """reap_inner_records：硬杀替内层守护扫尾，但别人的记录（别的组、别的标签、更早时刻）一条不碰。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-reapinner-")
        self.addCleanup(tmp.cleanup)
        self.running = Path(tmp.name) / "running"
        self.running.mkdir(parents=True)

    def record(self, pgid, name="somegame", started=None, pid=111, guard_pid=112):
        started = time.time() if started is None else started
        f = self.running / f"{pgid}.json"
        f.write_text(json.dumps({"pid": pid, "pgid": pgid, "guard_pid": guard_pid,
                                 "name": name, "started": started, "max_seconds": 600}))
        return f

    def reap(self, pgids, label, since, alive=False):
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "pid_alive", return_value=alive):
            return TOOL.reap_inner_records(pgids, label, since)

    def test_dead_inner_record_in_session_groups_is_removed(self):
        # 硬杀把内层守护连组带走，pid/guard_pid 都死了：替它删，并说清是哪一层的账
        rec = self.record(pgid=200, name="somegame")
        notes = self.reap([100, 200], "ingamesave-x", time.time() - 60, alive=False)
        self.assertFalse(rec.exists())
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("清掉了内层残留记录"), notes)

    def test_inner_record_with_live_pid_is_kept(self):
        # 还有一个活进程就不删：那层守护还在干活，pgid 也随时可能被回收，删了等于销别人的记录
        rec = self.record(pgid=200, name="somegame")
        notes = self.reap([100, 200], "ingamesave-x", time.time() - 60, alive=True)
        self.assertTrue(rec.exists())
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("内层记录"), notes)
        self.assertIn("不删", notes[0])

    def test_records_of_other_sessions_are_untouched(self):
        # 组不在本会话、name 就是本会话标签（reap_record 的活）、早于 since 的陈账：一条都不动
        other_group = self.record(pgid=300, name="somegame")
        own_label = self.record(pgid=100, name="ingamesave-x")
        stale = self.record(pgid=200, name="somegame", started=time.time() - 3600)
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "pid_alive", return_value=False) as alive:
            notes = TOOL.reap_inner_records([100, 200], "ingamesave-x", time.time() - 60)
        self.assertEqual(notes, [])
        for f in (other_group, own_label, stale):
            self.assertTrue(f.exists(), f)
        alive.assert_not_called()  # 三条全在筛选关就被跳过，连进程都不用探

    def test_unlink_failure_becomes_a_note_not_a_crash(self):
        # 删不动就说明白并继续：收尾绝不该被一条删不掉的记录炸停
        self.record(pgid=200, name="somegame")
        boom = OSError("disk fell off")
        with patch.object(TOOL, "RUNNING", self.running), \
             patch.object(TOOL, "pid_alive", return_value=False), \
             patch("pathlib.Path.unlink", side_effect=boom):
            notes = TOOL.reap_inner_records([200], "ingamesave-x", time.time() - 60)
        self.assertEqual(notes, [f"删不掉记录 {self.running / '200.json'}: {boom}"])


class DownInnerRecordsTests(unittest.TestCase):
    def test_hard_down_reaps_inner_records_of_the_session_groups(self):
        # down --hard 杀完组后得替内层守护扫尾：调本会话的组/标签/时刻，并把说明打进输出
        started = time.time() - 30
        state = {"label": "ingamesave-x", "started": started, "pgid": 100, "pgids": [100, 200]}
        with patch.object(TOOL, "read_state", return_value=state), \
             patch.object(TOOL, "load_table", return_value={}), \
             patch.object(TOOL, "force_kill_step", return_value=(True, "ok")), \
             patch.object(TOOL, "group_gone", return_value=True), \
             patch.object(TOOL, "reap_record", return_value=[]), \
             patch.object(TOOL, "reap_inner_records", return_value=["n1"]) as inner, \
             patch.object(TOOL, "clear_state"), \
             patch("time.sleep"), \
             patch("builtins.print") as pr:
            rc = TOOL.cmd_down("x", True)
        self.assertEqual(rc, 0)
        inner.assert_called_once_with([100, 200], "ingamesave-x", float(started))
        printed = "\n".join(" ".join(str(a) for a in c.args) for c in pr.call_args_list)
        self.assertIn("n1", printed)


class SessionPgidsTests(unittest.TestCase):
    """session_pgids：找窗口用的「此刻的会话组」——状态里记的组，加上带 app_dir 的状态
    按此刻进程表新认到的 Wine 野组。"""

    def test_state_without_app_dir_never_reads_the_process_table(self):
        # 老状态没有 app_dir：认不了野组，也不该为一次窗口查询去跑 ps
        with patch.object(TOOL, "process_rows") as rows:
            self.assertEqual(TOOL.session_pgids({"pgid": 100, "pgids": [100, 200]}), [100, 200])
        rows.assert_not_called()

    def test_app_dir_appends_new_app_pgids_without_duplicates(self):
        # 状态里的 100 与进程表撞号不重复；野组 300 补在后面
        app = Path("/Games/A B.app")
        win = TOOL.app_windows_path(app)
        rows = [(1, 100, win + "Binaries\\Win64\\G.exe"),
                (2, 300, win + "Binaries\\Win64\\H.exe")]
        state = {"pgid": 100, "pgids": [100, 200], "app_dir": str(app)}
        with patch.object(TOOL, "process_rows", return_value=rows) as pr, \
             patch("os.getpgid", return_value=999):
            self.assertEqual(TOOL.session_pgids(state), [100, 200, 300])
        pr.assert_called_once_with()


class RefreshWindowTests(unittest.TestCase):
    """refresh_window：投键/截图/点击前重认窗口——启动画面绝不能一直顶着「游戏窗口」的名分。"""

    def refresh(self, wins, pgid_of, state):
        def fake_getpgid(pid):
            if int(pid) not in pgid_of:
                raise ProcessLookupError(int(pid))
            return pgid_of[int(pid)]

        with patch.object(TOOL, "windows", return_value=wins), \
             patch("os.getpgid", fake_getpgid), \
             patch.object(TOOL, "process_rows") as rows:
            got = TOOL.refresh_window(state)
        rows.assert_not_called()  # 没有 app_dir 的状态不该为找窗口去动进程表
        return got

    def test_recorded_id_missing_is_replaced_by_the_session_window(self):
        # 启动画面一关，记录里的 window_id 就成了死号：重认到会话的真窗口并把 id/pid/bounds 记回状态
        state = {"window_id": 5, "window_pid": 11, "bounds": {"X": 0, "Y": 0}, "pgid": 100}
        win = {"id": 9, "pid": 42, "bounds": {"X": 7, "Y": 8}}
        self.assertEqual(self.refresh([win], {42: 100}, state), win)
        self.assertEqual((state["window_id"], state["window_pid"]), (9, 42))
        self.assertEqual(state["bounds"], {"X": 7, "Y": 8})

    def test_larger_real_window_wins_while_recorded_splash_still_exists(self):
        # 实测翻车现场：601x201 的启动画面还挂在列表里，1288x754 的真窗口更大也同属会话——
        # 窗口列表大者在前，取到的第一个就得是它
        state = {"window_id": 5, "window_pid": 11, "bounds": {"X": 0, "Y": 0}, "pgid": 100}
        splash = {"id": 5, "pid": 11, "bounds": {"X": 0, "Y": 0, "Width": 601, "Height": 201}}
        real = {"id": 20, "pid": 12, "bounds": {"X": 0, "Y": 0, "Width": 1288, "Height": 754}}
        self.assertEqual(self.refresh([real, splash], {11: 100, 12: 100}, state), real)
        self.assertEqual((state["window_id"], state["window_pid"]), (20, 12))

    def test_no_session_window_changes_nothing(self):
        # 会话里认不出任何窗口（游戏退了/还没出来）：不改状态、返回 None
        state = {"window_id": 5, "window_pid": 11, "bounds": {"X": 1, "Y": 2}, "pgid": 100}
        before = dict(state)
        self.assertIsNone(self.refresh([{"id": 9, "pid": 42}], {42: 777}, state))
        self.assertEqual(state, before)

    def test_larger_window_of_a_foreign_pgid_is_ignored(self):
        # 别人家的大窗口排在列表前面也不认：只认会话进程组名下的窗口
        state = {"window_id": 5, "window_pid": 11, "bounds": {"X": 0, "Y": 0}, "pgid": 100}
        mine = {"id": 5, "pid": 11, "bounds": {"X": 3, "Y": 4}}
        other = {"id": 99, "pid": 55, "bounds": {"X": 0, "Y": 0, "Width": 3000, "Height": 2000}}
        self.assertEqual(self.refresh([other, mine], {55: 777, 11: 100}, state), mine)


class ClickBoundsRefreshTests(unittest.TestCase):
    def test_missing_window_id_refreshes_to_the_new_session_bounds(self):
        # 记录的窗口 id 已不在列表里（启动画面关了）：按会话重认，点的是新窗口的 bounds，
        # 不是状态里那份过时的
        state = {"window_id": 7, "bounds": {"X": 1, "Y": 2}, "pgid": 100}
        win = {"id": 9, "pid": 42, "bounds": {"X": 100, "Y": 60}}

        def fake_getpgid(pid):
            return 100 if int(pid) == 42 else 999

        with patch.object(TOOL, "windows", return_value=[win]), patch("os.getpgid", fake_getpgid):
            self.assertEqual(TOOL.click_bounds(state), {"X": 100, "Y": 60})
        self.assertEqual(state["window_id"], 9)  # 重认的结果顺手记回状态


class ShootWindowTests(unittest.TestCase):
    def shoot(self, state, wins, pgid_of):
        def fake_getpgid(pid):
            if int(pid) not in pgid_of:
                raise ProcessLookupError(int(pid))
            return pgid_of[int(pid)]

        with patch.object(TOOL, "windows", return_value=wins), \
             patch("os.getpgid", fake_getpgid), \
             patch.object(TOOL, "shoot", side_effect=lambda wid, dest: [wid] and True) as sh:
            ok = TOOL.shoot_window(state, "dest.png")
        return ok, sh

    def test_refreshes_first_then_shoots_the_refreshed_id(self):
        # 记录的 5 号是启动画面：得先重认成 9 号，再把 9 号拍下来
        state = {"window_id": 5, "window_pid": 11, "pgid": 100}
        win = {"id": 9, "pid": 42, "bounds": {"X": 0, "Y": 0}}
        ok, sh = self.shoot(state, [win], {42: 100, 11: 100})
        self.assertTrue(ok)
        sh.assert_called_once_with(9, "dest.png")
        self.assertEqual(state["window_id"], 9)

    def test_without_any_window_id_returns_false_and_never_shoots(self):
        # up 没等到窗口、重认也没有：没有 id 可拍，绝不拿旧号瞎拍
        state = {"pgid": 100}
        ok, sh = self.shoot(state, [{"id": 9, "pid": 42}], {42: 777})
        self.assertFalse(ok)
        sh.assert_not_called()


class LoadAfterQuitStepTests(unittest.TestCase):
    """load_after_quit 的判法：存档没写成时距离再小也不许说 pass。"""

    def test_unwritten_save_is_unknown_even_with_tiny_distance(self):
        # 实测翻车现场：存档没落盘，两份截图都是主菜单、距离极小，照距离判就是假 pass
        status, detail = TOOL.load_after_quit_step(True, "投了 z", "fail", True, 0.4, 30.0)
        self.assertEqual(status, "unknown")
        self.assertTrue(detail.startswith("第一次存档没写成"), detail)
        self.assertEqual(detail,
                         "第一次存档没写成（save_written=fail），读档比对无从谈起"
                         "；距离照记 d(载入,存档)=0.4、d(载入,标题)=30.0")

    def test_unwritten_save_without_distances_leaves_the_tail_empty(self):
        _, detail = TOOL.load_after_quit_step(True, "投了 z", "fail", False, None, None)
        self.assertEqual(detail, "第一次存档没写成（save_written=fail），读档比对无从谈起")

    def test_written_save_keeps_the_old_distance_verdict(self):
        status, detail = TOOL.load_after_quit_step(True, "投了 z", "pass", True, 5.0, 30.0)
        self.assertEqual((status, detail), ("pass", "d(载入,存档)=5.0 < d(载入,标题)=30.0"))
        status, _ = TOOL.load_after_quit_step(True, "投了 z", "pass", True, 30.0, 5.0)
        self.assertEqual(status, "fail")

    def test_keysend_failure_is_still_a_fail(self):
        # 键没投出去是实打实的失败，不因存档写没写成而改口
        status, detail = TOOL.load_after_quit_step(False, "窗口 pid 9 不在，键没处投",
                                                   "fail", True, 0.4, 30.0)
        self.assertEqual((status, detail), ("fail", "键没投出去：窗口 pid 9 不在，键没处投"))


class LoadAfterKillStepTests(unittest.TestCase):
    UNWRITTEN = "第二次存档没有写盘（强杀前那份与第一份无从区分），强杀后读档无从验"

    def test_second_save_unwritten_is_unknown_with_the_exact_note(self):
        status, detail = TOOL.load_after_kill_step(True, "投了 z", False, True, 5.0, 20.0, 30.0, 40.0)
        self.assertEqual((status, detail), ("unknown", self.UNWRITTEN))

    def test_second_save_written_keeps_the_verdict(self):
        status, detail = TOOL.load_after_kill_step(True, "投了 z", True, True, 5.0, 20.0, 30.0, 40.0)
        self.assertEqual(status, "pass")
        self.assertIn("强杀前那份", detail)

    def test_keysend_failure_is_still_a_fail(self):
        status, detail = TOOL.load_after_kill_step(False, "窗口 pid 9 不在，键没处投",
                                                   False, True, 5.0, 20.0, 30.0, 40.0)
        self.assertEqual((status, detail), ("fail", "键没投出去：窗口 pid 9 不在，键没处投"))


if __name__ == "__main__":
    unittest.main()
