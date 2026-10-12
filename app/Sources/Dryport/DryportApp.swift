// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import SwiftUI
import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct DryportApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .onAppear { model.loadAll() }
        }
        .defaultSize(width: 1180, height: 740)
        .commands {
            CommandGroup(after: .toolbar) {
                Button("刷新") { model.loadAll() }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }
    }
}
