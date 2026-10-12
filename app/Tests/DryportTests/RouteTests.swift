// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

@Test
func webkitCatalogRouteHasOneNativeSidebarEntryAndVersionCheckLabel() throws {
    let game = try JSONDecoder().decode(GameRow.self, from: Data("""
        {"appid":"1518810","name":"Fixture","route":"webkit",
         "route_label":"WebKit 原生（安装时校验版本）","native":true,
         "download_bytes":12,"installed":false}
        """.utf8))
    #expect(Route.nativeSet.contains(game.route))
    #expect(!Route.pendingSet.contains(game.route))
    #expect(!Route.wineSet.contains(game.route))
    #expect(Route.order.filter { $0 == game.route }.count == 1)
    #expect(Route.label[game.route] == game.route_label)
}
