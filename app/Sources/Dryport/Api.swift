// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation

/// Thin wrapper over the configured toolchain's `api.py`. The toolchain stays the single source of
/// truth: this app only reads its JSON and streams its NDJSON. Nothing here writes to Steam.
enum Api {
    static let home = FileManager.default.homeDirectoryForCurrentUser.path
    static let pathKeys = ["SN_HOME", "SN_GAMES", "SN_RUNTIMES", "STEAM"]

    static func loadBundledPaths(_ bundle: Bundle) -> [String: String] {
        if let url = bundle.url(forResource: "toolchain-paths", withExtension: "plist"),
           let data = try? Data(contentsOf: url),
           let paths = try? PropertyListDecoder().decode([String: String].self, from: data) {
            // 独立包（app/build.sh --standalone）把工具链放在 app 里，路径记成 @bundle/…，随 app 移动而移动。
            return paths.mapValues { value in
                value.hasPrefix("@bundle/") ? bundle.bundlePath + "/" + String(value.dropFirst("@bundle/".count)) : value
            }
        }
        // 旧版构建只记录了源码根，保留读取能力，其他根继续遵循路径层默认值。
        if let url = bundle.url(forResource: "toolchain-root", withExtension: "txt"),
           let root = try? String(contentsOf: url, encoding: .utf8).trimmingCharacters(in: .newlines),
           !root.isEmpty {
            return ["SN_HOME": root]
        }
        return [:]
    }

    static let bundledPaths = loadBundledPaths(.main)

    /// 与 paths.sh 的 ${VAR:-default} 一致：非空的启动环境优先，空值不遮住构建时的路径。
    static func resolveEnvironment(_ environment: [String: String],
                                   bundledPaths: [String: String]) -> [String: String] {
        var resolved = environment
        for key in pathKeys {
            if let value = environment[key], !value.isEmpty { continue }
            if let value = bundledPaths[key], !value.isEmpty {
                resolved[key] = value
            } else {
                resolved.removeValue(forKey: key)
            }
        }
        return resolved
    }

    static var toolchainEnvironment: [String: String] {
        resolveEnvironment(ProcessInfo.processInfo.environment, bundledPaths: bundledPaths)
    }

    /// Finder 启动没有 shell 环境；入口和子进程使用同一份解析后的路径配置。
    static var scriptPath: String {
        if let root = toolchainEnvironment["SN_HOME"], !root.isEmpty {
            return root + "/lib/api.py"
        }
        // swift run / swift build 的产物沿父目录找入口，不依赖某个人的 clone 位置。
        var directory = Bundle.main.executableURL?.deletingLastPathComponent() ?? URL(fileURLWithPath: home)
        while directory.path != "/" {
            let script = directory.appendingPathComponent("lib/api.py").path
            if FileManager.default.isReadableFile(atPath: script) { return script }
            directory.deleteLastPathComponent()
        }
        return ""
    }

    static var workingDirectory: URL { URL(fileURLWithPath: scriptPath).deletingLastPathComponent() }

    /// 发布渠道：构建时写进 plist 的 SN_UPDATE_SOURCE（app/build.sh 读同名环境变量），或启动环境里的同名变量。没有就不提供升级。
    static var updateSource: String? {
        let env = ProcessInfo.processInfo.environment["SN_UPDATE_SOURCE"] ?? ""
        let value = env.isEmpty ? (bundledPaths["SN_UPDATE_SOURCE"] ?? "") : env
        return value.isEmpty ? nil : value
    }

    /// 跑 bin/self-update.sh；check=true 只比较（退出 0 最新、10 有新版本），否则真升级（旧版保留可回退）。
    static func runSelfUpdate(check: Bool) throws -> (code: Int32, output: String) {
        guard let source = updateSource else { throw Failure(message: "发布渠道未设置") }
        let script = URL(fileURLWithPath: scriptPath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("bin/self-update.sh").path
        guard FileManager.default.fileExists(atPath: script) else { throw Failure(message: "找不到 \(script)") }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [script, "--app", Bundle.main.bundlePath, "--from", source] + (check ? ["--check"] : [])
        p.environment = childEnvironment
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }

    /// System python3 is enough (api.py is standard library only), but the toolchain underneath
    /// shells out to `node`, which lives in Homebrew — a Finder-launched app has neither on PATH.
    static var python: String {
        let candidates = ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
    }

    static var childEnvironment: [String: String] {
        var env = toolchainEnvironment
        let extra = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        var path = env["PATH"].map { $0.split(separator: ":").map(String.init) } ?? []
        for p in extra where !path.contains(p) { path.append(p) }
        env["PATH"] = path.joined(separator: ":")
        env["HOME"] = home
        env["PYTHONDONTWRITEBYTECODE"] = "1"   // 独立包里的脚本在 app 内，不往签名封住的资源目录写 __pycache__
        return env
    }

    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    /// Blocking. Call it off the main thread.
    static func runJSON<T: Decodable>(_ type: T.Type, _ args: [String]) throws -> T {
        let data = try runRaw(args)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            let head = String(data: data.prefix(400), encoding: .utf8) ?? ""
            throw Failure(message: "api.py \(args.joined(separator: " ")) 的输出解析不了：\(error.localizedDescription)\n\(head)")
        }
    }

