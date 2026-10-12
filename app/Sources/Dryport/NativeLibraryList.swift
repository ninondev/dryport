// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import AppKit
import SwiftUI

/// 大库筛选只复用原生单元格，避免成千上万个 NSHostingView 的观察器注销阻塞主线程。
struct NativeLibraryList: NSViewRepresentable {
    let rows: [GameRow]
    let selection: String?
    let onSelect: (String?) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onSelect: onSelect) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = context.coordinator.makeScrollView()
        context.coordinator.update(rows: rows, selection: selection, onSelect: onSelect)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.update(rows: rows, selection: selection, onSelect: onSelect)
    }

    final class Coordinator: NSObject, NSTableViewDataSource, NSTableViewDelegate {
        private var rows: [GameRow] = []
        private var indexByID: [String: Int] = [:]
        private var selectedID: String?
        private var applyingModel = false
        private var onSelect: (String?) -> Void
        private weak var table: NSTableView?

        init(onSelect: @escaping (String?) -> Void) { self.onSelect = onSelect }

        func makeScrollView(table: NSTableView = NSTableView()) -> NSScrollView {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("game"))
            column.resizingMask = .autoresizingMask
            column.width = 360
            table.addTableColumn(column)
            table.headerView = nil
            table.style = .inset
            table.rowHeight = 48
            table.usesAutomaticRowHeights = false
            table.intercellSpacing = NSSize(width: 0, height: 0)
            table.allowsMultipleSelection = false
            table.allowsEmptySelection = true
            table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
            table.autoresizingMask = [.width, .height]
            table.setAccessibilityLabel("游戏列表")
            table.dataSource = self
            table.delegate = self
            self.table = table

            let scroll = NSScrollView()
            scroll.hasVerticalScroller = true
            scroll.hasHorizontalScroller = false
            scroll.autohidesScrollers = true
            scroll.borderType = .noBorder
            scroll.documentView = table
            return scroll
        }

        func update(rows newRows: [GameRow], selection: String?, onSelect: @escaping (String?) -> Void) {
            self.onSelect = onSelect
            guard let table else { return }
            let rowsChanged = rows != newRows
            let selectionChanged = selectedID != selection
            guard rowsChanged || selectionChanged else { return }
            let rowOrderChanged = rowsChanged && (rows.count != newRows.count
                || zip(rows, newRows).contains { $0.0.appid != $0.1.appid })

            // reloadData 会暂时清空选中项；那不是用户操作，不能反向清掉详情。
            applyingModel = true
            defer { applyingModel = false }
            if rowsChanged {
                rows = newRows
                indexByID = [:]
                indexByID.reserveCapacity(rows.count)
                for (index, row) in rows.enumerated() where indexByID[row.appid] == nil {
                    indexByID[row.appid] = index
                }
                table.reloadData()
            }
            selectedID = selection
            let index = selection.flatMap { indexByID[$0] }
            let indexes = index.map { IndexSet(integer: $0) } ?? IndexSet()
            if table.selectedRowIndexes != indexes {
                table.selectRowIndexes(indexes, byExtendingSelection: false)
            }
            // 资料或安装状态刷新不应打断用户手动滚动；筛选、排序或换选择时才定位。
            if let index, selectionChanged || rowOrderChanged { table.scrollRowToVisible(index) }
        }

        func numberOfRows(in tableView: NSTableView) -> Int { rows.count }

        func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
            guard rows.indices.contains(row) else { return nil }
            let id = LibraryTableCell.reuseID
            let cell = tableView.makeView(withIdentifier: id, owner: nil) as? LibraryTableCell
                ?? LibraryTableCell()
            cell.configure(rows[row])
            return cell
        }

        func tableViewSelectionDidChange(_ notification: Notification) {
            guard !applyingModel, let table else { return }
            let index = table.selectedRow
            let id = rows.indices.contains(index) ? rows[index].appid : nil
            guard id != selectedID else { return }
            selectedID = id
            onSelect(id)
        }
    }
}

final class LibraryTableCell: NSTableCellView {
    static let reuseID = NSUserInterfaceItemIdentifier("native-game-cell")
    private let nameField = NSTextField(labelWithString: "")
    private let summaryField = NSTextField(labelWithString: "")

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        identifier = Self.reuseID
        nameField.font = .systemFont(ofSize: NSFont.systemFontSize)
        summaryField.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        summaryField.textColor = .secondaryLabelColor
        for field in [nameField, summaryField] {
            field.translatesAutoresizingMaskIntoConstraints = false
            field.maximumNumberOfLines = 1
            field.lineBreakMode = .byTruncatingTail
            addSubview(field)
        }
        textField = nameField
        NSLayoutConstraint.activate([
            nameField.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 4),
            nameField.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -4),
            nameField.topAnchor.constraint(equalTo: topAnchor, constant: 6),
            summaryField.leadingAnchor.constraint(equalTo: nameField.leadingAnchor),
            summaryField.trailingAnchor.constraint(equalTo: nameField.trailingAnchor),
            summaryField.topAnchor.constraint(equalTo: nameField.bottomAnchor, constant: 3)
        ])
    }

    convenience init() { self.init(frame: .zero) }
    required init?(coder: NSCoder) { return nil }

    func configure(_ row: GameRow) {
        nameField.stringValue = row.name
        var parts = [row.route_label]
        if row.download_bytes > 0 { parts.append(humanBytes(row.download_bytes)) }
        if row.installed { parts.append("已安装") }
        summaryField.stringValue = parts.joined(separator: " · ")
        toolTip = row.name + "\n" + summaryField.stringValue
    }
}
