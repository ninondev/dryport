// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

private final class Counter {
    private let lock = NSLock()
    private var value = 0
    func increment() { lock.withLock { value += 1 } }
    var count: Int { lock.withLock { value } }
}

private final class PausedSnapshot {
    private let lock = NSLock()
    private var calls = 0
    let release = DispatchSemaphore(value: 0)

    var count: Int { lock.withLock { calls } }

    func load() throws -> LibrarySnapshot {
        let call = lock.withLock { calls += 1; return calls }
        var game = row("101")
        if call == 1 {
            guard release.wait(timeout: .now() + 4) == .success else {
                throw Api.Failure(message: "自有快照等待超时")
            }
        } else {
            game.installed = true
        }
        return snapshot([game])
    }
}

private final class InstallSnapshots {
    private let lock = NSLock()
    private var calls = 0
    let releaseOld = DispatchSemaphore(value: 0)
    let releaseFresh = DispatchSemaphore(value: 0)
    var count: Int { lock.withLock { calls } }

    func load() throws -> LibrarySnapshot {
        let call = lock.withLock { calls += 1; return calls }
        var game = row("101"), other = row("102")
        other.installed = true
        let gate = call == 2 ? releaseOld : (call == 3 ? releaseFresh : nil)
        if let gate, gate.wait(timeout: .now() + 4) != .success {
            throw Api.Failure(message: "自有安装快照等待超时")
        }
        game.installed = call >= 3
        return snapshot([game, other])
    }
}

private func installScript(in root: URL) throws -> URL {
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let script = root.appendingPathComponent("fixture.py")
    try """
        import json,time
        from pathlib import Path
        root=Path(__file__).resolve().parent
        until=time.monotonic()+3
        while not (root/'finish').exists():
            if time.monotonic()>until: raise SystemExit(71)
            time.sleep(0.005)
        print(json.dumps({"event":"log","line":"own install fixture"}))
        print(json.dumps({"event":"done","code":0,"seconds":1}))
        """.write(to: script, atomically: true, encoding: .utf8)
    return script
}

/// Records the arguments the model hands to the toolchain, then reports done.
private func argsScript(in root: URL) throws -> URL {
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let script = root.appendingPathComponent("args.py")
    try """
        import json,sys
        from pathlib import Path
        root=Path(__file__).resolve().parent
        (root/'args.json').write_text(json.dumps(sys.argv[1:]))
        print(json.dumps({"event":"done","code":0,"seconds":0}))
        """.write(to: script, atomically: true, encoding: .utf8)
    return script
}

private func recordedArgs(in root: URL) throws -> [String] {
    try JSONDecoder().decode([String].self, from: Data(contentsOf: root.appendingPathComponent("args.json")))
}

private func row(_ id: String, route: String = "mkxp") -> GameRow {
    GameRow(appid: id, name: "样例 \(id)", route: route, route_label: route,
            native: true, download_bytes: 0, installed: false)
}

private func snapshot(_ rows: [GameRow]) -> LibrarySnapshot {
    LibrarySnapshot(library: LibraryPage(total: rows.count, rows: rows),
                    state: MachineState(steam_running: true, debug_port: true, can_download: true,
                                        free_bytes: 0, library_size: rows.count, installed: 0,
                                        route_counts: [:], tools: [:]))
}

private func detail(_ id: String, code: Int) throws -> GameDetail {
    try JSONDecoder().decode(GameDetail.self, from: Data("""
        {"appid":"\(id)","found":true,"risk_gate":{"code":\(code),"text":"测试风险闸"}}
        """.utf8))
}

@MainActor
private func waitUntil(_ predicate: () -> Bool) async throws {
    let deadline = ContinuousClock.now + .seconds(4)
    while !predicate() && ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(predicate(), "后台加载应在 4 秒内结束")
}