    static func runRaw(_ args: [String]) throws -> Data {
        guard FileManager.default.fileExists(atPath: scriptPath) else {
            throw Failure(message: "找不到工具链入口 \(scriptPath)。请在项目目录重新运行 app/build.sh，再打开生成的 app。")
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [scriptPath] + args
        p.environment = childEnvironment
        p.currentDirectoryURL = workingDirectory
        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = outPipe
        try p.run()
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            let message = String(data: data, encoding: .utf8) ?? ""
            throw Failure(message: "api.py \(args.joined(separator: " ")) 执行失败（退出码 \(p.terminationStatus)）。\n\(message)")
        }
        if data.isEmpty {
            throw Failure(message: "api.py \(args.joined(separator: " ")) 没有输出（退出码 \(p.terminationStatus)）")
        }
        return data
    }

    /// Fire-and-forget commands whose output we do not need (play / reveal).
    static func fireAndForget(_ args: [String]) {
        DispatchQueue.global(qos: .userInitiated).async {
            _ = try? runRaw(args)
        }
    }
}

/// Streams one `api.py` subcommand: one JSON object per line, handed to `onEvent` on the main queue.
final class InstallRunner {
    private let process = Process()
    private let pipe = Pipe()
    private let scriptPath: String
    private(set) var pid: Int32 = 0

    init(scriptPath: String = Api.scriptPath) {
        self.scriptPath = scriptPath
    }

    func start(appid: String,
               onEvent: @escaping (InstallEvent) -> Void,
               onFinish: @escaping (Int32) -> Void) throws {
        try start(args: ["install", appid], onEvent: onEvent, onFinish: onFinish)
    }

    /// 同一条流式通道，install / uninstall / clean 共用。
    func start(args: [String],
               onEvent: @escaping (InstallEvent) -> Void,
               onFinish: @escaping (Int32) -> Void) throws {
        process.executableURL = URL(fileURLWithPath: Api.python)
        // -u: unbuffered, so progress lines reach us while the download is still running.
        process.arguments = ["-u", scriptPath] + args
        process.environment = Api.childEnvironment
        process.currentDirectoryURL = URL(fileURLWithPath: scriptPath).deletingLastPathComponent()
        process.standardOutput = pipe
        process.standardError = pipe

        try process.run()
        pid = process.processIdentifier
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            var buffer = Data()
            while true {
                let chunk = pipe.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let nl = buffer.firstIndex(of: 0x0a) {
                    let line = Data(buffer[buffer.startIndex..<nl])
                    buffer.removeSubrange(buffer.startIndex...nl)
                    deliver(line, onEvent: onEvent)
                }
            }
            if !buffer.isEmpty { deliver(buffer, onEvent: onEvent) }
            // 子进程退出时管道里还可能有最后一条 done。读到 EOF 再通知结束，确保事件顺序。
            process.waitUntilExit()
            let code = process.terminationStatus
            DispatchQueue.main.async { onFinish(code) }
        }
    }

    private func deliver(_ data: Data, onEvent: @escaping (InstallEvent) -> Void) {
        guard !data.isEmpty else { return }
        if let event = try? JSONDecoder().decode(InstallEvent.self, from: data) {
            DispatchQueue.main.async { onEvent(event) }
        } else if let raw = String(data: data, encoding: .utf8),
                  !raw.trimmingCharacters(in: .whitespaces).isEmpty {
            var event = InstallEvent(event: "log")
            event.line = raw
            DispatchQueue.main.async { onEvent(event) }
        }
    }

    /// Terminating api.py alone leaves the download running underneath it, so take the tree.
    func cancel() {
        guard process.isRunning else { return }
        let victims = ProcessTree.descendants(of: process.processIdentifier) + [process.processIdentifier]
        for pid in victims { kill(pid, SIGTERM) }
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
            for pid in victims where kill(pid, 0) == 0 { kill(pid, SIGKILL) }
        }
    }
}

enum ProcessTree {
    /// pid -> children, read from `ps`. Cheap enough for a one-shot cancel.
    static func descendants(of root: Int32) -> [Int32] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-axo", "pid=,ppid="]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        var children: [Int32: [Int32]] = [:]
        for line in (String(data: data, encoding: .utf8) ?? "").split(separator: "\n") {
            let f = line.split(separator: " ", omittingEmptySubsequences: true)
            guard f.count >= 2, let pid = Int32(f[0]), let ppid = Int32(f[1]) else { continue }
            children[ppid, default: []].append(pid)
        }
        var out: [Int32] = []
        var queue = children[root] ?? []
        while let next = queue.popLast() {
            out.append(next)
            queue.append(contentsOf: children[next] ?? [])
        }
        return out
    }
}
