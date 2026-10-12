// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import AppKit
import Testing
@testable import Dryport

private final class ReloadCountingTable: NSTableView {
    var reloads = 0
    var scrollRequests: [Int] = []
    override func reloadData() {
        reloads += 1
        super.reloadData()
    }
    override func scrollRowToVisible(_ row: Int) {
        scrollRequests.append(row)
        super.scrollRowToVisible(row)
    }
}

private func tableRow(_ index: Int, installed: Bool = false) -> GameRow {
    GameRow(appid: String(index), name: "完整游戏名称 \(index)", route: "unknown",
            route_label: "待核实", native: false, download_bytes: 1024, installed: installed)
}

@Test @MainActor
func nativeListModelChangesDoNotEchoTransientSelectionOrReloadUnchangedRows() {
    var callbacks: [String?] = []
    let selected: (String?) -> Void = { callbacks.append($0) }
    let coordinator = NativeLibraryList.Coordinator(onSelect: selected)
    let table = ReloadCountingTable()
    let scroll = coordinator.makeScrollView(table: table)
    let rows = (0..<6956).map { tableRow($0) }
    coordinator.update(rows: rows, selection: "3457", onSelect: selected)
    #expect(table.numberOfRows == 6956)
    #expect(table.selectedRow == 3457)
    #expect(callbacks.isEmpty)
    let reloads = table.reloads
    for _ in 0..<50 {
        coordinator.update(rows: rows, selection: "3457", onSelect: selected)
    }
    #expect(table.reloads == reloads)
    coordinator.update(rows: [rows[3457]], selection: "3457", onSelect: selected)
    #expect(table.numberOfRows == 1)
    #expect(table.selectedRow == 0)
    #expect(callbacks.isEmpty)
    coordinator.update(rows: rows, selection: "3457", onSelect: selected)
    #expect(table.selectedRow == 3457)
    #expect(callbacks.isEmpty)
    // 模拟原生表格产生的用户单选；模型回写同值不再产生第二次回调。
    table.selectRowIndexes(IndexSet(integer: 50), byExtendingSelection: false)
    #expect(callbacks.count == 1 && callbacks[0] == "50")
    coordinator.update(rows: rows, selection: "50", onSelect: selected)
    #expect(callbacks.count == 1)
    table.deselectAll(nil)
    #expect(callbacks.count == 2 && callbacks[1] == nil)
    withExtendedLifetime(scroll) {}
}

@Test @MainActor
func nativeListKeepsEveryGameAndUsesTextCellsWithoutHostingViews() {
    let coordinator = NativeLibraryList.Coordinator(onSelect: { _ in })
    let table = NSTableView()
    let scroll = coordinator.makeScrollView(table: table)
    let rows = (0..<6956).map { tableRow($0, installed: $0 == 6955) }
    coordinator.update(rows: rows, selection: "6955", onSelect: { _ in })
    #expect(table.numberOfRows == 6956)
    #expect(table.rowHeight == 48)
    #expect(!table.usesAutomaticRowHeights && !table.allowsMultipleSelection)
    let cell = coordinator.tableView(table, viewFor: table.tableColumns.first, row: 6955)
    let fields = cell?.subviews.compactMap { $0 as? NSTextField } ?? []
    #expect(fields.count == 2)
    #expect(fields[0].stringValue == rows[6955].name)
    #expect(fields[1].stringValue.contains(rows[6955].route_label))
    #expect(fields[1].stringValue.contains(humanBytes(rows[6955].download_bytes)))
    #expect(fields[1].stringValue.contains("已安装"))
    #expect(cell?.subviews.allSatisfy { $0 is NSTextField } == true)
    // 相邻的未安装游戏不能复用上一行的安装标识。
    (cell as? LibraryTableCell)?.configure(rows[6954])
    #expect(fields[0].stringValue == rows[6954].name)
    #expect(!fields[1].stringValue.contains("已安装"))
    coordinator.update(rows: [], selection: nil, onSelect: { _ in })
    #expect(table.numberOfRows == 0 && table.selectedRow == -1)
    withExtendedLifetime(scroll) {}
}

@Test @MainActor
func nativeListContentRefreshPreservesManualScrollButSelectionAndMembershipLocateTheRow() {
    var callbacks: [String?] = []
    let selected: (String?) -> Void = { callbacks.append($0) }
    let coordinator = NativeLibraryList.Coordinator(onSelect: selected)
    let table = ReloadCountingTable()
    let scroll = coordinator.makeScrollView(table: table)
    scroll.frame = NSRect(x: 0, y: 0, width: 360, height: 240)
    var rows = (0..<500).map { tableRow($0) }
    coordinator.update(rows: rows, selection: "0", onSelect: selected)
    scroll.layoutSubtreeIfNeeded()
    scroll.contentView.scroll(to: NSPoint(x: 0, y: 1200))
    scroll.reflectScrolledClipView(scroll.contentView)
    let manualOrigin = scroll.contentView.bounds.origin
    #expect(manualOrigin.y > 0)
    let requests = table.scrollRequests.count

    rows[0].installed = true
    rows[250].route_label = "路线资料已刷新"
    coordinator.update(rows: rows, selection: "0", onSelect: selected)
    #expect(table.scrollRequests.count == requests)
    #expect(scroll.contentView.bounds.origin == manualOrigin)
    #expect(table.selectedRow == 0)

    coordinator.update(rows: rows, selection: "50", onSelect: selected)
    #expect(table.scrollRequests.last == 50)
    #expect(table.scrollRequests.count == requests + 1)
    rows.swapAt(0, 499)
    coordinator.update(rows: rows, selection: "50", onSelect: selected)
    #expect(table.scrollRequests.last == 50)
    #expect(table.scrollRequests.count == requests + 2)
    rows.removeFirst()
    coordinator.update(rows: rows, selection: "50", onSelect: selected)
    #expect(table.scrollRequests.last == 49)
    #expect(table.scrollRequests.count == requests + 3)
    #expect(table.selectedRow == 49 && callbacks.isEmpty)
    withExtendedLifetime(scroll) {}
}
