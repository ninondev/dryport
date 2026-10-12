// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 列出或切换当前键盘输入源（TIS）。自动化按键前把输入法切到 ABC，免得游戏里的字母键被中文候选框截走。
//
// 编译：swiftc -O tools/input-source.swift -o "$SN_RUNTIMES/bin/input-source"
// 用法：
//   input-source                 打印当前输入源 id
//   input-source --list          列出可选输入源（id 与名字）
//   input-source --select <id>   切换，例如 com.apple.keylayout.ABC；成功打印切换后的 id
// 只改当前登录会话的输入源，不改系统设置里启用的输入源列表；测完可以再切回去。

import Carbon
import Foundation

func current() -> String {
    let source = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
    let ptr = TISGetInputSourceProperty(source, kTISPropertyInputSourceID)!
    return Unmanaged<CFString>.fromOpaque(ptr).takeUnretainedValue() as String
}

func list() -> [(String, String)] {
    let filter = [kTISPropertyInputSourceIsSelectCapable as String: true] as CFDictionary
    guard let sources = TISCreateInputSourceList(filter, false)?.takeRetainedValue() as? [TISInputSource] else { return [] }
    return sources.map { s in
        let id = Unmanaged<CFString>.fromOpaque(TISGetInputSourceProperty(s, kTISPropertyInputSourceID)!).takeUnretainedValue() as String
        let name = Unmanaged<CFString>.fromOpaque(TISGetInputSourceProperty(s, kTISPropertyLocalizedName)!).takeUnretainedValue() as String
        return (id, name)
    }
}

let args = CommandLine.arguments.dropFirst()
switch args.first {
case nil:
    print(current())
case "--list":
    for (id, name) in list() { print("\(id)\t\(name)") }
case "--select":
    guard args.count >= 2 else { FileHandle.standardError.write("--select 需要输入源 id\n".data(using: .utf8)!); exit(2) }
    let want = Array(args)[1]
    let filter = [kTISPropertyInputSourceID as String: want] as CFDictionary
    guard let sources = TISCreateInputSourceList(filter, false)?.takeRetainedValue() as? [TISInputSource], let source = sources.first else {
        FileHandle.standardError.write("没有这个输入源：\(want)（--list 看可选的）\n".data(using: .utf8)!); exit(3)
    }
    let status = TISSelectInputSource(source)
    if status != noErr { FileHandle.standardError.write("切换失败，OSStatus \(status)\n".data(using: .utf8)!); exit(4) }
    usleep(200_000)
    print(current())
default:
    FileHandle.standardError.write("用法：input-source [--list | --select <id>]\n".data(using: .utf8)!); exit(2)
}
