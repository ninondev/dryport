// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// keysend 的接收端自证：开一个窗口，收到 keyDown/keyUp 就打印一行 JSON（键码、时间、按住多久），N 秒后退出。
// 没有这一步不能对游戏下结论：辅助功能权限状态陈旧时 CGEventPostToPid 会静默失败。
// 编译：swiftc -O tools/keysend-selftest.swift -o "$SN_RUNTIMES/bin/keysend-selftest"
// 用法：keysend-selftest <秒数>   打印 {"pid": ...} 后开始等按键
import AppKit
import Foundation

let seconds = Double(CommandLine.arguments.dropFirst().first ?? "10") ?? 10
final class View: NSView {
    var downAt: [UInt16: Double] = [:]
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with e: NSEvent) {
        downAt[e.keyCode] = e.timestamp
        print("{\"event\": \"down\", \"keyCode\": \(e.keyCode), \"t\": \(e.timestamp)}"); fflush(stdout)
    }
    override func keyUp(with e: NSEvent) {
        let held = downAt[e.keyCode].map { (e.timestamp - $0) * 1000 } ?? -1
        print("{\"event\": \"up\", \"keyCode\": \(e.keyCode), \"held_ms\": \(Int(held))}"); fflush(stdout)
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let win = NSWindow(contentRect: NSRect(x: 200, y: 200, width: 320, height: 160), styleMask: [.titled], backing: .buffered, defer: false)
win.title = "keysend-selftest"
let view = View(frame: win.contentView!.bounds)
win.contentView = view
win.makeKeyAndOrderFront(nil)
win.makeFirstResponder(view)
print("{\"pid\": \(ProcessInfo.processInfo.processIdentifier)}"); fflush(stdout)
DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { app.terminate(nil) }
app.run()
