# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""快捷方式只用临时 Steam 目录；验证备份、账号与官方写入后的只读检查。"""
import base64
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
spec = importlib.util.spec_from_file_location("shortcut_check", ROOT / "lib/steam-shortcut-check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.steam = self.root / "steam"
        self.games = self.root / "games"
        self.cfg = self.steam / "userdata/42/config"
        self.cfg.mkdir(parents=True)
        (self.steam / "config").mkdir()
        self.file = self.cfg / "shortcuts.vdf"
        for obj, key, value in ((check, "STEAM", self.steam), (check, "SN_GAMES", self.games),
                                (check.mirror, "STEAM", str(self.steam))):
            context = patch.object(obj, key, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.dict("os.environ", {"SN_STEAM_ACCOUNT": ""})
        context.start()
        self.addCleanup(context.stop)
        self.login()

    def login(self, selected=(42,), account="fixture"):
        text = '"users"\n{\n'
        for uid in selected:
            sid = check.mirror.STEAMID64_BASE + uid
            text += f'\t"{sid}"\n\t{{\n\t\t"AccountName" "{account}"\n\t\t"AutoLogin" "1"\n\t}}\n'
        (self.steam / "config/loginusers.vdf").write_text(text + '}\n')

    def entries(self, *ids):
        return [(0, str(i), check.mirror.make_entry(n, str(n), f"/games/{n}.app", "")) for i, n in enumerate(ids)]

    @staticmethod
    def put(fields, name, value):
        for index, (kind, key, _) in enumerate(fields):
            if key.casefold() == name.casefold():
                fields[index] = (kind, key, value)
                return
        raise AssertionError("fixture field missing: " + name)

    def test_prepare_and_verify_preserve_old_bytes_and_only_read_after(self):
        check.mirror.save(str(self.file), self.entries(12))
        old = self.file.read_bytes()
        backup = check.prepare("/games/23.app")
        receipt = json.loads(backup.read_text())
        self.assertEqual(base64.b64decode(receipt["raw"]), old)
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        check.mirror.save(str(self.file), self.entries(12, 23))
        after = self.file.read_bytes()
        before_time = self.file.stat().st_mtime_ns
        check.verify(backup, 23)
        self.assertEqual(self.file.read_bytes(), after)
        self.assertEqual(self.file.stat().st_mtime_ns, before_time)

    def test_first_shortcut_records_absence(self):
        backup = check.prepare("/games/23.app")
        self.assertIsNone(json.loads(backup.read_text())["raw"])
        self.assertFalse(self.file.exists())
        check.mirror.save(str(self.file), self.entries(23))
        check.verify(backup, 23)

    def test_corrupt_existing_file_stops_before_backup(self):
        for content in (b"", b"\0shortcuts\0\x08", b"\0shortcuts\0\x08\x08junk"):
            self.file.write_bytes(content)
            with self.assertRaises((ValueError, IndexError)):
                check.prepare("/games/23.app")
            self.assertFalse(self.games.exists())
            self.assertEqual(self.file.read_bytes(), content)

    def test_removed_old_entry_wrong_path_and_duplicate_never_verify(self):
        check.mirror.save(str(self.file), self.entries(12))
        backup = check.prepare("/games/23.app")
        for entries in (self.entries(23), self.entries(12, 24), self.entries(12, 23, 23)):
            check.mirror.save(str(self.file), entries)
            with self.assertRaises(ValueError):
                check.verify(backup, 23)

    def test_no_guess_from_mtime_and_ambiguous_or_changed_account_stops(self):
        self.assertEqual(check.mirror.active_account(), "42")
        backup = check.prepare("/games/23.app")
        for selected in ((), (42, 43), (43,)):
            self.login(selected)
            with self.assertRaises(ValueError):
                check.verify(backup, 23)
        self.login(())
        self.assertIsNone(check.mirror.active_account())
        with self.assertRaises(ValueError):
            check.prepare("/games/23.app")
        self.login()
        (self.steam / "registry.vdf").write_text('"AutoLoginUser" "different"')
        self.assertIsNone(check.mirror.active_account())

    def test_explicit_foreign_account_cannot_direct_cdp(self):
        with patch.dict("os.environ", {"SN_STEAM_ACCOUNT": "43"}):
            with self.assertRaises(ValueError):
                check.prepare("/games/23.app")

    def test_duplicate_map_indices_and_fields_are_rejected(self):
        entries = self.entries(12, 23)
        entries[1] = (0, "0", entries[1][2])
        raw = b"\0shortcuts\0" + check.mirror.dump_map(entries) + b"\x08"
        with self.assertRaises(ValueError):
            check.parse_exact(raw)
        entries = self.entries(12)
        entries[0][2].append((1, "Exe", "/different.app"))
        raw = b"\0shortcuts\0" + check.mirror.dump_map(entries) + b"\x08"
        with self.assertRaises(ValueError):
            check.parse_exact(raw)

    def test_partial_steam_write_is_retried_before_success(self):
        raw = b"\0shortcuts\0\0"
        with self.assertRaises(ValueError):
            check.parse_exact(raw)
        with patch.object(sys, "argv", ["check", "verify", "receipt", "23"]), \
                patch.object(check, "verify", side_effect=[ValueError("partial"), None]) as verify, \
                patch.object(check.time, "sleep"):
            self.assertEqual(check.main(), 0)
        self.assertEqual(verify.call_count, 2)

    def removal_entries(self):
        entries = self.entries(12, 23, 24, 41)
        for index in (1, 2):
            self.put(entries[index][2], "Exe", '"/games/target.app"')
        entries[0][2].append((0, "FutureData", [(1, "raw", "完整文字"), (2, "flag", 4294967295)]))
        return entries

    def test_remove_backups_all_target_ids_and_preserves_original_bytes(self):
        check.mirror.save(str(self.file), self.removal_entries())
        old, mtime = self.file.read_bytes(), self.file.stat().st_mtime_ns
        backup = check.prepare_remove("/games/target.app")
        receipt = json.loads(backup.read_text())
        self.assertEqual(receipt["action"], "remove")
        self.assertEqual(receipt["uid"], "42")
        self.assertEqual(receipt["exe"], "/games/target.app")
        self.assertEqual(receipt["ids"], [23, 24])
        self.assertEqual(base64.b64decode(receipt["raw"], validate=True), old)
        self.assertEqual((self.file.read_bytes(), self.file.stat().st_mtime_ns), (old, mtime))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        entries = self.removal_entries()
        check.mirror.save(str(self.file), [entries[3], entries[0]])
        current, mtime = self.file.read_bytes(), self.file.stat().st_mtime_ns
        check.verify_remove(backup)
        self.assertEqual((self.file.read_bytes(), self.file.stat().st_mtime_ns), (current, mtime))

    def test_remove_no_target_or_missing_file_still_has_a_backup(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                if not missing:
                    check.mirror.save(str(self.file), self.entries(12))
                backup = check.prepare_remove("/games/target.app")
                receipt = json.loads(backup.read_text())
                self.assertEqual(receipt["ids"], [])
                self.assertEqual(receipt["raw"] is None, missing)
                check.verify_remove(backup)
        self.assertEqual(len(list((self.games / "shortcut-backups").glob("before-remove-*.json"))), 2)

    def test_remove_remaining_targets_changed_fields_or_new_entries_fail(self):
        entries = self.removal_entries()
        check.mirror.save(str(self.file), entries)
        backup = check.prepare_remove("/games/target.app")
        for case in ("left_target", "new_target_id", "missing_other", "changed_other", "new_other", "duplicate_other"):
            with self.subTest(case=case):
                current = self.removal_entries()
                keep = [current[0], current[3]]
                if case == "left_target": keep.append(current[1])
                elif case == "new_target_id":
                    self.put(current[1][2], "appid", 29)
                    keep.append(current[1])
                elif case == "missing_other": keep.pop()
                elif case == "changed_other": self.put(keep[0][2], "LaunchOptions", "changed")
                elif case == "new_other": keep += self.entries(55)
                else: keep.append(keep[0])
                check.mirror.save(str(self.file), keep)
                raw = self.file.read_bytes()
                with self.assertRaises(ValueError): check.verify_remove(backup)
                self.assertEqual(self.file.read_bytes(), raw)

    def test_remove_same_id_different_path_conflicts_before_backup(self):
        entries = self.entries(23, 23)
        self.put(entries[1][2], "Exe", '"/games/other.app"')
        check.mirror.save(str(self.file), entries)
        raw = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "同一.*ID.*路径"):
            check.prepare_remove("/games/23.app")
        self.assertFalse(self.games.exists())
        self.assertEqual(self.file.read_bytes(), raw)

    def test_remove_exact_path_does_not_resolve_other_shortcut_alias(self):
        real = self.root / "actual.app"
        alias = self.root / "alias.app"
        real.mkdir()
        alias.symlink_to(real, target_is_directory=True)
        entries = self.entries(23, 24)
        self.put(entries[0][2], "Exe", str(real.resolve()))
        self.put(entries[1][2], "Exe", '"' + str(alias) + '"')
        check.mirror.save(str(self.file), entries)
        backup = check.prepare_remove(str(real))
        self.assertEqual(json.loads(backup.read_text())["ids"], [23])
        check.mirror.save(str(self.file), [entries[1]])
        check.verify_remove(backup)

    def test_remove_corrupt_snapshots_and_forged_ids_are_rejected(self):
        for malformed in (b"", b"\0shortcuts\0\x08", b"\0shortcuts\0\0", b"\0shortcuts\0\x08\x08junk"):
            self.file.write_bytes(malformed)
            with self.assertRaises(ValueError): check.prepare_remove("/games/23.app")
            self.assertFalse(self.games.exists())
        check.mirror.save(str(self.file), self.entries(12, 23))
        backup = check.prepare_remove("/games/23.app")
        receipt = json.loads(backup.read_text())
        check.mirror.save(str(self.file), self.entries(12))
        raw = self.file.read_bytes()
        for key, value in (("action", "add"), ("ids", []), ("ids", [True]), ("ids", [23, 23]), ("protocol", True)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                check.validate_remove_snapshot({**receipt, key:value}, raw)
        for malformed in (b"", b"\0shortcuts\0\x08", b"\0shortcuts\0\x08\x08junk"):
            with self.assertRaises(ValueError): check.validate_remove_snapshot(receipt, malformed)

    def test_remove_account_root_and_file_changes_stop_verification(self):
        check.mirror.save(str(self.file), self.entries(12, 23))
        backup = check.prepare_remove("/games/23.app")
        check.mirror.save(str(self.file), self.entries(12))
        self.login((43,))
        with self.assertRaises(ValueError): check.verify_remove(backup)
        self.login()
        with patch.dict("os.environ", {"SN_STEAM_ACCOUNT": "43"}):
            with self.assertRaises(ValueError): check.verify_remove(backup)
        other = self.root / "other-steam"
        other.mkdir()
        with patch.object(check, "STEAM", other):
            with self.assertRaises(ValueError): check.verify_remove(backup)

    def test_remove_cli_retries_only_reads_and_reports_nonzero_failure(self):
        with patch.object(sys, "argv", ["check", "verify-remove", "receipt"]), \
                patch.object(check, "verify_remove", side_effect=[ValueError("pending"), None]) as verify, \
                patch.object(check.time, "sleep"):
            self.assertEqual(check.main(), 0)
        self.assertEqual(verify.call_count, 2)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["check", "verify-remove", "receipt"]), \
                patch.object(check, "verify_remove", side_effect=ValueError("still present")) as verify, \
                patch.object(check.time, "sleep"), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(check.main(), 1)
        self.assertEqual(verify.call_count, 11)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("still present", stderr.getvalue())

    def test_remove_duplicate_id_same_path_still_requires_every_entry_gone(self):
        entries = self.entries(12, 23, 23)
        check.mirror.save(str(self.file), entries)
        backup = check.prepare_remove("/games/23.app")
        self.assertEqual(json.loads(backup.read_text())["ids"], [23])
        check.mirror.save(str(self.file), entries[:2])
        with self.assertRaises(ValueError): check.verify_remove(backup)
        check.mirror.save(str(self.file), entries[:1])
        check.verify_remove(backup)

    def test_remove_last_target_may_leave_an_empty_or_absent_vdf(self):
        check.mirror.save(str(self.file), self.entries(23))
        backup = check.prepare_remove("/games/23.app")
        check.mirror.save(str(self.file), [])
        check.verify_remove(backup)
        self.file.unlink()
        check.verify_remove(backup)

    def test_remove_steam_root_replaced_at_same_path_is_rejected(self):
        check.mirror.save(str(self.file), self.entries(12, 23))
        backup = check.prepare_remove("/games/23.app")
        self.steam.rename(self.root / "previous-steam")
        self.cfg.mkdir(parents=True)
        (self.steam / "config").mkdir()
        self.login()
        check.mirror.save(str(self.file), self.entries(12))
        self.assertEqual(check.mirror.active_account(), "42")
        self.assertEqual(json.loads(backup.read_text())["steam"], str(self.steam.resolve()))
        with self.assertRaisesRegex(ValueError, "Steam 根发生变化"):
            check.verify_remove(backup)

    def test_remove_prepare_and_verify_detect_changes_during_observation(self):
        check.mirror.save(str(self.file), self.entries(12, 23))
        write_backup = check.write_backup
        def changing_backup(receipt, prefix):
            backup = write_backup(receipt, prefix)
            check.mirror.save(str(self.file), self.entries(12, 23, 41))
            return backup
        with patch.object(check, "write_backup", side_effect=changing_backup):
            with self.assertRaisesRegex(ValueError, "备份后.*变化"):
                check.prepare_remove("/games/23.app")
        backups = list((self.games / "shortcut-backups").glob("before-remove-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(check.parse_exact(base64.b64decode(json.loads(backups[0].read_text())["raw"]))), 2)
        check.mirror.save(str(self.file), self.entries(12, 23))
        backup = check.prepare_remove("/games/23.app")
        check.mirror.save(str(self.file), self.entries(12))
        validate = check.validate_remove_snapshot
        def changing_validation(receipt, raw):
            validate(receipt, raw)
            check.mirror.save(str(self.file), self.entries(12, 41))
        with patch.object(check, "validate_remove_snapshot", side_effect=changing_validation):
            with self.assertRaisesRegex(ValueError, "核对期间.*变化"):
                check.verify_remove(backup)

    def test_remove_invalid_known_field_types_and_id_collision_afterward_fail(self):
        for case in ("id_string", "zero_id", "exe_integer"):
            entries = self.entries(23)
            if case == "id_string": entries[0][2][0] = (1, "appid", "23")
            elif case == "zero_id": self.put(entries[0][2], "appid", 0)
            else: entries[0][2][2] = (2, "Exe", 23)
            check.mirror.save(str(self.file), entries)
            with self.subTest(case=case), self.assertRaises(ValueError):
                check.prepare_remove("/games/23.app")
            self.assertFalse(self.games.exists())
        check.mirror.save(str(self.file), self.entries(12, 23))
        backup = check.prepare_remove("/games/23.app")
        entries = self.entries(12, 23)
        self.put(entries[1][2], "Exe", '"/games/foreign.app"')
        check.mirror.save(str(self.file), entries)
        with self.assertRaises(ValueError): check.verify_remove(backup)

    def test_remove_cli_prepare_prints_only_backup_path(self):
        check.mirror.save(str(self.file), self.entries(23))
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["check", "prepare-remove", "/games/23.app"]), redirect_stdout(stdout):
            self.assertEqual(check.main(), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        receipt = json.loads(Path(lines[0]).read_text())
        self.assertEqual(receipt["action"], "remove")
        self.assertEqual(receipt["ids"], [23])


if __name__ == "__main__":
    unittest.main()
