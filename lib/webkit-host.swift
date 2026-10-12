// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 正式 WebKit 宿主：只读取包内固定配置，保存由系统 WKWebsiteDataStore 管理。
import AppKit
import CryptoKit
import Darwin
import Foundation
import WebKit

struct LaunchConfiguration: Decodable {
    let `protocol`: Int
    let appid: String
    let name: String
    let content_root: String
    let entry: String
    let store_uuid: String
    let lifecycle: String
    let resource_inventory: [String: ResourceRecord]

    var store: UUID { UUID(uuidString: store_uuid)! }

    static func read(bundle: Bundle) throws -> (LaunchConfiguration, URL) {
        guard let resources = bundle.resourceURL else { throw ProbeFailure("应用包缺少资源目录。") }
        for directory in [bundle.bundleURL.appendingPathComponent("Contents"), resources] {
            var info = stat()
            guard lstat(directory.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR else {
                throw ProbeFailure("应用资源容器包含软链或异常文件。")
            }
        }
        let data = try readRegular(resources.appendingPathComponent("webkit-launch.json"), limit: 16 * 1_048_576)
        let config = try JSONDecoder().decode(Self.self, from: data)
        guard config.protocol == 1, let appid = UInt32(config.appid), appid > 0,
              String(appid) == config.appid, !config.name.isEmpty, config.name.utf8.count <= 256,
              !config.name.unicodeScalars.contains(where: { $0.value < 32 }),
              config.content_root == "OriginalGame/resources/app", config.entry == "release.html",
              config.lifecycle == "terminal-beforeunload", let uuid = UUID(uuidString: config.store_uuid),
              uuid != UUID(uuidString: "00000000-0000-0000-0000-000000000000"),
              bundle.bundleIdentifier == "io.dryport.webkit.\(config.appid)",
              bundle.object(forInfoDictionaryKey: "CFBundleName") as? String == "WebKitGame\(config.appid)",
              bundle.object(forInfoDictionaryKey: "CFBundleExecutable") as? String == "WebKitGame",
              (1...4096).contains(config.resource_inventory.count),
              config.resource_inventory[config.entry] != nil else {
            throw ProbeFailure("应用配置或身份不符，请重新生成应用；原游戏和存档会保留。")
        }
        for (path, record) in config.resource_inventory {
            guard safeRelativePath(path), record.sha256.count == 64,
                  record.sha256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
                throw ProbeFailure("应用资源清单包含无效路径或摘要。")
            }
        }
        let root = resources.appendingPathComponent(config.content_root, isDirectory: true)
        // 拒绝包内任何一层链接，避免把用户另一个目录当作已核验原包。
        var current = resources
        for part in config.content_root.split(separator: "/") {
            current.appendPathComponent(String(part), isDirectory: true)
            var info = stat()
            guard lstat(current.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR else {
                throw ProbeFailure("应用内原资源目录缺失或包含软链。")
            }
        }
        return (config, root)
    }
}

func safeRelativePath(_ path: String) -> Bool {
    let components = path.split(separator: "/", omittingEmptySubsequences: false)
    return !components.isEmpty && !path.contains("\\") && !path.contains("\0") &&
        components.allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
}

func readRegular(_ url: URL, limit: Int) throws -> Data {
    let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard descriptor >= 0 else { throw ProbeFailure("无法只读打开配置：\(url.lastPathComponent)") }
    defer { Darwin.close(descriptor) }
    return try readDescriptor(descriptor, limit: limit)
}

func readDescriptor(_ descriptor: Int32, limit: Int) throws -> Data {
    var info = stat()
    guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
          info.st_size >= 0, info.st_size <= limit else { throw ProbeFailure("文件类型或大小不符合预期。") }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 16_384)
    while true {
        let count = Darwin.read(descriptor, &buffer, buffer.count)
        if count < 0 { if errno == EINTR { continue }; throw ProbeFailure("读取文件失败。") }
        if count == 0 { return data }
        guard data.count + count <= limit else { throw ProbeFailure("文件在读取时超出大小上限。") }
        data.append(contentsOf: buffer.prefix(count))
    }
}