@Test @MainActor
func launchUsesOneSnapshotAndOneDetail() async throws {
    let loads = Counter(), details = Counter()
    let model = AppModel(snapshotLoader: { loads.increment(); return snapshot([row("101")]) },
                         detailLoader: { details.increment(); return try detail($0, code: 0) })
    model.loadAll()
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(loads.count == 1)
    #expect(details.count == 1)
    #expect(model.canInstall(row("101")))
}

@Test @MainActor
func commandCompletionRequiresSnapshotStartedAfterCompletion() async throws {
    let snapshots = PausedSnapshot()
    defer { snapshots.release.signal() }
    let model = AppModel(snapshotLoader: { try snapshots.load() },
                         detailLoader: { try detail($0, code: 0) })
    model.loadAll()
    try await waitUntil { snapshots.count == 1 }
    model.handle(InstallEvent(event: "done", code: 0))
    model.finishStreamed(code: 0)
    snapshots.release.signal()
    try await waitUntil { !model.loading && model.installMessage != nil }
    #expect(snapshots.count == 2)
    #expect(model.selectedRow?.installed == true)
}

@Test(arguments: [false, true]) @MainActor
func installWaitsForFreshSnapshotWithoutStartingAnotherJobOrMovingItsMessage(changeSelection: Bool) async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let script = try installScript(in: root)
    let snapshots = InstallSnapshots(), runners = Counter()
    defer {
        snapshots.releaseOld.signal(); snapshots.releaseFresh.signal()
        try? FileManager.default.removeItem(at: root)
    }
    let model = AppModel(snapshotLoader: { try snapshots.load() },
                         detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { runners.increment(); return InstallRunner(scriptPath: script.path) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    model.install("101")
    #expect(model.installingAppid == "101")
    model.loadAll() // 与安装中仍可用的 Cmd-R 相同。
    try await waitUntil { snapshots.count == 2 }
    if changeSelection { model.select("102") }
    try Data().write(to: root.appendingPathComponent("finish"))
    try await waitUntil { model.installLine == "own install fixture" }
    // 旧快照放行后，安装结论仍必须等待另一次读取。
    snapshots.releaseOld.signal()
    try await waitUntil { snapshots.count == 3 }
    #expect(model.installingAppid == "101")
    model.archive("102")
    #expect(runners.count == 1)
    #expect(model.installingAppid == "101")
    model.cancelInstall() // 命令已经结束，刷新期间不能伪报取消。
    #expect(model.installMessage == nil)
    snapshots.releaseFresh.signal()
    try await waitUntil { !model.loading && model.installingAppid == nil }
    #expect(snapshots.count == 3)
    #expect(model.allRows.first { $0.appid == "101" }?.installed == true)
    #expect(!model.installFailed)
    if changeSelection {
        #expect(model.selection == "102")
        #expect(model.installMessage == nil)
        #expect(model.installTail.isEmpty)
    } else {
        #expect(model.installMessage?.contains("装完了") == true)
    }
}

@Test @MainActor
func installRefreshCannotReportSuccessFromAnUnavailableCatalog() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let script = try installScript(in: root)
    defer { try? FileManager.default.removeItem(at: root) }
    try Data().write(to: root.appendingPathComponent("finish"))
    let reads = Counter()
    let model = AppModel(snapshotLoader: {
        reads.increment()
        var game = row("101")
        game.installed = reads.count > 1
        var value = snapshot([game])
        if reads.count > 1 {
            value.state.catalog_status = "unavailable"
            value.state.catalog_error = "账号已切换"
        }
        return value
    }, detailLoader: { try detail($0, code: 0) },
       runnerFactory: { InstallRunner(scriptPath: script.path) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    model.install("101")
    try await waitUntil { model.installingAppid == nil && !model.loading }
    #expect(reads.count == 2)
    #expect(model.installFailed)
    #expect(model.installMessage?.contains("账号已切换") == true)
}

@Test @MainActor
func refinedDetailUpdatesItsListRowAndNativeFilter() async throws {
    var original = row("2118420", route: "renpy")
    original.ownership = "borrowed"
    original.license_source = "family_shared"
    original.folder = "/own/fixture/download"
    let game = original
    let model = AppModel(snapshotLoader: { snapshot([game]) }, detailLoader: {
        var value = try detail($0, code: 0)
        value.route = "renpy-native"
        value.route_label = "Ren'Py 原生（安装时校验版本）"
        value.native = true
        return value
    })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(model.selectedRow?.route == "renpy-native")
    #expect(model.visibleRows.first?.route_label == model.detail?.route_label)
    #expect(model.counts[.native] == 1)
    #expect(model.counts[.pending] == 0)
    #expect(model.selectedRow?.ownership == "borrowed")
    #expect(model.selectedRow?.license_source == "family_shared")
    #expect(model.selectedRow?.folder == game.folder)
    #expect(model.selectedRow?.installed == false)
    model.setSection(.native)
    #expect(model.selection == game.appid)
    #expect(model.visibleRows.map(\.appid) == [game.appid])
}

@Test @MainActor
func refinedRowLeavingPendingLoadsTheNextVisibleGame() async throws {
    let reads = Counter()
    let model = AppModel(snapshotLoader: {
        snapshot([row("2118420", route: "renpy"), row("42", route: "renpy")])
    }, detailLoader: { id in
        reads.increment()
        var value = try detail(id, code: 0)
        value.route = id == "2118420" ? "renpy-native" : "renpy"
        return value
    })
    model.setSection(.pending)
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(model.visibleRows.map(\.appid) == ["42"])
    #expect(model.selection == "42")
    #expect(model.detail?.appid == "42")
    #expect(model.counts[.native] == 1)
    #expect(model.counts[.pending] == 1)
    #expect(reads.count == 2)
}

@Test(arguments: [false, true]) @MainActor
func nativeFilterRevalidatesSelectedProfileAfterAnOlderSnapshotArrives(profileRevoked: Bool) async throws {
    let reads = Counter()
    let model = AppModel(snapshotLoader: {
        snapshot([row("2118420", route: "renpy")])
    }, detailLoader: { id in
        reads.increment()
        var value = try detail(id, code: 0)
        value.route = profileRevoked && reads.count > 1 ? "renpy" : "renpy-native"
        return value
    })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    model.setSection(.native)
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(reads.count == 2)
    #expect(model.counts[.native] == (profileRevoked ? 0 : 1))
    #expect(model.selection == (profileRevoked ? nil : "2118420"))
    #expect(model.visibleRows.count == (profileRevoked ? 0 : 1))
}

@Test @MainActor
func staleDetailCannotRelabelAnyRowAfterSelectionChanges() async throws {
    let started = Counter(), release = DispatchSemaphore(value: 0)
    defer { release.signal() }
    let model = AppModel(snapshotLoader: {
        snapshot([row("2118420", route: "renpy"), row("42", route: "renpy")])
    }, detailLoader: { id in
        var value = try detail(id, code: 0)
        if id == "2118420" {
            started.increment()
            guard release.wait(timeout: .now() + 4) == .success else {
                throw Api.Failure(message: "自有详情等待超时")
            }
            value.route = "renpy-native"
            value.native = true
        }
        return value
    })
    model.loadAll()
    try await waitUntil { started.count == 1 }
    model.select("42")
    release.signal()
    try await waitUntil { !model.detailLoading }
    #expect(model.detail?.appid == "42")
    #expect(model.allRows.allSatisfy { $0.route == "renpy" })
    #expect(model.counts[.native] == 0)
}

@Test(arguments: [1, 2, 3, 7, -1]) @MainActor
func everyNonzeroRiskResultBlocksInstall(code: Int) async throws {
    let game = row("101")
    let model = AppModel(snapshotLoader: { snapshot([game]) },
                         detailLoader: { try detail($0, code: code) })
    model.loadAll()
    #expect(!model.canInstall(game))
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(!model.canInstall(game))
}

@Test(arguments: ["library", "detail"]) @MainActor
func incompletePlatformBlocksInstallEvenWithAnIndependentRiskPass(source: String) async throws {
    var game = row("101", route: "unknown")
    if source == "library" { game.platform_note = "平台资料待核实" }
    let selected = game
    let model = AppModel(snapshotLoader: { snapshot([selected]) }, detailLoader: {
        var value = try detail($0, code: 0)
        if source == "detail" { value.platform_note = "平台资料待核实" }
        return value
    })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(!model.canInstall(selected))
}

@Test(arguments: ["fna", "renpy", "steam"]) @MainActor
func unfinishedRoutesRemainUnavailable(route: String) async throws {
    let game = row("101", route: route)
    let model = AppModel(snapshotLoader: { snapshot([game]) },
                         detailLoader: { try detail($0, code: 0) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(!model.canInstall(game))
}

@Test @MainActor
func rapidSelectionOnlyLoadsTheFinalGame() async throws {
    let details = Counter()
    let model = AppModel(snapshotLoader: { snapshot([row("101"), row("102"), row("103")]) },
                         detailLoader: { details.increment(); return try detail($0, code: 0) })
    model.loadAll()
    try await waitUntil { !model.loading }
    model.select("102")
    model.select("103")
    try await waitUntil { !model.detailLoading }
    #expect(model.detail?.appid == "103")
    #expect(details.count == 1)
}

@Test @MainActor
func riskFailureIsVisibleAndBlocksInstall() async throws {
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) },
                         detailLoader: { _ in throw Api.Failure(message: "样例故障") })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(model.detailError == "样例故障")
    #expect(!model.canInstall(row("101")))
}

@Test(arguments: ["renpy", "fna", "vn", "unknown"])
func candidateRoutesBelongToPending(route: String) {
    let game = row("101", route: route)
    #expect(!LibrarySection.native.matches(game))
    #expect(!LibrarySection.wine.matches(game))
    #expect(LibrarySection.pending.matches(game))
}

@Test @MainActor
func successfulCommandWithFailedRefreshRemainsFailure() async throws {
    let model = AppModel(snapshotLoader: { throw Api.Failure(message: "目录读取失败") })
    model.handle(InstallEvent(event: "done", code: 0))
    model.finishStreamed(code: 0)
    try await waitUntil { !model.loading }
    #expect(model.installFailed)
    #expect(model.installMessage?.contains("目录读取失败") == true)
}

@Test @MainActor
func accountChangeAfterCommandCannotShowSuccess() async throws {
    let model = AppModel(snapshotLoader: {
        var value = snapshot([])
        value.state.catalog_status = "unavailable"
        value.state.catalog_error = "账号已切换"
        return value
    })
    model.handle(InstallEvent(event: "done", code: 0))
    model.finishStreamed(code: 0)
    try await waitUntil { !model.loading }
    #expect(model.installFailed)
    #expect(model.installMessage?.contains("账号已切换") == true)
}

@Test(arguments: ["owned", "borrowed"]) @MainActor
func licenseSourceDoesNotChangeInstallEligibility(ownership: String) async throws {
    var game = row("101")
    game.ownership = ownership
    game.license_source = ownership == "owned" ? "self" : "family_shared"
    let model = AppModel(snapshotLoader: { snapshot([game]) },
                         detailLoader: { try detail($0, code: 0) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(model.canInstall(game))
}

@Test(arguments: ["fna", "renpy", "refuse"]) @MainActor
func refinedRouteControlsInstallEligibility(route: String) async throws {
    let game = row("101", route: "unknown")
    let model = AppModel(snapshotLoader: { snapshot([game]) }, detailLoader: {
        var value = try detail($0, code: 0)
        value.route = route
        return value
    })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    #expect(!model.canInstall(game))
}

@Test
func officialDistributionHasAVisibleSectionWithoutClaimingNativeRuntime() {
    let game = row("101", route: "steam")
    #expect(LibrarySection.all.matches(game))
    #expect(LibrarySection.steam.matches(game))
    #expect(!LibrarySection.native.matches(game))
    #expect(!LibrarySection.pending.matches(game))
}

// MARK: - 归档（可逆）/ 恢复 / 卸载（不可逆）

@Test(arguments: [false, true]) @MainActor
func uninstallPassesTheSavesFlagOnlyWhenAsked(saves: Bool) async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let script = try argsScript(in: root)
    defer { try? FileManager.default.removeItem(at: root) }
    var installed = row("101")
    installed.installed = true
    let game = installed
    let model = AppModel(snapshotLoader: { snapshot([game]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner(scriptPath: script.path) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    model.uninstall("101", saves: saves)
    #expect(model.installingAppid == "101")
    try await waitUntil { model.installingAppid == nil && !model.loading }
    #expect(try recordedArgs(in: root) == (saves ? ["uninstall", "101", "--saves"] : ["uninstall", "101"]))
    #expect(model.installMessage?.contains("做完了") == true)
}

@Test(arguments: ["archive", "restore"]) @MainActor
func archiveAndRestoreRunTheirOwnVerbsForTheSelectedGame(verb: String) async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let script = try argsScript(in: root)
    defer { try? FileManager.default.removeItem(at: root) }
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner(scriptPath: script.path) })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    if verb == "archive" { model.archive("101") } else { model.restore("101") }
    try await waitUntil { model.installingAppid == nil && !model.loading }
    #expect(try recordedArgs(in: root) == [verb, "101"])
    #expect(model.installMessage?.contains("做完了") == true)
}

@Test
func purgePlanDecodesTheDefaultFlagAndSavesRow() throws {
    let json = """
        {"protocol":1,"appid":"42","items":[
          {"kind":"download","path":"/g/dl","label":"下载的游戏文件","bytes":10,"default":true},
          {"kind":"saves","path":"/g/.renpy-state/42","label":"存档","bytes":3,"default":false}],
         "skipped":[{"kind":"download","path":"/g/other","why":"没有本工具的标记"}],
         "bytes_default":10,"bytes_saves":3,"note":"du 是上限"}
        """
    let plan = try JSONDecoder().decode(PurgePlan.self, from: Data(json.utf8))
    #expect(plan.items.map(\.isDefault) == [true, false])
    #expect(plan.hasSaves)
    #expect(plan.skipped.first?.why == "没有本工具的标记")
    #expect(plan.bytes_default == 10 && plan.bytes_saves == 3)
}

@Test @MainActor
func purgePlanComesFromTheInjectedLoaderAndKeepsOnlyTheLatestRequest() async throws {
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner() },
                         planLoader: { appid in
                             if appid == "bad" { throw Api.Failure(message: "自有账单失败") }
                             return try JSONDecoder().decode(PurgePlan.self, from: Data("""
                                 {"appid":"\(appid)","items":[],"skipped":[],"bytes_default":0,"bytes_saves":0,"note":""}
                                 """.utf8))
                         })
    model.loadPurgePlan("101")
    #expect(model.purgePlanLoading)
    #expect(model.purgePlan == nil)
    try await waitUntil { !model.purgePlanLoading }
    #expect(model.purgePlan?.appid == "101")
    #expect(model.purgePlanError == nil)
    model.loadPurgePlan("bad")
    try await waitUntil { !model.purgePlanLoading }
    #expect(model.purgePlan == nil)
    #expect(model.purgePlanError != nil)
}

