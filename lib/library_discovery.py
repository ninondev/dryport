#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""从当前 Steam 账号取得自有与家庭共享清单；默认只读，加 --write 原子发布。

collector: node steam-library.mjs [--identity | --license <appid>]，JSON protocol=2。
discover() 返回 catalog、route-all、owned-windows、final-routes、windows-library 五表。
默认用批量客户端资料分流；--deep 才读取全部已有文件清单，详情可按单个游戏细化。
owned-windows 只含自有 Windows 游戏；windows-library 是完整 Windows 下载元资料。
route-all 保留当前订阅的全部游戏，包含 macOS 发行版和资料未齐的游戏。
publish() 写进独立 generation，最后只替换 current 指针；旧四表目录可继续读取。
current_catalog_dir() 供 API/CLI 读取同一 generation，不混读新旧表。
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import uuid
import locale

from paths import SN_HOME, SN_LIB, SN_GAMES, STEAM
import risk
import route
import reclassify

SCHEMA = 2
CATALOG_DIR = ".dryport-catalog"
LEGACY_TABLES = ("catalog", "route-all", "owned-windows", "final-routes")
TABLES = (*LEGACY_TABLES, "windows-library")
STEAM_ID_BASE = 76561197960265728
GENERATION = re.compile(r"generation-[0-9a-f]{32}$")


class DiscoveryError(ValueError):
    """拥有证据、账号或本地清单无法可靠验证。"""


_mirror_spec = importlib.util.spec_from_file_location("library_shortcut_account", SN_LIB / "steam-shortcut-mirror.py")
_mirror = importlib.util.module_from_spec(_mirror_spec)
_mirror_spec.loader.exec_module(_mirror)


def current_account_id():
    value = _mirror.active_account()
    if not value:
        raise DiscoveryError("无法确认 Steam 自动登录账号；请在 Steam 选定账号并登录后重试")
    return decimal(value, 2**32 - 1, "本地账号 ID")


