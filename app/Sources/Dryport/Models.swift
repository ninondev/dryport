// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
import Foundation

// Mirrors of what `api.py` prints. Decoding only — this app never writes back to the toolchain.

struct MachineState: Decodable {
    var steam_running: Bool
    var debug_port: Bool
    var can_download: Bool
    var toolchain_error: String?
    var free_bytes: Int64
    var library_size: Int
    var installed: Int
    var archived: Int?
    var route_counts: [String: Int]
    var tools: [String: Bool]
    var catalog_status: String?
    var catalog_error: String?
    var borrowed_count: Int?
    var metadata_missing: Int?
    var risk_pending: Int?
    var catalog_warnings: [String]?
    var catalog_owned_games: Int?
    var catalog_shared_games: Int?

    var downloadBlockReason: String? {
        if let error = toolchain_error, !error.isEmpty { return error }
        if can_download { return nil }
        if !steam_running {
            return "先打开 Steam 并登录。下载使用当前客户端的登录状态，这里不接收账号密码。"
        }
        if !debug_port {
            return "Steam 的本机连接尚未开启。点状态栏的「启用 Steam 本机连接…」创建 Valve 的调试开关文件，再重启一次 Steam。"
        }
        return "下载准备尚未完成，请刷新状态后重试。"
    }
}

struct GameRow: Decodable, Identifiable, Hashable {
    var appid: String
    var name: String
    var engine: String?
    var route: String
    var route_label: String
    var native: Bool
    var tier: String?
    var tier_label: String?
    var tier_note: String?
    var confidence: String?
    var evidence: String?
    var platform_note: String?
    var risk: String?
    var download_bytes: Int64
    var installed: Bool
    var archived: Bool?
    var app: String?
    var folder: String?
    var ownership: String?
    var license_source: String?

    var id: String { appid }

    var steamPageURL: URL? {
        guard let id = UInt32(appid), id > 0 else { return nil }
        return URL(string: "steam://nav/games/details/\(id)")
    }
}

struct LibraryPage: Decodable {
    var total: Int
    var rows: [GameRow]
}

struct LibrarySnapshot: Decodable {
    var library: LibraryPage
    var state: MachineState
}

struct RiskGate: Decodable {
    var code: Int
    var text: String
}

struct GameDetail: Decodable {
    var appid: String
    var found: Bool
    var name: String?
    var engine: String?
    var route: String?
    var route_label: String?
    var native: Bool?
    var tier: String?
    var tier_label: String?
    var tier_note: String?
    var confidence: String?
    var evidence: String?
    var manifest_note: String?
    var platform_note: String?
    var risk: String?
    var download_bytes: Int64?
    var folder: String?
    var app: String?
    var folder_bytes: Int64?
    var app_bytes: Int64?
    var risk_gate: RiskGate?
    /// Valid archives of this game under `.uninstalled` (newest first); non-empty means 「恢复归档」 is possible.
    var archives: [ArchiveEntry]?
}

struct ArchiveEntry: Decodable, Hashable {
    var path: String
    var app: String
    var name: String
}

/// One line of the irreversible-uninstall bill from `api.py uninstall-plan`.
struct PurgeItem: Decodable, Identifiable, Hashable {
    var kind: String        // app / download / archive / saves
    var path: String
    var label: String
    var bytes: Int64        // du 账面，上限
    var isDefault: Bool     // saves 默认 false
    var id: String { path }
    enum CodingKeys: String, CodingKey { case kind, path, label, bytes, isDefault = "default" }
}

struct PurgeSkipped: Decodable, Identifiable, Hashable {
    var kind: String
    var path: String
    var why: String
    var id: String { path }
}

struct PurgePlan: Decodable {
    var appid: String
    var items: [PurgeItem]
    var skipped: [PurgeSkipped]
    var bytes_default: Int64
    var bytes_saves: Int64
    var note: String
    var hasSaves: Bool { items.contains { $0.kind == "saves" } }
}

/// `api.py steam-debug enable`: Valve's own debug switch file, created only after the user confirms.
struct SteamDebugResult: Decodable {
    var path: String
    var flag_exists: Bool
    var endpoint_ok: Bool
    var created: Bool?
    var restart_needed: Bool?
}

/// One NDJSON line from `api.py install`.
struct InstallEvent: Decodable {
    var event: String
    var appid: String?
    var line: String?
    var t: Double?
    var percent: Int?
    var code: Int?
    var seconds: Double?
    var msg: String?
    var how: String?
    var path: String?
}

enum Route {
    static let nativeSet: Set<String> = ["unity", "nwjs", "mkxp", "godot", "krkr", "love", "webkit", "renpy-native"]
    static let wineSet: Set<String> = ["wine"]
    static let pendingSet: Set<String> = ["maybe", "fna", "renpy", "vn", "unknown"]

    /// Display order for the per-route section of the sidebar.
    static let order: [String] = ["steam", "unity", "nwjs", "webkit", "love", "mkxp", "godot", "renpy-native", "renpy", "krkr", "vn",
                                  "fna", "maybe", "wine", "unknown", "refuse"]

    static let label: [String: String] = [
        "steam": "Steam macOS 发行版",
        "webkit": "WebKit 原生（安装时校验版本）",
        "renpy-native": "Ren'Py 原生（安装时校验版本）",
        "love": "LÖVE 原包直读（待游戏验证）",
        "unity": "Unity macOS", "nwjs": "NW.js 原生", "mkxp": "mkxp-z 原生",
        "renpy": "Ren'Py 候选（未接通）", "godot": "Godot 原生", "krkr": "krkrsdl2 原生",
        "vn": "视觉小说（引擎待确认）", "fna": "FNA/MonoGame 候选（未接通）", "wine": "Wine 兜底",
        "unknown": "待识别", "maybe": "待定，先看看", "refuse": "不碰（风险拦截）",
    ]
}

func humanBytes(_ b: Int64) -> String {
    if b <= 0 { return "—" }
    let f = ByteCountFormatter()
    f.countStyle = .file
    f.allowedUnits = [.useMB, .useGB, .useTB]
    return f.string(fromByteCount: b)
}