struct StateIdentity: Codable, Equatable {
    let `protocol`: Int
    let kind: String
    let appid: String
    let store_uuid: String
}

// 目录与锁只从已打开的父目录描述符解析，不跟随软链、不替换未知用户文件。
final class OwnedState {
    let descriptor: Int32
    let lockDescriptor: Int32
    let runDescriptor: Int32
    let logURL: URL
    private var logIdentity: (dev_t, ino_t)?

    static func systemHome() throws -> URL {
        guard let home = getpwuid(getuid())?.pointee.pw_dir else { throw ProbeFailure("无法确定当前用户的系统目录。") }
        return URL(fileURLWithPath: String(cString: home), isDirectory: true)
    }

    init(config: LaunchConfiguration, homeURL: URL) throws {
        let root = homeURL.appendingPathComponent("Library/Application Support/dryport/webkit", isDirectory: true)
        let path = root.appendingPathComponent(config.appid, isDirectory: true)
        var parent = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw ProbeFailure("无法打开状态目录根。") }
        defer { Darwin.close(parent) }
        for part in root.path.split(separator: "/").map(String.init) {
            if mkdirat(parent, part, 0o700) != 0 && errno != EEXIST { throw ProbeFailure("无法创建游戏状态目录。") }
            let next = openat(parent, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else { throw ProbeFailure("状态目录包含软链或异常文件，已停止。") }
            Darwin.close(parent); parent = next
        }
        let identity = StateIdentity(protocol: 1, kind: "dryport-webkit-state", appid: config.appid,
                                     store_uuid: config.store.uuidString.lowercased())
        let marker = ".dryport-webkit.json"
        var existing = stat()
        if fstatat(parent, config.appid, &existing, AT_SYMLINK_NOFOLLOW) != 0 {
            guard errno == ENOENT else { throw ProbeFailure("无法检查当前游戏状态目录。") }
            let pending = ".state-\(UUID().uuidString.lowercased())"
            guard mkdirat(parent, pending, 0o700) == 0 else { throw ProbeFailure("无法创建状态暂存目录。") }
            let pendingFD = openat(parent, pending, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard pendingFD >= 0 else { throw ProbeFailure("无法打开状态暂存目录。") }
            defer { Darwin.close(pendingFD) }
            try Self.writeNew(JSONEncoder().encode(identity), name: marker, in: pendingFD)
            guard fsync(pendingFD) == 0,
                  renameatx_np(parent, pending, parent, config.appid, UInt32(RENAME_EXCL)) == 0 else {
                throw ProbeFailure("状态目录未发布，已有目标未覆盖；请重试。")
            }
        }
        let state = openat(parent, config.appid, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard state >= 0 else { throw ProbeFailure("游戏状态目录不是普通目录。") }
        var retained = false
        defer { if !retained { Darwin.close(state) } }
        var info = stat()
        guard fstat(state, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
            throw ProbeFailure("游戏状态目录归属或权限异常，未覆盖其中数据。")
        }
        do {
            let fd = openat(state, marker, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
            guard fd >= 0 else { throw ProbeFailure("已有状态目录缺少归属记录，已保留并停止。") }
            defer { Darwin.close(fd) }
            guard try JSONDecoder().decode(StateIdentity.self, from: readDescriptor(fd, limit: 4096)) == identity else {
                throw ProbeFailure("已有状态目录属于其他应用或存储空间，已保留并停止。")
            }
        }
        let lock = openat(state, "process.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard lock >= 0 else { throw ProbeFailure("无法打开当前游戏进程锁。") }
        var lockRetained = false
        defer { if !lockRetained { Darwin.close(lock) } }
        guard fstat(lock, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(),
              info.st_nlink == 1, info.st_size == 0, info.st_mode & 0o077 == 0 else {
            throw ProbeFailure("进程锁文件异常，未覆盖原文件。")
        }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw ProbeFailure("这个游戏已在运行，请切回已有窗口。") }
        if mkdirat(state, "runs", 0o700) != 0 && errno != EEXIST { throw ProbeFailure("无法创建运行日志目录。") }
        let runs = openat(state, "runs", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard runs >= 0 else { throw ProbeFailure("运行日志目录包含软链或异常文件。") }
        defer { Darwin.close(runs) }
        guard fstat(runs, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
            throw ProbeFailure("运行日志目录归属或权限异常。")
        }
        let run = UUID().uuidString.lowercased()
        guard mkdirat(runs, run, 0o700) == 0 else { throw ProbeFailure("无法创建本次运行日志。") }
        let runFD = openat(runs, run, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard runFD >= 0 else { throw ProbeFailure("无法打开本次运行日志。") }
        descriptor = state; lockDescriptor = lock; runDescriptor = runFD
        logURL = path.appendingPathComponent("runs/\(run)/host.json")
        retained = true; lockRetained = true
    }

    deinit { Darwin.close(runDescriptor); Darwin.close(lockDescriptor); Darwin.close(descriptor) }

    static func writeNew(_ data: Data, name: String, in directory: Int32) throws {
        let fd = openat(directory, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw ProbeFailure("日志或归属记录已存在，未覆盖。") }
        defer { Darwin.close(fd) }
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw ProbeFailure("写入运行记录失败。") }
                offset += count
            }
        }
        guard fsync(fd) == 0 else { throw ProbeFailure("运行记录未能写入磁盘。") }
    }

    func writeLog(_ value: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        guard data.count <= 300_000 else { throw ProbeFailure("运行日志超出上限。") }
        let temp = ".host-\(UUID().uuidString).json"
        try Self.writeNew(data, name: temp, in: runDescriptor)
        var info = stat()
        if let (device, inode) = logIdentity {
            guard fstatat(runDescriptor, "host.json", &info, AT_SYMLINK_NOFOLLOW) == 0,
                  info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1,
                  info.st_dev == device, info.st_ino == inode else {
                throw ProbeFailure("本次日志文件被替换，已保留并停止更新。")
            }
        } else {
            guard fstatat(runDescriptor, "host.json", &info, AT_SYMLINK_NOFOLLOW) != 0, errno == ENOENT else {
                throw ProbeFailure("本次日志目标已存在，未覆盖。")
            }
        }
        // 本次独占的新目录中只替换自己之前的日志，终端链接也不会被跟随。
        guard renameat(runDescriptor, temp, runDescriptor, "host.json") == 0 else {
            throw ProbeFailure("无法发布本次运行日志。")
        }
        guard fstatat(runDescriptor, "host.json", &info, AT_SYMLINK_NOFOLLOW) == 0 else {
            throw ProbeFailure("无法核对已发布日志。")
        }
        logIdentity = (info.st_dev, info.st_ino)
    }
}

@available(macOS 14.0, *)
@MainActor
final class HostMessages: NSObject, WKScriptMessageHandler {
    weak var owner: GameHost?
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        owner?.record(message)
    }
}

@available(macOS 14.0, *)
@MainActor
final class GameHost: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let config: LaunchConfiguration
    let resources: URL
    let state: OwnedState
    let scheme: ResourceScheme
    let world = WKContentWorld.world(name: "dryport-lifecycle-observer")
    let messages = HostMessages()
    var window: NSWindow?
    var view: WKWebView?
    var closeNavigation: WKNavigation?
    var closeTask: Task<Void, Never>?
    var closingDocument: String?
    var currentDocument: String?
    var closing = false
    var closeLoaded = false
    var closeDelivered = false
    var closeReceipt = TerminalCloseReceipt()
    var failed = false
    var errorPanel = false
    var events: [[String: Any]] = []
    var errors: [String] = []
    let started = Date()

    init(config: LaunchConfiguration, resources: URL) throws {
        self.config = config; self.resources = resources
        scheme = try ResourceScheme(root: resources, host: config.store.uuidString.lowercased())
        guard try scheme.resourceInventory() == config.resource_inventory else {
            throw ProbeFailure("原资源清单或 SHA256 与应用记录不一致，已停止启动。")
        }
        state = try OwnedState(config: config, homeURL: OwnedState.systemHome())
        super.init()
        messages.owner = self
        try log(status: "starting")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let setup = WKWebViewConfiguration()
        setup.websiteDataStore = WKWebsiteDataStore(forIdentifier: config.store)
        setup.preferences.isElementFullscreenEnabled = false
        setup.preferences.javaScriptCanOpenWindowsAutomatically = false
        setup.mediaTypesRequiringUserActionForPlayback = .all
        setup.setURLSchemeHandler(scheme, forURLScheme: "sn-resource")
        setup.userContentController.add(messages, contentWorld: world, name: "snLifecycle")
        setup.userContentController.addUserScript(WKUserScript(source: terminalLifecycleScript,
            injectionTime: .atDocumentStart, forMainFrameOnly: true, in: world))
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let size = NSSize(width: min(1280, max(320, visible.width - 40)), height: min(720, max(240, visible.height - 70)))
        let web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: setup)
        web.navigationDelegate = self; web.uiDelegate = self
        let win = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        win.title = config.name; win.collectionBehavior = [.fullScreenNone]
        win.contentView = web; win.delegate = self; win.center()
        window = win; view = web
        win.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        let url = URL(string: "sn-resource://\(config.store.uuidString.lowercased())/\(config.entry)")!
        web.load(URLRequest(url: url))
    }

