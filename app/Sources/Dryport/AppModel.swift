// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import SwiftUI

/// 每个游戏都有路线归属，官方发行版也保留在同一个游戏库。
enum LibrarySection: String, Hashable, CaseIterable {
    case all, installed, archived, steam, native, wine, pending, refuse

    var title: String {
        switch self {
        case .all: return "全部"
        case .installed: return "已安装"
        case .archived: return "已归档"
        case .steam: return "Steam macOS 版"
        case .native: return "原生路线"
        case .wine: return "Wine 兜底"
        case .pending: return "待定"
        case .refuse: return "不碰"
        }
    }

    var symbol: String {
        switch self {
        case .all: return "square.grid.2x2"
        case .installed: return "internaldrive"
        case .archived: return "archivebox"
        case .steam: return "desktopcomputer"
        case .native: return "bolt.fill"
        case .wine: return "shippingbox"
        case .pending: return "questionmark.circle"
        case .refuse: return "hand.raised"
        }
    }

    func matches(_ row: GameRow) -> Bool {
        switch self {
        case .all: return true
        case .installed: return row.installed
        case .archived: return row.archived == true
        case .steam: return row.route == "steam"
        case .native: return Route.nativeSet.contains(row.route)
        case .wine: return Route.wineSet.contains(row.route)
        case .pending: return Route.pendingSet.contains(row.route)
        case .refuse: return row.route == "refuse"
        }
    }
}

/// Everything the UI reads. All mutation happens on the main queue; the shelling out does not.
final class AppModel: ObservableObject {
    // 库在后台一次加载；搜索只读内存，不启动 Python。
    @Published private(set) var allRows: [GameRow] = []
    @Published private(set) var visibleRows: [GameRow] = []
    @Published private(set) var counts: [LibrarySection: Int] = [:]
    @Published private(set) var loading = true
    @Published var loadError: String?

    @Published private(set) var state: MachineState?
    @Published private(set) var section: LibrarySection = .all
    @Published private(set) var query = ""
    @Published private(set) var selection: String?

    @Published private(set) var detail: GameDetail?
    @Published private(set) var detailLoading = false
    @Published private(set) var detailError: String?
    private var loadInFlight = false
    private var freshSnapshotRequired = false
    private var loadCompletions: [() -> Void] = []
    private var detailRequest = 0
    private var detailWork: DispatchWorkItem?
    private let detailQueue = DispatchQueue(label: "local.dryport.details", qos: .userInitiated)
    private let snapshotLoader: () throws -> LibrarySnapshot
    private let detailLoader: (String) throws -> GameDetail
    private let runnerFactory: () -> InstallRunner
    private let planLoader: (String) throws -> PurgePlan
    private let steamDebugEnabler: () throws -> SteamDebugResult
    private let updateRunner: (Bool) throws -> (code: Int32, output: String)
    let updateSourceConfigured: Bool

    init(snapshotLoader: @escaping () throws -> LibrarySnapshot = {
             try Api.runJSON(LibrarySnapshot.self, ["snapshot"])
         }, detailLoader: @escaping (String) throws -> GameDetail = {
             try Api.runJSON(GameDetail.self, ["detail", $0])
         }, runnerFactory: @escaping () -> InstallRunner = { InstallRunner() },
         planLoader: @escaping (String) throws -> PurgePlan = {
             try Api.runJSON(PurgePlan.self, ["uninstall-plan", $0])
         }, steamDebugEnabler: @escaping () throws -> SteamDebugResult = {
             try Api.runJSON(SteamDebugResult.self, ["steam-debug", "enable"])
         }, updateRunner: @escaping (Bool) throws -> (code: Int32, output: String) = { try Api.runSelfUpdate(check: $0) },
         updateSourceConfigured: Bool = Api.updateSource != nil) {
        self.snapshotLoader = snapshotLoader
        self.detailLoader = detailLoader
        self.runnerFactory = runnerFactory
        self.planLoader = planLoader
        self.steamDebugEnabler = steamDebugEnabler
        self.updateRunner = updateRunner
        self.updateSourceConfigured = updateSourceConfigured
    }

    // 升级：bin/self-update.sh 做全部核对与换包，这里只展示结果。
    enum UpdateState: Equatable {
        case checking, upToDate(String), available(String), updating, updated(String), failed(String)
    }
    static let previewUpdateMessage = "remote updates are off in this preview; update from a local folder"
    @Published private(set) var updateState: UpdateState?

