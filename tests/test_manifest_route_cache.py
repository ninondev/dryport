# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只缓存文件分类；所有样例均在隔离目录内，不读写真实 Steam。"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from manifest_route_cache import ManifestRouteCache
import manifest_route_cache as cache_module


def classify_a(files):
    return "Unity", "unity", "确证", "UnityPlayer.dll"


def classify_b(files):
    return "未识别", "unknown", "无", "no match"


class ManifestRouteCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sn-manifest-cache-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.steam = self.base / "Steam"
        (self.steam / "depotcache").mkdir(parents=True)
        self.root = self.base / "metadata/manifest-cache"
        self.rules = self.base / "classifier.py"
        self.rules.write_text("classifier version one")
        self.module = types.SimpleNamespace(classify=classify_a, RULES=[("Unity", "unity", "unity", "确证")],
                                            COMPILED=[("Unity", re.compile("unity", re.I), "unity", "确证")])
        self.depots = [{"depot": "43", "gid": "123"}, {"depot": "44", "gid": None}]
        self.manifest = self.steam / "depotcache/43_123.manifest.txt"
        self.manifest.write_text("UnityPlayer.dll")
        self.classification = classify_a([])
        self.cache = self.new_cache()

    def new_cache(self, **kwargs):
        return ManifestRouteCache(self.steam, self.root, classifier_path=self.rules,
                                  classifier_module=self.module, **kwargs)

    def store(self, **kwargs):
        return self.cache.store(self.depots, self.classification, 1, 1, **kwargs)

    def entry(self):
        return next(path for path in self.root.glob("*.json") if not path.name.startswith("."))

    def test_default_read_creates_nothing_and_store_roundtrips_only_classification(self):
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertFalse(self.root.parent.exists())
        self.assertTrue(self.store(expected=self.cache.fingerprint(self.depots)))
        self.assertEqual(self.cache.lookup(self.depots), {"classification": self.classification,
                                                        "files": 1, "found": 1, "total": 2})
        record = json.loads(self.entry().read_text())
        self.assertEqual(set(record), {"fingerprint", "result", "content_sha256"})
        self.assertEqual(record["fingerprint"]["protocol"], 2)
        self.assertFalse({"account", "owned", "risk", "appid"} & set(record["result"]))

    def test_depot_gid_order_and_missing_metadata_participate_in_key(self):
        self.assertTrue(self.store())
        for depots in (self.depots[::-1], self.depots[:1], self.depots + [self.depots[0]],
                       [{"depot": "43", "gid": "124"}, self.depots[1]],
                       [self.depots[0], {"depot": "44", "gid": "999"}]):
            with self.subTest(depots=depots):
                self.assertIsNone(self.cache.lookup(depots))

    def test_missing_manifest_appearing_invalidates_cached_absence(self):
        depots = [{"depot": "88", "gid": "99"}]
        self.assertTrue(self.cache.store(depots, classify_b([]), 0, 0))
        self.assertIsNotNone(self.cache.lookup(depots))
        (self.steam / "depotcache/88_99.manifest.txt").write_text("new manifest")
        self.assertIsNone(self.cache.lookup(depots))

    def test_size_mtime_ctime_and_replacement_invalidate(self):
        for change in ("size", "mtime", "ctime", "replace", "remove"):
            with self.subTest(change=change):
                self.manifest.write_text("UnityPlayer.dll")
                self.assertTrue(self.store())
                before = self.manifest.stat()
                if change == "size":
                    self.manifest.write_text("different and longer")
                elif change == "mtime":
                    os.utime(self.manifest, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                elif change == "ctime":
                    self.manifest.write_text("UNITYPLAYER.DLL")
                    os.utime(self.manifest, ns=(before.st_atime_ns, before.st_mtime_ns))
                elif change == "replace":
                    replacement = self.manifest.with_suffix(".new")
                    replacement.write_bytes(self.manifest.read_bytes())
                    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                    replacement.replace(self.manifest)
                else:
                    self.manifest.unlink()
                self.assertIsNone(self.cache.lookup(self.depots))

    def test_manifest_link_target_identity_is_checked(self):
        self.manifest.unlink()
        target = self.base / "actual-manifest"
        target.write_text("UnityPlayer.dll")
        self.manifest.symlink_to(target)
        self.assertTrue(self.store())
        target.write_text("OtherEngine.dll")
        self.assertIsNone(self.cache.lookup(self.depots))

    def test_plain_manifest_avoids_resolve_and_redundant_target_stat(self):
        original_stat = Path.stat
        followed_stats = []
        def observe(path, *args, **kwargs):
            if path == self.manifest and kwargs.get("follow_symlinks", True):
                followed_stats.append(path)
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "resolve", side_effect=AssertionError("普通清单不必遍历父路径")), \
                patch.object(Path, "stat", observe):
            token = self.cache.fingerprint(self.depots)
        self.assertIsNotNone(token)
        self.assertEqual(followed_stats, [])
        self.assertEqual(token["depots"][0]["target"], token["depots"][0]["identity"])

    def test_source_changes_and_loaded_function_or_rules_changes_miss(self):
        self.assertTrue(self.store())
        self.rules.write_text("classifier version two")
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertTrue(self.store())
        self.module.classify = classify_b
        self.assertIsNone(self.new_cache().lookup(self.depots))
        self.module.classify = classify_a
        self.module.COMPILED = [("Unity", re.compile("unity"), "unity", "确证")]
        self.assertIsNone(self.new_cache().lookup(self.depots))

    def test_new_helper_code_invalidates_without_source_change(self):
        self.module.helper = classify_a
        self.cache = self.new_cache()
        self.assertTrue(self.store())
        self.module.helper = classify_b
        self.assertIsNone(self.new_cache().lookup(self.depots))

    def test_real_classifier_cache_roundtrips_across_process_import_orders(self):
        script = '''import json, os, sys
sys.path.insert(0, sys.argv[1])
if sys.argv[4] == 'preload':
    import library_discovery
from manifest_route_cache import ManifestRouteCache
cache = ManifestRouteCache(sys.argv[2], sys.argv[3])
depots = [{"depot":"43","gid":"123"}]
result = cache.lookup(depots)
if sys.argv[5] == 'store':
    assert cache.store(depots, ('Unity','unity','确证','UnityPlayer.dll'), 1, 1)
    result = cache.lookup(depots)
print(json.dumps({'hash':cache._execution_hash, 'result':result}))
'''
        outputs = []
        for order, seed, action in (('direct', '1', 'store'), ('preload', '2', 'lookup'),
                                    ('direct', '3', 'lookup')):
            run = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT / 'lib'),
                                  str(self.steam), str(self.root), order, action],
                                 env={**os.environ, 'PYTHONHASHSEED': seed},
                                 text=True, capture_output=True, check=True)
            outputs.append(json.loads(run.stdout))
        self.assertIsNotNone(outputs[0]['result'])
        self.assertEqual(outputs[1:], outputs[:1] * 2)

    def test_compiled_source_and_pyc_have_same_execution_identity(self):
        source = self.base / 'classifier_fixture.py'
        source.write_text('''def classify(files, suffix=".dll"):
    def relevant(name):
        return name in {"UnityPlayer.dll", "GameAssembly.dll", "nw.dll"}
    return [name for name in files if relevant(name) and name.endswith(suffix)][:80]
''')
        script = '''import importlib.util, json, py_compile, sys, types
sys.path.insert(0, sys.argv[1])
from manifest_route_cache import ManifestRouteCache
path = sys.argv[2]
if sys.argv[3] == 'source':
    module = types.ModuleType('classifier_fixture')
    exec(compile(open(path).read(), path, 'exec'), module.__dict__)
else:
    py_compile.compile(path, doraise=True)
    spec = importlib.util.spec_from_file_location('classifier_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
cache = ManifestRouteCache('/unused', '/unused', classifier_path=path, classifier_module=module)
print(json.dumps({'hash':cache._execution_hash, 'result':module.classify(['nw.dll'])}))
'''
        outputs = []
        for mode, seed in (('source', '1'), ('pyc', '2'), ('source', '3')):
            output = subprocess.check_output([sys.executable, '-B', '-c', script,
                                               str(ROOT / 'lib'), str(source), mode],
                                              env={**os.environ, 'PYTHONHASHSEED': seed}, text=True)
            outputs.append(json.loads(output))
        self.assertEqual(outputs[1:], outputs[:1] * 2)

    def test_changed_function_defaults_and_nested_code_invalidate(self):
        namespace = {}
        source = 'def classify(files, route="unity"):\n    return tuple(route for _ in files)\n'
        exec(compile(source, str(self.rules), 'exec'), namespace)
        self.module.classify = namespace['classify']
        self.cache = self.new_cache()
        self.assertTrue(self.store())
        self.module.classify.__defaults__ = ('wine',)
        self.assertIsNone(self.new_cache().lookup(self.depots))
        self.module.classify.__defaults__ = ('unity',)
        self.assertIsNotNone(self.new_cache().lookup(self.depots))
        exec(compile(source.replace('route for', '"wine" for'), str(self.rules), 'exec'), namespace)
        self.module.classify = namespace['classify']
        self.assertIsNone(self.new_cache().lookup(self.depots))

    def test_slice_start_stop_step_changes_invalidate_and_none_stays_distinct(self):
        def selected(files, selection=slice(None, 80, None)):
            return files[selection]
        self.module.classify = selected
        self.cache = self.new_cache()
        self.assertTrue(self.store())
        for selection in (slice(0, 80, None), slice(None, 81, None), slice(None, 80, -1)):
            with self.subTest(selection=selection):
                selected.__defaults__ = (selection,)
                self.assertIsNone(self.new_cache().lookup(self.depots))
        selected.__defaults__ = (slice(None, 80, None),)
        self.assertIsNotNone(self.new_cache().lookup(self.depots))

    def test_code_identity_uses_supported_line_table_without_deprecation(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error', DeprecationWarning)
            frozen = cache_module._execution_value(classify_a.__code__)
            self.new_cache()
        fields = frozen[1]
        if hasattr(classify_a.__code__, 'co_linetable'):
            self.assertIn('co_linetable', fields)
            self.assertNotIn('co_lnotab', fields)
        else:
            self.assertIn('co_lnotab', fields)

    def test_scan_time_changes_and_missing_expected_token_refuse_store(self):
        token = self.cache.fingerprint(self.depots)
        self.manifest.write_text("changed during scan")
        self.assertFalse(self.store(expected=token))
        self.assertFalse(self.store(expected=None))
        self.assertFalse(self.root.exists())

    def test_bad_entry_is_miss_and_never_overwritten(self):
        self.assertTrue(self.store())
        entry = self.entry()
        for payload in ("broken", "[]", '{"fingerprint":null,"result":{}}'):
            with self.subTest(payload=payload):
                entry.write_text(payload)
                self.assertIsNone(self.cache.lookup(self.depots))
                self.assertFalse(self.store())
                self.assertEqual(entry.read_text(), payload)

    def test_valid_json_route_corruption_is_miss_even_when_fingerprint_matches(self):
        classification = ("EasyAntiCheat", "refuse", "确证", "easyanticheat/launcher.exe")
        self.assertTrue(self.cache.store(self.depots, classification, 1, 1))
        entry = self.entry()
        record = json.loads(entry.read_text())
        record["result"]["classification"][1] = "nwjs"
        entry.write_text(json.dumps(record))
        damaged = entry.read_bytes()
        self.assertEqual(record["fingerprint"], self.cache.fingerprint(self.depots))
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertFalse(self.cache.store(self.depots, classification, 1, 1))
        self.assertEqual(entry.read_bytes(), damaged)

    def test_missing_bad_digest_or_modified_counts_do_not_confirm_cache(self):
        self.assertTrue(self.store())
        entry = self.entry()
        original = json.loads(entry.read_text())
        for modification in ("missing", "wrong-digest", "invalid-digest-type", "files", "found"):
            with self.subTest(modification=modification):
                record = json.loads(json.dumps(original))
                if modification == "missing":
                    del record["content_sha256"]
                elif modification == "wrong-digest":
                    record["content_sha256"] = "0" * 64
                elif modification == "invalid-digest-type":
                    record["content_sha256"] = [original["content_sha256"]]
                else:
                    record["result"][modification] += 1
                entry.write_text(json.dumps(record))
                self.assertIsNone(self.cache.lookup(self.depots))

    def test_content_digest_ignores_json_whitespace_order_and_unicode_escaping(self):
        self.assertTrue(self.store())
        entry = self.entry()
        record = json.loads(entry.read_text())
        record = dict(reversed(list(record.items())))
        record["result"] = dict(reversed(list(record["result"].items())))
        entry.write_text(json.dumps(record, ensure_ascii=True, indent=4))
        self.assertEqual(self.cache.lookup(self.depots)["classification"], self.classification)
        self.assertTrue(self.store())

    def test_protocol_one_entry_is_preserved_while_new_key_can_be_published(self):
        self.root.mkdir(parents=True)
        owner = self.root / cache_module._OWNER
        owner.write_text(json.dumps(cache_module._IDENTITY))
        owner_before = owner.read_bytes()
        legacy_fingerprint = {**self.cache.fingerprint(self.depots), "protocol": 1}
        for depot in legacy_fingerprint["depots"]:
            if depot["path"] is not None:
                depot["resolved"] = str(Path(depot["path"]).resolve())
        legacy_key = hashlib.sha256(cache_module._json(legacy_fingerprint)).hexdigest() + ".json"
        legacy_entry = self.root / legacy_key
        legacy_entry.write_text(json.dumps({"fingerprint": legacy_fingerprint,
                                "result": {"classification": self.classification, "files": 1, "found": 1, "total": 2}}))
        before = legacy_entry.read_bytes()
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertTrue(self.store(expected=self.cache.fingerprint(self.depots)))
        self.assertEqual(self.cache.lookup(self.depots)["classification"], self.classification)
        self.assertEqual(legacy_entry.read_bytes(), before)
        self.assertEqual(owner.read_bytes(), owner_before)
        self.assertEqual(json.loads(owner.read_text())["protocol"], 1)
        self.assertEqual(len([p for p in self.root.glob("*.json") if not p.name.startswith(".")]), 2)

    def test_foreign_directory_and_marker_link_are_not_taken_over(self):
        self.root.mkdir(parents=True)
        user = self.root / "user-file"
        user.write_text("keep")
        self.assertFalse(self.store())
        self.assertEqual(list(self.root.iterdir()), [user])
        target = self.base / "marker-target"
        target.write_text(json.dumps(cache_module._IDENTITY))
        (self.root / cache_module._OWNER).symlink_to(target)
        self.assertFalse(self.store())
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertEqual(user.read_text(), "keep")

    def test_cache_root_and_parent_links_are_not_followed(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        self.root.parent.symlink_to(foreign)
        self.assertFalse(self.store())
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertEqual(list(foreign.iterdir()), [])
        self.root.parent.unlink()
        self.root.parent.mkdir()
        self.root.symlink_to(foreign)
        self.assertFalse(self.store())
        self.assertEqual(list(foreign.iterdir()), [])

    def test_entry_link_does_not_read_or_overwrite_external_file(self):
        self.assertTrue(self.store())
        entry = self.entry()
        outside = self.base / "external-user-file"
        outside.write_text(entry.read_text())
        entry.unlink()
        entry.symlink_to(outside)
        before = outside.read_bytes()
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertFalse(self.store())
        self.assertEqual(outside.read_bytes(), before)

    def test_atomic_entry_publication_failure_keeps_existing_directory(self):
        self.assertTrue(self.store())
        self.manifest.write_text("new identity")
        with patch.object(cache_module.os, "link", side_effect=OSError("fixture disk failure")):
            self.assertFalse(self.store())
        self.assertIsNone(self.cache.lookup(self.depots))
        self.assertFalse(any(path.name.startswith(".entry-") for path in self.root.iterdir()))

    def test_invalid_depot_gid_and_result_raise_without_creating_cache(self):
        for depots in ([{"depot": "../43", "gid": "1"}], [{"depot": "43", "gid": "../1"}],
                       [{"depot": True, "gid": "1"}], [{"depot": "43", "gid": str(2**64)}]):
            with self.subTest(depots=depots), self.assertRaises(ValueError):
                self.cache.lookup(depots)
        with self.assertRaises(ValueError):
            self.cache.store(self.depots, self.classification, 1, 3)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
