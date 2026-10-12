// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

private func renPyRow(_ appid: String = "2118420", route: String = "renpy-native",
                      ownership: String = "owned") -> GameRow {
    var game = GameRow(appid: appid, name: "自有路线样例", route: route,
                       route_label: Route.label[route] ?? route,
                       native: Route.nativeSet.contains(route), download_bytes: 0, installed: false)
    game.ownership = ownership
    game.license_source = ownership == "borrowed" ? "family_shared" : "self"
    return game
}

@MainActor
private func renPyModel(_ game: GameRow, detailRoute: String = "renpy-native",
                       riskCode: Int = 0) async throws -> AppModel {
    let model = AppModel(snapshotLoader: {
        LibrarySnapshot(library: LibraryPage(total: 1, rows: [game]),
                        state: MachineState(steam_running: true, debug_port: true, can_download: true,
                                            free_bytes: 0, library_size: 1, installed: 0,
                                            route_counts: [:], tools: [:]))
    }, detailLoader: { appid in
        try JSONDecoder().decode(GameDetail.self, from: Data("""
            {"appid":"\(appid)","found":true,"route":"\(detailRoute)",
             "risk_gate":{"code":\(riskCode),"text":"自有风险样例"}}
            """.utf8))
    })
    model.loadAll()
    let deadline = ContinuousClock.now + .seconds(4)
    while (model.loading || model.detailLoading) && ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(!model.loading && !model.detailLoading)
    return model
}

@Test
func renPyNativeProfileHasOneSidebarEntryAndGenericCandidatesStayPending() {
    let native = renPyRow()
    #expect(LibrarySection.native.matches(native))
    #expect(!LibrarySection.pending.matches(native))
    #expect(!LibrarySection.wine.matches(native))
    #expect(Route.order.filter { $0 == "renpy-native" }.count == 1)
    #expect(Route.label["renpy-native"] == "Ren'Py 原生（安装时校验版本）")
    let candidate = renPyRow("900001", route: "renpy")
    #expect(LibrarySection.pending.matches(candidate))
    #expect(!LibrarySection.native.matches(candidate))
}

@Test(arguments: ["owned", "borrowed"]) @MainActor
func renPyExactProfileCanInstallAfterRefinementForBothLicenseSources(ownership: String) async throws {
    let game = renPyRow(route: "renpy", ownership: ownership)
    let model = try await renPyModel(game)
    #expect(model.canInstall(game))
}

@Test(arguments: [1, 2, 3, 7, -1]) @MainActor
func renPyProfileStillRequiresSuccessfulCurrentRiskCheck(code: Int) async throws {
    let game = renPyRow()
    let model = try await renPyModel(game, riskCode: code)
    #expect(!model.canInstall(game))
}

@Test @MainActor
func renPyUnknownProfileAndForeignAppidRemainUnavailable() async throws {
    let candidate = renPyRow()
    let demoted = try await renPyModel(candidate, detailRoute: "renpy")
    #expect(!demoted.canInstall(candidate))
    let foreign = renPyRow("900002")
    let mismatched = try await renPyModel(foreign)
    #expect(!mismatched.canInstall(foreign))
}