    func checkForUpdates() {
        guard updateSourceConfigured, updateState != .checking, updateState != .updating else { return }
        updateState = .checking
        let runner = updateRunner
        detailQueue.async { [weak self] in
            let result = Result { try runner(true) }
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let r) where r.code == 0: self.updateState = .upToDate(r.output.trimmingCharacters(in: .whitespacesAndNewlines))
                case .success(let r) where r.code == 10: self.updateState = .available(r.output.trimmingCharacters(in: .whitespacesAndNewlines))
                case .success(let r) where r.code == 20:
                    self.updateState = .failed(Self.previewUpdateMessage)
                case .success(let r): self.updateState = .failed("检查失败（退出码 \(r.code)）：\(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
                case .failure(let error): self.updateState = .failed(error.localizedDescription)
                }
            }
        }
    }

    /// 界面已弹确认框。成功后 app 本体已换，需要重新打开；旧版由脚本保留。
    func applyUpdate() {
        guard updateSourceConfigured, updateState != .updating else { return }
        updateState = .updating
        let runner = updateRunner
        detailQueue.async { [weak self] in
            let result = Result { try runner(false) }
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let r) where r.code == 0:
                    self.updateState = .updated(r.output.trimmingCharacters(in: .whitespacesAndNewlines))
                    self.notice = "已升级，重新打开这个 app 生效；旧版保留在 app 旁的 .dryport-previous 目录。"
                case .success(let r) where r.code == 20:
                    self.updateState = .failed(Self.previewUpdateMessage)
                case .success(let r): self.updateState = .failed("升级没做成（退出码 \(r.code)），app 未改动：\(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
                case .failure(let error): self.updateState = .failed(error.localizedDescription)
                }
            }
        }
    }

    /// 状态栏里的一句话通知（例如开关文件已创建、请重启 Steam）；与安装消息分开，不占详情面板。
    @Published var notice: String?
    @Published private(set) var enablingSteamDebug = false

    /// 创建 Valve 的调试开关文件。界面已经弹过确认框；这里只跑命令、报结果、刷新状态。
    func enableSteamDebug() {
        guard !enablingSteamDebug else { return }
        enablingSteamDebug = true
        notice = nil
        let enabler = steamDebugEnabler
        detailQueue.async { [weak self] in
            let result = Result { try enabler() }
            DispatchQueue.main.async {
                guard let self else { return }
                self.enablingSteamDebug = false
                switch result {
                case .success(let r):
                    if r.endpoint_ok {
                        self.notice = "Steam 本机连接已可用。"
                    } else if r.created == true {
                        self.notice = "已创建开关文件，请退出并重新打开 Steam，再点「读取 Steam 库」或刷新。"
                    } else {
                        self.notice = "开关文件早就在（\(r.path)），请退出并重新打开 Steam。"
                    }
                case .failure(let error):
                    self.notice = "没创建成：\(error.localizedDescription)"
                }
                self.loadAll(fresh: true)
            }
        }
    }

    // 卸载确认单：逐项列会删什么、各占多少。每次打开重新算，不复用旧账。
    @Published private(set) var purgePlan: PurgePlan?
    @Published private(set) var purgePlanLoading = false
    @Published private(set) var purgePlanError: String?
    private var purgePlanRequest = 0

    func loadPurgePlan(_ appid: String) {
        purgePlanRequest += 1
        let request = purgePlanRequest
        purgePlan = nil
        purgePlanError = nil
        purgePlanLoading = true
        let loader = planLoader
        detailQueue.async { [weak self] in
            let result = Result { try loader(appid) }
            DispatchQueue.main.async {
                guard let self, self.purgePlanRequest == request else { return }
                self.purgePlanLoading = false
                switch result {
                case .success(let plan): self.purgePlan = plan
                case .failure(let error): self.purgePlanError = "账单没算出来：\(error.localizedDescription)"
                }
            }
        }
    }

    // Install job (one at a time).
    @Published private(set) var installingAppid: String?
    @Published private(set) var installPercent: Double?
    @Published private(set) var installLine = ""
    @Published private(set) var installSeconds = 0.0
    @Published var installMessage: String?
    @Published private(set) var installTail: [String] = []
    @Published private(set) var installFailed = false
    private var runner: InstallRunner?
    private var lastInstallAppid: String?
    private var lastInstallCode: Int32?
    private var operationGeneration = 0
    @Published private(set) var showingSurvey = false

    // MARK: - Loading

    func loadAll(fresh: Bool = false, then done: (() -> Void)? = nil) {
        if let done { loadCompletions.append(done) }
        if loadInFlight {
            if fresh { freshSnapshotRequired = true }
            return
        }
        loadInFlight = true
        loading = true
        DispatchQueue.global(qos: .userInitiated).async {
            let snapshot = Result { try self.snapshotLoader() }
            DispatchQueue.main.async {
                // 命令结束前启动的读取不能证明落盘结果；废弃它，完成回调等待下一次读取。
                if self.freshSnapshotRequired {
                    self.freshSnapshotRequired = false
                    self.loadInFlight = false
                    self.loadAll()
                    return
                }
                switch snapshot {
                case .success(let snapshot):
                    self.allRows = snapshot.library.rows
                    self.state = snapshot.state
                    self.loadError = nil
                case .failure(let e):
                    self.loadError = e.localizedDescription
                }
                self.recount()
                let selectionChanged = self.refilter(awaitingSelectedDetail: true)
                self.loading = false
                self.loadInFlight = false
                if !selectionChanged { self.loadDetail() }
                let completions = self.loadCompletions
                self.loadCompletions = []
                for completion in completions { completion() }
            }
        }
    }

    private func recount() {
        var c: [LibrarySection: Int] = [:]
        for s in LibrarySection.allCases { c[s] = 0 }
        for row in allRows {
            for s in LibrarySection.allCases where s.matches(row) { c[s, default: 0] += 1 }
        }
        counts = c
    }

    // MARK: - Filtering (local; never shells out per keystroke)

    func setSection(_ s: LibrarySection) {
        guard s != section else { return }
        section = s
        refilter()
    }

    func setQuery(_ q: String) {
        guard q != query else { return }
        query = q
        refilter()
    }

    @discardableResult
    private func refilter(awaitingSelectedDetail: Bool = false) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let sec = section
        var out: [GameRow] = []
        out.reserveCapacity(min(allRows.count, 512))
        for row in allRows where sec.matches(row) {
            if q.isEmpty || row.name.lowercased().contains(q) { out.append(row) }
        }
        visibleRows = out
        if selection == nil || !out.contains(where: { $0.appid == selection }) {
            // 新快照可能仍是候选路线。保留仍存在且符合搜索的选择到详情重查结束，
            // 不复用旧路线；届时按新证据决定是否继续留在当前分类。
            if awaitingSelectedDetail, let selection,
               allRows.contains(where: { $0.appid == selection && (q.isEmpty || $0.name.lowercased().contains(q)) }) {
                return false
            }
            // Keep the detail pane on something that is actually in view.
            select(out.first?.appid)
            return true
        }
        return false
    }

    // MARK: - Selection / detail

    func select(_ appid: String?) {
        guard appid != selection else { return }
        selection = appid
        // The install verdict belongs to the game it was about; do not let it follow the cursor.
        if installingAppid == nil && appid != lastInstallAppid && !showingSurvey {
            installMessage = nil
            installTail = []
            installFailed = false
        }
        loadDetail()
    }

    var selectedRow: GameRow? {
        guard let sel = selection else { return nil }
        return allRows.first { $0.appid == sel }
    }

    func loadDetail() {
        detailWork?.cancel()
        detailRequest += 1
        let request = detailRequest
        detailError = nil
        guard let appid = selection else { detail = nil; detailLoading = false; return }
        detail = nil
        detailLoading = true
        let loader = detailLoader
        let work = DispatchWorkItem { [weak self] in
            let result = Result { try loader(appid) }
            DispatchQueue.main.async {
                guard let self, self.selection == appid, self.detailRequest == request else { return }
                self.detailLoading = false
                switch result {
                case .success(let detail):
                    self.detail = detail
                    self.mergeDetailIntoLibrary(detail)
                case .failure(let error): self.detailError = error.localizedDescription
                }
                self.refilter()
            }
        }
        detailWork = work
        // 连续按方向键或输入搜索词时只查询最后停住的游戏，避免堆积风险检查进程。
        detailQueue.asyncAfter(deadline: .now() + 0.12, execute: work)
    }

    private func mergeDetailIntoLibrary(_ value: GameDetail) {
        guard value.found, value.appid == selection,
              let index = allRows.firstIndex(where: { $0.appid == value.appid }) else { return }
        var updated = allRows[index]
        if let route = value.route {
            updated.route = route
            updated.route_label = value.route_label ?? Route.label[route] ?? route
            updated.native = value.native ?? Route.nativeSet.contains(route)
        }
        if let engine = value.engine { updated.engine = engine }
        if let confidence = value.confidence { updated.confidence = confidence }
        if let evidence = value.evidence { updated.evidence = evidence }
        if let risk = value.risk { updated.risk = risk }
        // 详情只细化路线证据；安装位置、账号许可与平台缺项仍由完整快照确认。
        guard updated != allRows[index] else { return }
        allRows[index] = updated
        recount()
    }

    // MARK: - Actions

    var canDownload: Bool { state?.can_download ?? false }
    var canSurvey: Bool { canDownload && !loading && installingAppid == nil }

    func canInstall(_ row: GameRow) -> Bool {
        let route = detail?.appid == row.appid ? (detail?.route ?? row.route) : row.route
        return !["refuse", "fna", "renpy", "steam"].contains(route)
            && (route != "renpy-native" || row.appid == "2118420")
            && canDownload && installingAppid == nil && !loading && loadError == nil
            && state?.catalog_status != "unavailable"
            && !detailLoading && detail?.appid == row.appid && detail?.risk_gate?.code == 0
            && row.platform_note == nil && detail?.platform_note == nil
    }

    func install(_ appid: String) {
        guard let row = allRows.first(where: { $0.appid == appid }), canInstall(row) else { return }
        showingSurvey = false
        installingAppid = appid
        installPercent = nil
        installLine = "准备中…"
        installSeconds = 0
        installMessage = nil
        installTail = []
        installFailed = false
        lastInstallAppid = appid
        lastInstallCode = nil
        operationGeneration += 1
        let operation = operationGeneration
        let r = runnerFactory()
        runner = r
        do {
            try r.start(appid: appid,
                        onEvent: { [weak self] ev in
                            guard let self, self.operationGeneration == operation else { return }
                            self.handle(ev)
                        }, onFinish: { [weak self] code in
                            guard let self, self.operationGeneration == operation else { return }
                            self.finishInstall(code: code)
                        })
        } catch {
            installingAppid = nil
            runner = nil
            installMessage = "起不来安装进程：\(error.localizedDescription)"
            installFailed = true
        }
    }

    /// Called on the main queue by InstallRunner.
    func handle(_ ev: InstallEvent) {
        switch ev.event {
        case "log":
            if let l = ev.line, !l.isEmpty {
                installLine = l
                installTail.append(l)
                if installTail.count > 12 { installTail.removeFirst(installTail.count - 12) }
            }
            if let t = ev.t { installSeconds = t }
            if let p = ev.percent { installPercent = Double(p) }
        case "done":
            if let s = ev.seconds { installSeconds = s }
            lastInstallCode = Int32(ev.code ?? -1)
        case "error":
            installMessage = ev.msg
            installFailed = true
        default:
            break
        }
    }

    /// The toolchain has been seen to exit 0 on a download that produced nothing, so the exit code
    /// is not the verdict: the verdict is whether the game actually shows up as installed afterwards.
    private func finishInstall(code: Int32) {
        let appid = lastInstallAppid
        let seconds = Int(installSeconds)
        let rc = code != 0 ? code : (lastInstallCode ?? -1)
        let operation = operationGeneration
        installPercent = nil
        runner = nil
        loadAll(fresh: true) { [weak self] in
            guard let self, self.operationGeneration == operation else { return }
            self.installingAppid = nil
            if self.discardResultForAnotherSelection(appid) { return }
            if let error = self.loadError ?? self.state?.catalog_error {
                self.installFailed = true
                self.installMessage = "安装命令已结束，库刷新未完成：\(error)"
                return
            }
            let landed = appid.map { id in self.allRows.contains { $0.appid == id && $0.installed } } ?? false
            if landed && rc == 0 && !self.installFailed && self.loadError == nil {
                self.installFailed = false
                self.installMessage = "装完了，用时 \(seconds) 秒。"
            } else if self.installMessage == nil {
                self.installFailed = true
                self.installMessage = rc == 0
                    ? "没装上。命令退出码是 0，但库里查不到这个游戏，说明这一步没真正落盘。下面是最后几行输出。"
                    : "安装失败，退出码 \(rc)。下面是最后几行输出。"
            }
        }
    }

    func cancelInstall() {
        guard let runner else { return }
        runner.cancel()
        installMessage = "已取消。"
    }

    private func discardResultForAnotherSelection(_ appid: String?) -> Bool {
        guard let appid, selection != appid else { return false }
        installMessage = nil
        installTail = []
        installFailed = false
        return true
    }

    /// 卸载与清垃圾复用安装那套进度显示：同一条 NDJSON 流，同一个日志尾巴。
    private func runStreamed(_ args: [String], busy appid: String, firstLine: String) {
        guard installingAppid == nil else { return }
        showingSurvey = args.first == "survey"
        installingAppid = appid
        installPercent = nil
        installLine = firstLine
        installSeconds = 0
        installMessage = nil
        installTail = []
        installFailed = false
        lastInstallAppid = ["uninstall", "archive", "restore"].contains(args.first ?? "") ? appid : nil
        lastInstallCode = nil
        operationGeneration += 1
        let operation = operationGeneration
        let r = runnerFactory()
        runner = r
        do {
            try r.start(args: args,
                        onEvent: { [weak self] ev in
                            guard let self, self.operationGeneration == operation else { return }
                            self.handle(ev)
                        }, onFinish: { [weak self] code in
                            guard let self, self.operationGeneration == operation else { return }
                            self.finishStreamed(code: code)
                        })
        } catch {
            installingAppid = nil
            runner = nil
            installMessage = "起不来进程：\(error.localizedDescription)"
            installFailed = true
        }
    }

    func finishStreamed(code: Int32) {
        let seconds = Int(installSeconds)
        let rc = code != 0 ? code : (lastInstallCode ?? -1)
        let operation = operationGeneration
        let appid = lastInstallAppid
        installPercent = nil
        runner = nil
        loadAll(fresh: true) { [weak self] in
            guard let self, self.operationGeneration == operation else { return }
            self.installingAppid = nil
            if self.discardResultForAnotherSelection(appid) { return }
            if let error = self.loadError ?? self.state?.catalog_error {
                self.installFailed = true
                self.installMessage = "命令已结束，库刷新未完成：\(error)"
                return
            }
            if self.showingSurvey && self.state?.catalog_status != "current" {
                self.installFailed = true
                self.installMessage = "未读到当前账号的新游戏目录，请重新读取 Steam 库。"
                return
            }
            if rc == 0 && !self.installFailed {
                self.installFailed = false
                self.installMessage = self.showingSurvey
                    ? "游戏库已更新，用时 \(seconds) 秒。识别出引擎的游戏仍需逐个验证。"
                    : "做完了，用时 \(seconds) 秒。"
            } else {
                self.installFailed = true
                if self.installMessage == nil {
                    self.installMessage = "没做成，退出码 \(rc)。下面是最后几行输出。"
                }
            }
        }
    }

    func survey(deep: Bool = false) {
        guard canSurvey else { return }
        runStreamed(deep ? ["survey", "--deep"] : ["survey"], busy: "__survey__",
                    firstLine: deep ? "正在用本机文件清单补全路线；完成后会复用识别结果…" : "正在读取当前 Steam 账号的游戏库…")
    }

    /// 归档（可逆）：经官方接口移除快捷方式，app 原样移入 .uninstalled；下载与存档保留。
    func archive(_ appid: String) {
        runStreamed(["archive", appid], busy: appid, firstLine: "正在归档…")
    }

    /// 归档的反操作：最近一次归档的 app 放回游戏目录并重新加入 Steam 库。
    func restore(_ appid: String) {
        runStreamed(["restore", appid], busy: appid, firstLine: "正在恢复归档…")
    }

    /// 卸载（不可逆）：归档后删掉本工具自己的下载目录与归档 app；saves 为 true 才连存档状态目录。
    func uninstall(_ appid: String, saves: Bool) {
        var args = ["uninstall", appid]
        if saves { args.append("--saves") }
        runStreamed(args, busy: appid, firstLine: "正在卸载…")
    }

    /// 清这套工具产生的残渣。go=false 只算账不删。
    func clean(go: Bool, deep: Bool) {
        var args = ["clean"]
        if go { args.append("--go") }
        if deep { args.append("--deep") }
        runStreamed(args, busy: "__clean__", firstLine: go ? "正在清…" : "正在算账…")
    }

    func play(_ appid: String) { Api.fireAndForget(["play", appid]) }
    func reveal(_ appid: String) { Api.fireAndForget(["reveal", appid]) }
}
