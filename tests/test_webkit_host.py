# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""正式宿主的真实 Swift 配置、资源与状态机制；不创建 NSApplication 或运行游戏。"""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
import AppKit
import Foundation
import Darwin
import JavaScriptCore
@main struct HostCheck {
  @MainActor static func main() {
    do {
      let mode = CommandLine.arguments[1]
      let bundle = Bundle(url: URL(fileURLWithPath: CommandLine.arguments[2]))!
      let (config, root) = try LaunchConfiguration.read(bundle: bundle)
      if mode == "root-race" {
        let handler = try ResourceScheme(root:root,host:"self")
        let frozen = try handler.resourceInventory()
        let moved = root.deletingLastPathComponent().appendingPathComponent("kept-original")
        try FileManager.default.moveItem(at:root,to:moved)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:false)
        try Data("replacement".utf8).write(to:root.appendingPathComponent("release.html"))
        guard try inventory(root) != frozen, try handler.resourceInventory() == frozen else {
          throw ProbeFailure("validated a different root from reader")
        }
        let (file,_) = try handler.openResource("release.html")
        defer { try? file.close() }
        guard try file.readToEnd() == Data(contentsOf:moved.appendingPathComponent("release.html")) else {
          throw ProbeFailure("reader changed root")
        }
      } else if mode == "receipt" {
        var natural = TerminalCloseReceipt()
        natural.accept(kind:"beforeunload",fields:["trusted":true])
        natural.accept(kind:"pagehide",fields:["trusted":false,"persisted":false])
        guard !natural.delivered else { throw ProbeFailure("untrusted pagehide accepted") }
        natural.accept(kind:"pagehide",fields:["trusted":true,"persisted":true])
        guard !natural.delivered else { throw ProbeFailure("cached page accepted") }
        natural.accept(kind:"pagehide",fields:["trusted":true,"persisted":false])
        guard natural.delivered else { throw ProbeFailure("natural close missing") }
        var spoof = TerminalCloseReceipt()
        spoof.accept(kind:"beforeunload",fields:["trusted":false])
        spoof.accept(kind:"pagehide",fields:["trusted":true,"persisted":false])
        guard !spoof.delivered else { throw ProbeFailure("synthetic beforeunload accepted") }
        var synthetic = TerminalCloseReceipt()
        synthetic.accept(kind:"dispatch_started",fields:[:])
        synthetic.accept(kind:"dispatch_returned",fields:["accepted":true,"default_prevented":false,"return_value_present":false])
        guard synthetic.delivered && !synthetic.refused else { throw ProbeFailure("fallback receipt missing") }
        var refused = TerminalCloseReceipt()
        refused.accept(kind:"dispatch_started",fields:[:])
        refused.accept(kind:"dispatch_returned",fields:["accepted":true,"default_prevented":false,"return_value_present":true])
        guard refused.refused && !refused.delivered else { throw ProbeFailure("cancel ignored") }
      } else if mode == "lifecycle" {
        let setup = #"""
        const callbacks={}; let dispatched=0;
        globalThis.window={webkit:{messageHandlers:{snLifecycle:{postMessage:()=>{}}}},
          addEventListener:(kind,callback)=>{callbacks[kind]=callback;},
          dispatchEvent:event=>{dispatched++;callbacks.beforeunload(event);return true;}};
        globalThis.document={createEvent:()=>({isTrusted:false,defaultPrevented:false,returnValue:'',initEvent:()=>{}})};
        """#
        for trusted in [false,true] {
          let context = JSContext()!
          let check = "callbacks.beforeunload({isTrusted:\(trusted)}); callbacks.pagehide({isTrusted:true,persisted:false}); if(dispatched!==\(trusted ? 0 : 1))throw Error('wrong fallback count');"
          _ = context.evaluateScript(setup + terminalLifecycleScript + check)
          guard context.exception == nil else { throw ProbeFailure(context.exception!.toString()) }
        }
      } else if mode == "verify" {
        let actual = try ResourceScheme(root:root,host:"self").resourceInventory()
        guard actual == config.resource_inventory else { throw ProbeFailure("inventory mismatch: \(actual) != \(config.resource_inventory); root=\(root.path)") }
      } else {
        let home = URL(fileURLWithPath: CommandLine.arguments[3])
        var first: OwnedState? = try OwnedState(config: config, homeURL: home)
        if mode == "lock" {
          do { _ = try OwnedState(config: config, homeURL: home); throw ProbeFailure("second process accepted") }
          catch { guard String(describing:error).contains("已在运行") else { throw error } }
          try first!.writeLog(["round":1])
          try first!.writeLog(["round":2])
          first = nil
          _ = try OwnedState(config: config, homeURL: home)
        } else if mode == "log-alias" {
          try first!.writeLog(["round":1])
          let log = first!.logURL
          let other = home.appendingPathComponent("must-preserve")
          try Data("original".utf8).write(to: other)
          try FileManager.default.removeItem(at: log)
          try FileManager.default.createSymbolicLink(at: log, withDestinationURL: other)
          do { try first!.writeLog(["round":2]); throw ProbeFailure("link replaced") }
          catch { guard String(describing:error).contains("被替换") else { throw error } }
          guard try Data(contentsOf: other) == Data("original".utf8),
            (try? FileManager.default.destinationOfSymbolicLink(atPath: log.path)) == other.path else { throw ProbeFailure("outside overwritten") }
        }
      }
      print("ok")
    } catch { FileHandle.standardError.write(Data("\(error)\n".utf8)); exit(2) }
  }
}
'''


@unittest.skipUnless(sys.platform == "darwin", "需要 macOS Swift/WebKit；无 GUI")
class WebKitHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="sn-webkit-host-test-")
        base = Path(cls.build.name).resolve()
        harness = base / "check.swift"
        harness.write_text(HARNESS)
        cls.binary = base / "check"
        run = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "6",
            "-strict-concurrency=complete", "-enable-upcoming-feature", "NonisolatedNonsendingByDefault",
            "-D", "WEBKIT_HOST_TEST", "-O", "-target", "arm64-apple-macosx14.0",
            str(ROOT / "lib/webkit-core.swift"), str(ROOT / "lib/webkit-host.swift"), str(harness),
            "-o", str(cls.binary)], capture_output=True, text=True, timeout=60)
        if run.returncode:
            cls.build.cleanup()
            raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sn-webkit-host-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.bundle = self.base / "Fixture.app"
        self.resources = self.bundle / "Contents/Resources"
        self.content = self.resources / "OriginalGame/resources/app"
        self.content.mkdir(parents=True)
        data = b"<!doctype html><title>self-owned static fixture</title>"
        (self.content / "release.html").write_bytes(data)
        self.config = {"protocol": 1, "appid": "4294900001", "name": "Self fixture",
            "content_root": "OriginalGame/resources/app", "entry": "release.html",
            "store_uuid": str(uuid.uuid4()), "lifecycle": "terminal-beforeunload",
            "resource_inventory": {"release.html": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}
        self.info = {"CFBundleIdentifier": "io.dryport.webkit.4294900001", "CFBundleName": "WebKitGame4294900001",
            "CFBundleExecutable": "WebKitGame", "CFBundlePackageType": "APPL"}
        self.home = self.base / "Home"
        self.home.mkdir()
        self.write_config()

    def write_config(self):
        (self.resources / "webkit-launch.json").write_text(json.dumps(self.config))
        (self.bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))

    def run_check(self, mode="verify", success=True):
        run = subprocess.run([str(self.binary), mode, str(self.bundle), str(self.home)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0 if success else 2, run.stderr)
        return run

    def test_valid_bundle_and_exact_inventory(self):
        self.run_check()

    def test_wrong_bundle_identity_rejected(self):
        self.info["CFBundleIdentifier"] = "io.unrelated.application"
        self.write_config()
        self.run_check(success=False)

    def test_config_path_cannot_escape_or_select_another_entry(self):
        self.config["content_root"] = "../outside"
        self.write_config()
        self.run_check(success=False)

    def test_changed_resource_bytes_rejected(self):
        (self.content / "release.html").write_bytes(b"changed")
        self.run_check(success=False)

    def test_linked_launch_configuration_rejected(self):
        config = self.resources / "webkit-launch.json"
        outside = self.base / "outside.json"
        outside.write_bytes(config.read_bytes())
        config.unlink()
        config.symlink_to(outside)
        self.run_check(success=False)

    def test_linked_resources_container_rejected(self):
        outside = self.base / "outside-resources"
        self.resources.rename(outside)
        self.resources.symlink_to(outside, target_is_directory=True)
        self.run_check(success=False)

    def test_linked_resource_rejected(self):
        (self.content / "release.html").unlink()
        other = self.base / "outside.html"
        other.write_text("outside")
        (self.content / "release.html").symlink_to(other)
        self.run_check(success=False)

    def test_state_lock_and_own_log_updates(self):
        self.run_check("lock")

    def test_unknown_state_preserved(self):
        state = self.home / "Library/Application Support/dryport/webkit/4294900001"
        state.mkdir(parents=True, mode=0o700)
        (state / "keep").write_text("preserved")
        self.run_check("state", success=False)
        self.assertEqual([p.name for p in state.iterdir()], ["keep"])

    def test_state_parent_link_rejected_without_outside_writes(self):
        parent = self.home / "Library/Application Support"
        parent.mkdir(parents=True)
        outside = self.base / "outside"
        outside.mkdir()
        (parent / "dryport").symlink_to(outside, target_is_directory=True)
        self.run_check("state", success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_log_link_is_not_followed_or_replaced(self):
        self.run_check("log-alias")

    def test_natural_and_fallback_close_receipts(self):
        self.run_check("receipt")

    def test_exact_script_synthetic_beforeunload_cannot_steal_fallback(self):
        self.run_check("lifecycle")

    def test_abandoned_unpublished_state_does_not_block_retry(self):
        root = self.home / "Library/Application Support/dryport/webkit"
        abandoned = root / ".state-interrupted"
        abandoned.mkdir(parents=True, mode=0o700)
        (abandoned / ".dryport-webkit.json").write_text("partial")
        self.run_check("state")
        marker = root / "4294900001/.dryport-webkit.json"
        self.assertEqual(json.loads(marker.read_text())["appid"], "4294900001")
        self.assertEqual((abandoned / ".dryport-webkit.json").read_text(), "partial")

    def test_inventory_and_reader_share_fixed_root_after_path_replacement(self):
        self.run_check("root-race")

    def test_sparse_oversized_file_is_rejected_before_hashing(self):
        with (self.content / "sparse.dat").open("wb") as file:
            file.truncate(512 * 1024 * 1024 + 1)
        self.run_check(success=False)

    def test_resource_depth_limit(self):
        path = self.content
        for _ in range(17):
            path = path / "nested"
            path.mkdir()
        self.run_check(success=False)

    def test_resource_entry_count_limit_includes_empty_directories(self):
        for index in range(4096):
            (self.content / f"empty-{index}").mkdir()
        self.run_check(success=False)


if __name__ == "__main__":
    unittest.main()
