// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

@Test(arguments: [0, 7]) @MainActor
func finalProgressEventArrivesBeforeProcessCompletion(exitCode: Int) async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let script = root.appendingPathComponent("fixture.py")
    try """
        import json,sys
        for i in range(1500):
            print(json.dumps({"event":"log", "line":str(i)}))
        sys.stdout.write(json.dumps({"event":"done", "code":\(exitCode)}))
        raise SystemExit(\(exitCode))
        """.write(to: script, atomically: true, encoding: .utf8)
    let runner = InstallRunner(scriptPath: script.path)
    var events: [InstallEvent] = []
    let code: Int32 = try await withCheckedThrowingContinuation { continuation in
        do {
            try runner.start(args: [], onEvent: { events.append($0) }, onFinish: { code in
                continuation.resume(returning: code)
            })
        } catch {
            continuation.resume(throwing: error)
        }
    }
    #expect(code == Int32(exitCode))
    #expect(events.count == 1501)
    #expect(events.last?.event == "done")
    #expect(events.last?.code == exitCode)
}