    func log(status: String) throws {
        try state.writeLog(["protocol": 1, "appid": config.appid, "pid": getpid(), "status": status,
            "elapsed_seconds": Date().timeIntervalSince(started), "saving_verified": false,
            "lifecycle_delivered": closeDelivered, "events": events, "errors": errors,
            "boundary": "event receipt only; game-save content is not read"])
    }

    func record(_ message: WKScriptMessage) {
        guard !errorPanel else { return }
        guard message.name == "snLifecycle", message.world === world, message.webView === view,
              message.frameInfo.isMainFrame, let body = message.body as? [String: Any],
              let doc = body["document_id"] as? String, !doc.isEmpty, doc.utf8.count <= 80,
              let kind = body["kind"] as? String,
              ["document_started", "beforeunload", "pagehide", "dispatch_started", "dispatch_returned", "dispatch_failed", "script_error", "resource_error", "receipt_overflow"].contains(kind),
              JSONSerialization.isValidJSONObject(body),
              let data = try? JSONSerialization.data(withJSONObject: body), data.count <= 2048 else {
            fail("生命周期回执来源或格式异常。"); return
        }
        // 正常反复重载也可持续运行；只保留最近回执，不把日志容量当作游戏寿命。
        if events.count == 128 { events.removeFirst() }
        events.append(body)
        if kind == "document_started" { currentDocument = doc }
        if ["dispatch_failed", "script_error", "resource_error", "receipt_overflow"].contains(kind) {
            fail("游戏页面出现错误，未能确认当前操作已保存。")
            return
        }
        if closing, doc == closingDocument {
            closeReceipt.accept(kind: kind, fields: body)
            guard !closeReceipt.refused else {
                fail("游戏拒绝了退出事件，未能确认保存。")
                return
            }
            closeDelivered = closeReceipt.delivered
        }
        do { try log(status: closing ? "closing" : "running") } catch { fail("无法记录游戏退出状态。") }
        finishIfReady()
    }

