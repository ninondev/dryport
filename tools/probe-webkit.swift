// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 自有 WebKit 机制探针；原资源只读，不提供 Node 或游戏内容修改。
import AppKit
import CryptoKit
import Darwin
import Foundation
import UniformTypeIdentifiers
import WebKit

struct Options {
    let entry: URL
    let resources: URL
    let output: URL
    let store: UUID
    let timeout: Double
    let size: NSSize
    let mode: String
    let lifecycle: String
    var deadlineSeconds: Double { timeout + 5 }

    init(_ arguments: [String]) throws {
        var values: [String: String] = [:]
        let allowed: Set<String> = ["--entry", "--output", "--storeUUID", "--timeout", "--width", "--height", "--mode", "--lifecycle"]
        var index = 0
        while index < arguments.count {
            let key = arguments[index]
            guard allowed.contains(key), index + 1 < arguments.count, values[key] == nil else {
                throw ProbeFailure("用法：probe-webkit --entry <绝对 HTML> --output <新证据目录> --storeUUID <UUID> [--timeout 10] [--width 1280] [--height 720] [--mode file|scheme]")
            }
            values[key] = arguments[index + 1]
            index += 2
        }
        guard let path = values["--entry"], path.hasPrefix("/"),
              let destination = values["--output"], destination.hasPrefix("/"),
              let identifier = values["--storeUUID"], let uuid = UUID(uuidString: identifier),
              uuid.uuidString != "00000000-0000-0000-0000-000000000000" else {
            throw ProbeFailure("需要绝对入口、新证据目录及非零 UUID。")
        }
        entry = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
        resources = entry.deletingLastPathComponent()
        let file = try entry.resourceValues(forKeys: [.isRegularFileKey])
        guard file.isRegularFile == true, ["html", "htm"].contains(entry.pathExtension.lowercased()) else {
            throw ProbeFailure("入口必须是现有 HTML 文件：\(entry.path)")
        }
        let requested = URL(fileURLWithPath: destination).standardizedFileURL
        output = requested.deletingLastPathComponent().resolvingSymlinksInPath()
            .appendingPathComponent(requested.lastPathComponent, isDirectory: true)
        guard !isWithin(output, resources), !isWithin(resources, output),
              !FileManager.default.fileExists(atPath: output.path),
              (try? FileManager.default.destinationOfSymbolicLink(atPath: output.path)) == nil else {
            throw ProbeFailure("证据目录必须全新且不与原资源目录重叠：\(output.path)")
        }
        guard let seconds = Double(values["--timeout"] ?? "10"), seconds.isFinite, (2...300).contains(seconds),
              let width = Double(values["--width"] ?? "1280"), (320...2560).contains(width),
              let height = Double(values["--height"] ?? "720"), (240...1600).contains(height) else {
            throw ProbeFailure("超时须为 2–300 秒；窗口宽 320–2560、高 240–1600。")
        }
        timeout = seconds
        size = NSSize(width: width, height: height)
        store = uuid
        mode = values["--mode"] ?? "file"
        guard ["file", "scheme"].contains(mode) else { throw ProbeFailure("模式仅支持 file 或 scheme。") }
        lifecycle = values["--lifecycle"] ?? "none"
        guard ["none", "terminal-beforeunload"].contains(lifecycle), lifecycle == "none" || mode == "scheme" else {
            throw ProbeFailure("生命周期试验仅支持 scheme 模式的 terminal-beforeunload，默认 none。")
        }
    }
}

// 两条任务共用导航开始时的绝对基点。任务延后获调度不能重置观察时长。
struct ProbeSchedule {
    let clock = ContinuousClock()
    let origin = ContinuousClock.now

    var elapsed: Double {
        let parts = origin.duration(to: clock.now).components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }

    func wait(until offset: Double) async throws {
        try await clock.sleep(until: origin.advanced(by: .seconds(offset)), tolerance: .zero)
    }

    func checkpointOffsets(before timeout: Double) -> [Double] {
        Array(stride(from: 30.0, to: timeout, by: 30.0))
    }
}