def decimal(value, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        raise DiscoveryError(f"{label} 不是有效数字")
    result = int(value)
    if not 0 < result <= maximum:
        raise DiscoveryError(f"{label} 超出有效范围")
    return str(result)


def account_of(receipt):
    if not isinstance(receipt, dict) or type(receipt.get("protocol")) is not int or receipt["protocol"] not in (1, SCHEMA):
        raise DiscoveryError("Steam 许可凭据协议无效")
    account = receipt.get("account")
    if not isinstance(account, dict):
        raise DiscoveryError("Steam 没有返回当前账号")
    if not isinstance(account.get("steamid64"), str):
        raise DiscoveryError("Steam 64 位 ID 必须是字符串，避免精度丢失")
    sid = decimal(account.get("steamid64"), 2**64 - 1, "Steam ID")
    aid = decimal(account.get("account_id"), 2**32 - 1, "账号 ID")
    if int(sid) - STEAM_ID_BASE != int(aid):
        raise DiscoveryError("Steam 账号标识不一致，未生成清单")
    if account.get("id", aid) != aid:
        raise DiscoveryError("清单账号 ID 别名不一致")
    return {"steamid64": sid, "account_id": aid, "id": aid}


def validate_receipt(receipt):
    account = account_of(receipt)
    rows = receipt.get("apps")
    if not isinstance(rows, list):
        raise DiscoveryError("Steam 没有返回拥有列表，不能使用 appinfo 缓存代替")
    seen = set()
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoveryError("Steam 拥有列表记录格式无效")
        appid = decimal(row.get("appid"), 2**32 - 1, "appid")
        if row.get("subscribed") is not True or type(row.get("borrowed")) is not bool:
            raise DiscoveryError(f"appid {appid} 没有当前账号的明确订阅，已停止")
        owner = row.get("owner_account_id")
        if not isinstance(owner, str) or not owner.isdigit() or not 0 <= int(owner) <= 2**32 - 1:
            raise DiscoveryError(f"appid {appid} 许可来源账号无效")
        owner = str(int(owner))
        borrowed = owner != "0"
        expected = {"ownership": "borrowed" if borrowed else "owned",
                    "license_source": "family_shared" if borrowed else "self"}
        if row["borrowed"] != borrowed or owner == account["id"]:
            raise DiscoveryError(f"appid {appid} 的许可来源字段互相矛盾")
        if receipt["protocol"] == 1:
            if borrowed:
                raise DiscoveryError("旧协议不能表示家庭共享许可，请刷新游戏库")
        elif any(row.get(key) != value for key, value in expected.items()):
            raise DiscoveryError(f"appid {appid} 的自有/共享标记互相矛盾")
        if not isinstance(row.get("name"), str) or type(row.get("app_type")) is not int:
            raise DiscoveryError(f"appid {appid} 的客户端资料无效")
        if appid in seen:
            raise DiscoveryError(f"Steam 拥有列表含重复 appid {appid}")
        seen.add(appid)
        clean.append({**row, **expected, "appid": appid, "owner_account_id": owner})
    return account, clean


def read_client(identity_only=False, license_appid=None):
    args = ["node", str(SN_LIB / "steam-library.mjs")]
    if identity_only:
        args.append("--identity")
    if license_appid is not None:
        args.extend(["--license", decimal(license_appid, 2**32 - 1, "appid")])
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode and not (license_appid is not None and result.returncode == 3 and result.stdout.strip()):
        raise RuntimeError(result.stderr.strip() or "Steam 游戏库读取失败；请打开已登录的 Steam 库后重试")
    receipt = json.loads(result.stdout)
    if license_appid is not None and result.returncode and receipt.get("allowed") is True:
        raise DiscoveryError("Steam 许可检查退出异常，不能认作允许")
    return receipt


def check_license(appid):
    appid = decimal(appid, 2**32 - 1, "appid")
    receipt = read_client(license_appid=appid)
    account = account_of(receipt)
    if receipt.get("protocol") != SCHEMA or receipt.get("appid") != appid or type(receipt.get("allowed")) is not bool:
        raise DiscoveryError("Steam 即时许可凭据格式无效")
    status = receipt.get("status")
    if status not in ("available", "not_subscribed", "sharing_excluded", "in_use", "unknown") or receipt["allowed"] != (status == "available"):
        raise DiscoveryError("Steam 即时许可状态互相矛盾")
    if receipt["allowed"]:
        borrowed = receipt.get("ownership") == "borrowed"
        if (receipt.get("ownership") not in ("owned", "borrowed")
                or receipt.get("license_source") != ("family_shared" if borrowed else "self")
                or receipt.get("subscribed") is not True or receipt.get("source_confirmed") is not True
                or receipt.get("locked_by_other") is not False
                or (borrowed and receipt.get("sharing_excluded") is not False)):
            raise DiscoveryError("Steam 即时许可缺少允许依据")
    if account["id"] != current_account_id() or account_of(read_client(identity_only=True)) != account:
        raise DiscoveryError("许可检查期间 Steam 账号发生切换，请重试")
    return {**receipt, "account": account}


def os_names(value):
    return [name.strip() for name in str(value or "").lower().split(",") if name.strip()]


def byte_count(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        return None
    return int(value)


def _metadata_value(value, label, issues, depth=0):
    """保留原值和字段缺失的区别，不把未知约束转换成关闭。"""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, dict) and depth < 12:
        return {str(key): _metadata_value(item, f"{label}.{key}", issues, depth + 1)
                for key, item in value.items()}
    issues.append(f"{label} 的元资料类型无效")
    return None


def _metadata_id(value, label, issues):
    if value in (None, "", 0, "0") and not isinstance(value, bool):
        return ""
    try:
        if not re.fullmatch(r"[0-9]+", str(value)):
            raise ValueError()
        return decimal(value, 2**32 - 1, label)
    except ValueError:
        issues.append(f"{label} 不是有效 appid")
        return ""


def _manifest_metadata(value):
    issues = []
    record = value if isinstance(value, dict) else {"gid": value}
    try:
        gid = decimal(record.get("gid"), 2**64 - 1, "manifest gid")
    except ValueError:
        gid = None
        issues.append("manifest gid 缺失或无效")
    result = {"gid": gid}
    for key in ("download", "size"):
        number = byte_count(record.get(key))
        # 保留异常大整数的精度，JS 调用方可用 BigInt；不让 JSON Number 静默取整。
        result[key] = str(number) if number is not None and number > 2**53 - 1 else number
        if key in record and number is None:
            issues.append(f"manifest {key} 无效")
    if issues:
        result["metadata_issues"] = issues
    return result


def depot_metadata(app, *, windows_only=True):
    """保留 Steam 挂载次序与条件；这份本机元资料不是下载授权。"""
    result, issues, properties = [], [], {}
    depots = app.get("depots") if isinstance(app, dict) else None
    if not isinstance(depots, dict):
        return {"depots": [], "depot_metadata_version": 1, "depot_properties": {},
                "depot_metadata_issues": ["本机 depot 元资料缺失或格式无效"]}
    for order, (raw_depot, data) in enumerate(depots.items()):
        if not re.fullmatch(r"[0-9]+", str(raw_depot)):
            if isinstance(data, dict) and (str(raw_depot)[:1].isdigit() or raw_depot == "null"
                                           or "manifests" in data or "depotfromapp" in data):
                issues.append(f"depot 编号无效：{raw_depot}；不能确认完整内容包")
            else:
                properties[str(raw_depot)] = _metadata_value(data, str(raw_depot), issues)
            continue
        local = []
        try:
            depot = decimal(raw_depot, 2**32 - 1, "depot")
        except ValueError:
            issues.append(f"depot 编号超出范围：{raw_depot}")
            continue
        if not isinstance(data, dict):
            issues.append(f"depot {depot} 元资料格式无效")
            continue
        config = data.get("config", {})
        if not isinstance(config, dict):
            local.append("config 的元资料类型无效")
            config = {}
        config = _metadata_value(config, "config", local)
        for field in ("oslist", "language"):
            if field in config and not isinstance(config[field], str):
                local.append(f"config.{field} 不是文本")
        platforms = os_names(config.get("oslist")) if isinstance(config.get("oslist", ""), str) else []
        if windows_only and platforms and "windows" not in platforms:
            continue
        manifests, raw_manifests = {}, data.get("manifests", {})
        if isinstance(raw_manifests, dict):
            manifests = {str(branch): _manifest_metadata(value) for branch, value in raw_manifests.items()}
        else:
            local.append("manifests 的元资料类型无效")
        public = manifests.get("public", {})
        shared = _metadata_id(data.get("depotfromapp"), "depotfromapp", local)
        dlc = _metadata_id(data.get("dlcappid"), "dlcappid", local)
        optional_dlc = _metadata_id(config.get("optionaldlc"), "config.optionaldlc", local)
        optional = data.get("optional", 0)
        if type(optional) not in (int, str) or str(optional) not in ("0", "1"):
            local.append("optional 不是已知的 0/1 标记")
            optional = None
        else:
            optional = str(optional) == "1"
        # 额外字段原样保留；sharedinstall 的 1/2/空值不能被解释为同一个布尔值。
        extra = {key: _metadata_value(value, key, local) for key, value in data.items()
                 if key not in ("config", "manifests")}
        row = {**extra, "depot": depot, "mount_order": order, "config": config,
               "source_fields": list(data), "manifests": manifests,
               "gid": public.get("gid"), "dl": public.get("download"), "size": public.get("size"),
               "oslist": platforms, "language": str(config.get("language") or "").strip().lower(),
               "osarch": str(config.get("osarch") or "").strip(),
               "dlc_appid": dlc, "optional_dlc_appid": optional_dlc,
               "optional": optional, "optional_present": "optional" in data,
               "sharedinstall": extra.get("sharedinstall"), "shared_from_app": shared,
               "metadata_issues": local,
               "metadata_status": "invalid" if local else "available" if public.get("gid")
                   else "shared_manifest_pending" if shared else "public_manifest_missing"}
        result.append(row)
    return {"depots": result, "depot_metadata_version": 1, "depot_properties": properties,
            "depot_metadata_issues": issues}


def windows_depots(app):
    return depot_metadata(app)["depots"]


def _read_text_vdf(path):
    """只为读取语言配置解析键值树；拒绝截断和重复键，不触碰配置内容。"""
    text = Path(path).read_text(encoding="utf8")
    if len(text) > 8 * 1024 * 1024:
        raise ValueError("Steam 语言配置过大")
    token = re.compile(r'\s+|//[^\r\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')
    stack = [{}]
    pending = None
    position = 0
    for match in token.finditer(text):
        if match.start() != position:
            raise ValueError("Steam 语言配置有未闭合字符串")
        position = match.end()
        value = match.group()
        if value.isspace() or value.startswith("//"):
            continue
        if value == "{":
            if pending is None or len(stack) >= 64:
                raise ValueError("Steam 语言配置层级无效")
            child = {}
            stack[-1][pending] = child
            stack.append(child)
            pending = None
        elif value == "}":
            if pending is not None or len(stack) == 1:
                raise ValueError("Steam 语言配置括号不匹配")
            stack.pop()
        else:
            if value.startswith('"'):
                value = re.sub(r'\\([\\"])', r'\1', value[1:-1])
            if pending is None:
                pending = value.lower()
                if pending in stack[-1]:
                    raise ValueError("Steam 语言配置含重复键")
            else:
                stack[-1][pending] = value
                pending = None
    if position != len(text) or pending is not None or len(stack) != 1:
        raise ValueError("Steam 语言配置被截断")
    return stack[0]


def language_preferences(appid, account_id, *, steam=STEAM):
    # 只返回语言值；本机配置里的其他私人字段不离开此函数。
    sources = {
        "current_app_language": (Path(steam) / "userdata" / account_id / "config/localconfig.vdf",
                                 ("userlocalconfigstore", "software", "valve", "steam", "apps", appid, "language")),
        "steam_language": (Path(steam) / "registry.vdf", ("registry", "hkcu", "software", "valve", "steam", "language")),
    }
    result = {"language_provenance": {}}
    for field, (path, keys) in sources.items():
        value, status, reason = None, "unset", "当前账号未设置该语言偏好"
        try:
            node = _read_text_vdf(path)
            for key in keys:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, str) and node.strip():
                if not re.fullmatch(r"[a-z][a-z0-9_-]*", node.strip().lower()):
                    raise ValueError("Steam 语言代码格式无效")
                value, status, reason = node.strip().lower(), "set", "Steam 配置明确设置"
            elif node is not None and not isinstance(node, str):
                raise ValueError("Steam 语言值不是文本")
        except FileNotFoundError:
            reason = "Steam 尚未保存此配置"
        except (OSError, ValueError) as error:
            status, reason = "unavailable", str(error)
        result[field], result[field + "_status"] = value, status
        result["language_provenance"][field] = {"source": "current-account-localconfig" if field.startswith("current") else "steam-registry-hkcu",
                                                "status": status, "reason": reason}
    try:
        process_locale = locale.getlocale()[0]
    except ValueError:
        process_locale = None
    result["os_language_candidates"] = [process_locale] if process_locale else []
    result["language_provenance"]["os_language_candidates"] = {"source": "process-locale", "selected": False}
    return result


