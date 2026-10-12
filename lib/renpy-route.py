# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只把固定原包清单映射到安装入口；完整游戏字节仍在安装时验证。"""
import importlib

import risk


APPID = "2118420"
LABEL = "Ren'Py 原生（安装时校验版本）"
PROFILE_NOTE = "；清单匹配 Ren'Py 已验证版本 "


def refine_profile(row, windows_row, checked_depots, classification):
    # profile 不进入通用引擎缓存；旧目录里的升级也必须用当前证据重新取得。
    if row.get("route") == "renpy-native":
        evidence = (row.get("evidence") or "Ren'Py 清单线索").split(PROFILE_NOTE, 1)[0]
        row = {**row, "route": "renpy", "route_name": "Ren'Py 候选（未接通）",
               "evidence": evidence, "why": evidence}
    if (str(row.get("appid")) != APPID or str(windows_row.get("appid")) != APPID
            or row.get("route") != "renpy" or row.get("risk") != risk.REPACK
            or row.get("engine") != "Ren'Py" or tuple(classification[:2]) != ("Ren'Py", "renpy")
            or row.get("manifest_complete") is not True
            or type(row.get("manifest_depots")) is not int or row["manifest_depots"] != 1
            or type(row.get("total_depots")) is not int or row["total_depots"] != 1
            or row.get("source") != "manifest" or len(checked_depots) != 1):
        return row
    for record in (row, windows_row):
        platforms = record.get("oslist")
        if (not isinstance(platforms, list) or any(not isinstance(name, str) for name in platforms)
                or "windows" not in platforms or "macos" in {name.strip().lower() for name in platforms}
                or record.get("platform_note") or record.get("metadata_status") != "available"
                or record.get("depot_metadata_issues")):
            return row
    raw_depots = windows_row.get("depots")
    if not isinstance(raw_depots, list) or len(raw_depots) != 1 or not isinstance(raw_depots[0], dict):
        return row
    if any(raw_depots[0].get(key) for key in
           ("metadata_issues", "dlc_appid", "optional_dlc_appid", "optional", "shared_from_app")):
        return row
    try:
        profile = importlib.import_module("renpy-profile").PROFILES.get(APPID)
    except (ImportError, AttributeError, TypeError):
        return row
    if (not isinstance(profile, dict) or not isinstance(profile.get("profile"), str)
            or not profile["profile"]
            or checked_depots != [{"depot": profile.get("depot"), "gid": profile.get("manifest")}]
            or str(raw_depots[0].get("depot")) != profile.get("depot")
            or str(raw_depots[0].get("gid")) != profile.get("manifest")):
        return row
    evidence = (row.get("evidence") or "Ren'Py 清单线索") + PROFILE_NOTE + profile["profile"] + \
        "；安装时仍须校验完整原游戏 SHA-256"
    return {**row, "route": "renpy-native", "route_name": LABEL, "evidence": evidence, "why": evidence}