let observationScript = #"""
(() => {
  const inputTypes = ['mousedown', 'mousemove', 'mouseup', 'mouseleave', 'pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'pointerleave'];
  const state = {errors: [], rejections: [], raf: {frames: 0, intervals: [], last: null},
    input: {counts: Object.fromEntries(inputTypes.map(type => [type, 0])), last: []}, start: performance.now()};
  Object.defineProperty(window, '__steamNativeWebKitObservation', {value: state});
  for (const type of inputTypes) {
    document.addEventListener(type, event => {
      state.input.counts[type]++;
      const target = event.target;
      state.input.last.push({type, time: performance.now(),
        clientX: typeof event.clientX === 'number' ? event.clientX : null,
        clientY: typeof event.clientY === 'number' ? event.clientY : null,
        buttons: typeof event.buttons === 'number' ? event.buttons : null,
        button: typeof event.button === 'number' ? event.button : null,
        target: {id: typeof target?.id === 'string' ? target.id : '', tag: target?.tagName || target?.nodeName || ''},
        isTrusted: event.isTrusted === true});
      if (state.input.last.length > 32) state.input.last.shift();
    }, {capture: true, passive: true});
  }
  addEventListener('error', event => {
    if (state.errors.length < 100) state.errors.push({
      message: String(event.message || 'resource error'), filename: event.filename || '',
      line: event.lineno || 0, column: event.colno || 0,
      resource: event.target && (event.target.src || event.target.href) || ''
    });
  }, true);
  addEventListener('unhandledrejection', event => {
    if (state.rejections.length < 100) state.rejections.push(String(event.reason));
  });
  function frame(now) {
    const raf = state.raf;
    if (raf.last !== null && raf.intervals.length < 7200) raf.intervals.push(now - raf.last);
    raf.last = now;
    raf.frames++;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
"""#

let pullObservation = #"""
JSON.stringify({
  href: location.href, readyState: document.readyState, title: document.title,
  viewport: {width: innerWidth, height: innerHeight, scale: devicePixelRatio},
  requireType: typeof window.require,
  messageHandlersPresent: !!(window.webkit && window.webkit.messageHandlers),
  fullscreenEnabled: document.fullscreenEnabled ?? document.webkitFullscreenEnabled ?? null,
  localStorage: (() => {
    try {
      const keyLengths = Object.create(null);
      for (let index = 0; index < window.localStorage.length; index++) {
        const key = window.localStorage.key(index);
        if (key !== null) {
          const value = window.localStorage.getItem(key);
          keyLengths[key] = value === null ? null : value.length;
        }
      }
      return {ok: true, keyLengths};
    } catch { return {ok: false, error: '无法读取此 origin 的 localStorage'}; }
  })(),
  observation: window.__steamNativeWebKitObservation || null,
  fixture: window.__steamNativeWebKitFixture || null
})
"""#

// 周期采样只取有界输入历史和存储长度，不取 RAF 数组、不做 GPU 快照。
let pullCheckpoint = #"""
JSON.stringify({
  href: location.href, readyState: document.readyState,
  input: window.__steamNativeWebKitObservation?.input || null,
  error_count: window.__steamNativeWebKitObservation?.errors.length ?? null,
  rejection_count: window.__steamNativeWebKitObservation?.rejections.length ?? null,
  localStorage: (() => {
    try {
      const keyLengths = Object.create(null);
      for (let index = 0; index < window.localStorage.length; index++) {
        const key = window.localStorage.key(index);
        if (key !== null) {
          const value = window.localStorage.getItem(key);
          keyLengths[key] = value === null ? null : value.length;
        }
      }
      return {ok: true, keyLengths};
    } catch { return {ok: false, error: '无法读取此 origin 的 localStorage'}; }
  })()
})
"""#

@available(macOS 14.0, *)
@MainActor
final class LifecycleMessages: NSObject, WKScriptMessageHandler {
    weak var owner: Probe?
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        owner?.recordLifecycle(message)
    }
}

@available(macOS 14.0, *)
@MainActor
final class Probe: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let options: Options
    let before: [String: ResourceRecord]
    let started = Date()
    var window: NSWindow?
    var view: WKWebView?
    var timer: Task<Void, Never>?
    var deadline: Task<Void, Never>?
    var finishing = false
    var finishedNavigation = false
    var failures: [String] = []
    var blocked: [String] = []
    var dialogs: [String] = []
    var navigationErrors: [String] = []
    var latestStage = "not_started"
    var stages: [[String: Any]] = []
    var pageCaptured = false
    var schedule: ProbeSchedule?
    var timingEvents: [[String: Any]] = []
    var closingNavigation: WKNavigation?
    var closingReason: String?
    var closeDeadline: Task<Void, Never>?
    let lifecycleWorld = WKContentWorld.world(name: "dryport-lifecycle-observer")
    let lifecycleMessages = LifecycleMessages()
    var lifecycleEvents: [[String: Any]] = []
    let resourceScheme: ResourceScheme?

    init(options: Options) throws {
        self.options = options
        resourceScheme = options.mode == "scheme"
            ? try ResourceScheme(root: options.resources, host: options.store.uuidString.lowercased()) : nil
        before = try inventory(options.resources)
        try FileManager.default.createDirectory(at: options.output, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        super.init()
        lifecycleMessages.owner = self
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = WKWebsiteDataStore(forIdentifier: options.store)
        configuration.preferences.isElementFullscreenEnabled = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        if let handler = resourceScheme {
            configuration.setURLSchemeHandler(handler, forURLScheme: "sn-resource")
        }
        // 添加 message handler 会改变部分游戏的 iOS 分支判断。
        configuration.userContentController.addUserScript(WKUserScript(source: observationScript,
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        if options.lifecycle == "terminal-beforeunload" {
            // 只在独立命名空间开放只写回执口；页面世界看不到 messageHandlers。
            configuration.userContentController.add(lifecycleMessages, contentWorld: lifecycleWorld, name: "snLifecycle")
            configuration.userContentController.addUserScript(WKUserScript(source: terminalLifecycleScript,
                injectionTime: .atDocumentStart, forMainFrameOnly: true, in: lifecycleWorld))
        }
        let web = WKWebView(frame: NSRect(origin: .zero, size: options.size), configuration: configuration)
        web.navigationDelegate = self
        web.uiDelegate = self
        let win = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable, .miniaturizable],
                           backing: .buffered, defer: false)
        win.title = "WebKit 自有机制观察"
        win.collectionBehavior = [.fullScreenNone]
        win.contentView = web
        win.delegate = self
        win.center()
        win.makeKeyAndOrderFront(nil)
        window = win
        view = web
        let timing = ProbeSchedule()
        schedule = timing
        recordTimer("scheduled", planned: 0)
        recordProgress("navigation_started")
        NSApplication.shared.activate(ignoringOtherApps: true)
        if options.mode == "scheme" {
            let root = URL(string: "sn-resource://\(options.store.uuidString.lowercased())/")!
            web.load(URLRequest(url: root.appendingPathComponent(options.entry.lastPathComponent)))
        } else {
            web.loadFileURL(options.entry, allowingReadAccessTo: options.resources)
        }
        timer = Task { [weak self] in
            guard let self else { return }
            recordTimer("observer_task_started", planned: options.timeout)
            for offset in timing.checkpointOffsets(before: options.timeout) {
                guard !finishing else { return }
                // 不因此前 IPC 慢而补发一串采样；结束观察的绝对时刻不变。
                if timing.elapsed >= offset {
                    recordTimer("checkpoint_skipped_late", planned: offset)
                    continue
                }
                do { try await timing.wait(until: offset) } catch { return }
                guard !finishing else { return }
                recordTimer("checkpoint_woke", planned: offset)
                if timing.elapsed < options.timeout { await checkpoint(planned: offset, timing: timing) }
            }
            do { try await timing.wait(until: options.timeout) } catch { return }
            recordTimer("observer_woke", planned: options.timeout)
            await finish(reason: "observation_interval_elapsed")
        }
        deadline = Task { [weak self] in
            guard let self else { return }
            recordTimer("deadline_task_started", planned: options.deadlineSeconds)
            do { try await timing.wait(until: options.deadlineSeconds) } catch { return }
            recordTimer("deadline_woke", planned: options.deadlineSeconds)
            // 页面脚本或快照 IPC 无响应时，不能无限等待它返回。
            let failure: [String: Any] = ["protocol": 1, "ok": false,
                "reason": "observation_deadline_exceeded", "entry": options.entry.path,
                "store_uuid": options.store.uuidString,
                "deadline_seconds": options.deadlineSeconds,
                "navigation_elapsed_seconds": timing.elapsed, "timers_file": "timers.json",
                "stage_at_deadline": latestStage, "progress_file": "progress.json",
                "page_captured": pageCaptured,
                "failures": ["观察未能在截止前完成，探针已退出"]]
            if let data = try? JSONSerialization.data(withJSONObject: failure, options: [.sortedKeys]) {
                try? data.write(to: options.output.appendingPathComponent("deadline.json"), options: .withoutOverwriting)
            }
            FileHandle.standardError.write(Data("WebKit 观察超时，证据目录：\(options.output.path)\n".utf8))
            exit(1)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        finishedNavigation = true
        if let closingNavigation, navigation === closingNavigation, let reason = closingReason {
            Task { await collect(reason: reason) }
        }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
        navigationErrors.append(error.localizedDescription)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error) {
        navigationErrors.append(error.localizedDescription)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        failures.append("WebKit 内容进程终止")
        Task { await finish(reason: "web_content_process_terminated") }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        let localFile = url.map { $0.isFileURL && isWithin($0.standardizedFileURL.resolvingSymlinksInPath(), options.resources) } ?? false
        let localScheme = url.map { options.mode == "scheme" && $0.scheme == "sn-resource" && $0.host == options.store.uuidString.lowercased() } ?? false
        guard (localFile && options.mode == "file") || localScheme,
              navigationAction.targetFrame != nil else {
            blocked.append(navigationAction.request.url?.absoluteString ?? "无目标")
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        blocked.append(navigationAction.request.url?.absoluteString ?? "新窗口")
        return nil
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor () -> Void) {
        dialogs.append(message)
        completionHandler()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (Bool) -> Void) {
        dialogs.append(message)
        completionHandler(false)
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (String?) -> Void) {
        dialogs.append(prompt)
        completionHandler(nil)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        Task { await finish(reason: "window_closed") }
        return false
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task { await finish(reason: "application_quit") }
        return .terminateLater
    }

    func recordLifecycle(_ message: WKScriptMessage) {
        guard message.name == "snLifecycle", message.world === lifecycleWorld,
              message.frameInfo.isMainFrame, message.webView === view,
              let body = message.body as? [String: Any],
              let documentID = body["document_id"] as? String, documentID.utf8.count <= 80,
              let kind = body["kind"] as? String,
              ["document_started", "beforeunload", "pagehide", "dispatch_started", "dispatch_returned",
               "dispatch_failed", "script_error", "resource_error", "receipt_overflow"].contains(kind),
              JSONSerialization.isValidJSONObject(body),
              let encoded = try? JSONSerialization.data(withJSONObject: body), encoded.count <= 2048 else {
            if !failures.contains("生命周期回执来源或格式不符。") { failures.append("生命周期回执来源或格式不符。") }
            return
        }
        guard lifecycleEvents.count < 128 else {
            if !failures.contains("生命周期回执超过 128 项上限。") { failures.append("生命周期回执超过 128 项上限。") }
            return
        }
        var event = body
        event["host_elapsed_seconds"] = schedule?.elapsed ?? 0
        lifecycleEvents.append(event)
        if ["dispatch_failed", "script_error", "resource_error", "receipt_overflow"].contains(kind) {
            failures.append("文档生命周期记录到错误，见 lifecycle.json。")
        }
        let report: [String: Any] = ["protocol": 1, "world": lifecycleWorld.name ?? "",
            "events": lifecycleEvents, "saving_verified": false,
            "boundary": "cross-document event/error receipt; not durable save verification"]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: options.output.appendingPathComponent("lifecycle.json"), options: .atomic)
        } catch { failures.append("生命周期回执写入失败：\(error)") }
    }

    func recordProgress(_ stage: String) {
        latestStage = stage
        stages.append(["stage": stage, "elapsed_seconds": Date().timeIntervalSince(started)])
        let progress: [String: Any] = ["protocol": 1, "stage": stage, "stages": stages,
            "entry": options.entry.path, "store_uuid": options.store.uuidString,
            "page_captured": pageCaptured, "complete": stage == "completed"]
        do {
            let data = try JSONSerialization.data(withJSONObject: progress, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: options.output.appendingPathComponent("progress.json"), options: .atomic)
        } catch { failures.append("阶段回执写入失败：\(error)") }
    }

    func recordTimer(_ event: String, planned: Double) {
        guard let schedule else { return }
        let elapsed = schedule.elapsed
        timingEvents.append(["event": event, "planned_offset_seconds": planned,
                             "navigation_elapsed_seconds": elapsed,
                             "lateness_seconds": max(0, elapsed - planned)])
        let report: [String: Any] = ["protocol": 1, "clock": "ContinuousClock",
            "origin": "before navigation request", "tolerance_seconds": 0,
            "observation_offset_seconds": options.timeout,
            "deadline_offset_seconds": options.deadlineSeconds,
            "events": timingEvents,
            "boundary": "MainActor scheduling is not hard realtime; external process supervisor bounds synchronous stalls"]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: options.output.appendingPathComponent("timers.json"), options: .atomic)
        } catch { failures.append("计时回执写入失败：\(error)") }
    }

    func checkpoint(planned: Double, timing: ProbeSchedule) async {
        guard !finishing, let web = view else { return }
        let began = timing.elapsed
        recordProgress("checkpoint_evaluate_started")
        do {
            let value = try await web.evaluateJavaScript(pullCheckpoint)
            guard !finishing else { return }
            guard let text = value as? String, let data = text.data(using: .utf8) else {
                throw ProbeFailure("WebKit 未返回 JSON 周期观察数据")
            }
            let report: [String: Any] = ["protocol": 1, "kind": "passive input and storage-length checkpoint",
                "planned_offset_seconds": planned, "evaluation_started_seconds": began,
                "navigation_elapsed_seconds": timing.elapsed,
                "evaluation_seconds": timing.elapsed - began,
                "page": try JSONSerialization.jsonObject(with: data)]
            let captured = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            let name = String(format: "checkpoint-%04d.json", Int(planned))
            try captured.write(to: options.output.appendingPathComponent(name), options: .withoutOverwriting)
            recordProgress("checkpoint_saved")
        } catch {
            guard !finishing else { return }
            failures.append("周期观察失败：\(error)")
            recordProgress("checkpoint_failed")
        }
    }

    func finish(reason: String) async {
        guard !finishing else { return }
        let normal = ["window_closed", "application_quit", "observation_interval_elapsed"].contains(reason)
        if options.lifecycle == "terminal-beforeunload", normal, let web = view {
            guard closingReason == nil else { return }
            closingReason = reason
            recordProgress("terminal_reload_requested")
            // 同 URL 重载离开当前文档；成功后马上销毁视图，不返回旧的游戏对象。
            let closeClock = ContinuousClock()
            let closeDue = closeClock.now.advanced(by: .seconds(5))
            closeDeadline = Task { [weak self] in
                do { try await closeClock.sleep(until: closeDue, tolerance: .zero) } catch { return }
                guard let self else { return }
                let result: [String: Any] = ["protocol": 1, "ok": false,
                    "reason": "terminal_reload_or_capture_deadline", "stage": latestStage,
                    "lifecycle": options.lifecycle, "saving_verified": false]
                if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
                    try? data.write(to: options.output.appendingPathComponent("close-deadline.json"), options: .withoutOverwriting)
                }
                exit(1)
            }
            closingNavigation = web.reload()
            if closingNavigation != nil { return }
            failures.append("终态重载未能开始，未验证退出时保存。")
        }
        await collect(reason: reason)
    }

    func collect(reason: String) async {
        guard !finishing else { return }
        finishing = true
        recordProgress("finish_started")
        var report: [String: Any] = [
            "protocol": 1, "kind": "WKWebView mechanism observation; not game acceptance or comparative performance",
            "ok_means": "observation, snapshot, and unchanged resources recorded; page behavior requires separate assertions",
            "entry": options.entry.path, "read_access_root": options.resources.path,
            "mode": options.mode, "lifecycle": options.lifecycle,
            "saving_verified": false,
            "lifecycle_boundary": "opt-in untrusted terminal event; no cancellation or generic compatibility guarantee",
            "element_fullscreen_preference": false,
            "store_uuid": options.store.uuidString, "navigation_finished": finishedNavigation,
            "reason": reason, "elapsed_seconds": Date().timeIntervalSince(started),
            "blocked_navigations": blocked, "dialogs": dialogs,
            "navigation_errors": navigationErrors, "denied_resource_requests": resourceScheme?.denied ?? [],
            "window_fullscreen": window?.styleMask.contains(.fullScreen) ?? false
        ]
        if let web = view {
            recordProgress("evaluate_javascript_started")
            do {
                let value = try await web.evaluateJavaScript(pullObservation)
                guard let text = value as? String, let data = text.data(using: .utf8) else {
                    throw ProbeFailure("WebKit 未返回 JSON 观察数据")
                }
                report["page"] = try JSONSerialization.jsonObject(with: data)
                // 快照 IPC 可能独立挂起；已读到的页面证据必须先于快照持久化。
                var partial = report
                partial["stage"] = "page_captured"
                partial["complete"] = false
                partial["elapsed_seconds"] = Date().timeIntervalSince(started)
                let captured = try JSONSerialization.data(withJSONObject: partial, options: [.prettyPrinted, .sortedKeys])
                try captured.write(to: options.output.appendingPathComponent("page-observation.json"), options: .withoutOverwriting)
                pageCaptured = true
                recordProgress("page_captured")
            } catch {
                failures.append("读取或保存页面观察数据失败：\(error)")
                recordProgress("evaluate_javascript_failed")
            }
            recordProgress("snapshot_started")
            do {
                let picture = try await web.takeSnapshot(configuration: nil)
                guard let tiff = picture.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
                      let png = bitmap.representation(using: .png, properties: [:]) else {
                    throw ProbeFailure("WebKit 快照无法编码为 PNG")
                }
                try png.write(to: options.output.appendingPathComponent("snapshot.png"), options: .withoutOverwriting)
                report["snapshot"] = "snapshot.png"
                recordProgress("snapshot_saved")
            } catch {
                failures.append("WebKit 快照失败：\(error)")
                recordProgress("snapshot_failed")
            }
        }
        recordProgress("resource_inventory_started")
        do {
            let after = try inventory(options.resources)
            report["resources_unchanged"] = before == after
            if before != after { failures.append("原资源清单或 SHA-256 发生变化") }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            try encoder.encode(before).write(to: options.output.appendingPathComponent("resources-before.json"), options: .withoutOverwriting)
            try encoder.encode(after).write(to: options.output.appendingPathComponent("resources-after.json"), options: .withoutOverwriting)
            recordProgress("resources_compared")
        } catch {
            failures.append("原资源复核失败：\(error)")
            recordProgress("resource_inventory_failed")
        }
        if !finishedNavigation { failures.append("观察时间内页面未完成导航") }
        recordProgress("writing_final_observation")
        report["lifecycle_events"] = lifecycleEvents
        report["failures"] = failures
        report["ok"] = failures.isEmpty
        report["observation_ok"] = failures.isEmpty
        report["elapsed_seconds"] = Date().timeIntervalSince(started)
        report["stages"] = stages
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: options.output.appendingPathComponent("observation.json"), options: .withoutOverwriting)
            print(options.output.appendingPathComponent("observation.json").path)
        } catch {
            FileHandle.standardError.write(Data("写入探针证据失败：\(error)\n".utf8))
            exit(1)
        }
        recordProgress("completed")
        if (report["failures"] as? [String]) != failures {
            // 最后阶段回执失败也必须归并到总回执，不能留下 ok:true 却以失败退出。
            report["failures"] = failures
            report["ok"] = false
            report["observation_ok"] = false
            do {
                let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                try data.write(to: options.output.appendingPathComponent("observation.json"), options: .atomic)
            } catch { FileHandle.standardError.write(Data("更新失败回执未完成：\(error)\n".utf8)) }
        }
        view?.stopLoading()
        window?.orderOut(nil)
        view = nil
        exit(failures.isEmpty ? 0 : 1)
    }
}

@main
struct Main {
    @MainActor static func main() {
        do {
            let options = try Options(Array(CommandLine.arguments.dropFirst()))
            guard #available(macOS 14.0, *) else { throw ProbeFailure("机制探针需要 macOS 14 或更新版本。") }
            let app = NSApplication.shared
            app.setActivationPolicy(.regular)
            // 命令行创建的 NSApplication 没有默认退出菜单，需显式接通 Cmd-Q。
            let menu = NSMenu()
            let applicationItem = NSMenuItem()
            let applicationMenu = NSMenu()
            applicationMenu.addItem(withTitle: "退出观察", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
            applicationItem.submenu = applicationMenu
            menu.addItem(applicationItem)
            app.mainMenu = menu
            let probe = try Probe(options: options)
            app.delegate = probe
            withExtendedLifetime(probe) { app.run() }
        } catch {
            FileHandle.standardError.write(Data("WebKit 探针未启动：\(error)\n".utf8))
            exit(2)
        }
    }
}
