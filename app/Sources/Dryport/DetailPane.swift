// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import SwiftUI

struct DetailPane: View {
    @ObservedObject var model: AppModel

    var body: some View {
        if let row = model.selectedRow {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header(row)
                    factsBox(row)
                    if row.route == "steam" {
                        Text("Steam 元资料记录了 macOS 发行版。打开客户端查看安装和兼容性；旧版 macOS 游戏未必支持当前系统。")
                            .font(.callout).foregroundStyle(.secondary)
                    } else {
                        riskBox()
                    }
                    sizeBox(row)
                    actions(row)
                    if model.installingAppid == row.appid { installBox() }
                    if let msg = model.installMessage, model.installingAppid == nil {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(msg, systemImage: model.installFailed ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                                .font(.callout)
                                .foregroundStyle(model.installFailed ? Color.orange : Color.green)
                                .fixedSize(horizontal: false, vertical: true)
                            if model.installFailed && !model.installTail.isEmpty {
                                ScrollView {
                                    Text(model.installTail.joined(separator: "\n"))
                                        .font(.system(.caption, design: .monospaced))
                                        .textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .frame(maxHeight: 160)
                                .padding(6)
                                .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.1)))
                            }
                        }
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            Text("左边选一个游戏")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    // MARK: - Pieces

    private func header(_ row: GameRow) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(row.name)
                .font(.title2).bold()
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                RouteBadge(label: model.detail?.route_label ?? row.route_label,
                           route: model.detail?.route ?? row.route)
                if row.installed {
                    Label("已安装", systemImage: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.green)
                }
                Text("appid \(row.appid)").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func factsBox(_ row: GameRow) -> some View {
        DetailSection("路线与理由") {
            VStack(alignment: .leading, spacing: 6) {
                field("引擎", model.detail?.engine ?? row.engine ?? "未知")
                field("路线", model.detail?.route_label ?? row.route_label)
                // 预览版承诺：分了层（tier 非空）才显示；注释另起一行，缩进到与上面 field 的值同列。
                if let tier = model.detail?.tier ?? row.tier {
                    field("预览版", model.detail?.tier_label ?? row.tier_label ?? tier)
                    if let note = model.detail?.tier_note ?? row.tier_note, !note.isEmpty {
                        Text(note)
                            .font(.callout).foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.leading, 76)   // 68(标签宽) + 8(间距)，对齐 field 的值列
                    }
                }
                field("置信度", model.detail?.confidence ?? row.confidence ?? "未知")
                if let ev = model.detail?.evidence ?? row.evidence, !ev.isEmpty {
                    field("依据", ev)
                }
                if let note = model.detail?.manifest_note { field("清单", note) }
                if let note = model.detail?.platform_note ?? row.platform_note { field("平台", note) }
                if let risk = model.detail?.risk ?? row.risk, !risk.isEmpty, row.route != "steam" {
                    field("判定", risk)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 2)
        }
    }

    /// The ban-risk gate is safety information: show it whole, never truncated.
    private func riskBox() -> some View {
        DetailSection("封号风险闸") {
            Group {
                if model.detailLoading {
                    HStack(spacing: 6) { ProgressView().controlSize(.small); Text("读取中…") }
                        .foregroundStyle(.secondary)
                } else if let gate = model.detail?.risk_gate, !gate.text.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: gate.code == 0 ? "checkmark.shield" : "exclamationmark.triangle.fill")
                            .foregroundStyle(gate.code == 0 ? Color.green : Color.orange)
                        Text(gate.text)
                            .font(.callout)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } else if let error = model.detailError {
                    Text("风险检查未完成，暂不能安装。\n\(error)")
                        .textSelection(.enabled)
                        .foregroundStyle(.orange)
                } else {
                    Text("尚未取得风险检查结果，暂不能安装。").foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 2)
        }
    }

