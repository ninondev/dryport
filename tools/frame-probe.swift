// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 按窗口 id 订阅窗口的合成帧（ScreenCaptureKit），逐帧输出时间戳与状态，供 tools/bench-game.py 算帧时间。
//
// 为什么这样做：ffmpeg/avfoundation 与 screencapture -v 只能录整屏，会把别的窗口带进证据；
// CGWindowListCreateImage 在 macOS 15 起已下线。SCStream 只在窗口内容真的更新时交付一帧（status=complete），
// 所以「有效新帧」直接来自窗口服务器，不用截图比哈希。画面内容不落盘，只记时间戳。
//
// 编译（产物放 SN_RUNTIMES/bin，不进仓库）：
//   swiftc -O tools/frame-probe.swift -o "$SN_RUNTIMES/bin/frame-probe"
// 用法：
//   frame-probe <windowid> <秒数> [采样上限 Hz，默认 240]
// 输出：每行一条 JSON {"t": 相对秒, "pts": 帧的呈现时间戳秒, "status": "complete|idle|blank|suspended|started|stopped"}，
// 最后一行 {"done": true, "frames": 有效新帧数, "delivered": 交付总数, "seconds": 实际秒数}。
// 需要「屏幕录制」权限；没有权限时 SCShareableContent 会报错并以 3 退出。

import AppKit
import Foundation
import ScreenCaptureKit
import CoreMedia

let args = CommandLine.arguments
guard args.count >= 3, let windowID = UInt32(args[1]), let seconds = Double(args[2]) else {
    FileHandle.standardError.write("用法: frame-probe <windowid> <秒数> [Hz]\n".data(using: .utf8)!)
    exit(2)
}
let hz = args.count >= 4 ? (Int32(args[3]) ?? 240) : 240

final class Sink: NSObject, SCStreamOutput {
    let start = CFAbsoluteTimeGetCurrent()
    var delivered = 0
    var frames = 0
    let out = FileHandle.standardOutput
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]]
        let raw = attachments?.first?[.status] as? Int ?? -1
        let status: String
        switch SCFrameStatus(rawValue: raw) {
        case .complete: status = "complete"
        case .idle: status = "idle"
        case .blank: status = "blank"
        case .suspended: status = "suspended"
        case .started: status = "started"
        case .stopped: status = "stopped"
        default: status = "unknown"
        }
        delivered += 1
        if status == "complete" { frames += 1 }
        let pts = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        let line = String(format: "{\"t\": %.6f, \"pts\": %.6f, \"status\": \"%@\"}\n", CFAbsoluteTimeGetCurrent() - start, pts, status)
        out.write(line.data(using: .utf8)!)
    }
}

// 命令行进程要先接上窗口服务器，否则 ScreenCaptureKit 在 CGS_REQUIRE_INIT 上断言退出。
_ = NSApplication.shared
NSApp.setActivationPolicy(.prohibited)

let sink = Sink()
let semaphore = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0

// 看门狗：窗口在采样中途消失时 stopCapture 可能永远不返回（2026-09-14 一批冒烟留下 22 个 frame-probe 孤儿，
// replayd 吃掉三个核）。到点就带着已数到的帧数退出，进程不能比它要观察的窗口活得久。
DispatchQueue.global().asyncAfter(deadline: .now() + seconds + 15) {
    let total = CFAbsoluteTimeGetCurrent() - sink.start
    print("{\"done\": true, \"frames\": \(sink.frames), \"delivered\": \(sink.delivered), \"seconds\": \(String(format: "%.3f", total)), \"watchdog\": true}")
    exit(exitCode == 0 ? 5 : exitCode)
}

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let window = content.windows.first(where: { $0.windowID == windowID }) else {
            FileHandle.standardError.write("找不到窗口 id \(windowID)\n".data(using: .utf8)!)
            exitCode = 4; semaphore.signal(); return
        }
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let config = SCStreamConfiguration()
        config.width = 160; config.height = 90              // 只要「有没有新帧」，缩到最小省 GPU
        config.minimumFrameInterval = CMTime(value: 1, timescale: hz)
        config.queueDepth = 8
        config.showsCursor = false
        config.pixelFormat = kCVPixelFormatType_32BGRA
        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        try stream.addStreamOutput(sink, type: .screen, sampleHandlerQueue: DispatchQueue(label: "frame-probe"))
        try await stream.startCapture()
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
        try await stream.stopCapture()
    } catch {
        FileHandle.standardError.write("frame-probe: \(error)\n".data(using: .utf8)!)
        exitCode = 3
    }
    semaphore.signal()
}
semaphore.wait()
let total = CFAbsoluteTimeGetCurrent() - sink.start
print("{\"done\": true, \"frames\": \(sink.frames), \"delivered\": \(sink.delivered), \"seconds\": \(String(format: "%.3f", total))}")
exit(exitCode)