def get_depot_metadata(appid, *, steam=STEAM, max_apps=128, max_depth=8):
    """选定安装时读取宿主与共享来源；不扫描 manifest，不据此授予许可。"""
    appid = decimal(appid, 2**32 - 1, "appid")
    account = account_of(read_client(identity_only=True))
    if account["id"] != current_account_id():
        raise DiscoveryError("Steam 客户端与自动登录账号不一致，请完成登录后重试")
    appinfo = Path(steam) / "appcache/appinfo.vdf"
    def fingerprint():
        try:
            stat = appinfo.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None
    before = fingerprint()
    pending, seen, metadata, expansion_issues = {int(appid)}, set(), {}, []
    for _ in range(max_depth):
        if not pending:
            break
        if len(seen | pending) > max_apps:
            expansion_issues.append("共享内容来源超过读取上限，不能确认完整安装计划")
            break
        current = pending - seen
        seen.update(current)
        loaded = risk.load_appinfo(appinfo, appids=current)
        pending = set()
        for aid in sorted(current):
            app = loaded.get(aid) or {}
            item = {"appid": str(aid), **depot_metadata(app, windows_only=False)}
            common = app.get("common") or {}
            item["free_to_download"] = common.get("FreeToDownload", common.get("freetodownload"))
            metadata[str(aid)] = item
            for depot in item["depots"]:
                if depot["shared_from_app"] and int(depot["shared_from_app"]) not in seen:
                    pending.add(int(depot["shared_from_app"]))
    if pending:
        expansion_issues.append("共享内容来源尚未展开完整，不能确认安装计划")
    host = metadata.get(appid, {"appid": appid, **depot_metadata({})})
    host["depot_metadata_issues"].extend(expansion_issues)
    # 环是同一个 depot 的来源链，不把两个应用共享不同内容包误判为循环。
    for item in metadata.values():
        for depot in item["depots"]:
            aid, current, visited = item["appid"], depot, set()
            while current.get("shared_from_app"):
                pair = (aid, current["depot"])
                if pair in visited:
                    depot["metadata_issues"].append("共享 depot 来源形成循环")
                    break
                visited.add(pair)
                aid = current["shared_from_app"]
                current = next((candidate for candidate in metadata.get(aid, {}).get("depots", [])
                                if candidate["depot"] == depot["depot"]), None)
                if current is None:
                    depot["metadata_issues"].append(f"共享来源 {aid} 缺少 depot {depot['depot']} 元资料")
                    break
    preferences = language_preferences(appid, account["id"], steam=steam)
    if before != fingerprint():
        raise DiscoveryError("读取期间 Steam 内容包元资料已变化，请重试")
    if account_of(read_client(identity_only=True)) != account or current_account_id() != account["id"]:
        raise DiscoveryError("读取内容包元资料期间 Steam 账号发生切换，请重试")
    return {"protocol": 1, "event": "depot_metadata", "account": account, **host,
            "metadata_source": "local-steam-appinfo", "license_evidence": False,
            "sharedApps": {aid: item for aid, item in metadata.items() if aid != appid}, **preferences}


