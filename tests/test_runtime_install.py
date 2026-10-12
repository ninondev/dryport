# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""安装失败不能变成功、不能覆盖已有内容；用临时目录验证入口行为。"""
import os
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-runtime-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.rt = self.base / "runtime's data"
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_RUNTIMES": str(self.rt),
                    "SN_GAMES": str(self.base / "games"), "STEAM": str(self.base / "steam")}
        # 旧综合入口仍检查 CEF；回归只模拟不可读，不连接真实 Steam。
        commands = self.base / "offline commands"
        commands.mkdir()
        python = commands / "python3"
        python.write_text('#!/bin/bash\n'
                          'if [ "$#" = 1 ] && [ "$1" = - ]; then\n'
                          ' script=$(cat)\n'
                          ' case "$script" in *"http://127.0.0.1:8080/json"*) exit 1;; esac\n'
                          ' printf "%s\\n" "$script" | ' + shlex.quote(sys.executable) + ' -\n'
                          'else\n exec ' + shlex.quote(sys.executable) + ' "$@"\nfi\n')
        python.chmod(0o755)
        self.env["PATH"] = str(commands) + os.pathsep + os.environ["PATH"]

    def invoke(self, script, *args):
        return subprocess.run(["/bin/bash", str(ROOT / script), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_plan_preserves_paths_without_writing(self):
        result = self.invoke("bin/install.sh", "--plan", "--with-unity", "--with-nwjs", "--with-love", "--with-renpy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.rt), result.stdout)
        self.assertIn("SHA-256", result.stdout)
        self.assertIn("love-11.5-macos.zip", result.stdout)
        self.assertIn("renpy-8.1.3-sdk.zip", result.stdout)
        self.assertIn("renpy-8.1.3-steam.zip", result.stdout)
        self.assertIn("136589334", result.stdout)
        self.assertIn("不启动游戏", result.stdout)
        self.assertFalse(self.rt.exists())
        self.assertNotIn("unbound variable", result.stderr)

    def test_unknown_option_is_a_real_failure(self):
        for script in ("bin/install.sh", "tools/build-wine.sh"):
            result = self.invoke(script, "--not-a-real-option")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.rt.exists())

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
    def test_check_reports_missing_dependencies_without_creating_runtime(self):
        result = self.invoke("bin/install.sh", "--check", "--with-unity", "--with-nwjs", "--with-love", "--with-renpy")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("缺反作弊数据库", result.stderr)
        self.assertIn("所选 Unity 路线依赖未齐", result.stderr)
        self.assertIn("所选 NW.js 路线依赖未齐", result.stderr)
        self.assertIn("所选 LÖVE 路线依赖未齐", result.stderr)
        self.assertIn("所选 Ren'Py 路线", result.stderr)
        self.assertFalse(self.rt.exists())

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
    def test_corrupt_database_remains_unchanged_and_install_fails(self):
        self.rt.mkdir()
        db = self.rt / "anticheat-db.json"
        db.write_bytes(b"not-json\n")
        result = self.invoke("bin/install.sh")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("反作弊数据库损坏", result.stderr)
        self.assertEqual(db.read_bytes(), b"not-json\n")
        self.assertEqual(list(self.rt.iterdir()), [db])

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
    def test_node_without_websocket_is_required_dependency_failure(self):
        fakebin = self.base / "fakebin"
        fakebin.mkdir()
        node = fakebin / "node"
        node.write_text("#!/bin/sh\nexit 1\n")
        node.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env["PATH"]
        result = self.invoke("bin/install.sh", "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Node.js 缺失或没有内建 WebSocket", result.stderr)
        self.assertFalse(self.rt.exists())

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
    def test_failed_lock_acquisition_does_not_remove_other_install_lock(self):
        self.rt.mkdir()
        lock = self.rt / ".dryport-install.lock"
        lock.mkdir()
        result = self.invoke("bin/install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(lock.is_dir())
        self.assertIn("另一个安装占用了", result.stderr)

    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
    def test_renpy_install_dispatch_preserves_helper_failure_and_invalid_receipt(self):
        if subprocess.run(["/usr/bin/xcrun", "--find", "clang"], capture_output=True).returncode:
            self.skipTest("需要本机 Command Line Tools，只检查可用性，不编译")
        home = self.base / "fixture code"
        (home / "lib").mkdir(parents=True)
        helper = home / "lib/renpy-runtime.py"
        helper.write_text('''import json, os, sys
from pathlib import Path
Path(os.environ["TEST_RENPY_DISPATCH"]).write_text(json.dumps(sys.argv[1:]))
mode = os.environ["TEST_RENPY_RESULT"]
if mode == "failure":
    print("fixture fixed archive rejected", file=sys.stderr)
    raise SystemExit(7)
if mode == "invalid":
    print("not a JSON receipt")
else:
    print(json.dumps({"version": "8.1.3.23091805", "package": "/fixture/verified-package"}))
''')
        binary = self.base / "fakebin"
        binary.mkdir()
        # 只模拟基础依赖与只读 CEF 检查；安装器源码及 Ren'Py helper 的调用/返回码链真实执行。
        python = binary / "python3"
        python.write_text('#!/bin/bash\n'
                          'if [ "$#" = 1 ] && [ "$1" = - ]; then\n'
                          '  script=$(cat)\n'
                          '  case "$script" in *"http://127.0.0.1:8080/json"*) exit 0;; esac\n'
                          '  printf "%s\\n" "$script" | ' + shlex.quote(sys.executable) + ' -\n'
                          'else\n  exec ' + shlex.quote(sys.executable) + ' "$@"\nfi\n')
        python.chmod(0o755)
        node = binary / "node"
        node.write_text('#!/bin/sh\n[ "${1:-}" != --version ] || echo v22-fixture\nexit 0\n')
        node.chmod(0o755)
        self.rt.mkdir()
        (self.rt / "anticheat-db.json").write_text(json.dumps([
            {"name": "fixture", "anticheats": [], "storeIds": {}}]))
        steam = Path(self.env["STEAM"])
        (steam / "appcache").mkdir(parents=True)
        (steam / "appcache/appinfo.vdf").touch()
        dispatch = self.base / "dispatch.json"
        self.env.update(SN_HOME=str(home), PATH=str(binary) + os.pathsep + self.env.get("PATH", os.environ["PATH"]),
                        TEST_RENPY_DISPATCH=str(dispatch))
        for mode in ("success", "failure", "invalid"):
            with self.subTest(mode=mode):
                self.env["TEST_RENPY_RESULT"] = mode
                result = self.invoke("bin/install.sh", "--with-renpy")
                self.assertEqual(json.loads(dispatch.read_text()), ["install"])
                if mode == "success":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Ren'Py 8.1.3.23091805：/fixture/verified-package", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("固定原厂归档与运行依赖已核对", result.stdout)
                self.assertFalse((self.rt / ".dryport-install.lock").exists())
        self.assertFalse((self.base / "games").exists())

    def test_wine_rejects_wrong_hash_before_extraction(self):
        archive = self.base / "source.tar.gz"
        archive.write_bytes(b"not the pinned source")
        work = self.base / "work"
        result = self.invoke("tools/build-wine.sh", "--prepare", "--source", str(archive),
                             "--work", str(work), "--prefix", str(self.base / "wine"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("源码 SHA-256 不符", result.stderr)
        self.assertFalse((work / "sources").exists())
        self.assertFalse((work / ".lock").exists())
        self.assertEqual(archive.read_bytes(), b"not the pinned source")

    def test_wine_leaves_unowned_work_directory_untouched(self):
        work = self.base / "work"
        work.mkdir()
        evidence = work / "user-data.txt"
        evidence.write_text("keep me")
        result = self.invoke("tools/build-wine.sh", "--prepare", "--work", str(work),
                             "--prefix", str(self.base / "wine"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("不属于本脚本", result.stderr)
        self.assertEqual(list(work.iterdir()), [evidence])
        self.assertEqual(evidence.read_text(), "keep me")


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
class UnityPythonSelectionTests(unittest.TestCase):
    """模拟 Finder 的旧 Python 在前；不安装 Python、包或编译着色器。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-unity-python-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.first = self.base / "first tools"
        self.next = self.base / "later tools"
        self.first.mkdir()
        self.next.mkdir()
        self.rt = self.base / "runtime's data"
        (self.rt / "unity-retarget").mkdir(parents=True)
        self.venv = self.rt / "unity-retarget/venv"
        self.log = self.base / "calls.jsonl"
        steam = self.base / "steam"
        (steam / "appcache").mkdir(parents=True)
        (steam / "appcache/appinfo.vdf").touch()
        (self.rt / "anticheat-db.json").write_text(json.dumps([
            {"name": "fixture", "anticheats": [], "storeIds": {}}]))
        self.write_executable(self.rt / "unity-retarget/hlslcc-cli", "#!/bin/sh\nexit 99\n")
        self.write_executable(self.first / "node", "#!/bin/sh\nexit 0\n")
        self.write_executable(self.first / "file", '#!/bin/sh\nprintf "Mach-O arm64 fixture\\n"\n')
        self.write_executable(self.first / "curl", '#!/bin/sh\nprintf "unexpected network\\n" >&2\nexit 97\n')
        # 外层标准库仍用当前测试解释器；仅模拟首个 python3 不满足 Unity 版本门。
        old = '#!/bin/bash\ncase "$*" in *"sys.version_info < (3, 10)"*) exit 1;; esac\n'
        old += 'if [ "$#" = 1 ] && [ "$1" = - ]; then\n script=$(cat)\n'
        old += ' case "$script" in *"http://127.0.0.1:8080/json"*) exit 0;; esac\n'
        old += ' printf "%s\\n" "$script" | ' + shlex.quote(sys.executable) + ' -\n'
        old += 'else\n exec ' + shlex.quote(sys.executable) + ' "$@"\nfi\n'
        self.write_executable(self.first / "python3", old)
        self.env = {**os.environ, "SN_HOME": str(ROOT), "SN_RUNTIMES": str(self.rt),
                    "SN_GAMES": str(self.base / "games"), "STEAM": str(steam),
                    "PATH": str(self.first) + ":" + str(self.next) + ":/usr/bin:/bin:/usr/sbin:/sbin"}
        self.env.pop("NWJS_APP", None)

    def write_executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)
        return path

    def ready_venv(self):
        return self.write_executable(self.venv / "bin/python", "#!/bin/sh\nexit 0\n")

    def candidate(self):
        code = ('#!' + sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\n'
                'args=sys.argv[1:]\n'
                'with Path(' + repr(str(self.log)) + ').open("a") as f:\n'
                ' f.write(json.dumps({"argv":sys.argv,"path":os.environ["PATH"]})+"\\n")\n'
                'if "-c" in args: raise SystemExit(0)\n'
                'if len(args)==3 and args[:2]==["-m","venv"]:\n'
                ' p=Path(args[2])/"bin/python";p.parent.mkdir(parents=True,exist_ok=True)\n'
                ' p.write_text("#!/bin/sh\\nexit 0\\n");p.chmod(0o755)\n'
                'else: raise SystemExit(98)\n')
        return self.write_executable(self.next / "python3", code)

    def invoke(self, *args):
        return subprocess.run(["/bin/bash", str(ROOT / "bin/install.sh"), "--with-unity", *args],
                              env=self.env, text=True, capture_output=True, timeout=20)

    def test_existing_valid_venv_works_with_older_first_python(self):
        venv_python = self.ready_venv()
        before = (venv_python.read_bytes(), venv_python.stat().st_mtime_ns)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((venv_python.read_bytes(), venv_python.stat().st_mtime_ns), before)
        self.assertFalse(self.log.exists())

    def test_check_reuses_valid_venv_without_selecting_host_python(self):
        venv_python = self.ready_venv()
        self.candidate()
        before = {str(p.relative_to(self.rt)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.rt.rglob("*") if p.is_file()}
        result = self.invoke("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {str(p.relative_to(self.rt)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.rt.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(self.log.exists())
        self.assertTrue(venv_python.is_file())

    def test_new_venv_uses_first_qualified_absolute_python_without_changing_path(self):
        selected = self.candidate()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        creation = [c for c in calls if c["argv"][1:3] == ["-m", "venv"]]
        self.assertEqual(len(creation), 1)
        self.assertEqual(creation[0]["argv"], [str(selected), "-m", "venv", str(self.venv)])
        self.assertTrue(all(c["path"] == self.env["PATH"] for c in calls))
        self.assertTrue((self.venv / ".dryport-created").is_file())

    def test_no_qualified_python_fails_without_creating_venv(self):
        # 路径中的系统 Python 在本 fixture 里也用首个旧版本包装器表示。
        self.env["PATH"] = str(self.first) + ":" + str(self.next)
        for name in ("dirname", "uname", "mkdir", "mktemp", "rmdir", "tail", "cat", "grep"):
            target = next((Path(d) / name for d in ("/usr/bin", "/bin") if (Path(d) / name).exists()), None)
            self.assertIsNotNone(target, name)
            (self.first / name).symlink_to(target)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10", result.stderr)
        self.assertFalse(self.venv.exists())
        self.assertFalse(self.log.exists())

    def test_unowned_invalid_venv_is_preserved_before_choosing_python(self):
        self.venv.mkdir()
        keep = self.venv / "keep.txt"
        keep.write_text("user environment")
        self.candidate()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("现有 Python 环境", result.stderr)
        self.assertEqual(list(self.venv.iterdir()), [keep])
        self.assertEqual(keep.read_text(), "user environment")
        self.assertFalse(self.log.exists())

    def test_timed_out_candidate_is_reaped_and_next_candidate_is_selected(self):
        slow = self.base / "slow tools"
        slow.mkdir()
        pidfile = self.base / "timed-out.pid"
        self.write_executable(slow / "python3", '#!/bin/sh\necho $$ > ' + shlex.quote(str(pidfile)) + '\nexec /bin/sleep 20\n')
        selected = self.candidate()
        self.env["PATH"] = str(self.first) + ":" + str(slow) + ":" + str(self.next) + ":/usr/bin:/bin:/usr/sbin:/sbin"
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(c["argv"] == [str(selected), "-m", "venv", str(self.venv)] for c in calls))
        self.assertTrue(all(c["path"] == self.env["PATH"] for c in calls))
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_dangling_venv_symlink_is_preserved(self):
        absent = self.base / "absent environment"
        self.venv.symlink_to(absent, target_is_directory=True)
        self.candidate()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("现有 Python 环境", result.stderr)
        self.assertTrue(self.venv.is_symlink())
        self.assertFalse(absent.exists())
        self.assertFalse(self.log.exists())


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "macOS arm64 入口")
class RuntimeScopeTests(unittest.TestCase):
    """运行时作用域不访问下载基础设施；假命令只记录调用，不联网。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-runtime-scope-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "fixture source"
        self.binary = self.base / "commands"
        self.rt = self.base / "runtime's data"
        self.steam = self.base / "steam"
        self.audit = self.base / "audit.log"
        (self.home / "lib").mkdir(parents=True)
        self.binary.mkdir()
        self.env = {**os.environ, "SN_HOME": str(self.home), "SN_RUNTIMES": str(self.rt),
                    "SN_GAMES": str(self.base / "games"), "STEAM": str(self.steam),
                    "PATH": str(self.binary) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                    "SCOPE_AUDIT": str(self.audit), "SCOPE_CEF_RC": "1", "SCOPE_NODE_RC": "1",
                    "SCOPE_LOVE_RESULT": "success", "SCOPE_RENPY_RESULT": "success",
                    "SCOPE_FREE_GIB": "5", "NWJS_APP": str(self.base / "foreign-nwjs.app")}
        wrapper = '#!/bin/bash\nif [ "${1:-}" = - ]; then\n script=$(cat)\n'
        wrapper += ' case "$script" in *"http://127.0.0.1:8080/json"*) echo cef >> "$SCOPE_AUDIT"; exit "$SCOPE_CEF_RC";;\n'
        wrapper += ' *"shutil.disk_usage"*) echo "$SCOPE_FREE_GIB"; exit 0;; esac\n'
        wrapper += ' case "${2:-}" in */anticheat-db.json) echo risk-read >> "$SCOPE_AUDIT";; esac\n'
        wrapper += ' printf "%s\\n" "$script" | ' + shlex.quote(sys.executable) + ' "$@"\n'
        wrapper += 'else\n exec ' + shlex.quote(sys.executable) + ' "$@"\nfi\n'
        self.script(self.binary / "python3", wrapper)
        self.script(self.binary / "node", '#!/bin/sh\necho node >> "$SCOPE_AUDIT"\nexit "$SCOPE_NODE_RC"\n')
        self.script(self.binary / "curl", '#!/bin/sh\necho curl >> "$SCOPE_AUDIT"\nexit 97\n')
        self.script(self.binary / "file", '#!/bin/sh\necho file >> "$SCOPE_AUDIT"\necho "Mach-O arm64 fixture"\n')
        for filename, kind in (("runtime_archive.py", "love"), ("renpy-runtime.py", "renpy")):
            (self.home / "lib" / filename).write_text('''import json, os, sys
from pathlib import Path
kind = KIND
with Path(os.environ["SCOPE_AUDIT"]).open("a") as f:
    f.write(kind + ":" + ",".join(sys.argv[1:]) + "\\n")
mode = os.environ["SCOPE_" + kind.upper() + "_RESULT"]
if mode == "failure":
    print("fixture archive verification failed", file=sys.stderr)
    raise SystemExit(7)
if mode == "invalid": print("not-json")
elif mode == "empty": print("")
elif mode == "invalid-fields": print(json.dumps({"version": "", "package": ""}))
elif kind == "love": print("/fixture/verified-love-runtime")
else: print(json.dumps({"version": "8.1.3.23091805", "package": "/fixture/verified-renpy-runtime"}))
'''.replace("KIND", repr(kind)))

    def script(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def calls(self):
        return self.audit.read_text().splitlines() if self.audit.exists() else []

    def invoke(self, *args, **env):
        return subprocess.run(["/bin/bash", str(ROOT / "bin/install.sh"), *args],
                              env={**self.env, **env}, cwd=self.base, capture_output=True, text=True, timeout=20)

    def ready_basics(self):
        self.rt.mkdir(exist_ok=True)
        (self.rt / "anticheat-db.json").write_text(json.dumps([
            {"name": "fixture", "anticheats": [], "storeIds": {}}]))
        (self.steam / "appcache").mkdir(parents=True, exist_ok=True)
        (self.steam / "appcache/appinfo.vdf").touch()
        self.env.update(SCOPE_CEF_RC="0", SCOPE_NODE_RC="0", NWJS_APP="")

    def test_check_prints_explicit_next_commands(self):
        self.ready_basics()
        result = self.invoke("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        flattened = " ".join(result.stdout.split())
        self.assertIn("装全部基础环境与反作弊数据库：", flattened)
        self.assertIn("只装某一路线的运行时", flattened)
        all_command = next(line.split("：", 1)[1] for line in result.stdout.splitlines()
                           if line.startswith("  装全部基础环境与反作弊数据库："))
        route_command = next(line.split("：", 1)[1] for line in result.stdout.splitlines()
                             if line.startswith("  只装某一路线的运行时"))
        self.assertEqual(shlex.split(all_command), [str(self.home / "bin/install.sh")])
        self.assertEqual(shlex.split(route_command),
                         [str(self.home / "bin/install.sh"), "--runtime-only", "--with-unity"])

    def require_clang(self):
        if subprocess.run(["/usr/bin/xcrun", "--find", "clang"], capture_output=True).returncode:
            self.skipTest("Ren'Py 路线需要现有 Command Line Tools；本测试不安装")

    def test_runtime_scope_requires_a_selected_route_before_writing(self):
        for mode in ([], ["--check"], ["--plan"]):
            with self.subTest(mode=mode):
                result = self.invoke("--runtime-only", *mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--with-", result.stderr)
        self.assertFalse(self.rt.exists())
        self.assertEqual(self.calls(), [])

    def test_runtime_scope_skips_download_checks_and_unselected_engines(self):
        # 即使有可执行的其他引擎，也不应启动其检查或读取其二进制。
        self.script(self.rt / "unity-retarget/venv/bin/python", '#!/bin/sh\necho unselected-unity >> "$SCOPE_AUDIT"\nexit 1\n')
        self.script(self.rt / "mkxp-build/Build/Products/Release/Z-universal.app/Contents/MacOS/Z-universal", "#!/bin/sh\nexit 99\n")
        db = self.rt / "anticheat-db.json"
        db.write_bytes(b"invalid database must remain untouched")
        before = (db.read_bytes(), db.stat().st_mtime_ns)
        result = self.invoke("--runtime-only", "--with-love")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["love:install-love"])
        self.assertEqual((db.read_bytes(), db.stat().st_mtime_ns), before)
        self.assertFalse(self.steam.exists())
        self.assertFalse((self.base / "games").exists())
        self.assertNotIn("基础环境及所选依赖已就绪", result.stdout)
        self.assertIn("所选路线依赖已就绪", result.stdout)
        next_command = next(line.split("：", 1)[1] for line in result.stdout.splitlines()
                            if line.startswith("检查下载基础环境："))
        self.assertEqual(shlex.split(next_command), [str(self.home / "bin/install.sh"), "--check"])
        self.assertNotIn("NWJS_APP", result.stdout + result.stderr)
        self.assertFalse((self.rt / ".dryport-install.lock").exists())

    def test_runtime_scope_check_is_read_only_and_uses_check_actions(self):
        self.require_clang()
        result = self.invoke("--check", "--runtime-only", "--with-love", "--with-renpy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["love:check-love", "renpy:check"])
        self.assertFalse(self.rt.exists())
        self.assertFalse(self.steam.exists())

    def test_runtime_scope_selected_unity_and_nwjs_check_their_own_dependencies(self):
        venv = self.rt / "unity-retarget/venv/bin/python"
        self.script(venv, '#!/bin/sh\necho selected-unity >> "$SCOPE_AUDIT"\nexit 0\n')
        self.script(self.rt / "unity-retarget/hlslcc-cli", "#!/bin/sh\nexit 99\n")
        nwapp = self.base / "selected nwjs.app"
        self.script(nwapp / "Contents/MacOS/nwjs", "#!/bin/sh\nexit 99\n")
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                  for folder in (self.rt, nwapp) for p in folder.rglob("*") if p.is_file()}
        for args in (("--check",), ()):
            with self.subTest(args=args):
                result = self.invoke("--runtime-only", "--with-unity", "--with-nwjs", *args,
                                     NWJS_APP=str(nwapp))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Unity Python 固定版本", result.stdout)
                self.assertIn("NW.js arm64 运行时", result.stdout)
                self.assertTrue(set(self.calls()) <= {"selected-unity", "file"}, self.calls())
                self.assertFalse((self.rt / ".dryport-install.lock").exists())
                for path, expected in before.items():
                    self.assertEqual((Path(path).read_bytes(), Path(path).stat().st_mtime_ns), expected)
        self.assertIn("selected-unity", self.calls())
        self.assertIn("file", self.calls())
        self.assertFalse(self.steam.exists())

    def test_runtime_scope_missing_selected_unity_and_nwjs_fail_without_writes(self):
        result = self.invoke("--runtime-only", "--check", "--with-unity", "--with-nwjs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("所选 Unity 路线依赖未齐", result.stderr)
        self.assertIn("所选 NW.js 路线依赖未齐", result.stderr)
        self.assertFalse(self.rt.exists())
        self.assertEqual(self.calls(), [])

    def test_runtime_scope_plan_lists_only_selected_sources_without_checking(self):
        result = self.invoke("--plan", "--runtime-only", "--with-love", "--with-renpy", SN_HOME=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("love-11.5-macos.zip", result.stdout)
        self.assertIn("renpy-8.1.3-sdk.zip", result.stdout)
        self.assertNotIn("games.json", result.stdout)
        self.assertNotIn("基础操作", result.stdout)
        self.assertNotIn("所选路线依赖已就绪", result.stdout)
        self.assertFalse(self.rt.exists())
        self.assertEqual(self.calls(), [])

    def test_legacy_scope_keeps_download_and_unselected_override_failures(self):
        self.ready_basics()
        for changes, phrase in (({"SCOPE_CEF_RC": "1"}, "客户端下载端点不可读"),
                                ({"SCOPE_NODE_RC": "1"}, "Node.js 缺失"),
                                ({"NWJS_APP": str(self.base / "bad.app")}, "NWJS_APP")):
            with self.subTest(changes=changes):
                result = self.invoke("--with-love", **changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(phrase, result.stderr)
                self.assertIn("/fixture/verified-love-runtime", result.stdout)

    def test_runtime_scope_helper_failure_or_invalid_json_never_claims_success(self):
        self.require_clang()
        for route, result_mode in (("love", "failure"), ("love", "empty"), ("love", "invalid"), ("renpy", "failure"),
                                   ("renpy", "invalid"), ("renpy", "empty"), ("renpy", "invalid-fields")):
            with self.subTest(route=route, result=result_mode):
                result = self.invoke("--runtime-only", "--with-" + route,
                                     **{"SCOPE_" + route.upper() + "_RESULT": result_mode})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("所选路线依赖已就绪", result.stdout)
                self.assertFalse((self.rt / ".dryport-install.lock").exists())

    def test_runtime_scope_multiple_selection_preserves_any_failure(self):
        self.require_clang()
        for failed in ("LOVE", "RENPY"):
            with self.subTest(failed=failed):
                result = self.invoke("--runtime-only", "--with-love", "--with-renpy",
                                     **{"SCOPE_" + failed + "_RESULT": "failure"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("所选路线依赖已就绪", result.stdout)
        self.assertTrue(any(c.startswith("love:") for c in self.calls()))
        self.assertTrue(any(c.startswith("renpy:") for c in self.calls()))

    def test_runtime_scope_does_not_remove_another_lock_or_change_space_gate(self):
        lock = self.rt / ".dryport-install.lock"
        lock.mkdir(parents=True)
        result = self.invoke("--runtime-only", "--with-love")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("另一个安装", result.stderr)
        self.assertTrue(lock.is_dir())
        result = self.invoke("--runtime-only", "--with-love", SCOPE_FREE_GIB="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 GiB", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
