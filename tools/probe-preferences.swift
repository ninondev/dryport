// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 自有偏好域机制探针；不创建窗口，不查询全域列表，不作为隔离保证。
import Foundation
import CoreFoundation
import Darwin

enum Operation: String {
    case read, write, clear
}

enum ProbeFailure: Error {
    case invalidArguments
    case invalidDomain
    case bundleMismatch
    case invalidToken
    case unexpectedValueType
    case synchronizationFailed
    case readbackMismatch

    var message: String {
        switch self {
        case .invalidArguments: "用法：<expected-domain> <read|write|clear> [token]；仅 write 接受 token。"
        case .invalidDomain: "只接受 io.dryport.prefprobe. 加 32 位小写十六进制的自有域。"
        case .bundleMismatch: "Bundle.main.bundleIdentifier 与 expected-domain 不一致，未访问偏好。"
        case .invalidToken: "测试 token 必须为 1 至 256 字节且不含控制字符。"
        case .unexpectedValueType: "自有测试键包含非字符串值，未将其作为成功结果。"
        case .synchronizationFailed: "偏好同步返回失败；写入可能已部分发生，请保留回执。"
        case .readbackMismatch: "本进程回读与操作目标不符；新进程持久化仍需外部矩阵核对。"
        }
    }
}

func stringValue(_ value: Any?) throws -> String? {
    guard let value else { return nil }
    guard let string = value as? String else { throw ProbeFailure.unexpectedValueType }
    return string
}

let arguments = Array(CommandLine.arguments.dropFirst())
let bundleID = Bundle.main.bundleIdentifier
var report: [String: Any] = [
    "home": NSHomeDirectory(),
    "bundleId": bundleID as Any? ?? NSNull(),
    "op": arguments.count > 1 ? arguments[1] : NSNull(),
    "ok": false,
    "sync": ["defaults": NSNull(), "cf": NSNull()],
    "values": ["SNProbeDefaults": NSNull(), "SNProbeCF": NSNull()]
]
var status: Int32 = 1

do {
    guard arguments.count >= 2, let operation = Operation(rawValue: arguments[1]),
          arguments.count == (operation == .write ? 3 : 2) else {
        throw ProbeFailure.invalidArguments
    }
    let domain = arguments[0]
    let prefix = "io.dryport.prefprobe."
    let suffix = domain.dropFirst(prefix.count)
    guard domain.hasPrefix(prefix), suffix.utf8.count == 32,
          suffix.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
        throw ProbeFailure.invalidDomain
    }
    guard bundleID == domain else { throw ProbeFailure.bundleMismatch }
    let token = operation == .write ? arguments[2] : nil
    if let token {
        guard (1...256).contains(token.utf8.count),
              !token.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw ProbeFailure.invalidToken
        }
    }

    // 通过域和参数校验后才访问这两个键；不枚举或清空任何偏好域。
    let defaults = UserDefaults.standard
    let defaultsKey = "SNProbeDefaults"
    let cfKey = "SNProbeCF" as CFString
    var defaultsSynced: Bool?
    var cfSynced: Bool?
    switch operation {
    case .read:
        break
    case .write:
        defaults.set(token, forKey: defaultsKey)
        defaultsSynced = defaults.synchronize()
        CFPreferencesSetAppValue(cfKey, token as CFString?, kCFPreferencesCurrentApplication)
        cfSynced = CFPreferencesAppSynchronize(kCFPreferencesCurrentApplication)
    case .clear:
        defaults.removeObject(forKey: defaultsKey)
        defaultsSynced = defaults.synchronize()
        CFPreferencesSetAppValue(cfKey, nil, kCFPreferencesCurrentApplication)
        cfSynced = CFPreferencesAppSynchronize(kCFPreferencesCurrentApplication)
    }
    report["sync"] = ["defaults": defaultsSynced as Any? ?? NSNull(),
                      "cf": cfSynced as Any? ?? NSNull()]
    let defaultsValue = try stringValue(defaults.object(forKey: defaultsKey))
    let cfValue = try stringValue(CFPreferencesCopyAppValue(cfKey, kCFPreferencesCurrentApplication))
    report["values"] = ["SNProbeDefaults": defaultsValue as Any? ?? NSNull(),
                        "SNProbeCF": cfValue as Any? ?? NSNull()]
    guard defaultsSynced != false, cfSynced != false else { throw ProbeFailure.synchronizationFailed }
    if operation != .read {
        guard defaultsValue == token, cfValue == token else { throw ProbeFailure.readbackMismatch }
    }
    report["ok"] = true
    status = 0
} catch let failure as ProbeFailure {
    report["error"] = failure.message
} catch {
    report["error"] = "偏好探针发生异常：\(error)"
}

do {
    var data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
    data.append(0x0a)
    try FileHandle.standardOutput.write(contentsOf: data)
} catch {
    status = 1
}
exit(status)