def manifest_files(depots, steam):
    files, found = [], 0
    for depot in depots:
        if not depot["gid"]:
            continue
        path = steam / "depotcache" / f"{depot['depot']}_{depot['gid']}.manifest.txt"
        if not path.is_file():
            continue
        found += 1
        for line in path.read_text(encoding="latin1").splitlines():
            match = reclassify.NAME.match(line)
            if match:
                files.append(match.group(1).replace("\\", "/").lower())
    return files, found


def apply_risk(appid, app, ac, ac_error=None):
    verdict = risk.assess(int(appid), app, ac or {})
    if ac_error and verdict["verdict"] != risk.REFUSE:
        verdict = {**verdict, "verdict": risk.UNKNOWN, "why": [str(ac_error), *verdict["why"]]}
    return verdict


def make_route(appid, name, app, verdict, classification=None):
    if classification is None:
        engine, confidence, selected, evidence = route.classify(app)
    else:
        engine, selected, confidence, evidence = classification
    if verdict["verdict"] == risk.REFUSE or selected == "refuse":
        selected = "refuse"
    elif verdict["verdict"] == risk.UNKNOWN:
        selected = "unknown"
    elif verdict["verdict"] == risk.WINE_ONLY and selected != "refuse":
        selected = "wine"
    return {"appid": appid, "name": name, "engine": engine, "confidence": confidence,
            "route": selected, "route_name": route.ROUTE_NAME.get(selected, selected),
            "why": evidence, "evidence": evidence, "risk": verdict["verdict"],
            "risk_why": "；".join(verdict["why"]), "source": "manifest" if classification else "appinfo"}


def metadata_issues_clear(value):
    # 与 depot-plan 的 issues 契约一致；空对象、空字符串等不等于已核实。
    return value is None or isinstance(value, list) and not value


def manifest_content_scope(windows_row):
    """分类读取已列出的内容；实际基础包/语言/DLC 选择仍由 depot-plan.mjs 负责。"""
    depots = windows_row.get("depots", [])
    if "depots" not in windows_row and windows_row.get("depot") and windows_row.get("gid"):
        depots = [{"depot": windows_row["depot"], "gid": windows_row["gid"]}]
    if not isinstance(depots, list):
        raise DiscoveryError("单游戏 depot 资料格式无效")
    checked, external, seen = [], [], set()
    metadata_valid = metadata_issues_clear(windows_row.get("depot_metadata_issues"))
    for depot in depots:
        if not isinstance(depot, dict):
            raise DiscoveryError("单游戏 depot 记录格式无效")
        item = {"depot": decimal(depot.get("depot"), 2**32 - 1, "depot"),
                "gid": decimal(depot["gid"], 2**64 - 1, "manifest gid") if depot.get("gid") is not None else None}
        if item["depot"] in seen:
            raise DiscoveryError("单游戏 depot 重复，无法确认内容范围")
        seen.add(item["depot"])
        metadata_valid = metadata_valid and metadata_issues_clear(depot.get("metadata_issues"))
        manifests = depot.get("manifests", {})
        if not isinstance(manifests, dict):
            metadata_valid = False
        elif "public" in manifests:
            public = manifests["public"]
            metadata_valid = (metadata_valid and isinstance(public, dict)
                              and metadata_issues_clear(public.get("metadata_issues")))
        # 与生产 planner 的公共运行库判据一致；不按编号、文件名或缺 gid 猜测。
        # 数据来自 depot_metadata 的规范化 shared_from_app。保留原 depots 给安装器。
        source = depot.get("shared_from_app")
        if type(source) in (int, str) and str(source) == "228980":
            external.append({**item, "appid": "228980", "kind": "steam_common_redistributable",
                             "status": "not_checked"})
        else:
            checked.append(item)
    return checked, {"manifest_scope": "listed_content_depots", "listed_depots": len(depots),
                     "external_dependency_depots": len(external), "external_dependencies": external,
                     "manifest_metadata_valid": bool(metadata_valid), "total_depots": len(checked)}


