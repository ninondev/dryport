// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

private func toolchainState(_ fields: [String: Any] = [:]) throws -> MachineState {
    var json: [String: Any] = [
        "steam_running": true, "debug_port": true, "can_download": true,
        "free_bytes": 0, "library_size": 0, "installed": 0,
        "route_counts": [:], "tools": [:]
    ]
    json.merge(fields) { _, new in new }
    return try JSONDecoder().decode(MachineState.self, from: JSONSerialization.data(withJSONObject: json))
}

@Test
func olderStateWithoutToolchainFieldStillDecodes() throws {
    let state = try toolchainState()
    #expect(state.toolchain_error == nil)
    #expect(state.downloadBlockReason == nil)
}

@Test
func missingToolReasonDoesNotAskToReopenSteam() throws {
    let reason = "没有找到 Node.js。请安装 Node.js 22 或更新版本后刷新。"
    let state = try toolchainState(["can_download": false, "toolchain_error": reason])
    #expect(state.downloadBlockReason == reason)
    #expect(state.downloadBlockReason?.contains("打开 Steam") == false)
}

@Test
func steamAndUnexplainedReadinessFailuresHaveDistinctReasons() throws {
    let stopped = try toolchainState(["can_download": false, "steam_running": false])
    #expect(stopped.downloadBlockReason?.contains("打开 Steam") == true)
    let disconnected = try toolchainState(["can_download": false, "debug_port": false])
    #expect(disconnected.downloadBlockReason?.contains("本机连接") == true)
    let unexplained = try toolchainState(["can_download": false])
    #expect(unexplained.downloadBlockReason?.contains("刷新状态") == true)
}

@Test @MainActor
func unavailableNodeDisablesSurveyAndInstall() async throws {
    let state = try toolchainState(["can_download": false, "toolchain_error": "Node.js 检查超时：/fixture/node。"])
    let row = GameRow(appid: "1", name: "自有样例", route: "love", route_label: "LÖVE",
                      native: true, download_bytes: 0, installed: false)
    let model = AppModel(snapshotLoader: {
        LibrarySnapshot(library: LibraryPage(total: 1, rows: [row]), state: state)
    }, detailLoader: { _ in
        try JSONDecoder().decode(GameDetail.self, from: Data(
            #"{"appid":"1","found":true,"risk_gate":{"code":0,"text":"自有风险样例通过"}}"#.utf8))
    })
    model.loadAll()
    let deadline = ContinuousClock.now + .seconds(3)
    while (model.loading || model.detailLoading) && ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(!model.loading && !model.detailLoading)
    #expect(model.state?.toolchain_error == state.toolchain_error)
    #expect(model.detail?.risk_gate?.code == 0)
    #expect(!model.canDownload)
    #expect(!model.canSurvey)
    #expect(!model.canInstall(row))
}
