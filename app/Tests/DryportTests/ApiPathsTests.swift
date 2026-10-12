// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation
import Testing
@testable import Dryport

@Test
func finderLaunchRetainsEverySavedRoot() {
    let paths = ["SN_HOME": "/tmp/代码", "SN_GAMES": "/tmp/game files",
                 "SN_RUNTIMES": "/tmp/runtime's cache", "STEAM": "/tmp/steam profile"]
    let resolved = Api.resolveEnvironment(["LANG": "zh_CN.UTF-8"], bundledPaths: paths)
    for (key, value) in paths { #expect(resolved[key] == value) }
    #expect(resolved["LANG"] == "zh_CN.UTF-8")
}

@Test(arguments: ["SN_HOME", "SN_GAMES", "SN_RUNTIMES", "STEAM"])
func explicitEnvironmentOverridesOnlyItsRoot(key: String) {
    let saved = Dictionary(uniqueKeysWithValues: Api.pathKeys.map { ($0, "/tmp/saved/\($0)") })
    let resolved = Api.resolveEnvironment([key: "/tmp/explicit path "], bundledPaths: saved)
    #expect(resolved[key] == "/tmp/explicit path ")
    for other in Api.pathKeys where other != key { #expect(resolved[other] == saved[other]) }
}

@Test(arguments: ["SN_HOME", "SN_GAMES", "SN_RUNTIMES", "STEAM"])
func emptyEnvironmentDoesNotEraseSavedRoot(key: String) {
    #expect(Api.resolveEnvironment([key: ""], bundledPaths: [key: "/tmp/saved"])[key] == "/tmp/saved")
    #expect(Api.resolveEnvironment([key: ""], bundledPaths: [key: ""])[key] == nil)
}

@Test
func internalPlistIsDecodedWithoutChangingPathCharacters() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".app")
    let contents = root.appendingPathComponent("Contents")
    let resources = contents.appendingPathComponent("Resources")
    try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let info = ["CFBundleIdentifier": "local.dryport.path-test", "CFBundlePackageType": "APPL"]
    try PropertyListEncoder().encode(info).write(to: contents.appendingPathComponent("Info.plist"))
    let paths = ["SN_HOME": "/tmp/code", "SN_GAMES": "/tmp/游戏 & \"目录\"",
                 "SN_RUNTIMES": "/tmp/runtime's cache", "STEAM": "/tmp/steam profile"]
    try PropertyListEncoder().encode(paths).write(to: resources.appendingPathComponent("toolchain-paths.plist"))
    let bundle = try #require(Bundle(url: root))
    #expect(Api.loadBundledPaths(bundle) == paths)
}

@Test
func bundleRelativeHomeResolvesAgainstTheBundleItself() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".app")
    let contents = root.appendingPathComponent("Contents")
    let resources = contents.appendingPathComponent("Resources")
    try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let info = ["CFBundleIdentifier": "local.dryport.standalone-test", "CFBundlePackageType": "APPL"]
    try PropertyListEncoder().encode(info).write(to: contents.appendingPathComponent("Info.plist"))
    let paths = ["SN_HOME": "@bundle/Contents/Resources/toolchain", "SN_GAMES": "/tmp/games"]
    try PropertyListEncoder().encode(paths).write(to: resources.appendingPathComponent("toolchain-paths.plist"))
    let bundle = try #require(Bundle(url: root))
    let loaded = Api.loadBundledPaths(bundle)
    #expect(loaded["SN_HOME"] == bundle.bundlePath + "/Contents/Resources/toolchain")
    #expect(loaded["SN_GAMES"] == "/tmp/games")
}
