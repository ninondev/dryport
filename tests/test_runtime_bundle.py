# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""预编译运行时包：打包只收通过架构核对的产物；安装先核 SHA256 再解包再核主程序，目标已存在默认不动。
明文 http 来源、越界的清单路径、越界的归档成员一律拒绝；清单本身要过哈希认证（本地目录可 --untrusted-local）。全部用临时目录。"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/runtime-bundle.py"


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sn-bundle-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.src = self.base / "src-runtimes"
        toy = self.src / "toy/build/toy.app/Contents/MacOS"; toy.mkdir(parents=True)
        shutil.copy("/bin/ls", toy / "toy")                      # 真 Mach-O，file 报告含 arm64
        (self.src / "toy/build/toy.app/Contents/Resources").mkdir()
        (self.src / "toy/build/toy.app/Contents/Resources/data.bin").write_bytes(b"toy payload " * 100)
        (self.src / "toy/build/CMakeCache.txt").write_text("build intermediates stay out")
        (self.src / "toy/build/LICENSE.txt").write_text("toy license text\n")   # 在 product 目录外：只能靠许可证机制进归档
        flat = self.src / "tools"; flat.mkdir()
        real = self.src / "build/flat-tool.real"; real.parent.mkdir()
        real.write_bytes(b"#!/bin/sh\necho flat\n"); real.chmod(0o755)
        (flat / "flat-tool").symlink_to(real)                    # 单文件产物是软链：要打真文件
        (flat / "FLAT-NOTICE.txt").write_text("flat license text\n")
        (self.src / "steambridge").mkdir()
        for name in ("steam_api64.dll", "steambridge-helper", "sbtest.exe", "helper.abc.log"):
            (self.src / "steambridge" / name).write_bytes(b"x" * 10)
        shutil.copy("/bin/ls", self.src / "steambridge/steambridge-helper")
        (self.src / "steambridge/LICENSE.txt").write_text("bridge license text\n")
        self.registry = self.base / "registry.json"
        self.registry.write_text(json.dumps({"runtimes": {
            "toy": {"product": "toy/build/toy.app", "check": {"path": "toy/build/toy.app/Contents/MacOS/toy", "arch": "arm64"},
                    "license": "MIT", "source": "fixture", "version_from": ["const:1.0"],
                    "license_files": ["toy/build/LICENSE.txt"], "notice": ["toy: (c) 2026 fixture authors. MIT."]},
            "flat": {"product": "tools/flat-tool", "check": {"path": "tools/flat-tool", "arch": None},
                     "license": "MIT", "source": "fixture", "version_from": ["file:tools/nonexistent", "const:v2"],
                     "license_files": ["@SN_HOME/LICENSE", "tools/FLAT-NOTICE.txt"], "notice": ["flat: (c) 2026 fixture authors. MIT."]},
            "bridge": {"product": "steambridge", "include": ["steam_api64.dll", "steambridge-helper", "sbtest.exe"],
                       "check": {"path": "steambridge/steambridge-helper", "arch": "arm64"}, "license": "MIT", "source": "fixture",
                       "license_files": ["steambridge/LICENSE.txt"], "notice": ["bridge: (c) 2026 fixture authors. MIT."]},
            "absent": {"product": "absent/thing.app", "check": {"path": "absent/thing.app/Contents/MacOS/thing", "arch": "arm64"},
                       "license": "MIT", "source": "fixture"},
        }}))
        self.out = self.base / "bundle"

    def run_tool(self, *args, runtimes):
        return subprocess.run([sys.executable, "-B", str(TOOL), "--registry", str(self.registry), "--runtimes", str(runtimes), *args],
                              capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def pack(self):
        r = self.run_tool("pack", "--out", str(self.out), "--level", "1", runtimes=self.src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads((self.out / "runtime-manifest.json").read_text())

    def trust_manifest(self):
        """模拟把发布清单的 SHA256 登记进 registry。"""
        data = json.loads(self.registry.read_text())
        data.setdefault("manifest_hashes", {})[
            hashlib.sha256((self.out / "runtime-manifest.json").read_bytes()).hexdigest()] = "fixture-bundle"
        self.registry.write_text(json.dumps(data))

    def test_pack_lists_only_valid_products_with_hashes_and_skips_intermediates(self):
        man = self.pack()
        names = {e["name"]: e for e in man["runtimes"]}
        self.assertEqual(set(names), {"toy", "flat", "bridge"})
        self.assertEqual([s["name"] for s in man["skipped"]], ["absent"])
        for e in names.values():
            archive = self.out / e["archive"]
            self.assertTrue(archive.is_file())
            self.assertEqual(e["bytes"], archive.stat().st_size)
            self.assertEqual(len(e["sha256"]), 64)
        self.assertEqual(names["toy"]["version"], "1.0"); self.assertEqual(names["flat"]["version"], "v2")
        listing = subprocess.run(["tar", "-tf", str(self.out / names["toy"]["archive"])], capture_output=True, text=True).stdout
        self.assertIn("toy.app/Contents/MacOS/toy", listing); self.assertNotIn("CMakeCache", listing)
        listing = subprocess.run(["tar", "-tf", str(self.out / names["bridge"]["archive"])], capture_output=True, text=True).stdout
        self.assertIn("steambridge/steambridge-helper", listing); self.assertNotIn("helper.abc.log", listing)
        listing = subprocess.run(["tar", "-tvf", str(self.out / names["flat"]["archive"])], capture_output=True, text=True).stdout
        self.assertNotIn("->", listing)                            # 不是软链条目
        self.assertGreater(names["flat"]["bytes"], 60)
        # --only 重打一项时并入已有清单
        r = self.run_tool("pack", "--out", str(self.out), "--only", "flat", "--level", "1", runtimes=self.src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        merged = json.loads((self.out / "runtime-manifest.json").read_text())
        self.assertEqual({e["name"] for e in merged["runtimes"]}, {"toy", "flat", "bridge"})
        self.assertEqual([x["name"] for x in merged["skipped"]], ["absent"])

    def test_install_verifies_sha_unpacks_atomically_and_skips_when_ready(self):
        man = self.pack()
        self.trust_manifest()
        dest = self.base / "clean-runtimes"
        r = self.run_tool("install", "--from", str(self.out), runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest / "toy/build/toy.app/Contents/MacOS/toy").is_file())
        self.assertTrue((dest / "toy/build/toy.app/Contents/Resources/data.bin").is_file())
        self.assertFalse((dest / "toy/build/CMakeCache.txt").exists())
        self.assertTrue((dest / "tools/flat-tool").is_file())
        self.assertTrue((dest / "steambridge/steam_api64.dll").is_file())
        self.assertTrue((dest / "third-party-licenses/toy/NOTICE.txt").is_file())
        self.assertTrue((dest / "third-party-licenses/toy/toy__build__LICENSE.txt").is_file())
        self.assertTrue((dest / "third-party-licenses/flat/LICENSE").is_file())      # @SN_HOME/ 前缀去掉后平铺
        bridge_notice = (dest / "third-party-licenses/bridge/NOTICE.txt").read_text()
        self.assertIn("bridge", bridge_notice); self.assertIn("bridge: (c) 2026 fixture authors. MIT.", bridge_notice)
        rec = json.loads((dest / "installed-runtimes.json").read_text())
        self.assertEqual(set(rec), {"toy", "flat", "bridge"})
        self.assertEqual(rec["toy"]["sha256"], next(e["sha256"] for e in man["runtimes"] if e["name"] == "toy"))
        self.assertEqual([p.name for p in dest.glob(".bundle-stage.*")], [])
        again = self.run_tool("install", "--from", str(self.out), runtimes=dest)
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(again.stdout.count("跳过"), 3)
        check = self.run_tool("check", "--from", str(self.out), runtimes=dest)
        self.assertEqual(check.stdout.count("已就绪"), 3, check.stdout)
        self.assertIn("absent", check.stdout)

    def test_tampered_archive_is_refused_and_nothing_is_installed(self):
        man = self.pack()
        self.trust_manifest()
        toy = next(e for e in man["runtimes"] if e["name"] == "toy")
        archive = self.out / toy["archive"]
        data = bytearray(archive.read_bytes()); data[-1] ^= 0xFF; archive.write_bytes(bytes(data))
        dest = self.base / "clean-runtimes"
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA256 不符", r.stdout)
        self.assertFalse((dest / "toy").exists())
        self.assertTrue(list((dest / "build/bundles").glob("*.bad")))
        self.assertFalse((dest / "installed-runtimes.json").exists())

    def test_existing_target_is_kept_unless_replace(self):
        self.pack()
        self.trust_manifest()
        dest = self.base / "clean-runtimes"
        stale = dest / "toy/build/toy.app"; stale.mkdir(parents=True)
        (stale / "stale.txt").write_text("someone else's directory")
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("目标已存在", r.stdout)
        self.assertTrue((stale / "stale.txt").exists())
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", "--replace", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest / "toy/build/toy.app/Contents/MacOS/toy").is_file())
        kept = list((dest / "toy/build").glob("toy.app.replaced-*"))
        self.assertEqual(len(kept), 1); self.assertTrue((kept[0] / "stale.txt").exists())

    def test_pack_includes_license_texts_and_notice(self):
        man = self.pack()
        names = {e["name"]: e for e in man["runtimes"]}
        self.assertEqual(names["flat"]["license_members"],
                         ["third-party-licenses/flat/LICENSE", "third-party-licenses/flat/tools__FLAT-NOTICE.txt",
                          "third-party-licenses/flat/NOTICE.txt"])
        listing = subprocess.run(["tar", "-tf", str(self.out / names["flat"]["archive"])], capture_output=True, text=True).stdout
        for member in names["flat"]["license_members"]:
            self.assertIn(member, listing)
        listing = subprocess.run(["tar", "-tf", str(self.out / names["toy"]["archive"])], capture_output=True, text=True).stdout
        self.assertIn("third-party-licenses/toy/NOTICE.txt", listing)
        self.assertIn("third-party-licenses/toy/toy__build__LICENSE.txt", listing)
        notice = subprocess.run(["tar", "-xOf", str(self.out / names["toy"]["archive"]), "third-party-licenses/toy/NOTICE.txt"],
                                capture_output=True, text=True).stdout
        for needle in ("toy", "1.0", "MIT", "fixture", "toy: (c) 2026 fixture authors. MIT."):
            self.assertIn(needle, notice)
        self.assertEqual(list(self.out.glob(".pack-lic-*")), [])     # 暂存目录用完就删

    def test_pack_skips_runtime_with_missing_license_text(self):
        (self.src / "toy/build/LICENSE.txt").unlink()
        out2 = self.base / "bundle-missing"
        r = self.run_tool("pack", "--out", str(out2), "--level", "1", runtimes=self.src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        man = json.loads((out2 / "runtime-manifest.json").read_text())
        self.assertEqual({e["name"] for e in man["runtimes"]}, {"flat", "bridge"})
        why = {s["name"]: s["why"] for s in man["skipped"]}
        self.assertEqual(why["toy"], "缺许可证原文：toy/build/LICENSE.txt")
        self.assertEqual(why["absent"], "缺主程序 absent/thing.app/Contents/MacOS/thing")   # 其他原因的跳过照旧
        self.assertEqual(list(out2.glob("toy-*")), [])                  # 缺原文连归档都不产出
        listing = subprocess.run(["tar", "-tf", str(out2 / next(e["archive"] for e in man["runtimes"] if e["name"] == "flat"))],
                                 capture_output=True, text=True).stdout
        self.assertIn("third-party-licenses/flat/NOTICE.txt", listing)   # 其余运行时不受影响
        self.assertEqual(list(out2.glob(".pack-lic-*")), [])

    def test_pack_skips_runtime_without_registered_license_files(self):
        data = json.loads(self.registry.read_text())
        data["runtimes"]["bare"] = {"product": "toy/build/toy.app", "check": {"path": "toy/build/toy.app/Contents/MacOS/toy", "arch": "arm64"},
                                    "license": "MIT", "source": "fixture", "version_from": ["const:1.0"]}
        self.registry.write_text(json.dumps(data))
        out2 = self.base / "bundle-bare"
        r = self.run_tool("pack", "--out", str(out2), "--only", "bare", "--level", "1", runtimes=self.src)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)       # 一项都没打成
        man = json.loads((out2 / "runtime-manifest.json").read_text())
        self.assertEqual(man["runtimes"], [])
        self.assertEqual([s["why"] for s in man["skipped"]], ["清单没登记许可证原文"])

    def test_install_keeps_old_license_texts_when_replacing(self):
        self.pack()
        self.trust_manifest()
        dest = self.base / "clean-runtimes"
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", "--replace", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        kept = list((dest / "third-party-licenses").glob("toy.replaced-*"))
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "NOTICE.txt").is_file())
        self.assertTrue((kept[0] / "toy__build__LICENSE.txt").is_file())
        self.assertTrue((dest / "third-party-licenses/toy/NOTICE.txt").is_file())

    def test_install_archive_without_texts_still_installs(self):
        # 旧版发布包的归档没有 third-party-licenses/：照样装，只提示
        olddir = self.base / "old-bundle"; olddir.mkdir()
        archive = olddir / "toy-0.9.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted((self.src / "toy/build").rglob("*")):
                if path.is_file() and path.name != "CMakeCache.txt":
                    tar.add(path, arcname=str(path.relative_to(self.src)))
        (olddir / "runtime-manifest.json").write_text(json.dumps({
            "manifest_version": 1, "compression": "gzip", "skipped": [], "runtimes": [
                {"name": "toy", "version": "0.9", "archive": archive.name,
                 "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "bytes": archive.stat().st_size,
                 "unpack_to": ".", "product": "toy/build/toy.app",
                 "check": {"path": "toy/build/toy.app/Contents/MacOS/toy", "arch": "arm64"}, "also_required": [],
                 "license": "MIT", "source": "fixture"}]}))
        dest = self.base / "old-dest"
        # 这份手造清单没登记哈希，本地目录须 --untrusted-local 显式信任才装
        r = self.run_tool("install", "--from", str(olddir), "--untrusted-local", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--untrusted-local", r.stdout)
        self.assertIn("这个归档没带许可证原文（旧版发布包）", r.stdout)
        self.assertTrue((dest / "toy/build/toy.app/Contents/MacOS/toy").is_file())
        self.assertFalse((dest / "third-party-licenses").exists())


class HostileManifestTests(RuntimeBundleTests):
    """敌意清单与敌意归档逐项被拒，且不写出暂存目录之外。路径类敌意用 --untrusted-local 隔离认证因素。"""

    def rewrite_toy(self, **changes):
        """改写发布清单里 toy 一项后回写（SHA 认证与路径规则互不影响各自的断言）。
        按 product 定位：本类会改 name 本身，不能拿 name 当钥匙。"""
        manifest = json.loads((self.out / "runtime-manifest.json").read_text())
        entry = next(e for e in manifest["runtimes"] if e.get("product") == "toy/build/toy.app")
        entry.update(changes)
        (self.out / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    def test_plain_http_source_is_refused_before_anything_is_fetched(self):
        self.pack(); self.trust_manifest()
        dest = self.base / "http-dest"
        r = self.run_tool("install", "--from", "http://127.0.0.1:1/bundle", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("http", r.stderr)
        self.assertIn("https", r.stderr)
        self.assertFalse((dest / "build/bundles").exists())
        self.assertFalse((dest / "installed-runtimes.json").exists())

    def test_absolute_unpack_to_is_refused_before_fetch_and_writes_nowhere(self):
        self.pack(); self.trust_manifest()
        self.rewrite_toy(unpack_to="/tmp/sn-a4-must-not-exist")
        dest = self.base / "abs-dest"
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", "--untrusted-local", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unpack_to", r.stdout)
        r2 = self.run_tool("check", "--from", str(self.out), runtimes=dest)   # check 只读，不因敌意清单写任何东西
        self.assertFalse(Path("/tmp/sn-a4-must-not-exist").exists())
        self.assertFalse((dest / "toy").exists())
        # 缓存目录现在启动时就建好并核验：存在的只能是空目录，一个归档都不取
        self.assertEqual(list((dest / "build/bundles").iterdir()), [])
        self.assertEqual(list(dest.glob(".bundle-stage.*")), [])

    def test_dotdot_in_product_is_refused_and_nothing_escapes_the_runtimes_root(self):
        self.pack(); self.trust_manifest()
        self.rewrite_toy(product="../escaped-by-product")
        dest = self.base / "dotdot-dest"
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", "--untrusted-local", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("product", r.stdout)
        self.assertFalse((self.base / "escaped-by-product").exists())
        self.assertFalse((dest / "toy").exists())
        self.assertEqual(list((dest / "build/bundles").iterdir()), [])   # 缓存目录存在但必须为空
        self.assertEqual(list(dest.glob(".bundle-stage.*")), [])

    def test_symlink_member_pointing_outside_is_refused_before_unpack(self):
        self.pack(); self.trust_manifest()
        # 手造一个归档：成员是指向 / 的软链；清单里登记它的正确 SHA256，所以拒绝只能来自成员检查
        hostile = self.out / "toy-hostile.tar.gz"
        with tarfile.open(hostile, "w:gz") as tar:
            info = tarfile.TarInfo("toy.app/escape")
            info.type = tarfile.SYMTYPE; info.linkname = "/"
            tar.addfile(info)
        self.rewrite_toy(archive=hostile.name, sha256=hashlib.sha256(hostile.read_bytes()).hexdigest(),
                         bytes=hostile.stat().st_size)
        dest = self.base / "link-dest"
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", "--untrusted-local", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("链接", r.stdout)
        self.assertFalse((dest / "toy").exists())
        self.assertEqual(list(dest.glob(".bundle-stage.*")), [])

    def test_unregistered_manifest_is_refused_unless_local_and_untrusted_local(self):
        self.pack()
        dest = self.base / "auth-dest"
        r = self.run_tool("install", "--from", str(self.out), runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("认证", r.stderr)
        self.assertFalse((dest / "toy").exists())
        self.assertFalse((dest / "installed-runtimes.json").exists())
        r = self.run_tool("install", "--from", str(self.out), "--untrusted-local", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--untrusted-local", r.stdout)
        self.assertTrue((dest / "toy/build/toy.app/Contents/MacOS/toy").is_file())
        self.trust_manifest()      # 登记后不需要旗子（https 来源也只有这条路）
        dest2 = self.base / "auth-dest-2"
        r = self.run_tool("install", "--from", str(self.out), runtimes=dest2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest2 / "toy/build/toy.app/Contents/MacOS/toy").is_file())

    def test_changed_manifest_byte_fails_authentication(self):
        self.pack(); self.trust_manifest()
        path = self.out / "runtime-manifest.json"
        data = bytearray(path.read_text(encoding="utf8").encode())
        marker = '"license": "MIT"'.encode()
        at = bytes(data).index(marker)
        data[at + 12] = ord("X")          # 改一个字节（仍保持 JSON 合法）
        path.write_text(json.dumps(json.loads(bytes(data)), ensure_ascii=False, indent=1) + "\n")
        # 重打包成同构但不同的字节后，登记的哈希不再匹配
        dest = self.base / "changed-dest"
        r = self.run_tool("install", "--from", str(self.out), runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("认证", r.stderr)
        self.assertFalse((dest / "toy").exists())

    def test_absolute_or_escaping_name_is_refused_before_anything_moves(self):
        # name 会拼进 third-party-licenses/<name>，绝对路径或 ../ 能把外面的目录搬去 .replaced-*
        self.pack(); self.trust_manifest()
        dest = self.base / "name-dest"
        victim = dest / "outside-license-root"
        for bad in ("/tmp/sn-a4-name", "../outside-license-root", ".", "..", "evil/name"):
            with self.subTest(name=bad):
                victim.parent.mkdir(parents=True, exist_ok=True)
                victim.mkdir(exist_ok=True)
                self.rewrite_toy(name=bad)
                r = self.run_tool("install", "--from", str(self.out), "--untrusted-local", runtimes=dest)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("name", r.stdout + r.stderr)
                self.assertTrue(victim.is_dir(), "外面的目录不能被搬走")
                self.assertEqual(list(victim.iterdir()), [])
                self.assertEqual(list(dest.glob("outside-license-root*")), [victim], "不能出现 .replaced-* 搬移")
                self.assertFalse((dest / "toy").exists())
        self.rewrite_toy(name="toy")      # 普通标识符照常装
        r = self.run_tool("install", "--from", str(self.out), "--untrusted-local", runtimes=dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest / "toy/build/toy.app/Contents/MacOS/toy").is_file())

    def test_archive_cache_behind_a_symbolic_link_is_refused(self):
        # build/bundles 与 .part 文件都不能躲在符号链接后面
        self.pack(); self.trust_manifest()
        dest = self.base / "cache-dest"
        outside = self.base / "outside-cache"
        outside.mkdir()
        (dest / "build").mkdir(parents=True)
        (dest / "build/bundles").symlink_to(outside)
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("缓存", r.stdout + r.stderr)
        self.assertEqual(list(outside.iterdir()), [], "符号链接后面一个字节都不能落")
        self.assertFalse((dest / "toy").exists())
        # 目录正常、但已存在的 .part 是指向外面的符号链接：逐文件也要拒
        (dest / "build/bundles").unlink()
        (dest / "build/bundles").mkdir()
        toy = next(e for e in json.loads((self.out / "runtime-manifest.json").read_text())["runtimes"] if e["name"] == "toy")
        (dest / "build/bundles" / (toy["archive"] + ".part")).symlink_to(outside / "trap")
        r = self.run_tool("install", "--from", str(self.out), "--only", "toy", runtimes=dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("缓存", r.stdout + r.stderr)
        self.assertFalse((outside / "trap").exists(), ".part 不能写穿符号链接")
        self.assertFalse((dest / "toy").exists())


if __name__ == "__main__":
    unittest.main()
