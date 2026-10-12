// swift-tools-version:5.9
// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import PackageDescription

let package = Package(
    name: "Dryport",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "Dryport", path: "Sources/Dryport"),
        .testTarget(name: "DryportTests", dependencies: ["Dryport"])
    ]
)