// MARK: - Steam 本机连接开关

@Test(arguments: [true, false]) @MainActor
func enablingTheSteamDebugSwitchReportsAndRefreshes(created: Bool) async throws {
    let loads = Counter()
    let model = AppModel(snapshotLoader: { loads.increment(); return snapshot([row("101")]) },
                         detailLoader: { try detail($0, code: 0) }, runnerFactory: { InstallRunner() },
                         steamDebugEnabler: {
                             try JSONDecoder().decode(SteamDebugResult.self, from: Data("""
                                 {"path":"/tmp/Steam/.cef-enable-remote-debugging","flag_exists":true,"endpoint_ok":false,"created":\(created),"restart_needed":true}
                                 """.utf8))
                         })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    let before = loads.count
    model.enableSteamDebug()
    #expect(model.enablingSteamDebug)
    try await waitUntil { !model.enablingSteamDebug && !model.loading && loads.count > before }
    #expect(model.notice?.contains("重新打开 Steam") == true)
    #expect(model.notice?.contains(created ? "已创建" : "早就在") == true)
}

@Test @MainActor
func failedSteamDebugSwitchShowsTheError() async throws {
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner() }, steamDebugEnabler: { throw Api.Failure(message: "没有找到 Steam 客户端包") })
    model.loadAll()
    try await waitUntil { !model.loading && !model.detailLoading }
    model.enableSteamDebug()
    try await waitUntil { !model.enablingSteamDebug && !model.loading }
    #expect(model.notice?.contains("没有找到 Steam 客户端包") == true)
}