def refine_route(row, windows_row, *, steam=STEAM, route_cache=None, cache_only=False, cache_write=False):
    """只读一个游戏已有的 depot 清单；不连接 Steam、不重载全库元资料。"""
    if not isinstance(row, dict) or not isinstance(windows_row, dict):
        raise DiscoveryError("单游戏路线资料格式无效")
    appid = decimal(row.get("appid"), 2**32 - 1, "appid")
    if decimal(windows_row.get("appid"), 2**32 - 1, "appid") != appid:
        raise DiscoveryError("单游戏路线与下载资料的 appid 不一致")
    if row.get("route") == "steam":
        return {**row, "manifest_scan": "not_applicable", "manifest_complete": False}
    checked, scope = manifest_content_scope(windows_row)
    scope["manifest_metadata_valid"] = (scope["manifest_metadata_valid"]
                                        and metadata_issues_clear(row.get("depot_metadata_issues")))
    cached = route_cache.lookup(checked) if route_cache is not None else None
    if cached is None and cache_only:
        return {**row, **scope, "manifest_scan": "deferred", "manifest_complete": False}
    if cached is not None:
        classification, count, found = cached["classification"], cached["files"], cached["found"]
    else:
        token = route_cache.fingerprint(checked) if route_cache is not None and cache_write else None
        try:
            files, found = manifest_files(checked, Path(steam))
        except OSError as error:
            return {**row, **scope, "manifest_scan": "unavailable", "manifest_complete": False,
                    "manifest_note": f"本机文件清单无法读取，保留已有路线线索：{error}"}
        count = len(files)
        classification = reclassify.classify(files)
        if route_cache is not None and cache_write:
            route_cache.store(checked, classification, count, found, expected=token)
    result = {**row, **scope, "manifest_scan": "cached" if cached is not None else "complete", "files": count,
              "manifest_depots": found,
              "manifest_complete": bool(checked) and found == len(checked) and scope["manifest_metadata_valid"],
              "source": row.get("source") or "appinfo"}
    verdict = {"verdict": row.get("risk", risk.UNKNOWN), "why": [row.get("risk_why") or "风险资料待核实"]}
    if verdict["verdict"] not in (risk.REFUSE, risk.UNKNOWN, risk.WINE_ONLY, risk.REPACK):
        verdict["verdict"] = risk.UNKNOWN
    if count:
        has_engine = row.get("engine") not in (None, "未识别") and row.get("confidence") not in (None, "无")
        if classification[1] == "unknown" and (has_engine or row.get("route") not in (None, "unknown", "refuse")):
            # 语言包或不含引擎的资源清单不能推翻已有正面线索。
            result["manifest_note"] = "已有文件清单未发现引擎特征，保留原来的候选路线；仍需实际验证。"
        elif has_engine and row.get("confidence") == "确证" and classification[2] != "确证" and classification[1] != "refuse":
            result["manifest_note"] = "已有文件清单仅给出候选线索，保留原有确证引擎；仍需实际验证。"
        else:
            result.update(make_route(appid, row.get("name", windows_row.get("name", f"app {appid}")),
                                     {}, verdict, classification))
    # 已有拒绝证据不能被另一份清单降级；缺失风险证据也不能被引擎特征替代。
    if row.get("route") == "refuse" or verdict["verdict"] == risk.REFUSE:
        result["route"] = "refuse"
    elif verdict["verdict"] == risk.UNKNOWN and result.get("route") != "refuse":
        result["route"] = "unknown"
    elif verdict["verdict"] == risk.WINE_ONLY and result.get("route") != "refuse":
        result["route"] = "wine"
    # 引擎文件不能证明 Windows 专属；旧表缺 oslist 字段时仍沿用原有入口约定。
    platforms = row.get("oslist")
    valid_platforms = (isinstance(platforms, str) or
                       isinstance(platforms, list) and all(isinstance(name, str) for name in platforms))
    if ("oslist" in row and (not valid_platforms or "windows" not in platforms)
            and result.get("route") != "refuse"):
        result["route"] = "unknown"
    result["risk"] = verdict["verdict"]
    # 只映射本次已读/缓存清单的结果，缓存里仍是通用引擎分类。
    result = importlib.import_module("webkit-route").refine_profile(result, windows_row, checked, classification)
    result = importlib.import_module("renpy-route").refine_profile(result, windows_row, checked, classification)
    result["route_name"] = route.ROUTE_NAME.get(result.get("route"), result.get("route"))
    return result


