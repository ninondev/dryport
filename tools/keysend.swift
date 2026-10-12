// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 投一次按键或一次左键点击：按下、按住 N 毫秒、抬起；按键和 --click 投给指定进程，--click-hid 投进系统事件流。给自动冒烟的「输入有反应」软信号与输入注入实验用。
//
// 为什么按住而不是瞬按：RGSS（mkxp-z）这类引擎每帧轮询键盘状态，按下抬起落在同一帧就看不到；
// 2026-09-13 用 osascript 瞬按对 entry-08 零响应，这是要验证的假设之一。
// 为什么投给 pid：CGEventPostToPid 不用把游戏窗口切到前台，不抢用户焦点；需要「辅助功能」权限，
// macOS 15 起权限状态陈旧时会静默失败（StackOverflow 79518299），所以先对 tools/keysend-selftest.swift 的接收窗口自证。
// --click 也是投给 pid：事件自带坐标，进程以为光标就在那儿，真实光标不动、焦点不被抢，适合点游戏内按钮。
// --click-hid 投给 HID 事件抽：事件走系统事件流，像真点一样落在坐标下面的窗口上，谁在坐标下面谁收到。
// 为什么多这一路：2026-09 实测 Chromium 系（NW.js）窗口不理投给 pid 的点击，窗口在前台也没用；HID 这路它们认。
// 代价：--click-hid 会短暂移动真实光标（移过去、点完再移回来），只给测试窗口用——空闲 CPU 窗口、没人在用这台 Mac。
//
// 编译：swiftc -O tools/keysend.swift -o "$SN_RUNTIMES/bin/keysend"
// 用法：keysend --pid <pid> --key <return|space|escape|z|x|enter|a-z|0-9|up|down|left|right> [--hold-ms 80] [--mod cmd|shift|ctrl|alt ...]
//       keysend --pid <pid> --click <x>,<y> [--hold-ms 80]        全局屏幕坐标（点），整数或小数
//       keysend [--pid <pid>] --click-hid <x>,<y> [--hold-ms 80]  全局屏幕坐标（点），整数或小数；--pid 给了也被忽略
//       keysend --check      只报告 AXIsProcessTrusted
// --key、--click、--click-hid 恰好给一个。--mod 只作用于 --key，点击不带修饰键。
// --mod 给「正常退出」用（cmd+q 走应用自己的退出路径，和用户按的一样；SIGTERM 不算正常退出）。
// 退出码：0 投出；3 没有辅助功能权限；2 参数错；4 事件创建失败。投出不等于送达，送达要看接收方。
import ApplicationServices
import CoreGraphics
import Foundation

let keyCodes: [String: CGKeyCode] = [
    "return": 36, "enter": 36, "space": 49, "escape": 53, "tab": 48, "delete": 51,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
    "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
]

let modFlags: [String: CGEventFlags] = [
    "cmd": .maskCommand, "shift": .maskShift, "ctrl": .maskControl, "alt": .maskAlternate,
]

