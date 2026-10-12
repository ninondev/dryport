# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""NW.js 的原文件保留、输出归属和运行数据隔离回归；不启动真实游戏。"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "lib" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BUILD = module("nwjs-build")
LAUNCH = module("nwjs-launch")
RUNTIME = os.environ.get("NWJS_TEST_APP")


class NWJSPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sn-nwjs-path-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.game = self.base / "game"
        self.game.mkdir()
        (self.game / "package.json").write_text('{"main":"index.html"}')
        (self.game / "index.html").write_text("original HTML")

    @unittest.skipUnless(sys.platform == "darwin", "需要 macOS renamex_np")
    def test_exclusive_publish_preserves_existing_empty_directory(self):
        stage = self.base / "stage.app"
        stage.mkdir()
        (stage / "new").write_text("new")
        target = self.base / "existing.app"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            BUILD.publish(stage, target)
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue((stage / "new").exists())

    @unittest.skipUnless(sys.platform == "darwin", "需要 macOS renamex_np")
    def test_exclusive_publish_preserves_foreign_symlink(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "user").write_text("keep")
        target = self.base / "existing.app"
        target.symlink_to(foreign)
        stage = self.base / "stage.app"
        stage.mkdir()
        with self.assertRaises(FileExistsError):
            BUILD.publish(stage, target)
        self.assertTrue(target.is_symlink())
        self.assertEqual((foreign / "user").read_text(), "keep")

    def test_inventory_rejects_external_links(self):
        outside = self.base / "user-secret"
        outside.write_text("keep private")
        (self.game / "external").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "目录外"):
            BUILD.inventory(self.game)

    @unittest.skipUnless(sys.platform == "darwin", "需要 macOS sandbox-exec")
    def test_sandbox_blocks_ancestor_rename_before_alias_write(self):
        source = self.base / "source-parent/source"
        app = self.base / "bundle-parent/Game.app"
        state = self.base / "state"
        for directory in (source, app, state):
            directory.mkdir(parents=True)
        for directory in (source, app):
            (directory / "protected.txt").write_text("original")
        probe = self.base / "ancestor-probe.py"
        probe.write_text('''from pathlib import Path
import json,sys
root=Path(sys.argv[1]); result={}
for label,relative in [('source','source-parent/source'),('bundle','bundle-parent/Game.app')]:
    original=root/relative; entry={}
    try: (original/'protected.txt').write_bytes(b'DIRECT WRITE'); entry['direct']='allowed'
    except OSError as error: entry['direct']=error.errno
    renamed=root/(label+'-moved')
    try: original.parent.rename(renamed); entry['rename']='allowed'
    except OSError as error: entry['rename']=error.errno
    if renamed.exists():
        try: (renamed/original.name/'protected.txt').write_bytes(b'ALIAS WRITE'); entry['alias']='allowed'
        except OSError as error: entry['alias']=error.errno
    result[label]=entry
(root/'state/result.json').write_text(json.dumps(result))
''')
        args = LAUNCH.sandbox_prefix(app, source, state, self.base, ROOT / "lib/nwjs-runtime.sb")
        result = subprocess.run(args + [sys.executable, str(probe), str(self.base)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads((state / "result.json").read_text())
        self.assertEqual(observed, {"source": {"direct": 1, "rename": 1}, "bundle": {"direct": 1, "rename": 1}})
        for directory in (source, app):
            self.assertEqual((directory / "protected.txt").read_text(), "original")

    @unittest.skipUnless(sys.platform == "darwin", "打包入口仅支持 macOS")
    def test_existing_output_is_rejected_before_runtime_lookup(self):
        output = self.base / "existing.app"
        output.mkdir()
        (output / "user").write_text("keep")
        with self.assertRaisesRegex(ValueError, "输出已存在"):
            BUILD.build(self.game, "fixture", output, "42")
        self.assertEqual((output / "user").read_text(), "keep")

    @unittest.skipUnless(sys.platform == "darwin", "打包入口仅支持 macOS")
    def test_existing_saves_are_not_copied(self):
        save = self.game / "save"
        save.mkdir()
        (save / "slot.rpgsave").write_bytes(b"user save")
        before = BUILD.inventory(self.game)
        with self.assertRaisesRegex(ValueError, "既有存档"):
            BUILD.build(self.game, "fixture", self.base / "new.app", "42")
        self.assertEqual(BUILD.inventory(self.game), before)
        self.assertFalse((self.base / "new.app").exists())


@unittest.skipUnless(sys.platform == "darwin" and RUNTIME,
                     "真实 NW.js 机制测试需显式设置 NWJS_TEST_APP，避免自动下载")
class NWJSRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sn-nwjs-runtime-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.game = self.base / "原游戏 with spaces"
        (self.game / "www").mkdir(parents=True)
        self.package = b'{\r\n "name":"original game", "main":"www/index.html", "window":{"width":816,"height":624,"fullscreen":true}\r\n}'
        (self.game / "package.json").write_bytes(self.package)
        (self.game / "Windows.exe").write_bytes(b"MZ original executable")
        (self.game / "module.node").write_bytes(b"MZ original native module")
        (self.game / ".dryport-nwjs-view.json").write_bytes(b"original game file with reserved-looking name")
        (self.game / "www/data.json").write_text('{"source":"original"}')
        (self.game / "www/index.html").write_text("<!doctype html><title>fixture</title>")
        self.app = self.base / "Generated.app"
        self.state_root = self.base / "new state"
        self.env = {**os.environ, "NWJS_APP": RUNTIME, "NWJS_STATE_ROOT": str(self.state_root)}

    def build(self):
        before = BUILD.inventory(self.game)
        with patch.dict(os.environ, self.env):
            BUILD.build(self.game, "Fixture", self.app, "42")
        self.assertEqual(BUILD.inventory(self.game), before)
        self.assertEqual(BUILD.inventory(self.app / "Contents/Resources/OriginalGame"), before)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(self.app)],
                       check=True, capture_output=True, timeout=10)
        return before

    def prepare(self):
        with patch.dict(os.environ, self.env):
            return LAUNCH.prepare(self.app)

    def test_manifest_is_separate_and_every_original_byte_is_preserved(self):
        self.build()
        _, _, state, view = self.prepare()
        self.assertEqual((view / ".dryport-nwjs-view.json").read_bytes(),
                         b"original game file with reserved-looking name")
        self.assertEqual(BUILD.inventory(self.app / "Contents/Resources/OriginalGame"), BUILD.inventory(self.game))
        self.assertEqual((self.game / "package.json").read_bytes(), self.package)
        manifest = json.loads((view / "package.json").read_text())
        self.assertFalse(manifest["window"]["fullscreen"])
        self.assertEqual(manifest["window"]["width"], 816)
        self.assertTrue((view / "Windows.exe").is_symlink())
        self.assertTrue((view / "module.node").is_symlink())
        (state / "saves/www/save/user-save").write_text("saved by this test")
        self.prepare()
        self.assertEqual((view / "www/save/user-save").read_text(), "saved by this test")
        self.assertFalse((self.game / "www/save").exists())
        (view / "save/user-save").write_text("different root save")
        self.assertEqual((view / "www/save/user-save").read_text(), "saved by this test")

    def test_verified_depot_seed_initializes_only_new_independent_state(self):
        seed = self.game / "www/save"
        seed.mkdir()
        (seed / "initial.rpgsave").write_bytes(b"factory initial save")
        steam = self.base / "Steam"
        depot = steam / "steamapps/content/app_42/depot_43/www/save"
        depot.mkdir(parents=True)
        (depot / "initial.rpgsave").write_bytes(b"factory initial save")
        with patch.object(BUILD, "STEAM", steam):
            self.build()
        _, _, state, view = self.prepare()
        self.assertEqual((view / "www/save/initial.rpgsave").read_bytes(), b"factory initial save")
        self.assertFalse((view / "save/initial.rpgsave").exists())
        (view / "www/save/initial.rpgsave").write_bytes(b"user progress")
        self.prepare()
        self.assertEqual((view / "www/save/initial.rpgsave").read_bytes(), b"user progress")
        self.assertEqual((self.game / "www/save/initial.rpgsave").read_bytes(), b"factory initial save")
        self.assertEqual((self.app / "Contents/Resources/OriginalGame/www/save/initial.rpgsave").read_bytes(),
                         b"factory initial save")

    def test_foreign_state_directory_is_preserved(self):
        self.build()
        foreign = self.state_root / "42"
        foreign.mkdir(parents=True)
        (foreign / "user").write_text("keep")
        with self.assertRaisesRegex(ValueError, "归属无法确认"):
            self.prepare()
        self.assertEqual(list(foreign.iterdir()), [foreign / "user"])

    def test_state_symlink_into_original_is_rejected_before_writing(self):
        before = self.build()
        self.state_root.mkdir()
        (self.state_root / "42").symlink_to(self.game)
        with self.assertRaisesRegex(ValueError, "不能位于"):
            self.prepare()
        self.assertEqual(BUILD.inventory(self.game), before)

    def test_old_flat_save_layout_stops_without_hiding_user_progress(self):
        self.build()
        state = self.state_root / "42"
        (state / "saves").mkdir(parents=True)
        (state / ".dryport-nwjs-state.json").write_text(json.dumps({"protocol": 1, "appid": "42"}))
        (state / "saves/progress.rpgsave").write_bytes(b"existing user progress")
        before = BUILD.inventory(state)
        with self.assertRaisesRegex(ValueError, "旧格式存档"):
            self.prepare()
        self.assertEqual(BUILD.inventory(state), before)

    def test_interruption_before_view_commit_can_retry_with_existing_saves(self):
        before = self.build()
        publish = LAUNCH.publish_new
        def interrupt_view(source, destination):
            if destination.parent.name == "views":
                self.assertTrue((source / "receipt.json").is_file())
                self.assertTrue((source / "app/package.json").is_file())
                raise OSError("fixture interrupted before commit")
            publish(source, destination)
        with patch.object(LAUNCH, "publish_new", interrupt_view):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.prepare()
        state = self.state_root / "42"
        self.assertFalse(any(p.name[0] != "." for p in (state / "views").iterdir()))
        (state / "saves/www/save/progress.rpgsave").write_bytes(b"existing progress")
        _, _, _, view = self.prepare()
        self.assertTrue((view.parent / "receipt.json").is_file())
        self.assertEqual((view / "www/save/progress.rpgsave").read_bytes(), b"existing progress")
        self.assertEqual(BUILD.inventory(self.game), before)

    def test_view_commit_preserves_concurrently_created_target(self):
        self.build()
        publish = LAUNCH.publish_new
        target = None
        def competing_view(source, destination):
            nonlocal target
            if destination.parent.name == "views":
                destination.mkdir()
                (destination / "user-file").write_bytes(b"keep foreign target")
                target = destination
            publish(source, destination)
        with patch.object(LAUNCH, "publish_new", competing_view):
            with self.assertRaises(FileExistsError):
                self.prepare()
        self.assertEqual((target / "user-file").read_bytes(), b"keep foreign target")
        self.assertEqual(list(target.iterdir()), [target / "user-file"])

    def test_hidden_runtime_reads_original_saves_separately_and_denies_original_writes(self):
        (self.game / "www/index.html").write_text('''<!doctype html><script>
const fs=require('fs'),path=require('path');
const report={main:process.mainModule.filename,href:location.href,arch:process.arch};
window.addEventListener('error',e=>fs.writeFileSync(process.env.SN_TEST_REPORT,JSON.stringify({...report,error:String(e.message)})));
fs.writeFileSync(process.env.SN_TEST_REPORT,JSON.stringify({...report,phase:'entered'}));
fs.writeFileSync(path.join(path.dirname(process.mainModule.filename),'save/slot.txt'),'new save');
for (const key of ['SN_TEST_SOURCE','SN_TEST_ORIGINAL','SN_TEST_FOREIGN']) {
  try {fs.writeFileSync(process.env[key],'damage');report[key]='write allowed'}
  catch(e){report[key]=e.code}
}
try {fs.linkSync(process.env.SN_TEST_ORIGINAL,path.join(process.cwd(),'hardlink.txt'));report.hardlink='allowed'}
catch(e){report.hardlink=e.code}
try {fs.symlinkSync(process.env.SN_TEST_ORIGINAL,path.join(process.cwd(),'alias.txt'));fs.writeFileSync(path.join(process.cwd(),'alias.txt'),'damage');report.alias='write allowed'}
catch(e){report.alias=e.code}
const request=new XMLHttpRequest();request.open('GET','data.json');
request.onload=()=>{report.xhr=request.responseText;fs.writeFileSync(process.env.SN_TEST_REPORT,JSON.stringify(report));nw.App.quit()};
request.onerror=()=>{fs.writeFileSync(process.env.SN_TEST_REPORT,JSON.stringify({error:'XHR failed'}));nw.App.quit()};request.send();
</script>''')
        before = self.build()
        _, _, state, view = self.prepare()
        # 测试只改项目生成的独立 manifest，不改游戏，也不修改已签名 app。
        manifest = json.loads((view / "package.json").read_text())
        manifest["window"].update({"show": False, "show_in_taskbar": False})
        (view / "package.json").write_text(json.dumps(manifest))
        foreign_base = tempfile.TemporaryDirectory(prefix="sn-nw-write-deny-", dir=Path.home())
        self.addCleanup(foreign_base.cleanup)
        foreign = Path(foreign_base.name) / "user-file"
        foreign.write_text("keep")
        report_path = state / "probe.json"
        env = {**self.env, "SN_TEST_SOURCE": str(self.game / "www/data.json"),
               "SN_TEST_ORIGINAL": str(self.app / "Contents/Resources/OriginalGame/www/data.json"),
               "SN_TEST_FOREIGN": str(foreign), "SN_TEST_REPORT": str(report_path)}
        with (self.base / "probe.log").open("w") as log:
            proc = subprocess.Popen([str(self.app / "Contents/MacOS/launcher")], env=env,
                                    stdout=log, stderr=log, start_new_session=True)
            try:
                # 新签名脚本的首轮 macOS dyld 校验实测可超过 12 秒；独立于 NW.js 执行。
                proc.wait(timeout=35)
            except subprocess.TimeoutExpired:
                self.fail("NW.js 探针超时：" + (report_path.read_text() if report_path.exists() else "无报告")
                          + "\n" + (self.base / "probe.log").read_text())
            finally:
                # 只清理本次测试独立进程组；不匹配或终止任何已有游戏。
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
        self.assertEqual(proc.returncode, 0, (self.base / "probe.log").read_text())
        self.assertTrue(report_path.exists(), (self.base / "probe.log").read_text())
        report = json.loads(report_path.read_text())
        self.assertEqual(report["arch"], "arm64")
        self.assertTrue(report["href"].startswith("chrome-extension://"))
        self.assertEqual(report["main"], str(view / "www/index.html"))
        self.assertEqual(json.loads(report["xhr"]), {"source": "original"})
        for key in ("SN_TEST_SOURCE", "SN_TEST_ORIGINAL", "SN_TEST_FOREIGN"):
            self.assertEqual(report[key], "EPERM", report)
        self.assertEqual(report["hardlink"], "EPERM", report)
        self.assertEqual(report["alias"], "EPERM", report)
        self.assertEqual((state / "saves/www/save/slot.txt").read_text(), "new save")
        self.assertEqual(foreign.read_text(), "keep")
        self.assertEqual(BUILD.inventory(self.game), before)
        self.assertEqual(BUILD.inventory(self.app / "Contents/Resources/OriginalGame"), before)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(self.app)],
                       check=True, capture_output=True, timeout=10)


class NwjsMainNormalizationTests(unittest.TestCase):
    """Tyrano Builder 等把 main 写成 app://./index.html；启动层只认目录内相对路径。"""

    def test_app_scheme_prefix_is_stripped_and_plain_paths_untouched(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nwjs_build", Path(__file__).resolve().parents[1] / "lib" / "nwjs-build.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.assertEqual(mod.normalize_main("app://./index.html"), "index.html")
        self.assertEqual(mod.normalize_main("app://index.html"), "index.html")
        self.assertEqual(mod.normalize_main("app:///www/index.html"), "www/index.html")
        self.assertEqual(mod.normalize_main("app://"), "index.html")
        self.assertEqual(mod.normalize_main("www/index.html"), "www/index.html")
        self.assertEqual(mod.normalize_main("index.html"), "index.html")


if __name__ == "__main__":
    unittest.main()
