// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import SwiftUI

struct ContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        NavigationSplitView {
            SidebarView(model: model)
                .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 260)
        } content: {
            LibraryListView(model: model)
                .navigationSplitViewColumnWidth(min: 300, ideal: 380)
        } detail: {
            DetailPane(model: model)
                .navigationSplitViewColumnWidth(min: 340, ideal: 460)
        }
        .navigationTitle("游戏库")
        .safeAreaInset(edge: .bottom, spacing: 0) { StatusBar(model: model) }
        .frame(minWidth: 980, minHeight: 620)
    }
}

// MARK: - Sidebar

struct SidebarView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        List(selection: Binding(get: { model.section }, set: { model.setSection($0 ?? .all) })) {
            ForEach(LibrarySection.allCases, id: \.self) { s in
                Label(s.title, systemImage: s.symbol)
                    .badge(model.counts[s] ?? 0)
                    .tag(s)
            }
        }
        .listStyle(.sidebar)
    }
}

// MARK: - Middle column

struct LibraryListView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索游戏名", text: Binding(get: { model.query }, set: { model.setQuery($0) }))
                    .textFieldStyle(.plain)
                if !model.query.isEmpty {
                    Button {
                        model.setQuery("")
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                Text("\(model.visibleRows.count) 个")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(.bar)

            Divider()

            if model.loading {
                VStack(spacing: 8) {
                    ProgressView()
                    Text("读取库…").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let err = model.loadError {
                ScrollView {
                    Text(err).font(.callout).padding()
                }
            } else if model.allRows.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text(emptyTitle).font(.headline)
                    Text("读取当前账号可用的 Steam 游戏，再按引擎选择路线。这一步不下载游戏本体。")
                        .foregroundStyle(.secondary)
                    Button("读取我的 Steam 游戏库") { model.survey() }
                        .disabled(!model.canSurvey)
                    if let reason = model.state?.downloadBlockReason {
                        Text(reason).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
            } else if model.visibleRows.isEmpty {
                Text("没有符合当前分类和搜索词的游戏")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                NativeLibraryList(rows: model.visibleRows, selection: model.selection) { model.select($0) }
            }
        }
    }

    private var emptyTitle: String {
        if let missing = model.state?.metadata_missing, missing > 0 {
            return "还有 \(missing) 个条目资料待补齐"
        }
        return model.state?.catalog_status == "current"
            ? "没有发现可访问的游戏" : "从你的 Steam 库开始"
    }
}

struct GameRowView: View {
    let row: GameRow

    var body: some View {
        HStack(spacing: 8) {
            if row.installed {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(row.name).lineLimit(1)
                HStack(spacing: 6) {
                    RouteBadge(label: row.route_label, route: row.route)
                    // 实验路线的小标：样式与路线徽章一致但用中性色；悬停看分层说明（tier_label）。
                    if row.tier == "experimental" {
                        Text("实验")
                            .font(.caption2)
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(Capsule().fill(Color.secondary.opacity(0.18)))
                            .foregroundStyle(Color.secondary)
                            .help(row.tier_label ?? "")
                    }
                    if row.download_bytes > 0 {
                        Text(humanBytes(row.download_bytes))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
    }
}

struct RouteBadge: View {
    let label: String
    let route: String

    private var tint: Color {
        if route == "refuse" { return .red }
        if Route.nativeSet.contains(route) { return .green }
        if Route.pendingSet.contains(route) { return .orange }
        return .secondary
    }

    var body: some View {
        Text(label)
            .font(.caption2)
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background(Capsule().fill(tint.opacity(0.18)))
            .foregroundStyle(tint == .secondary ? Color.secondary : tint)
    }
}

// MARK: - Status bar

struct StatusBar: View {
    @State private var confirmClean = false
    @State private var confirmSteamDebug = false
    @State private var confirmUpdate = false

    /// 检查更新：发布渠道没设时禁用并说明；有新版本时给「立即升级」，确认后由 bin/self-update.sh 换包。
    @ViewBuilder private var updateControl: some View {
        switch model.updateState {
        case .checking:
            HStack(spacing: 4) { ProgressView().controlSize(.small); Text("检查更新中…").foregroundStyle(.secondary) }
        case .updating:
            HStack(spacing: 4) { ProgressView().controlSize(.small); Text("升级中…").foregroundStyle(.secondary) }
        case .available(let text):
            Button { confirmUpdate = true } label: { Label(text, systemImage: "arrow.down.circle.fill").foregroundStyle(.orange) }
                .buttonStyle(.plain)
                .confirmationDialog("升级这个 app？", isPresented: $confirmUpdate, titleVisibility: .visible) {
                    Button("升级，然后我重新打开它") { model.applyUpdate() }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("仅从你信任的本地目录更新。SHA256 与 app 结构检查不证明发布者身份；远程更新已暂停。旧版保留在 app 旁边，可回退。游戏、存档与 Steam 不动。")
                }
        case .updated(let text):
            Text(text).foregroundStyle(.green).lineLimit(1)
        case .upToDate(let text):
            Text(text).foregroundStyle(.secondary).lineLimit(1)
        case .failed(let text):
            Text(text).foregroundStyle(.orange).lineLimit(1).truncationMode(.middle).help(text)
        case nil:
            VStack(alignment: .leading, spacing: 4) {
                Button("检查更新") { model.checkForUpdates() }
                    .controlSize(.small)
                    .disabled(!model.updateSourceConfigured)
                    .help(model.updateSourceConfigured ? "比较本地发布目录中的版本" : "本地更新目录未设置：构建或启动时设置 SN_UPDATE_SOURCE")
                Text(AppModel.previewUpdateMessage).font(.caption).foregroundStyle(.secondary)
                    .lineLimit(2).help(AppModel.previewUpdateMessage)
            }
        }
    }
    @State private var confirmCleanDeep = false

    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            if let error = model.state?.toolchain_error {
                Text(error).foregroundStyle(.orange).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }
            if let error = model.state?.catalog_error {
                Text(error).foregroundStyle(.orange).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            } else if model.state?.catalog_status == "legacy" {
                Text("当前显示历史路线快照，账号归属未确认。点击“读取 Steam 库”更新。")
                    .foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }
            if let warnings = model.state?.catalog_warnings, !warnings.isEmpty {
                Text(warnings.joined(separator: "\n"))
                    .foregroundStyle(.orange).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }
            if model.showingSurvey {
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        if model.installingAppid != nil { ProgressView().controlSize(.small) }
                        Text(model.installMessage ?? model.installLine)
                            .foregroundStyle(model.installFailed ? .red : .secondary)
                        Spacer()
                        if model.installingAppid != nil {
                            Button("取消读取") { model.cancelInstall() }
                        }
                    }
                    if model.installFailed {
                        Text(model.installTail.joined(separator: "\n"))
                            .font(.caption).textSelection(.enabled).lineLimit(12)
                    }
                }.padding(8)
            }
            HStack(spacing: 14) {
                HStack(spacing: 5) {
                    Circle()
                        .fill((model.state?.steam_running ?? false) ? Color.green : Color.red)
                        .frame(width: 8, height: 8)
                    Text((model.state?.steam_running ?? false) ? "Steam 运行中" : "Steam 没开")
                }
                if let s = model.state, s.steam_running, !s.debug_port {
                    Button {
                        confirmSteamDebug = true
                    } label: {
                        Label("启用 Steam 本机连接…", systemImage: "bolt.horizontal.circle").foregroundStyle(.orange)
                    }
                    .buttonStyle(.plain)
                    .disabled(model.enablingSteamDebug)
                    .confirmationDialog("创建 Steam 的调试开关文件？", isPresented: $confirmSteamDebug, titleVisibility: .visible) {
                        Button("创建，然后我去重启 Steam") { model.enableSteamDebug() }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("在 Steam 客户端包内新建一个空文件 .cef-enable-remote-debugging。这是 Valve 自己的调试开关，不改任何 Steam 二进制或配置内容；开启后 Steam 在本机 8080 端口开一个只对本机的连接，本工具用它经你已登录的客户端下载游戏、添加快捷方式。创建后需要退出并重新打开 Steam。")
                    }
                }
                if let notice = model.notice {
                    Text(notice).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                Divider().frame(height: 12)
                Text("可用磁盘 \(humanBytes(model.state?.free_bytes ?? 0))")
                Divider().frame(height: 12)
                Text("已装 \(model.state?.installed ?? 0) 个")
                Divider().frame(height: 12)
                Text("库 \(model.state?.library_size ?? 0) 个")
                Spacer()
                Button("读取 Steam 库") { model.survey() }
                    .controlSize(.small)
                    .disabled(!model.canSurvey)
                Button("补全路线") { model.survey(deep: true) }
                    .controlSize(.small)
                    .disabled(!model.canSurvey || model.state?.catalog_status != "current")
                updateControl
                Menu {
                    Button("先算账，看能清出多少") { model.clean(go: false, deep: false) }
                    Divider()
                    Button("清掉残渣", role: .destructive) { confirmClean = true }
                    Button("连构建中间件一起清", role: .destructive) { confirmCleanDeep = true }
                } label: {
                    Label("清垃圾", systemImage: "sparkles")
                }
                .menuStyle(.borderlessButton)
                .controlSize(.small)
                .fixedSize()
                .disabled(model.installingAppid != nil)
                .confirmationDialog("清掉这套工具的残渣？", isPresented: $confirmClean,
                                    titleVisibility: .visible) {
                    Button("清", role: .destructive) { model.clean(go: true, deep: false) }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("只删残渣：depot 暂存区、日志截图、测试用的 Wine prefix、解包完的安装器原件、打包失败的空壳。游戏本体、存档和编好的运行时都不碰。")
                }
                .confirmationDialog("连构建中间件一起清？", isPresented: $confirmCleanDeep,
                                    titleVisibility: .visible) {
                    Button("清", role: .destructive) { model.clean(go: true, deep: true) }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("除上面那些，还会删掉源码树与构建中间件（Wine 源码、旧的 DXVK、回滚掉的实验）。游戏照样能跑，但以后要重新编译运行时就得重下。")
                }
                Button {
                    model.loadAll()
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                .controlSize(.small)
                .disabled(model.loading || model.installingAppid != nil)
            }
            .font(.caption)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.bar)
        }
    }
}