// MARK: - 检查更新 / 升级

@Test @MainActor
func updateCheckMapsExitCodesToStatesAndApplyReportsSuccess() async throws {
    var calls: [Bool] = []
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner() },
                         updateRunner: { check in
                             calls.append(check)
                             return check ? (10, "有新版本：0.2（abc）；已装 dev\n") : (0, "已升级到 0.2（abc）：/tmp/App.app\n")
                         }, updateSourceConfigured: true)
    model.checkForUpdates()
    #expect(model.updateState == .checking)
    try await waitUntil { model.updateState != .checking }
    #expect(model.updateState == .available("有新版本：0.2（abc）；已装 dev"))
    model.applyUpdate()
    try await waitUntil { model.updateState != .updating }
    #expect(model.updateState == .updated("已升级到 0.2（abc）：/tmp/App.app"))
    #expect(model.notice?.contains("重新打开") == true)
    #expect(calls == [true, false])
}

@Test @MainActor
func updateCheckWithoutAChannelDoesNothingAndFailuresAreShown() async throws {
    let silent = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                          runnerFactory: { InstallRunner() }, updateRunner: { _ in (0, "") }, updateSourceConfigured: false)
    silent.checkForUpdates()
    #expect(silent.updateState == nil)
    let failing = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                           runnerFactory: { InstallRunner() }, updateRunner: { _ in (1, "错: 读不到发布清单") }, updateSourceConfigured: true)
    failing.checkForUpdates()
    try await waitUntil { failing.updateState != .checking }
    if case .failed(let text)? = failing.updateState { #expect(text.contains("读不到发布清单")) } else { #expect(Bool(false), "应是 failed") }
    let upToDate = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                            runnerFactory: { InstallRunner() }, updateRunner: { _ in (0, "已是最新：0.1（abc）") }, updateSourceConfigured: true)
    upToDate.checkForUpdates()
    try await waitUntil { upToDate.updateState != .checking }
    #expect(upToDate.updateState == .upToDate("已是最新：0.1（abc）"))
}

@Test @MainActor
func remotePreviewUpdatesExplainLocalOnlyPolicy() async throws {
    let message = "remote updates are off in this preview; update from a local folder"
    let model = AppModel(snapshotLoader: { snapshot([row("101")]) }, detailLoader: { try detail($0, code: 0) },
                         runnerFactory: { InstallRunner() }, updateRunner: { _ in (20, message) },
                         updateSourceConfigured: true)
    model.checkForUpdates()
    try await waitUntil { model.updateState != .checking }
    #expect(model.updateState == .failed(message))
    model.applyUpdate()
    try await waitUntil { model.updateState != .updating }
    #expect(model.updateState == .failed(message))
}