var pid: pid_t = 0
var key: String?
var click: (Double, Double)?
var clickHid: (Double, Double)?
var holdMs = 80
var checkOnly = false
var flags: CGEventFlags = []
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    switch a {
    case "--pid": pid = pid_t(args.removeFirst()) ?? 0
    case "--key": key = args.removeFirst().lowercased()
    case "--click":
        let xy = args.removeFirst().split(separator: ",", omittingEmptySubsequences: false)
        guard xy.count == 2, let x = Double(String(xy[0]).trimmingCharacters(in: .whitespaces)),
              let y = Double(String(xy[1]).trimmingCharacters(in: .whitespaces)) else {
            FileHandle.standardError.write("--click 要一对 X,Y 坐标（点），例如 --click 640,360\n".data(using: .utf8)!); exit(2)
        }
        click = (x, y)
    case "--click-hid":
        let xy = args.removeFirst().split(separator: ",", omittingEmptySubsequences: false)
        guard xy.count == 2, let x = Double(String(xy[0]).trimmingCharacters(in: .whitespaces)),
              let y = Double(String(xy[1]).trimmingCharacters(in: .whitespaces)) else {
            FileHandle.standardError.write("--click-hid 要一对 X,Y 坐标（点），例如 --click-hid 640,360\n".data(using: .utf8)!); exit(2)
        }
        clickHid = (x, y)
    case "--hold-ms": holdMs = Int(args.removeFirst()) ?? 80
    case "--check": checkOnly = true
    case "--mod":
        let m = args.removeFirst().lowercased()
        guard let f = modFlags[m] else {
            FileHandle.standardError.write("不认识的修饰键：\(m)\n".data(using: .utf8)!); exit(2)
        }
        flags.insert(f)
    default:
        FileHandle.standardError.write("不认识的参数：\(a)\n".data(using: .utf8)!); exit(2)
    }
}
let trusted = AXIsProcessTrusted()
if checkOnly {
    print("{\"trusted\": \(trusted)}"); exit(trusted ? 0 : 3)
}
if key == nil && !flags.isEmpty {
    FileHandle.standardError.write("--mod 只对 --key 有效，点击不带修饰键\n".data(using: .utf8)!); exit(2)
}
let modeCount = [key != nil, click != nil, clickHid != nil].filter { $0 }.count
guard modeCount == 1, clickHid != nil || pid > 0, key == nil || keyCodes[key!] != nil else {
    FileHandle.standardError.write(("用法：keysend --pid <pid> --key <名字> [--hold-ms 80] [--mod cmd|shift|ctrl|alt ...]\n"
        + "      keysend --pid <pid> --click <x>,<y> [--hold-ms 80]\n"
        + "      keysend [--pid <pid>] --click-hid <x>,<y> [--hold-ms 80]（三种模式恰好给一个）\n").data(using: .utf8)!); exit(2)
}
if !trusted {
    FileHandle.standardError.write("本进程没有「辅助功能」权限（AXIsProcessTrusted=false），不投\n".data(using: .utf8)!); exit(3)
}
let source = CGEventSource(stateID: .hidSystemState)
if let (cx, cy) = click {
    // 点击和按键一样投给 pid：进程收到的是这个坐标上的左键事件，真实光标不动、焦点不被抢。
    let pt = CGPoint(x: cx, y: cy)
    guard let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left) else { exit(4) }
    down.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    down.postToPid(pid)
    usleep(useconds_t(max(1, holdMs) * 1000))
    up.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    up.postToPid(pid)
    print("{\"posted\": true, \"pid\": \(pid), \"click\": \"\(cx),\(cy)\", \"hold_ms\": \(holdMs)}")
} else if let (hx, hy) = clickHid {
    // Chromium 系（NW.js）窗口不理投给 pid 的点击，这一路投 HID 事件抽：事件进系统事件流，坐标下面是哪个窗口就送到谁头上。
    // 代价是会真的动光标：先记下当前位置，移过去、点、再移回来；只给没人在用的测试窗口。
    let pt = CGPoint(x: hx, y: hy)
    guard let saved = CGEvent(source: nil)?.location,
          let moveIn = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left),
          let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left),
          let moveBack = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: saved, mouseButton: .left) else { exit(4) }
    moveIn.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    moveIn.post(tap: .cghidEventTap)
    usleep(30_000) // 缓 30ms 让移动先被窗口服务器消化，down 别和移动挤在同一瞬间。
    down.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    down.post(tap: .cghidEventTap)
    usleep(useconds_t(max(1, holdMs) * 1000))
    up.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    up.post(tap: .cghidEventTap)
    usleep(30_000) // up 之后也缓 30ms 再移回原位，点击和恢复别挤在一起。
    moveBack.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    moveBack.post(tap: .cghidEventTap)
    print("{\"posted\": true, \"click_hid\": \"\(hx),\(hy)\", \"hold_ms\": \(holdMs), \"cursor_restored\": \"\(saved.x),\(saved.y)\"}")
} else {
    let code = keyCodes[key!]!
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false) else { exit(4) }
    if !flags.isEmpty {
        down.flags = flags
        up.flags = flags
    }
    down.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    down.postToPid(pid)
    usleep(useconds_t(max(1, holdMs) * 1000))
    up.timestamp = CGEventTimestamp(clock_gettime_nsec_np(CLOCK_UPTIME_RAW))
    up.postToPid(pid)
    print("{\"posted\": true, \"pid\": \(pid), \"key\": \"\(key!)\", \"hold_ms\": \(holdMs), \"mods\": \(flags.rawValue)}")
}