def build_snapshot(receipt, apps, ac, *, ac_error=None, metadata_error=None, steam=STEAM,
                   deep=False, route_cache=None, cache_write=False):
    account, subscribed = validate_receipt(receipt)
    catalog, first, windows, final = [], [], [], []
    missing = 0
    for client_row in subscribed:
        appid = client_row["appid"]
        app = apps.get(int(appid)) or {}
        common = app.get("common") or {}
        name = common.get("name") or client_row["name"] or f"app {appid}"
        kind = str(common.get("type") or {1: "game", 4: "tool"}.get(client_row["app_type"], "unknown")).lower()
        platform_set, platform_evidence = risk.app_platforms(app)
        platforms = sorted(platform_set)
        complete = bool(app and common.get("type") and platforms)
        if not complete and kind == "game":
            missing += 1          # 工具、DLC 一类没有平台字段不算事，不进提示
        metadata_status = ("available" if platform_evidence == "oslist" else "derived" if platform_evidence in ("launch", "depot")
                           else "default" if platform_evidence == "default" else "missing") if complete else "missing"
        # 平台未齐时保留全部平台的只读证据；是否进入 Windows 下载表仍只看 common.oslist。
        depot_info = depot_metadata(app, windows_only=bool(platforms))
        depots = depot_info["depots"]
        license_fields = {key: client_row[key] for key in ("ownership", "license_source", "owner_account_id")}
        license_fields.update(subscribed=True, availability="not_checked")
        row = {"appid": appid, "name": name, "type": kind, "oslist": platforms, **license_fields,
               "metadata_status": metadata_status, "platform_evidence": platform_evidence,
               **depot_info}
        catalog.append(row)
        if kind != "game":
            continue
        verdict = apply_risk(appid, app, ac, ac_error)
        if not complete and verdict["verdict"] != risk.REFUSE:
            verdict = {**verdict, "verdict": risk.UNKNOWN,
                       "why": ["本地元资料尚未齐全", *verdict["why"]]}
        route_row = make_route(appid, name, app, verdict)
        route_row.update(license_fields)
        route_row.update(type=kind, oslist=platforms, metadata_status=row["metadata_status"], platform_evidence=platform_evidence)
        route_row.update(manifest_scan="deferred", manifest_complete=False)
        if platform_evidence == "default":
            route_row["platform_note"] = "Steam 资料未标平台，按 Steam 惯例视为 Windows 专属（老游戏常见）"
        elif platform_evidence in ("launch", "depot"):
            route_row["platform_note"] = "平台来自 Steam 的启动项或内容包资料（游戏总表里没标）"
        if "macos" in platforms:
            route_row.update(route="steam", route_name="Steam 官方 macOS 版", engine="Steam macOS 发行版",
                             confidence="确证", why="Steam 已提供 macOS 版本，请在 Steam 中安装或开始游戏",
                             evidence="Steam appinfo oslist 包含 macos", manifest_scan="not_applicable")
        elif not complete or "windows" not in platforms:
            why = "Steam 本地元资料尚未齐全，等待更新后确认路线" if not complete else "本地资料未标出 Windows 或 macOS 版本，尚不能确认运行路线"
            selected = "refuse" if verdict["verdict"] == risk.REFUSE else "unknown"
            route_row.update(route=selected, route_name=route.ROUTE_NAME[selected],
                             platform_note=why + "；已有引擎线索不代表 Windows 专属或可打包")
            row["platform_note"] = route_row["platform_note"]
        first.append(route_row)
        # 无平台资料也可保留已有清单证据；只读分类不授予 Windows 下载资格。
        classified = None
        if "macos" not in platforms and (deep or route_cache is not None):
            classified = refine_route(route_row, row, steam=steam, route_cache=route_cache,
                                      cache_only=not deep, cache_write=deep and cache_write)
            # 即使清单没有引擎信号，快照也保留和详情相同的内容/依赖分母。
            for key in ("manifest_scan", "manifest_complete", "manifest_scope", "manifest_depots",
                        "total_depots", "listed_depots", "external_dependency_depots",
                        "external_dependencies", "manifest_metadata_valid", "files"):
                if key in classified:
                    route_row[key] = classified[key]
            if classified.get("manifest_note"):
                route_row["manifest_note"] = classified["manifest_note"]
            if classified["source"] == "manifest":
                final.append(classified)
        if "windows" not in platforms or "macos" in platforms:
            continue
        downloadable = {**row, **route_row, "depot": None, "gid": None, "dl": 0}
        # 旧下载入口只支持一个 depot；语言备选、共享内容与 DLC 必须交给显式选择。
        if (len(depots) == 1 and depots[0]["gid"] and not depot_info["depot_metadata_issues"]
                and not depots[0]["metadata_issues"] and not depots[0]["dlc_appid"]
                and not depots[0]["optional_dlc_appid"] and not depots[0]["optional"]):
            downloadable.update({key: depots[0][key] for key in ("depot", "gid")})
            downloadable["dl"] = depots[0]["dl"] or 0
        windows.append(downloadable)
    for rows in (catalog, first, windows, final):
        rows.sort(key=lambda row: int(row["appid"]))
    now = datetime.now(timezone.utc).isoformat()
    owned_count = sum(row["ownership"] == "owned" for row in catalog)
    shared_count = len(catalog) - owned_count
    owned_windows = [row for row in windows if row["ownership"] == "owned"]
    games_owned_count = sum(row["ownership"] == "owned" for row in first)
    warnings = [str(error) for error in (metadata_error, ac_error) if error]
    if missing:
        warnings.append(f"{missing} 个游戏的 Steam 资料缺平台字段，已按未知保留，不影响其它游戏")
    manifest_unavailable = sum(row["manifest_scan"] == "unavailable" for row in first)
    if manifest_unavailable:
        warnings.append(f"{manifest_unavailable} 个游戏的本机文件清单无法读取；游戏与已有路线线索保留，请在详情中查看原因")
    excluded = {key: value for key, value in receipt.get("excluded", {}).items() if key != "borrowed"}
    return {"catalog": {"protocol": SCHEMA, "generated_at": now, "account": account,
                        "source": "steam-client-current-account", "apps": catalog,
                        "excluded": excluded, "metadata_missing": missing,
                        "owned_count": owned_count, "shared_count": shared_count,
                        "games": len(first), "games_owned_count": games_owned_count,
                        "games_shared_count": len(first) - games_owned_count,
                        "count_unit": "subscribed app entries, including games and tools",
                        "windows_owned_count": len(owned_windows),
                        "windows_shared_count": len(windows) - len(owned_windows),
                        "windows_count_unit": "Windows-only games",
                        "borrowed_count": shared_count,
                        "license_sources": {"owned": "Steam subscribed_to 且 owner_account_id=0",
                                            "borrowed": "Steam subscribed_to 且来自其他账号的家庭共享；下载时由 Steam 再次确认许可"},
                        "risk_pending": sum(row["risk"] == risk.UNKNOWN and row["route"] != "steam" for row in first),
                        "manifest_scan": "complete" if deep else "deferred",
                        "manifest_cached": sum(row["manifest_scan"] == "cached" for row in first),
                        "manifest_unavailable": manifest_unavailable,
                        "warnings": warnings},
            "route-all": first, "owned-windows": owned_windows, "final-routes": final,
            "windows-library": windows}