    func requestClose() {
        if errorPanel { exit(1) }
        guard !closing else { return }
        guard let web = view, let document = currentDocument else { fail("页面尚未准备好，无法确认关闭时保存。"); return }
        closing = true; closingDocument = document
        let clock = ContinuousClock(); let due = clock.now.advanced(by: .seconds(5))
        closeTask = Task { [weak self] in
            do { try await clock.sleep(until: due, tolerance: .zero) } catch { return }
            self?.fail("关闭等待超过 5 秒，已停止游戏视图；本次保存未获确认。")
        }
        closeNavigation = web.reload()
        if closeNavigation == nil { fail("无法开始退出时的页面重载，保存未获确认。") }
    }

    func finishIfReady() {
        guard closing, closeLoaded, closeDelivered, !failed else { return }
        closeTask?.cancel()
        do { try log(status: "closed") } catch { fail("退出回执写入失败，保存状态未获确认。"); return }
        view?.stopLoading(); window?.orderOut(nil); view = nil
        exit(0)
    }

    func fail(_ message: String) {
        guard !errorPanel else { return }
        failed = true; errorPanel = true; closeTask?.cancel()
        if errors.count < 16 { errors.append(String(message.prefix(512))) }
        try? log(status: "failed")
        FileHandle.standardError.write(Data("\(message) 日志：\(state.logURL.path)\n".utf8))
        view?.stopLoading(); view?.navigationDelegate = nil; view?.uiDelegate = nil
        let label = NSTextField(wrappingLabelWithString: "\(message)\n\n游戏原件未改写；本工具不会自动清理存档。\n日志：\(state.logURL.path)\n\n关闭此窗口后可重试。")
        label.frame = NSRect(x: 28, y: 28, width: max(260, (window?.contentView?.bounds.width ?? 600) - 56), height: 230)
        let panel = NSView(frame: window?.contentView?.bounds ?? NSRect(x: 0, y: 0, width: 600, height: 300))
        panel.addSubview(label); window?.contentView = panel; view = nil
        window?.makeKeyAndOrderFront(nil)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { requestClose(); return false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { requestClose(); return .terminateLater }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let closeNavigation, navigation === closeNavigation { closeLoaded = true; finishIfReady() }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) { fail("页面加载失败：\(error.localizedDescription)") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error) { fail("页面无法打开：\(error.localizedDescription)") }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { fail("游戏页面进程已终止，保存未获确认。") }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        let allowed = url?.scheme == "sn-resource" && url?.host == config.store.uuidString.lowercased() && navigationAction.targetFrame != nil
        decisionHandler(allowed ? .allow : .cancel)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor () -> Void) {
        let alert = NSAlert(); alert.messageText = config.name; alert.informativeText = String(message.prefix(2048))
        if let window { alert.beginSheetModal(for: window) { _ in completionHandler() } } else { completionHandler() }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = config.name; alert.informativeText = String(message.prefix(2048))
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        if let window { alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) } } else { completionHandler(false) }
    }
}

#if !WEBKIT_HOST_TEST
@main
struct WebKitGameMain {
    @MainActor static func main() {
        let app = NSApplication.shared; app.setActivationPolicy(.regular)
        do {
            guard #available(macOS 14.0, *) else { throw ProbeFailure("此游戏宿主需要 macOS 14 或更新版本。") }
            let (config, resources) = try LaunchConfiguration.read(bundle: .main)
            let host = try GameHost(config: config, resources: resources)
            let menu = NSMenu(); let item = NSMenuItem(); let appMenu = NSMenu()
            appMenu.addItem(withTitle: "退出 \(config.name)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
            item.submenu = appMenu; menu.addItem(item); app.mainMenu = menu
            app.delegate = host
            withExtendedLifetime(host) { app.run() }
        } catch {
            FileHandle.standardError.write(Data("游戏未启动：\(error)\n".utf8))
            let alert = NSAlert(); alert.messageText = "游戏未启动"; alert.informativeText = String(describing: error)
            alert.addButton(withTitle: "关闭"); app.activate(ignoringOtherApps: true); alert.runModal()
            exit(2)
        }
    }
}
#endif
