# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Compile only our savebridge against a synthetic, directory-backed Cloud API."""
import hashlib
import json
import os
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc")


class SaveLauncherTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-save-launcher-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.app = self.base / "Fixture.app"
        self.macos = self.app / "Contents/MacOS"
        self.macos.mkdir(parents=True)
        (self.macos / "fixture").write_text("#!/bin/sh\nexit 0\n")
        (self.macos / "fixture").chmod(0o755)
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "fixture", "CFBundleIdentifier": "local.save.fixture"}))
        self.tool = self.base / "installer"
        self.tool.mkdir()
        shutil.copyfile(ROOT / "lib/savebridge/install-into-app.sh", self.tool / "install.sh")
        shutil.copyfile(ROOT / "lib/savebridge/settings.py", self.tool / "settings.py")
        shutil.copyfile(ROOT / "lib/bundle_safety.py", self.base / "bundle_safety.py")
        (self.tool / "savebridge").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$2" >> "$SAVE_MOCK_LOG"\n')
        (self.tool / "savebridge").chmod(0o755)
        self.saves = self.base / "saves"
        self.saves.mkdir()
        self.log = self.base / "mock-calls"

    def install(self, prefix="", explicit=True, pattern="*.sav"):
        result = subprocess.run(["/bin/bash", str(self.tool / "install.sh"), str(self.app),
                                 "480", str(prefix), str(self.saves) if explicit else "", pattern],
                                env={**os.environ, **getattr(self, "env", {}), "HOME": str(self.base),
                                     "SN_GAMES": str(self.base / "Games")},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_successful_install_without_pattern_returns_zero(self):
        self.install(pattern="")

    def test_cloud_sync_is_off_until_this_game_persistently_enables_it(self):
        self.install()
        env = {**os.environ, "HOME": str(self.base), "SN_GAMES": str(self.base / "Games"),
               "SAVE_MOCK_LOG": str(self.log)}
        env.pop("SN_SAVEBRIDGE", None)
        launcher = self.macos / "launcher"
        result = subprocess.run(["/bin/bash", str(launcher)], env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists(), "Installing the hook must not enable Cloud")
        result = subprocess.run([sys.executable, str(ROOT / "lib/savebridge/settings.py"), "enable", "480", "--yes"],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(["/bin/bash", str(launcher)],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["pull", "push"])

    def test_prefix_migration_preserves_differing_original_save(self):
        resources = self.app / "Contents/Resources/Data"
        resources.mkdir(parents=True)
        (resources / "app.info").write_text("FixtureCompany\nFixtureProduct\n")
        prefix = self.base / "prefix"
        original = prefix / "drive_c/users/fixture/AppData/LocalLow/FixtureCompany/FixtureProduct"
        original.mkdir(parents=True)
        (original / "slot.sav").write_bytes(b"wine progress")
        self.saves = self.base / "Library/Application Support/FixtureCompany/FixtureProduct"
        self.saves.mkdir(parents=True)
        (self.saves / "slot.sav").write_bytes(b"native progress")
        self.install(prefix, explicit=False)
        self.assertEqual((self.saves / "slot.sav").read_bytes(), b"native progress")
        backups = list(original.parent.glob("FixtureProduct.savebridge-backup.*/original/slot.sav"))
        self.assertEqual(len(backups), 1, "The differing Wine save must have a recovery copy")
        self.assertEqual(backups[0].read_bytes(), b"wine progress")

MOCK = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
typedef void *H;
static const char *names[] = {".savebridge.json", "slot.sav"};
static void path(char *out, const char *name) { snprintf(out, 2048, "%s/%s", getenv("SAVE_MOCK_CLOUD"), name); }
int SteamAPI_Init(void) { return 1; }
void SteamAPI_Shutdown(void) {}
void SteamAPI_RunCallbacks(void) {}
H SteamAPI_SteamRemoteStorage_v016(void) { return (H)1; }
int32_t SteamAPI_ISteamRemoteStorage_GetFileCount(H h) { return 2; }
int32_t SteamAPI_ISteamRemoteStorage_GetFileSize(H h, const char *name) {
    char p[2048]; struct stat st; path(p, name); return stat(p, &st) ? 0 : (int32_t)st.st_size;
}
const char *SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(H h, int32_t i, int32_t *size) {
    const char *name = i == 1 && getenv("SAVE_MOCK_NAME") ? getenv("SAVE_MOCK_NAME") : names[i];
    *size = SteamAPI_ISteamRemoteStorage_GetFileSize(h, name); return name;
}
int64_t SteamAPI_ISteamRemoteStorage_GetFileTimestamp(H h, const char *name) { return 0; }
int SteamAPI_ISteamRemoteStorage_FileExists(H h, const char *name) { char p[2048]; struct stat st; path(p,name); return stat(p,&st)==0; }
int SteamAPI_ISteamRemoteStorage_FileWrite(H h, const char *name, const void *data, int32_t n) {
    char p[2048]; path(p,name); FILE *f=fopen(p,"wb"); if(!f) return 0;
    int ok=fwrite(data,1,n,f)==n; fclose(f); return ok;
}
int32_t SteamAPI_ISteamRemoteStorage_FileRead(H h, const char *name, void *data, int32_t n) {
    char p[2048]; path(p,name); FILE *f=fopen(p,"rb"); if(!f) return 0;
    int32_t got=(int32_t)fread(data,1,n,f); fclose(f); return got;
}
int SteamAPI_ISteamRemoteStorage_FileDelete(H h, const char *name) { return 0; }
int SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount(H h) { return 1; }
int SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp(H h) { return 1; }
int SteamAPI_ISteamRemoteStorage_GetQuota(H h, uint64_t *total, uint64_t *available) { *total=*available=1000000; return 1; }
int SteamAPI_ISteamRemoteStorage_SetSyncPlatforms(H h, const char *name, uint32_t mask) { return 1; }
'''


@unittest.skipUnless(CC and sys.platform == "darwin", "savebridge requires the macOS C toolchain")
class SaveConflictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="sn-save-conflict-")
        cls.base = Path(cls.tmp.name)
        cls.executable = cls.base / "savebridge"
        mock = cls.base / "mock.c"
        mock.write_text(MOCK)
        result = subprocess.run([CC, str(ROOT / "lib/savebridge/savebridge.c"), str(mock),
                                 "-o", str(cls.executable)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            cls.tmp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_first_pull_and_repeated_unresolved_pull_keep_both_versions(self):
        with tempfile.TemporaryDirectory(prefix="sn-save-fixture-") as tmp:
            base = Path(tmp)
            local = base / "local"
            cloud = base / "cloud"
            local.mkdir()
            cloud.mkdir()
            (local / "slot.sav").write_bytes(b"local progress")
            (cloud / "slot.sav").write_bytes(b"cloud progress")
            (cloud / ".savebridge.json").write_text(json.dumps({
                "slot.sav": {"sha": hashlib.sha1(b"cloud progress").hexdigest(), "size": 14, "mtime": 0}},
                separators=(",", ":")))
            env = {**os.environ, "SAVE_MOCK_CLOUD": str(cloud), "SN_SAVEBRIDGE": "1"}
            for attempt in range(2):
                result = subprocess.run([str(self.executable), "480", "pull", str(local)],
                                        env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertEqual((local / "slot.sav").read_bytes(), b"local progress")
                copies = list(local.glob("slot.sav.cloud-*"))
                self.assertEqual(len(copies), attempt + 1)
                self.assertTrue(all(p.read_bytes() == b"cloud progress" for p in copies))
                self.assertEqual((cloud / "slot.sav").read_bytes(), b"cloud progress")

    def test_identical_first_pull_is_inert(self):
        with tempfile.TemporaryDirectory(prefix="sn-save-identical-") as tmp:
            base = Path(tmp)
            local = base / "local"
            cloud = base / "cloud"
            local.mkdir()
            cloud.mkdir()
            for folder in (local, cloud):
                (folder / "slot.sav").write_bytes(b"same progress")
            (cloud / ".savebridge.json").write_text(json.dumps({
                "slot.sav": {"sha": hashlib.sha1(b"same progress").hexdigest(), "size": 13, "mtime": 0}},
                separators=(",", ":")))
            result = subprocess.run([str(self.executable), "480", "pull", str(local)],
                                    env={**os.environ, "SAVE_MOCK_CLOUD": str(cloud), "SN_SAVEBRIDGE": "1"},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((local / "slot.sav").read_bytes(), b"same progress")
            self.assertEqual(list(local.glob("slot.sav.cloud-*")), [])

    def test_pull_never_writes_files_excluded_from_local_scan(self):
        for name, pattern in (("slot.sav", "*.other"), (".hidden.sav", "*"),
                              ("trace.log", "*"), ("../outside.sav", "*"),
                              ("slot.sav.cloud-old", "*")):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="sn-save-scope-") as tmp:
                base = Path(tmp)
                local, cloud = base / "local", base / "remote/cloud"
                local.mkdir(); cloud.mkdir(parents=True)
                target = local / name
                target.write_bytes(b"local original")
                cloud_target = cloud / name
                cloud_target.write_bytes(b"cloud candidate")
                original = target.read_bytes()
                (cloud / ".savebridge.json").write_text(json.dumps({name: {
                    "sha": hashlib.sha1(cloud_target.read_bytes()).hexdigest(), "size": cloud_target.stat().st_size,
                    "mtime": 0}}, separators=(",", ":")))
                result = subprocess.run([str(self.executable), "480", "pull", str(local), pattern],
                                        env={**os.environ, "SAVE_MOCK_CLOUD": str(cloud),
                                             "SAVE_MOCK_NAME": name, "SN_SAVEBRIDGE": "1"},
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(target.read_bytes(), original)
                self.assertIn("拉下 0", result.stdout)
                self.assertNotIn("拉下 " + name + " (", result.stdout)
                self.assertEqual(list(local.glob("*.cloud-*")), [target] if ".cloud-" in name else [])
