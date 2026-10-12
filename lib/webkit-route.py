# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""清单只选择已验证版本的安装入口；原资源 SHA 仍由安装检查器逐项核对。"""
import importlib

import risk


LABEL = "WebKit 原生（安装时校验版本）"


def refine_profile(row, windows_row, checked_depots, classification):
    # 不写入 manifest 分类缓存：风险、平台和生产 profile 都必须重新应用。
    if (row.get("route") != "maybe" or row.get("risk") != risk.REPACK
            or row.get("engine") != "Electron 候选"
            or tuple(classification[:2]) != ("Electron 候选", "maybe")
            or row.get("manifest_complete") is not True
            or row.get("manifest_depots") != 1 or row.get("total_depots") != 1
            or row.get("source") != "manifest" or len(checked_depots) != 1):
        return row
    for record in (row, windows_row):
        platforms = record.get("oslist")
        if (not isinstance(platforms, list) or any(not isinstance(name, str) for name in platforms)
                or "windows" not in platforms or "macos" in {name.strip().lower() for name in platforms}
                or record.get("platform_note") or record.get("metadata_status") == "missing"
                or record.get("depot_metadata_issues")):
            return row
    raw_depots = windows_row.get("depots")
    if raw_depots is not None:
        if not isinstance(raw_depots, list) or len(raw_depots) != 1 or not isinstance(raw_depots[0], dict):
            return row
        if any(raw_depots[0].get(key) for key in
               ("metadata_issues", "dlc_appid", "optional_dlc_appid", "optional")):
            return row
    try:
        profiles = importlib.import_module("webkit-inspect").PROFILES
        profile = profiles.get(str(row.get("appid")))
    except (ImportError, AttributeError, TypeError):
        return row
    if (not isinstance(profile, dict) or not isinstance(profile.get("profile"), str)
            or checked_depots != [{"depot": profile.get("depot"), "gid": profile.get("manifest")}]
            or str(row.get("appid")) != str(windows_row.get("appid"))):
        return row
    evidence = (row.get("evidence") or "Electron 清单线索") + \
        "；清单匹配 WebKit 已验证版本 " + profile["profile"] + "；安装时仍须校验完整原资源 SHA-256"
    return {**row, "route": "webkit", "route_name": LABEL, "evidence": evidence, "why": evidence}
