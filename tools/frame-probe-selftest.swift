// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// frame-probe 的自证窗口：一个小窗口按指定 Hz 换颜色重绘，用来核对 frame-probe 量到的帧率是不是它。
// 不是游戏，不进产品；只在本机验工具。
//
// 编译：swiftc -O tools/frame-probe-selftest.swift -o "$SN_RUNTIMES/bin/frame-probe-selftest"
// 用法：frame-probe-selftest <Hz> <秒数>   启动后在 stdout 打印一行 {"windowid": N}，到时自动退出。

import AppKit

let args = CommandLine.arguments
let hz = args.count > 1 ? (Double(args[1]) ?? 60) : 60
let seconds = args.count > 2 ? (Double(args[2]) ?? 15) : 15

final class Canvas: NSView {
    var tick = 0
    override func draw(_ rect: NSRect) {
        let hue = CGFloat(tick % 360) / 360
        NSColor(hue: hue, saturation: 0.8, brightness: 0.9, alpha: 1).setFill()
        rect.fill()
        let text = "\(tick)" as NSString
        text.draw(at: NSPoint(x: 10, y: 10), withAttributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 24, weight: .bold)])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect: NSRect(x: 60, y: 60, width: 320, height: 200),
                      styleMask: [.titled], backing: .buffered, defer: false)
window.title = "frame-probe selftest"
let canvas = Canvas(frame: window.contentView!.bounds)
canvas.autoresizingMask = [.width, .height]
window.contentView?.addSubview(canvas)
window.makeKeyAndOrderFront(nil)
print("{\"windowid\": \(window.windowNumber)}")
fflush(stdout)

let timer = Timer(timeInterval: 1.0 / hz, repeats: true) { _ in
    canvas.tick += 1
    canvas.needsDisplay = true
}
RunLoop.main.add(timer, forMode: .common)
DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { app.terminate(nil) }
app.run()