def discover(*, deep=False, cache_write=False):
    if cache_write:
        private_games_root(SN_GAMES)
    receipt = read_client()
    account, subscribed = validate_receipt(receipt)
    metadata_error = ac_error = None
    try:
        apps = risk.load_appinfo(appids={int(row["appid"]) for row in subscribed})
    except (OSError, ValueError, IndexError, struct.error) as error:
        apps = {}
        metadata_error = f"本机 Steam 游戏元资料不可用：{error}；已保留游戏目录，运行路线待核实"
    if receipt["apps"] and not apps and not metadata_error:
        metadata_error = "本机 Steam 游戏元资料为空；已保留游戏目录，请等待 Steam 库资料加载后刷新"
    try:
        ac = risk.load_ac()
    except (OSError, ValueError) as error:
        ac, ac_error = {}, f"反作弊资料不可用：{error}；请更新反作弊数据库后重试"
    from manifest_route_cache import ManifestRouteCache
    route_cache = ManifestRouteCache(STEAM, SN_GAMES.resolve() / CATALOG_DIR / "manifest-cache")
    snapshot = build_snapshot(receipt, apps, ac, ac_error=ac_error, metadata_error=metadata_error,
                              deep=deep, route_cache=route_cache, cache_write=cache_write)
    if account_of(read_client(identity_only=True)) != account:
        raise DiscoveryError("读取期间 Steam 账号发生切换，未发布清单；请重试")
    return snapshot


def table_names(catalog):
    protocol = catalog.get("protocol") if isinstance(catalog, dict) else None
    if type(protocol) is not int or protocol not in (1, SCHEMA):
        raise DiscoveryError("私人清单协议无效；请重新读取 Steam 库")
    return LEGACY_TABLES if protocol == 1 else TABLES


def _catalog_dir(games=SN_GAMES, *, validate_tables=True):
    root = Path(games).resolve() / CATALOG_DIR
    current = root / "current"
    if root.is_symlink():
        raise DiscoveryError(f"私人清单目录不能是软链：{root}")
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise DiscoveryError(f"清单指针格式无效：{current}")
    target = os.readlink(current)
    if not GENERATION.fullmatch(target):
        raise DiscoveryError(f"清单指针超出受控目录：{current}")
    directory = root / target
    if directory.is_symlink() or not directory.is_dir():
        raise DiscoveryError(f"清单目录缺失或是软链：{directory}")
    if not validate_tables:
        return directory
    catalog_path = directory / "catalog.json"
    if catalog_path.is_symlink() or not catalog_path.is_file():
        raise DiscoveryError(f"清单表缺失或是软链：{catalog_path}")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf8"))
    except (OSError, ValueError) as error:
        raise DiscoveryError(f"清单损坏：{catalog_path}；请重新读取 Steam 库") from error
    for table in table_names(catalog):
        path = directory / f"{table}.json"
        if path.is_symlink() or not path.is_file():
            raise DiscoveryError(f"清单表缺失或是软链：{path}")
    return directory


def current_catalog_dir(games=SN_GAMES):
    try:
        directory = _catalog_dir(games)
        if directory is None:
            return None
        try:
            catalog = json.loads((directory / "catalog.json").read_text(encoding="utf8"))
        except (OSError, ValueError) as error:
            raise DiscoveryError(f"清单损坏：{directory / 'catalog.json'}；请重新读取 Steam 库") from error
        account = account_of(catalog)
        if account["id"] != current_account_id():
            raise DiscoveryError("Steam 账号已切换；旧账号清单不显示，请刷新当前账号游戏库")
        for table in table_names(catalog)[1:]:
            try:
                rows = json.loads((directory / f"{table}.json").read_text(encoding="utf8"))
            except (OSError, ValueError) as error:
                raise DiscoveryError(f"清单损坏：{directory / (table + '.json')}；请重新读取 Steam 库") from error
            if not isinstance(rows, list) or any(not isinstance(row, dict) or "appid" not in row for row in rows):
                raise DiscoveryError(f"清单表格式损坏：{table}.json；请重新读取 Steam 库")
            for row in rows:
                decimal(row["appid"], 2**32 - 1, f"{table}.json 的 appid")
                if table != "owned-windows" and not isinstance(row.get("name"), str):
                    raise DiscoveryError(f"清单表名称损坏：{table}.json；请重新读取 Steam 库")
        return directory
    except (OSError, ValueError) as error:
        raise DiscoveryError(str(error)) from error


def private_games_root(games):
    games = Path(games).resolve()
    home = SN_HOME.resolve()
    if games == home or home in games.parents:
        raise DiscoveryError("个人清单不能写入源码仓库；请把 SN_GAMES 指向独立游戏目录")
    return games