    private func sizeBox(_ row: GameRow) -> some View {
        DetailSection("体积") {
            VStack(alignment: .leading, spacing: 6) {
                field("下载", row.download_bytes > 0 ? humanBytes(row.download_bytes) : "未知")
                if let fb = model.detail?.folder_bytes, fb > 0 { field("下载目录", humanBytes(fb)) }
                if let ab = model.detail?.app_bytes, ab > 0 { field("成品 app", humanBytes(ab)) }
                if let folder = row.folder { field("目录", folder) }
                if let app = row.app { field("app", app) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 2)
        }
    }

    @State private var confirmArchive = false
    @State private var confirmRestore = false
    @State private var showUninstall = false

    private var hasArchive: Bool { !(model.detail?.archives ?? []).isEmpty }
    /// 没装着但还有本工具的东西（归档 app 或下载目录）时，也给「卸载…」入口清掉。
    private var hasLeftovers: Bool { hasArchive || model.detail?.folder != nil }

    private func actions(_ row: GameRow) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                if row.route == "steam", let url = row.steamPageURL {
                    Link(destination: url) {
                        Label("在 Steam 中打开", systemImage: "arrow.up.forward.app").frame(minWidth: 84)
                    }
                    .controlSize(.large)
                } else if row.installed {
                    Button {
                        model.play(row.appid)
                    } label: {
                        Label("开始玩", systemImage: "play.fill").frame(minWidth: 84)
                    }
                    .keyboardShortcut(.defaultAction)
                    .controlSize(.large)

                    Button {
                        model.reveal(row.appid)
                    } label: {
                        Label("在 Finder 里显示", systemImage: "folder")
                    }
                    .controlSize(.large)

                    Button {
                        confirmArchive = true
                    } label: {
                        Label("归档", systemImage: "archivebox")
                    }
                    .controlSize(.large)
                    .disabled(model.installingAppid != nil)
                    .confirmationDialog("归档 \(row.name)？", isPresented: $confirmArchive, titleVisibility: .visible) {
                        Button("移除快捷方式并归档 app") {
                            model.archive(row.appid)
                        }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("可逆：app 原样移入游戏目录的 .uninstalled 文件夹，资源和存档一并保留，随时能在这里「恢复归档」。下载文件和其他位置的存档不改动；这一步不释放磁盘空间。")
                    }

                    uninstallButton(row)
                } else {
                    if hasArchive {
                        Button {
                            confirmRestore = true
                        } label: {
                            Label("恢复归档", systemImage: "arrow.uturn.backward").frame(minWidth: 84)
                        }
                        .controlSize(.large)
                        .disabled(model.installingAppid != nil)
                        .confirmationDialog("恢复 \(row.name)？", isPresented: $confirmRestore, titleVisibility: .visible) {
                            Button("放回并重新加入 Steam 库") {
                                model.restore(row.appid)
                            }
                            Button("取消", role: .cancel) {}
                        } message: {
                            Text("最近一次归档的 app 原样放回游戏目录，再经 Steam 官方接口加回当前账号的库；不下载、不重建。请保持 Steam 开启。")
                        }
                    }

                    Button {
                        model.install(row.appid)
                    } label: {
                        Label(model.installingAppid == row.appid ? "安装中…" : "安装",
                              systemImage: "arrow.down.circle").frame(minWidth: 84)
                    }
                    .keyboardShortcut(.defaultAction)
                    .controlSize(.large)
                    .disabled(!model.canInstall(row))

                    if hasLeftovers { uninstallButton(row) }
                }
            }
            if let note = blockNote(row) {
                Text(note).font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if !row.installed, (model.detail?.route ?? row.route) == "wine" {
                Text("Wine + DXMT 兜底，有翻译层：首次启动初始化约 1 分钟，默认 1280x720 窗口不独占全屏；带 steam_api64.dll 的游戏经桥拿到 Steam 身份与成就。画面与操作按游戏各自验证。")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 卸载（不可逆）：先弹账单，逐项列路径与大小，存档单独一行默认不勾。
    private func uninstallButton(_ row: GameRow) -> some View {
        Button(role: .destructive) {
            model.loadPurgePlan(row.appid)
            showUninstall = true
        } label: {
            Label("卸载…", systemImage: "trash")
        }
        .controlSize(.large)
        .disabled(model.installingAppid != nil)
        .sheet(isPresented: $showUninstall) {
            UninstallSheet(model: model, row: row)
        }
    }

    private func blockNote(_ row: GameRow) -> String? {
        if row.installed || row.route == "steam" { return nil }
        let route = model.detail?.route ?? row.route
        if route == "refuse" {
            return "这个不碰：带第三方反作弊或联机，改包有封号风险，所以不提供安装。"
        }
        if ["fna", "renpy"].contains(route) {
            return "已识别引擎，但这一条自动打包流程尚未接通。"
        }
        if model.installingAppid != nil && model.installingAppid != row.appid {
            return "正在装别的游戏，一次只装一个。"
        }
        if !model.canDownload {
            return model.state?.downloadBlockReason ?? "正在读取本机下载状态。"
        }
        if model.detailLoading { return "正在检查游戏风险，完成后才能安装。" }
        if model.detail?.risk_gate?.code != 0 {
            return "风险检查尚未通过，请查看上方的具体原因。"
        }
        return nil
    }

    private func installBox() -> some View {
        DetailSection("安装进度") {
            VStack(alignment: .leading, spacing: 8) {
                if let p = model.installPercent {
                    ProgressView(value: p, total: 100)
                    Text("\(Int(p))%  ·  已用 \(Int(model.installSeconds)) 秒").font(.caption).monospacedDigit()
                } else {
                    ProgressView().progressViewStyle(.linear)
                    Text("已用 \(Int(model.installSeconds)) 秒").font(.caption).monospacedDigit()
                }
                Text(model.installLine)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(role: .destructive) {
                    model.cancelInstall()
                } label: {
                    Label("取消", systemImage: "stop.fill")
                }
                .controlSize(.small)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 2)
        }
    }

    private func field(_ k: String, _ v: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(k).font(.callout).foregroundStyle(.secondary).frame(width: 68, alignment: .leading)
            Text(v).font(.callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// 一行隔离样例中，系统 GroupBox 会令当前读屏连接中断；保留内容与标题，用普通布局呈现。
/// 卸载确认单。数据来自 `api.py uninstall-plan`：只列本工具自己创建的东西，账面是 du 上限。
private struct UninstallSheet: View {
    @ObservedObject var model: AppModel
    let row: GameRow
    @Environment(\.dismiss) private var dismiss
    @State private var deleteSaves = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("卸载 \(row.name)").font(.title3).bold()
            Text("不可逆。只删这套工具自己创建的东西；其他位置的存档和 Steam 自己的文件一律不动。只想暂时下架、以后原样放回，用「归档」。")
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if model.purgePlanLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在逐项算账…").foregroundStyle(.secondary)
                }
            } else if let error = model.purgePlanError {
                Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let plan = model.purgePlan {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(plan.items.filter { $0.kind != "saves" }) { item in
                        planRow(systemImage: icon(item.kind), label: item.label, path: item.path, bytes: item.bytes)
                    }
                    ForEach(plan.items.filter { $0.kind == "saves" }) { item in
                        Toggle(isOn: $deleteSaves) {
                            planRow(systemImage: "bookmark", label: item.label + "（默认保留）", path: item.path, bytes: item.bytes)
                        }
                        .toggleStyle(.checkbox)
                    }
                    ForEach(plan.skipped) { skipped in
                        Label("不动 \(skipped.path)：\(skipped.why)", systemImage: "hand.raised")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if plan.items.isEmpty {
                        Text("没有本工具创建的文件可删。").foregroundStyle(.secondary)
                    }
                }
                Text("账面合计 \(bytesText(plan.bytes_default + (deleteSaves ? plan.bytes_saves : 0)))，是上限（APFS 克隆会重复计）；删完按磁盘可用空间前后差报实际释放量。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("删除并卸载", role: .destructive) {
                    model.uninstall(row.appid, saves: deleteSaves)
                    dismiss()
                }
                .disabled(model.purgePlan == nil || model.purgePlan?.items.isEmpty == true || model.installingAppid != nil)
            }
        }
        .padding(20)
        .frame(minWidth: 540, idealWidth: 580)
    }

    private func icon(_ kind: String) -> String {
        switch kind {
        case "app": return "app.dashed"
        case "download": return "arrow.down.circle"
        case "archive": return "archivebox"
        default: return "doc"
        }
    }

    private func bytesText(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }

    private func planRow(systemImage: String, label: String, path: String, bytes: Int64) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: systemImage).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(label).fixedSize(horizontal: false, vertical: true)
                Text(path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Text(bytesText(bytes)).monospacedDigit()
        }
    }
}

private struct DetailSection<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.06)))
    }
}
