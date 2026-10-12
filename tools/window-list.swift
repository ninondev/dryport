// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 按 owner 名列出屏幕上的窗口，输出 JSON：[{id, pid, owner, title, layer, bounds}]。
//
// 为什么需要它：取证截图必须按窗口 id 截（screencapture -l <id>），
// 全屏截图会被前景窗口盖住，还会把别的窗口内容带进证据。
// 而 osascript 的 System Events 看不到别的应用的窗口，系统 python3 也没有 Quartz 模块，
// 所以这里用 CoreGraphics 直接问。
//
// 编译（产物放 SN_RUNTIMES，不进仓库）：
//   swiftc -O tools/window-list.swift -o "$SN_RUNTIMES/bin/window-list"
// 用法：
//   window-list              列出全部
//   window-list "Cat or"     只列 owner 名含这个子串的（大小写不敏感）
//   window-list --screen     只打印主显示器尺寸 {width, height}（判「新窗口是不是铺满整屏」用）
//
// 注意：Wine 游戏的 owner 名常常不是游戏名（Unreal 是 exe 名，GameMaker 是 "wine"），
// 所以调用方要么传子串，要么不传参数自己挑；pid 字段（2026-09-14 加）让调用方能按进程对窗口，
// 自动冒烟靠「启动前后 id 差集 + pid」认游戏窗口，不猜 owner 名。
// kCGWindowName 只在本进程有屏幕录制权限时对别的 app 非空，标题只能当次要信号。

import CoreGraphics
import Foundation

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--screen" {
    let id = CGMainDisplayID()
    let dict: [String: Any] = ["width": Int(CGDisplayPixelsWide(id)), "height": Int(CGDisplayPixelsHigh(id))]
    let data = try JSONSerialization.data(withJSONObject: dict, options: [])
    print(String(data: data, encoding: .utf8) ?? "{}")
    exit(0)
}
let needle = args.first?.lowercased() ?? ""

let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
guard let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
    print("[]")
    exit(0)
}

var result: [[String: Any]] = []
for window in windows {
    let owner = window[kCGWindowOwnerName as String] as? String ?? ""
    if !needle.isEmpty && !owner.lowercased().contains(needle) { continue }
    guard let raw = window[kCGWindowBounds as String] as? [String: Any],
          let bounds = CGRect(dictionaryRepresentation: raw as CFDictionary) else { continue }
    // 菜单栏、控制中心之类的小窗口对取证没有意义，滤掉
    if bounds.width < 120 || bounds.height < 120 { continue }
    let id = window[kCGWindowNumber as String] as? Int ?? 0
    let pid = window[kCGWindowOwnerPID as String] as? Int ?? 0
    let layer = window[kCGWindowLayer as String] as? Int ?? 0
    result.append([
        "id": id,
        "pid": pid,
        "owner": owner,
        "title": window[kCGWindowName as String] as? String ?? "",
        "layer": layer,
        "bounds": ["X": Int(bounds.origin.x), "Y": Int(bounds.origin.y),
                   "Width": Int(bounds.width), "Height": Int(bounds.height)],
    ])
}

// 大窗口优先，取证时通常要的是游戏主窗口
result.sort { a, b in
    let ab = a["bounds"] as! [String: Int], bb = b["bounds"] as! [String: Int]
    return ab["Width"]! * ab["Height"]! > bb["Width"]! * bb["Height"]!
}

let data = try JSONSerialization.data(withJSONObject: result, options: [])
print(String(data: data, encoding: .utf8) ?? "[]")
