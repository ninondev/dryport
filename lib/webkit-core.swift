// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// WebKit 正式宿主与机制探针共用；资源只读和终态事件只有这一份实现。
import AppKit
import CryptoKit
import Darwin
import Foundation
import UniformTypeIdentifiers
import WebKit

struct ProbeFailure: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

// 仅用于已核实不会取消的终态清理处理器。缓存页面不补发，避免返回已释放的对象。
// 这不是浏览器的可信事件，不能提供取消导航或通用 Electron 兼容保证。
let terminalLifecycleScript = #"""
(() => {
  let seen = false;
  let sent = 0;
  const documentID = String(Date.now()) + '-' + Math.random().toString(36).slice(2);
  const send = (kind, fields = {}) => {
    if (sent > 64) return;
    if (sent === 64) { kind = 'receipt_overflow'; fields = {}; }
    sent++;
    window.webkit.messageHandlers.snLifecycle.postMessage({document_id: documentID, kind, ...fields});
  };
  send('document_started');
  window.addEventListener('error', event => {
    send(typeof event.message === 'string' ? 'script_error' : 'resource_error',
      {message: String(event.message || 'resource event').slice(0, 512)});
  }, {capture: true});
  window.addEventListener('beforeunload', event => {
    if (event.isTrusted === true) seen = true;
    send('beforeunload', {trusted: event.isTrusted, cancelable: event.cancelable});
  }, {capture: true});
  window.addEventListener('pagehide', event => {
    send('pagehide', {trusted: event.isTrusted, persisted: event.persisted === true});
    if (event.isTrusted !== true || event.persisted !== false || seen) return;
    send('dispatch_started');
    try {
      const closing = document.createEvent('BeforeUnloadEvent');
      closing.initEvent('beforeunload', false, true);
      seen = true;
      const accepted = window.dispatchEvent(closing);
      send('dispatch_returned', {accepted, default_prevented: closing.defaultPrevented,
        return_value_present: closing.returnValue !== ''});
    } catch (error) {
      send('dispatch_failed', {message: String(error).slice(0, 512)});
      throw error;
    }
  });
})();
"""#

// 只聚合同一关闭文档的事件；类型检查由宿主消息来源校验负责。
struct TerminalCloseReceipt {
    private(set) var naturalBeforeUnload = false
    private(set) var dispatchStarted = false
    private(set) var delivered = false
    private(set) var refused = false

    mutating func accept(kind: String, fields: [String: Any]) {
        if kind == "beforeunload", fields["trusted"] as? Bool == true { naturalBeforeUnload = true }
        if kind == "dispatch_started" { dispatchStarted = true }
        if kind == "pagehide", naturalBeforeUnload, fields["trusted"] as? Bool == true,
           fields["persisted"] as? Bool == false { delivered = true }
        if kind == "dispatch_returned" {
            let accepted = dispatchStarted && fields["accepted"] as? Bool == true &&
                fields["default_prevented"] as? Bool == false && fields["return_value_present"] as? Bool == false
            if accepted { delivered = true } else { refused = true }
        }
    }
}

func isWithin(_ path: URL, _ root: URL) -> Bool {
    path.path == root.path || path.path.hasPrefix(root.path + "/")
}

struct ResourceRecord: Codable, Equatable {
    let bytes: UInt64
    let sha256: String
}

func inventory(_ requestedRoot: URL) throws -> [String: ResourceRecord] {
    // Bundle URL 有时返回 /var，而枚举器返回 /private/var；相对路径须用同一根。
    let root = requestedRoot.resolvingSymlinksInPath()
    let keys: [URLResourceKey] = [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey]
    guard let enumerator = FileManager.default.enumerator(atPath: root.path) else {
        throw ProbeFailure("无法枚举原资源目录：\(root.path)")
    }
    var result: [String: ResourceRecord] = [:]
    for case let relative as String in enumerator {
        let file = root.appendingPathComponent(relative)
        let info = try file.resourceValues(forKeys: Set(keys))
        guard info.isSymbolicLink != true else {
            throw ProbeFailure("只读探针当前拒绝资源软链：\(file.path)")
        }
        if info.isDirectory == true { continue }
        guard info.isRegularFile == true else { throw ProbeFailure("资源含特殊文件：\(file.path)") }
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hash = SHA256()
        var bytes: UInt64 = 0
        while let block = try handle.read(upToCount: 1_048_576), !block.isEmpty {
            hash.update(data: block)
            bytes += UInt64(block.count)
        }
        result[relative] =
            ResourceRecord(bytes: bytes, sha256: hash.finalize().map { String(format: "%02x", $0) }.joined())
    }
    return result
}

// 公开自定义 URL 协议的只读对照。它不改 HTML、不代理网络、不暴露原目录之外的路径。
@MainActor
final class ResourceScheme: NSObject, WKURLSchemeHandler {
    let root: URL
    let host: String
    let rootDescriptor: Int32
    var denied: [String] = []
    init(root: URL, host: String) throws {
        self.root = root
        self.host = host
        // 后续请求只从这个目录对象出发，资源祖先改名或改为软链也不能重解释根路径。
        rootDescriptor = Darwin.open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard rootDescriptor >= 0 else { throw ProbeFailure("无法固定只读资源根") }
    }

    deinit { Darwin.close(rootDescriptor) }

    func resourceInventory() throws -> [String: ResourceRecord] {
        var result: [String: ResourceRecord] = [:]
        var visited = 0
        var totalBytes: UInt64 = 0
        let maximumBytes: UInt64 = 512 * 1_048_576
        func walk(_ parent: Int32, prefix: String) throws {
            // openat(".") 创建独立枚举游标，且始终指向已固定的目录对象。
            let fd = openat(parent, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard fd >= 0 else { throw ProbeFailure("无法枚举已固定的资源目录。") }
            guard let directory = fdopendir(fd) else { Darwin.close(fd); throw ProbeFailure("无法读取资源目录项。") }
            defer { closedir(directory) }
            while true {
                errno = 0
                guard let entry = readdir(directory) else {
                    guard errno == 0 else { throw ProbeFailure("资源目录枚举未完成。") }
                    break
                }
                let nameCapacity = Int(entry.pointee.d_namlen) + 1
                var nameBytes = entry.pointee.d_name
                let name: String? = withUnsafePointer(to: &nameBytes) {
                    $0.withMemoryRebound(to: CChar.self, capacity: nameCapacity) {
                        String(validatingCString: $0)
                    }
                }
                guard let name else { throw ProbeFailure("资源文件名不是有效 UTF-8。") }
                if name == "." || name == ".." { continue }
                let relative = prefix + name
                visited += 1
                guard visited <= 4096, relative.split(separator: "/").count <= 16 else {
                    throw ProbeFailure("资源目录规模超出 4096 项或 16 层上限。")
                }
                var info = stat()
                guard fstatat(fd, name, &info, AT_SYMLINK_NOFOLLOW) == 0 else {
                    throw ProbeFailure("资源目录在校验时发生变化。")
                }
                if info.st_mode & S_IFMT == S_IFDIR {
                    let child = openat(fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
                    guard child >= 0 else { throw ProbeFailure("资源子目录含软链或无法读取。") }
                    defer { Darwin.close(child) }
                    try walk(child, prefix: relative + "/")
                } else {
                    guard info.st_mode & S_IFMT == S_IFREG else {
                        throw ProbeFailure("资源含软链、特殊文件或数量超限。")
                    }
                    guard info.st_size >= 0, UInt64(info.st_size) <= maximumBytes - totalBytes else {
                        throw ProbeFailure("资源内容超过 512 MiB 校验上限。")
                    }
                    let leaf = openat(fd, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
                    guard leaf >= 0 else { throw ProbeFailure("无法只读校验资源文件。") }
                    let handle = FileHandle(fileDescriptor: leaf, closeOnDealloc: true)
                    defer { try? handle.close() }
                    var opened = stat()
                    guard fstat(leaf, &opened) == 0, opened.st_mode & S_IFMT == S_IFREG,
                          opened.st_dev == info.st_dev, opened.st_ino == info.st_ino,
                          opened.st_size == info.st_size else {
                        throw ProbeFailure("资源文件在打开时被替换。")
                    }
                    var hash = SHA256(); var bytes: UInt64 = 0
                    while let data = try handle.read(upToCount: 1_048_576), !data.isEmpty {
                        bytes += UInt64(data.count)
                        guard bytes <= UInt64(opened.st_size), bytes <= maximumBytes - totalBytes else {
                            throw ProbeFailure("资源在读取时增长，已停止校验。")
                        }
                        hash.update(data: data)
                    }
                    var after = stat()
                    guard fstat(leaf, &after) == 0, opened.st_size == after.st_size,
                          opened.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
                          opened.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
                          opened.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec,
                          opened.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec,
                          after.st_size >= 0, bytes == UInt64(after.st_size) else {
                        throw ProbeFailure("资源内容在校验时发生变化。")
                    }
                    result[relative] = ResourceRecord(bytes: bytes,
                        sha256: hash.finalize().map { String(format: "%02x", $0) }.joined())
                    totalBytes += bytes
                }
            }
        }
        try walk(rootDescriptor, prefix: "")
        return result
    }

    func openResource(_ path: String) throws -> (FileHandle, Int) {
        let components = path.split(separator: "/").map(String.init)
        guard !components.isEmpty else { throw ProbeFailure("只读取普通文件") }
        var directory = fcntl(rootDescriptor, F_DUPFD_CLOEXEC, 0)
        guard directory >= 0 else { throw ProbeFailure("无法打开只读资源根") }
        defer { Darwin.close(directory) }
        for part in components.dropLast() {
            let next = openat(directory, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else { throw ProbeFailure("拒绝不存在的目录、软链或不可读取路径") }
            Darwin.close(directory)
            directory = next
        }
        let descriptor = openat(directory, components.last!, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw ProbeFailure("拒绝不存在的资源、软链或不可读取文件") }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG else {
            Darwin.close(descriptor)
            throw ProbeFailure("资源不是普通文件")
        }
        return (FileHandle(fileDescriptor: descriptor, closeOnDealloc: true), Int(info.st_size))
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: any WKURLSchemeTask) {
        do {
            guard let url = urlSchemeTask.request.url, url.scheme == "sn-resource", url.host == host,
                  url.user == nil, url.password == nil, url.port == nil,
                  ["GET", "HEAD"].contains(urlSchemeTask.request.httpMethod ?? "GET"),
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  let path = parts.percentEncodedPath.removingPercentEncoding,
                  !path.contains("\\"), !path.contains("\0"),
                  !path.split(separator: "/").contains(where: { $0 == ".." || $0 == "." }) else {
                throw ProbeFailure("只读资源请求路径或方法不受支持")
            }
            let (handle, size) = try openResource(path)
            defer { try? handle.close() }
            let mime = UTType(filenameExtension: (path as NSString).pathExtension)?.preferredMIMEType ?? "application/octet-stream"
            urlSchemeTask.didReceive(URLResponse(url: url, mimeType: mime, expectedContentLength: size, textEncodingName: nil))
            if urlSchemeTask.request.httpMethod != "HEAD" {
                while let data = try handle.read(upToCount: 131_072), !data.isEmpty { urlSchemeTask.didReceive(data) }
            }
            urlSchemeTask.didFinish()
        } catch {
            if denied.count < 128 { denied.append(String((urlSchemeTask.request.url?.absoluteString ?? "无 URL").prefix(2048))) }
            urlSchemeTask.didFailWithError(NSError(domain: NSURLErrorDomain, code: NSURLErrorNoPermissionsToReadFile,
                userInfo: [NSLocalizedDescriptionKey: "只读资源未交付：\(error)"]))
        }
    }
    func webView(_ webView: WKWebView, stop urlSchemeTask: any WKURLSchemeTask) {}
}