def publish(snapshot, games=SN_GAMES):
    tables = table_names(snapshot.get("catalog"))
    if any(table not in snapshot for table in tables):
        raise DiscoveryError("私人清单表不完整，旧清单保留")
    account = account_of(snapshot["catalog"])
    if account_of(read_client(identity_only=True)) != account:
        raise DiscoveryError("发布前 Steam 账号发生切换，旧清单保留；请重试")
    if account["id"] != current_account_id():
        raise DiscoveryError("Steam 客户端与自动登录账号不一致，旧清单保留；请完成登录后重试")
    games = private_games_root(games)
    root = games / CATALOG_DIR
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise DiscoveryError(f"私人清单路径不是普通目录：{root}")
    _catalog_dir(games, validate_tables=False)  # 保留旧版本，允许刷新来修复损坏的旧表。
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    generation = "generation-" + uuid.uuid4().hex
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    pointer = root / (".current-" + uuid.uuid4().hex)
    try:
        for table in tables:
            target = stage / f"{table}.json"
            with target.open("x", encoding="utf8") as stream:
                json.dump(snapshot[table], stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            target.chmod(0o600)
        destination = root / generation
        stage.rename(destination)
        if account_of(read_client(identity_only=True)) != account or current_account_id() != account["id"]:
            raise DiscoveryError("写入期间 Steam 账号发生切换，旧清单保留；请重试")
        pointer.symlink_to(generation)
        os.replace(pointer, root / "current")
        return destination
    finally:
        if pointer.is_symlink():
            pointer.unlink()
        # 失败产物保留，旧 current 始终可读；不清理用户目录或历史版本。


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把清单写入私人游戏目录，保留上一版")
    parser.add_argument("--human", action="store_true", help="输出简短中文结果")
    parser.add_argument("--deep", action="store_true", help="逐游戏读取本机已有文件清单，进一步识别引擎；大型库可能较慢")
    parser.add_argument("--path", action="store_true", help="只输出账号校验通过的当前清单目录；没有清单返回 1")
    parser.add_argument("--license", metavar="APPID", help="只读检查一个游戏的当前许可，不下载")
    parser.add_argument("--depot-metadata", metavar="APPID", help="只读一个游戏和共享来源的内容包条件，不下载")
    args = parser.parse_args(argv)
    try:
        if args.depot_metadata is not None:
            if args.write or args.path or args.deep or args.license is not None or args.human:
                raise DiscoveryError("--depot-metadata 不能与其他操作一起使用")
            print(json.dumps(get_depot_metadata(args.depot_metadata), ensure_ascii=False))
            return 0
        if args.license is not None:
            if args.write or args.path or args.deep:
                raise DiscoveryError("--license 不能与 --write、--path 或 --deep 一起使用")
            receipt = check_license(args.license)
            if args.human:
                print("Steam 当前许可已确认；实际下载仍由 Steam 授权。" if receipt["allowed"]
                      else f"Steam 当前许可未通过：{receipt['status']}；请在 Steam 库检查游戏状态。")
            else:
                print(json.dumps(receipt, ensure_ascii=False))
            return 0 if receipt["allowed"] else 3
        if args.path:
            if args.write or args.human or args.deep:
                raise DiscoveryError("--path 不能与 --write、--human 或 --deep 一起使用")
            directory = current_catalog_dir()
            if directory is None:
                return 1
            print(directory)
            return 0
        snapshot = discover(deep=args.deep, cache_write=args.write)
        directory = publish(snapshot) if args.write else None
        catalog = snapshot["catalog"]
        if args.human:
            print(f"当前账号 {catalog['account']['id']}：已读取 {catalog['games']} 个游戏。")
            print("已扫描本机已有文件清单。" if catalog["manifest_scan"] == "complete"
                  else "已根据 Steam 元资料分流；打开游戏详情时会读取已有文件清单，进一步识别引擎。")
            if catalog["manifest_cached"]:
                print(f"已复用 {catalog['manifest_cached']} 个游戏的文件识别结果，输入或规则变化后会自动重识别。")
            if directory:
                print(f"清单已保存：{directory}")
            else:
                print("只读预演，未写入清单。")
            for warning in catalog["warnings"]:
                print(warning)
            if catalog["risk_pending"]:
                print(f"风险待核实 {catalog['risk_pending']} 个，补全资料前不能打包。")
            return 0
        print(json.dumps({"event": "catalog", "protocol": SCHEMA, "written": bool(directory),
                          "directory": str(directory) if directory else None,
                          "account": catalog["account"], "owned_apps": catalog["owned_count"],
                          "shared_apps": catalog["shared_count"],
                          "games": catalog["games"], "games_owned_count": catalog["games_owned_count"],
                          "games_shared_count": catalog["games_shared_count"],
                          "windows_games": len(snapshot["windows-library"]),
                          "windows_owned_count": catalog["windows_owned_count"],
                          "windows_shared_count": catalog["windows_shared_count"],
                          "manifest_classified": len(snapshot["final-routes"]),
                          "manifest_scan": catalog["manifest_scan"],
                          "metadata_missing": catalog["metadata_missing"],
                          "risk_pending": catalog["risk_pending"],
                          "excluded": catalog["excluded"], "borrowed_count": catalog["borrowed_count"],
                          "license_sources": catalog["license_sources"], "warnings": catalog["warnings"]}, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        if args.human or args.path:
            print(f"游戏清单未更新：{error}", file=sys.stderr)
        else:
            print(json.dumps({"event": "error", "msg": str(error), "written": False}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
