#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Gun-Dog 8.1.3 有界初始化实验；prepare 不启动游戏，run 须单独授权。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sn_postinit_base", ROOT / "tools/probe-renpy-postinit.py")
POST = importlib.util.module_from_spec(spec)
spec.loader.exec_module(POST)
BASE = POST.BASE
APPID = "2118420"
COMMAND = "sn_real_postinit"
STEAM_SOURCE = "https://partner.steamgames.com/doc/api/steam_api#SteamAPI_Init"
SOURCES = ("tools/probe-renpy-realinit.py", "tools/probe-renpy-postinit.py", "tools/probe-renpy.py",
           "lib/love-runtime.py", "lib/paths.py", "lib/risk.py", "lib/library_discovery.py")

# 原值全部保留；时间命名只能被发现，不能在观测层被抹掉。
NAMEMAP_HELPERS = r'''
import hashlib, json, math, os
# 上限约束扫描分母、实际输出及关键文件明细；普通剧情节点只留完整摘要。
NAMEMAP_LIMITS = {"entries": 600000, "files": 4096, "total_bytes": 32 * 1024 * 1024,
                  "part_bytes": 768 * 1024, "index_bytes": 4 * 1024 * 1024,
                  "detail_entries": 8192, "key_bytes": 4096, "key_depth": 8}
NAMEMAP_ALGORITHM = "sha256(sorted(sha256(canonical_record).digest()))-v1"
NAMEMAP_FIELDS = ("protocol", "encoding", "aggregation", "count", "file_count", "detail_count",
                  "canonical_sha256", "canonical_key_sha256", "non_early_count", "non_early_sha256",
                  "capture_epoch", "edge_scan")
SUMMARY_FIELDS = ("filename", "count", "sha256", "key_sha256", "non_early_count", "non_early_sha256")

def nm_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")

def nm_key(value, depth=0):
    if depth > NAMEMAP_LIMITS["key_depth"]:
        raise ValueError("namemap_key_depth_limit")
    kind = type(value)
    if value is None:
        result = {"type": "none", "value": None}
    elif kind is bool:
        result = {"type": "bool", "value": value}
    elif kind is int:
        result = {"type": "int", "value": str(value)}
    elif kind is float:
        if not math.isfinite(value):
            raise ValueError("namemap_nonfinite_key")
        result = {"type": "float", "value": value.hex()}
    elif kind is str:
        if len(value.encode("utf-8")) > NAMEMAP_LIMITS["key_bytes"]:
            raise ValueError("namemap_key_bytes_limit")
        result = {"type": "str", "value": value}
    elif kind is tuple:
        if len(value) > NAMEMAP_LIMITS["key_bytes"]:
            raise ValueError("namemap_key_items_limit")
        result = {"type": "tuple", "items": [nm_key(item, depth + 1) for item in value]}
    else:
        raise ValueError("namemap_unsupported_key_type:" + kind.__module__ + "." + kind.__qualname__)
    if len(nm_json(result)) > NAMEMAP_LIMITS["key_bytes"]:
        raise ValueError("namemap_key_bytes_limit")
    return result

def nm_digest(packed):
    # 固定 32 字节边界且不去重，重排不改变结果，新增/删除相同记录也会改变结果。
    digest = hashlib.sha256()
    for item in sorted(packed[i:i + 32] for i in range(0, len(packed), 32)):
        digest.update(item)
    return digest.hexdigest()

def nm_totals(files):
    overall, keys, non_early = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    for item in files:
        overall.update(nm_json({key: item[key] for key in ("filename", "count", "sha256")}) + b"\n")
        keys.update(nm_json({"filename": item["filename"], "count": item["count"],
                             "sha256": item["key_sha256"]}) + b"\n")
        if item["non_early_count"]:
            non_early.update(nm_json({"filename": item["filename"], "count": item["non_early_count"],
                                      "sha256": item["non_early_sha256"]}) + b"\n")
    return {"canonical_sha256": overall.hexdigest(), "canonical_key_sha256": keys.hexdigest(),
            "non_early_count": sum(item["non_early_count"] for item in files),
            "non_early_sha256": non_early.hexdigest()}

def capture_statement_identity(namemap, directory, started_epoch, finished_epoch, all_stmts=None,
                               detail_files=()):
    if type(namemap) is not dict:
        raise ValueError("namemap_type:" + type(namemap).__module__ + "." + type(namemap).__qualname__)
    if len(namemap) > NAMEMAP_LIMITS["entries"]:
        raise ValueError("namemap_entries_limit:%d>%d" % (len(namemap), NAMEMAP_LIMITS["entries"]))
    if all_stmts is None:
        all_stmts = list(namemap.values())
    if type(all_stmts) is not list or len(all_stmts) > NAMEMAP_LIMITS["entries"]:
        raise ValueError("namemap_all_stmts_limit_or_type")
    if (type(detail_files) not in (list, tuple) or len(detail_files) > NAMEMAP_LIMITS["files"]
            or any(type(name) is not str or len(name.encode("utf-8")) > 4096 for name in detail_files)):
        raise ValueError("namemap_detail_files_limit_or_type")
    selected = set(detail_files)
    for node in namemap.values():
        if type(node).__module__ + "." + type(node).__qualname__ == "renpy.ast.EarlyPython":
            selected.add(node.filename)
    # all_stmts 还可能保留被同名 label 遮盖的节点；统计对象入边不丢这一层。
    unique_ids, incoming = set(), {}
    for node in all_stmts:
        identity = id(node)
        if identity in unique_ids:
            continue
        unique_ids.add(identity)
        following = getattr(node, "next", None)
        if following is not None:
            incoming[id(following)] = incoming.get(id(following), 0) + 1
    groups, detail_count, detail_bytes = {}, 0, 0
    for key, node in namemap.items():
        encoded = nm_key(key)
        filename, line = node.filename, node.linenumber
        if (type(filename) is not str or len(filename.encode("utf-8")) > 4096
                or (line is not None and type(line) is not int)):
            raise ValueError("namemap_node_metadata_type_or_limit")
        node_type = type(node).__module__ + "." + type(node).__qualname__
        following = getattr(node, "next", None)
        early_python = None
        if node_type == "renpy.ast.EarlyPython":
            source = node.code.source
            if (type(source) is not str or len(source) > 1024 * 1024 or type(node.hide) is not bool
                    or type(node.store) is not str or len(node.store.encode("utf-8")) > 4096):
                raise ValueError("namemap_early_python_metadata_type_or_limit")
            source_bytes = source.encode("utf-8")
            if len(source_bytes) > 1024 * 1024:
                raise ValueError("namemap_early_python_source_bytes_limit")
            early_python = {"source_type": "str", "source_bytes": len(source_bytes),
                            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                            "hide": node.hide, "store": node.store}
        record = {"key": encoded, "filename": filename, "linenumber": line, "node_type": node_type,
                  "next_present": following is not None,
                  "next_key": nm_key(following.name) if following is not None else None,
                  "incoming_next_count": incoming.get(id(node), 0), "early_python": early_python}
        blob, key_blob = nm_json(record), nm_json(encoded)
        if filename not in groups:
            if len(groups) >= NAMEMAP_LIMITS["files"]:
                raise ValueError("namemap_files_limit")
            # bytearray 只留摘要原字节，避免为几十万节点保留整条 JSON 和大量 bytes 对象。
            groups[filename] = {"records": bytearray(), "keys": bytearray(), "non_early": bytearray(), "details": []}
        group = groups[filename]
        digest = hashlib.sha256(blob).digest()
        group["records"].extend(digest)
        group["keys"].extend(hashlib.sha256(key_blob).digest())
        if early_python is None:
            group["non_early"].extend(digest)
        if filename in selected:
            detail_count += 1
            detail_bytes += len(blob) + 1
            if detail_count > NAMEMAP_LIMITS["detail_entries"] or detail_bytes > NAMEMAP_LIMITS["total_bytes"]:
                raise ValueError("namemap_detail_entries_or_bytes_limit")
            group["details"].append((key_blob, blob))
    os.mkdir(directory, 0o700)
    files, written_total, ordinal = [], 0, 0

    def write_part(name, content):
        nonlocal written_total
        written_total += len(content)
        limit = NAMEMAP_LIMITS["index_bytes"] if name == "index.json" else NAMEMAP_LIMITS["part_bytes"]
        if len(content) > limit or written_total > NAMEMAP_LIMITS["total_bytes"]:
            raise ValueError("namemap_part_or_total_bytes_limit")
        fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        return {"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    for filename in sorted(groups):
        group = groups[filename]
        rows = sorted(group["details"], key=lambda item: item[0])
        header = b'{"filename":' + nm_json(filename) + b',"protocol":2,"entries":['
        parts, current, current_size = [], [], len(header) + 2
        def flush():
            nonlocal current, current_size, ordinal
            if not current:
                return
            part = write_part("%06d.json" % ordinal, header + b",".join(current) + b"]}")
            part["count"] = len(current)
            parts.append(part)
            ordinal += 1
            current, current_size = [], len(header) + 2
        for _key, blob in rows:
            if len(header) + len(blob) + 2 > NAMEMAP_LIMITS["part_bytes"]:
                raise ValueError("namemap_single_entry_part_limit")
            if current_size + len(blob) + (1 if current else 0) > NAMEMAP_LIMITS["part_bytes"]:
                flush()
            current_size += len(blob) + (1 if current else 0)
            current.append(blob)
        flush()
        files.append({"filename": filename, "count": len(group["records"]) // 32,
                      "sha256": nm_digest(group["records"]), "key_sha256": nm_digest(group["keys"]),
                      "non_early_count": len(group["non_early"]) // 32,
                      "non_early_sha256": nm_digest(group["non_early"]),
                      "detail_reason": ("early_python_file" if len(group["non_early"]) < len(group["records"])
                                        else "explicit_file" if filename in selected else None),
                      "detail_count": len(rows), "parts": parts})
    index = {"protocol": 2, "encoding": "typed-scalar-tuple-v1", "aggregation": NAMEMAP_ALGORITHM,
             "count": len(namemap), "file_count": len(files), "detail_count": detail_count, "files": files,
             "capture_epoch": [started_epoch, finished_epoch], "limits": NAMEMAP_LIMITS,
             "edge_scan": {"source": "all_stmts", "list_count": len(all_stmts),
                           "unique_node_count": len(unique_ids), "edge_count": sum(incoming.values())}}
    index.update(nm_totals(files))
    index_file = write_part("index.json", nm_json(index))
    return {key: index[key] for key in NAMEMAP_FIELDS} | {"index": index_file}

'''

_namemap_helpers = {}
exec(NAMEMAP_HELPERS, _namemap_helpers)
NAMEMAP_LIMITS = _namemap_helpers["NAMEMAP_LIMITS"]
nm_json = _namemap_helpers["nm_json"]

# 原厂入口不改字节。此 RPE 只增加实验哨兵和尾部偏好操作；不接管 Steam 初始化。
RPE = NAMEMAP_HELPERS + r'''
import copy, hashlib, json, math, os, platform, sys, time
import renpy

statement_capture_started_epoch = time.time()
phase = os.environ["SN_REAL_PHASE"]
savedir = os.path.realpath(os.environ["SN_REAL_SAVE"])
autorun = os.path.realpath(os.environ["SN_REAL_AUTORUN"])
receipt = os.environ["SN_REAL_RECEIPT"]
events = []
locations = []
deferrals = 0
attempts = 0
report = {"protocol": 1, "phase": phase, "pid": os.getpid(), "ok": False,
          "version": renpy.version_only, "architecture": platform.machine(),
          "saving_verified": False}

def record(event):
    if len(events) >= 64:
        raise SystemExit(79)
    events.append(event)
    # 真实 init 在尾部之前失败时也保留最后到达的阶段；不读取存档正文。
    with open(os.environ["SN_REAL_EVENTS"], "a") as stream:
        stream.write(json.dumps({"event": event, "pid": os.getpid()}) + "\n")

def finish(error=None):
    if error:
        report["error"] = error
        report["ok"] = False
    report.update(events=events, file_locations_seen=locations,
                  interface_attempts=attempts, deferrals=deferrals)
    with open(receipt, "x") as stream:
        json.dump(report, stream, sort_keys=True)

def stop(error):
    finish(error)
    raise SystemExit(79)

record("rpe_loaded")
if (phase not in ("seed", "precheck") or renpy.game.args.command != "sn_real_postinit"
        or os.environ.get("SDL_VIDEODRIVER") != "dummy"
        or os.environ.get("SDL_AUDIODRIVER") != "dummy"
        or "RENPY_NO_STEAM" in os.environ):
    stop("entry_or_dummy_driver_mismatch")
if renpy.game.interface is not None:
    stop("interface_exists_before_rpe")
if (os.path.realpath(renpy.config.renpy_base) != autorun
        or os.path.realpath(renpy.config.commondir) != autorun + "/renpy/common"
        or os.path.realpath(renpy.game.args.savedir) != savedir
        or not renpy.macapp):
    stop("original_entry_or_savedir_mismatch")
for module in (renpy, renpy.bootstrap, renpy.main, renpy.script):
    if not os.path.realpath(module.__file__).startswith(autorun + "/renpy/"):
        stop("python_engine_not_from_original")
if renpy.config.extra_savedirs:
    stop("extra_savedirs_before_script")

def reject_interface(self, *args, **kwargs):
    global attempts
    attempts += 1
    stop("interface_constructor_attempted")

renpy.display.core.Interface.__init__ = reject_interface
original_location_init = renpy.savelocation.FileLocation.__init__
allowed = {savedir, savedir + "/sync"}
def guarded_location_init(self, directory):
    path = os.path.realpath(directory)
    if path not in allowed or len(locations) >= 16:
        stop("unexpected_save_location_before_write")
    locations.append(path)
    original_location_init(self, directory)
renpy.savelocation.FileLocation.__init__ = guarded_location_init
renpy.arguments.register_command("sn_real_postinit", lambda: False, uses_display=False)
record("guards_installed_before_script")

# 原 infra/logging.rpy 用官方 config.logdir 拼 gundog.log；保留其完整 basicConfig。
# 只移动日志目录，不接管 logging/FileHandler，也不允许原包目录写入。
logdir = os.path.realpath(os.environ["SN_REAL_LOGDIR"])
expected_logdir = os.path.dirname(savedir) + "/logs/real-init/" + phase + "/game-logs"
if (os.path.realpath(renpy.config.logdir) != autorun or logdir != expected_logdir
        or logdir != os.environ["SN_REAL_LOGDIR"] or not os.path.isdir(logdir)
        or os.path.dirname(receipt) != os.path.dirname(logdir)):
    stop("original_or_external_logdir_mismatch")
renpy.config.logdir = logdir
record("logdir_redirected_before_script")

def normalize(value, depth=0, path="preferences", budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > 4096:
        raise ValueError("preference_total_item_limit:" + path)
    if depth > 16:
        raise ValueError("preference_depth_limit:" + path)
    kind = type(value)
    if value is None or kind in (str, bool, int):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite_preference:" + path)
        return value
    # 固定 8.1.3 的实际类型引用，不能凭类名或 isinstance 放行任意用户子类。
    labels = {dict: "dict", list: "list", tuple: "tuple", set: "set", frozenset: "frozenset",
        renpy.revertable.RevertableDict: "renpy.revertable.RevertableDict",
        renpy.revertable.RevertableList: "renpy.revertable.RevertableList",
        renpy.revertable.RevertableSet: "renpy.revertable.RevertableSet"}
    if kind not in labels:
        raise ValueError("unsupported_preference_type:" + path + ":" + kind.__module__ + "." + kind.__qualname__)
    if len(value) > 4096:
        raise ValueError("preference_item_limit:" + path)
    if kind in (dict, renpy.revertable.RevertableDict):
        items = [[type(key).__module__ + "." + type(key).__qualname__,
                  normalize(key, depth + 1, path + "[key]", budget),
                  normalize(item, depth + 1, path + "[value]", budget)] for key, item in dict.items(value)]
        items.sort(key=lambda item: json.dumps(item[:2], sort_keys=True, allow_nan=False))
    else:
        items = [normalize(item, depth + 1, path + "[element]", budget) for item in value]
        if kind in (set, frozenset, renpy.revertable.RevertableSet):
            items.sort(key=lambda item: json.dumps(item, sort_keys=True, allow_nan=False))
    # 普通容器也编码类型，避免同名普通 dict 内容碰撞 Revertable 容器的表示。
    return {"type": labels[kind], "items": items}

def preferences():
    values = vars(renpy.game.preferences)
    if len(values) > 4096 or any(type(key) is not str for key in values):
        raise ValueError("preference_fields_invalid")
    budget = [0]
    result = {key: normalize(value, path="preferences." + key, budget=budget) for key, value in values.items()}
    if len(json.dumps(result, allow_nan=False).encode()) > 128 * 1024:
        raise ValueError("preference_bytes_limit")
    return result

def finalize():
    global deferrals
    if renpy.game.post_init[-1] is not finalizer:
        deferrals += 1
        if deferrals > 8:
            stop("post_init_did_not_settle")
        record("defer_until_original_callbacks_finish")
        renpy.game.post_init.append(finalizer)
        return
    record("original_post_init_finished")
    if (renpy.game.interface is not None or renpy.display.core.Interface.__init__ is not reject_interface
            or renpy.savelocation.FileLocation.__init__ is not guarded_location_init):
        stop("interface_or_guard_changed")
    if renpy.config.logdir != logdir:
        stop("logdir_changed_after_original_init")
    active_objects = renpy.loadsave.location.active_locations()
    all_objects = renpy.loadsave.location.locations
    def describe(item):
        return {"type": type(item).__module__ + "." + type(item).__qualname__,
                "directory": getattr(item, "directory", None)}
    report["save_layout"] = {"extra": list(renpy.config.extra_savedirs),
        "active": [describe(item) for item in active_objects], "all": [describe(item) for item in all_objects],
        "config_savedir": renpy.config.savedir}
    if any(type(item) is not renpy.savelocation.FileLocation for item in [*active_objects, *all_objects]):
        stop("unexpected_save_location_type")
    extra = [os.path.realpath(path) for path in renpy.config.extra_savedirs]
    active = [os.path.realpath(item.directory) for item in active_objects]
    all_locations = [os.path.realpath(item.directory) for item in all_objects]
    # 原 options.rpyc 的 Steam 分支自己清空 extra_savedirs；不能套用 SDK 样例的 sync 布局。
    if (extra != [] or active != [savedir] or all_locations != [savedir]
            or os.path.realpath(renpy.config.savedir) != savedir):
        stop("save_locations_changed")
    if not renpy.game.persistent._set_preferences:
        stop("original_defaults_not_initialized")
    achievement = sys.modules.get("store.achievement")
    steam = getattr(achievement, "steam", None)
    api = getattr(achievement, "steamapi", None)
    report["steam_initialized"] = steam is not None and api is not None
    if renpy.config.enable_steam is not True or not report["steam_initialized"]:
        stop("original_steam_initialization_failed")
    store = sys.modules.get("store")
    report["original_steam_layout"] = {"steam_build": getattr(store, "steam_build", None),
        "has_sync": getattr(renpy.config, "has_sync", None),
        "token_dir": getattr(renpy.savetoken, "token_dir", None)}
    if report["original_steam_layout"]["steam_build"] is not True:
        stop("original_steam_build_not_true")
    # 只查询原厂已初始化接口的 app/账号身份，不添加成就、票据或其他调用。
    appid = int(api.SteamUtils().GetAppID())
    steamid = str(int(api.SteamUser().GetSteamID()))
    report.update(appid=str(appid), steamid64=steamid)
    if appid != 2118420 or steamid != os.environ["SN_REAL_STEAMID"]:
        stop("live_steam_identity_mismatch")
    before = preferences()
    report.update(before=before, extra_savedirs=extra, active_save_locations=active,
                  savedir=savedir, interface_absent=True, preferences_initialized=True,
                  logdir=logdir, original_game_log_present=os.path.isfile(logdir + "/gundog.log"),
                  script_digest=renpy.game.script.digest.hexdigest(),
                  statement_names_sha256=hashlib.sha256(json.dumps(sorted(map(repr, renpy.game.script.namemap)),
                                                                  sort_keys=True).encode()).hexdigest())
    report["statement_identity_input"] = {
        "namemap_type": type(renpy.game.script.namemap).__module__ + "." + type(renpy.game.script.namemap).__qualname__,
        "namemap_count": len(renpy.game.script.namemap),
        "all_stmts_type": type(renpy.game.script.all_stmts).__module__ + "." + type(renpy.game.script.all_stmts).__qualname__,
        "all_stmts_count": len(renpy.game.script.all_stmts)}
    report["statement_identity"] = capture_statement_identity(renpy.game.script.namemap,
        os.path.join(os.path.dirname(receipt), "statement-identities"),
        statement_capture_started_epoch, time.time(), renpy.game.script.all_stmts)
    if type(before.get("fullscreen")) is not bool:
        stop("fullscreen_not_boolean")
    if phase == "seed":
        renpy.game.preferences.fullscreen = False
        after = preferences()
        restored = copy.deepcopy(after)
        restored["fullscreen"] = before["fullscreen"]
        if json.dumps(restored, sort_keys=True, allow_nan=False) != json.dumps(before, sort_keys=True, allow_nan=False):
            stop("changed_more_than_fullscreen")
        renpy.exports.save_persistent()
        report["save_call_returned"] = True
        report["persistent_file_present"] = os.path.isfile(savedir + "/persistent")
        if not report["persistent_file_present"]:
            stop("save_returned_without_persistent")
    elif before["fullscreen"] is not False:
        stop("fullscreen_not_restored")
    report["after"] = preferences()
    report["ok"] = True
    finish()
    raise renpy.game.QuitException(status=0)

def finalizer():
    try:
        finalize()
    except renpy.game.QuitException:
        raise
    except Exception as error:
        stop("post_init_error:" + repr(error)[:400])

def queue_finalizer():
    record("original_init_finished")
    renpy.game.post_init.append(finalizer)
renpy.game.post_init.append(queue_finalizer)
'''

CF_IDENTITY = r'''
def cf_identity():
    import ctypes
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFBundleGetMainBundle.restype = ctypes.c_void_p
    cf.CFBundleGetIdentifier.argtypes = [ctypes.c_void_p]
    cf.CFBundleGetIdentifier.restype = ctypes.c_void_p
    cf.CFBundleCopyBundleURL.argtypes = [ctypes.c_void_p]
    cf.CFBundleCopyBundleURL.restype = ctypes.c_void_p
    cf.CFURLCopyFileSystemPath.argtypes = [ctypes.c_void_p, ctypes.c_int]
    cf.CFURLCopyFileSystemPath.restype = ctypes.c_void_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    def string(pointer):
        if not pointer:
            return None
        value = ctypes.create_string_buffer(4096)
        if not cf.CFStringGetCString(pointer, value, len(value), 0x08000100):
            raise ValueError("CFString conversion failed")
        return value.value.decode("utf-8")
    bundle = cf.CFBundleGetMainBundle()
    if not bundle:
        return {"identifier": None, "bundle_path": None, "main_bundle_present": False}
    url = cf.CFBundleCopyBundleURL(bundle)
    path = cf.CFURLCopyFileSystemPath(url, 0) if url else None
    try:
        return {"identifier": string(cf.CFBundleGetIdentifier(bundle)), "bundle_path": string(path),
                "main_bundle_present": True}
    finally:
        if path: cf.CFRelease(path)
        if url: cf.CFRelease(url)
'''

ORIGIN_PROBE = CF_IDENTITY + r'''
import importlib.util,json,platform,sys
print(json.dumps({"architecture":platform.machine(),"executable":sys.executable,
                  "path":sys.path,"renpy_absent":importlib.util.find_spec("renpy") is None,
                  "core_foundation": cf_identity()}))
'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sources():
    return {name: sha(ROOT / name) for name in SOURCES}


def write_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    Path(path).chmod(0o600)


def json_command(args):
    result = subprocess.run(args, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=35)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("检查响应过大")
    value = json.loads(result.stdout) if result.stdout else None
    if result.returncode or not isinstance(value, dict):
        raise ValueError("检查失败：" + " ".join(map(str, args[:3])) + ": " + result.stderr[-500:])
    return value


def authorization():
    gate = json_command([sys.executable, str(ROOT / "lib/risk.py"), "--gate-json", APPID])
    license_ = json_command([sys.executable, str(ROOT / "lib/library_discovery.py"), "--license", APPID])
    account = license_.get("account") or {}
    sid, aid = account.get("steamid64"), account.get("account_id")
    if (gate.get("protocol") != 1 or gate.get("code") != 0 or str(gate.get("appid")) != APPID
            or license_.get("protocol") != 2 or license_.get("appid") != APPID
            or license_.get("status") != "available" or license_.get("allowed") is not True
            or not isinstance(sid, str) or not sid.isdecimal()
            or not isinstance(aid, str) or not aid.isdecimal()
            or not 0 < int(aid) <= 0xffffffff or int(sid) != 76561197960265728 + int(aid)):
        raise ValueError("当前风险、可用许可或账号身份不满足初始化条件")
    return {"gate": gate, "license": license_, "steamid64": sid}


def verify_delivery(game):
    result = json_command(["node", str(ROOT / "lib/steam-download.mjs"), "--verify-delivery", APPID, str(game)])
    if result.get("complete") is not True or result.get("scope") != "base_game" or result.get("appid") != APPID:
        raise ValueError("原包没有完整基础内容回执")
    return result


def plain_tree(root):
    if not stat.S_ISDIR(Path(root).lstat().st_mode):
        raise ValueError("输入根不是普通目录")
    items = BASE.inventory(root)
    if not items or any(item[0] == "link" for item in items.values()):
        raise ValueError("真实输入或派生视图不接受软链")
    return items


def directory_identity(path):
    path = Path(path)
    data = path.lstat()
    if not stat.S_ISDIR(data.st_mode) or path.resolve(strict=True) != path:
        raise ValueError("准备目录不能经软链重新解释")
    return [data.st_dev, data.st_ino]


def preference_files(root):
    """只读文件摘要；不 flush cfprefsd，不声称覆盖尚未落盘或其他服务状态。"""
    root = Path(root)
    result = {}
    for directory in (root, root / "ByHost"):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.plist")):
            relative = path.relative_to(root).as_posix()
            try:
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode):
                    result[relative] = {"bytes": metadata.st_size, "sha256": sha(path)}
                else:
                    result[relative] = {"skipped": "non_regular"}
            except OSError as error:
                # TCC 保护的无关域只记录不可读，不能索取权限或宣称已验其字节。
                result[relative] = {"unreadable_errno": error.errno}
    return result


def assert_state(prepared, prepared_file):
    state = Path(prepared["state"])
    if state != prepared_file.parent / "state":
        raise ValueError("准备材料不属于相邻的独立 state")
    expected = {"runtime": state / "Runtime", "rpe": state / "extensions",
                "autorun": state / "Original.app/Contents/Resources/autorun"}
    if any(Path(prepared[key]) != path for key, path in expected.items()):
        raise ValueError("准备材料运行路径不符")
    for path, identity in prepared["directory_identities"].items():
        if directory_identity(path) != identity:
            raise ValueError("准备目录身份变化：" + path)
    # 两次进程间允许自有保存文件增加，但不接受其变成通向旧数据的链接。
    for name in ("saves", "home", "tmp", "renpy", "multipersistent", "logs"):
        items = BASE.inventory(state / name)
        if any(item[0] == "link" for item in items.values()):
            raise ValueError("本次可写目录出现软链：" + name)


def environment(state, rpe):
    # 保留本次启动的 locale，不用固定英文环境改变原游戏的默认语言选择。
    locale = {key: os.environ[key] for key in ("LANG", "LC_ALL", "LC_CTYPE") if key in os.environ}
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **locale,
            "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
            "PYTHONDONTWRITEBYTECODE": "1", "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "RENPY_PATH_TO_SAVES": str(state / "renpy"), "RENPY_MULTIPERSISTENT": str(state / "multipersistent"),
            "RENPY_SEARCHPATH": str(rpe), "RENPY_LOG_TO_STDOUT": "1"}


def validate_origin(data, runtime, origin_script):
    binary = runtime / "lib/py3-mac-universal/pythonw"
    paths = data.get("path", [])
    # pythonw 执行文件与 -c 的首项不同；只允许本次自有脚本所在目录这一项。
    if (data.get("renpy_absent") is not True or data.get("architecture") != "arm64"
            or data.get("executable") != str(binary) or not isinstance(paths, list) or len(paths) < 2
            or paths[0] != str(origin_script.parent)
            or any(not str(Path(path).resolve()).startswith(str(runtime / "lib") + "/") for path in paths[1:])):
        raise ValueError("原厂 Python 搜索路径不符合已审入口，未启动游戏")


def assert_integrity(prepared):
    for label, item in prepared["trees"].items():
        if plain_tree(Path(item["path"])) != item["inventory"]:
            raise ValueError("输入或派生材料已变化：" + label)
    if sources() != prepared["sources"]:
        raise ValueError("准备后的运行代码已变化，请保留旧试验并重新准备")


def prepare(materials, game, output):
    if sys.platform != "darwin":
        raise ValueError("仅 macOS")
    materials, game = Path(materials).resolve(strict=True), Path(game).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError("输出已存在，保留：" + str(output))
    output = output.parent.resolve(strict=True) / output.name
    if any(output == root or root in output.parents or output in root.parents for root in (ROOT, materials, game)):
        raise ValueError("输出不能与代码或原始材料重叠")
    delivery = verify_delivery(game)
    auth = authorization()
    sdk, steam, archives = BASE.verify_materials(materials)
    game_inventory = plain_tree(game)
    if ((game / "gundog.py").read_bytes() != (sdk / "renpy.py").read_bytes()
            or (game / "renpy/vc_version.py").read_bytes() != (sdk / "renpy/vc_version.py").read_bytes()
            or any(Path(name).name == "environment.txt" or name.lower().endswith(".rpe") for name in game_inventory)):
        raise ValueError("不是已核对的原始入口，或含未核对的早期扩展")
    output.mkdir(mode=0o700)
    state = output / "state"
    state.mkdir(mode=0o700)
    for name in ("home", "tmp", "saves", "renpy", "multipersistent", "logs", "guarded"):
        (state / name).mkdir()
    runtime = state / "Runtime"
    BASE.prepare_view(sdk, steam, runtime)
    binary_dir = runtime / "lib/py3-mac-universal"
    # Valve SteamAPI_Init 官方文档明确从 cwd 读取；与原 Ren'Py 的可执行目录约定一致。
    (binary_dir / "steam_appid.txt").write_text(APPID + "\n")
    autorun = state / "Original.app/Contents/Resources/autorun"
    autorun.parent.mkdir(parents=True)
    subprocess.run(["/bin/cp", "-cR", str(game), str(autorun)], check=True, timeout=30)
    if plain_tree(autorun) != game_inventory:
        raise ValueError("完整原包副本字节不一致")
    rpe_dir = state / "extensions"
    rpe_dir.mkdir()
    with zipfile.ZipFile(rpe_dir / "sn-real-postinit.rpe", "x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("autorun.py", RPE)
    origin_script = state / "guarded/origin.py"
    origin_script.write_text(ORIGIN_PROBE)
    (state / "guarded/canary").write_bytes(b"owned protected data")
    roots = {"original": game, "copy": autorun, "sdk": sdk, "steam": steam,
             "runtime": runtime, "rpe": rpe_dir, "guards": state / "guarded"}
    prepared = {"protocol": 1, "kind": "gun_dog_realinit_prepared", "appid": APPID,
                "prepared_at_utc": datetime.now(timezone.utc).isoformat(), "sources": sources(),
                "state": str(state), "runtime": str(runtime), "autorun": str(autorun),
                "rpe": str(rpe_dir), "steam_appid_source": STEAM_SOURCE, "authorization": auth,
                "delivery": delivery, "official_archives": archives, "game_started": False,
                "trees": {label: {"path": str(path), "inventory": plain_tree(path)} for label, path in roots.items()}}
    fixed_dirs = {output, state, *state.iterdir(), autorun, *autorun.parents}
    prepared["directory_identities"] = {str(path): directory_identity(path) for path in sorted(fixed_dirs)}
    # 在执行前保存实际源字节，后续修复不只剩旧 SHA 或靠记忆重建。
    for name in (*SOURCES, "tests/test_renpy_realinit.py"):
        snapshot = output / "source-snapshot" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        content = (ROOT / name).read_bytes()
        if name in prepared["sources"] and hashlib.sha256(content).hexdigest() != prepared["sources"][name]:
            raise ValueError("源文件在准备期间变化")
        with snapshot.open("xb") as stream:
            stream.write(content)
        snapshot.chmod(0o600)
    env = environment(state, rpe_dir)
    policy = BASE.sandbox(state, list(roots.values()))
    logdir = state / "logs/prepare"
    logdir.mkdir()
    # 写闸在任何引擎执行前用自有 canary 验证；不向真实原文件写探针内容。
    canary = POST.run_owned(policy + [sys.executable, "-I", "-S", "-B", "-c",
        'import errno,json,os,sys\nopen(sys.argv[1],"xb").write(b"allowed")\ntry:\n open(sys.argv[2],"wb").write(b"wrong")\nexcept OSError as e:\n print(json.dumps({"denied":e.errno==errno.EPERM}));sys.exit(0 if e.errno==errno.EPERM else 2)\nsys.exit(3)',
        str(logdir / "allowed"), str(state / "guarded/canary")], env=env, cwd=state,
        logs=logdir, name="write-preflight", timeout=10)
    if canary["returncode"] or canary["timed_out"] or json.loads(canary["stdout"]).get("denied") is not True:
        raise ValueError("写闸预检失败，未启动游戏")
    prepared["write_preflight"] = canary
    prepared["signatures"] = []
    for name in ("pythonw", "librenpython.dylib", "libsteam_api.dylib"):
        checked = BASE.run_bounded(["/usr/bin/codesign", "--verify", "--strict", "--arch", "arm64", str(binary_dir / name)], timeout=10)
        if checked["returncode"] or checked["timed_out"]:
            raise ValueError("原厂 arm64 签名失败：" + name)
        prepared["signatures"].append(checked)
    origin = POST.run_owned(policy + ["/usr/bin/arch", "-arm64", str(binary_dir / "pythonw"), str(origin_script)],
                            env=env, cwd=binary_dir, logs=logdir, name="stdlib-origin", timeout=15)
    data = json.loads(origin["stdout"])
    if origin["returncode"] or origin["timed_out"]:
        raise ValueError("原厂 Python 自有脚本未完成，未启动游戏")
    validate_origin(data, runtime, origin_script)
    prepared["stdlib_origin"] = origin
    prepared["main_bundle_identity"] = data["core_foundation"]
    # 实际 pythonw 没有 .app/Info.plist 时记录缺失，不能套用 SDK 启动器的域名。
    bundle_path = data["core_foundation"].get("bundle_path")
    info = Path(bundle_path) / "Contents/Info.plist" if bundle_path else None
    prepared["main_bundle_info_plist"] = ({"path": str(info), "sha256": sha(info),
        "identifier": plistlib.loads(info.read_bytes()).get("CFBundleIdentifier")}
        if info and info.is_file() else None)
    prepared["host_preference_root"] = str(Path.home() / "Library/Preferences")
    prepared["host_preferences_after_stdlib_preflight"] = preference_files(prepared["host_preference_root"])
    assert_integrity(prepared)
    assert_state(prepared, output / "prepared.json")
    write_json(output / "prepared.json", prepared)
    return {"prepared": str(output / "prepared.json"), "appid": APPID, "game_started": False}


def validate_run(run, phase, prepared):
    data = run.get("receipt") or {}
    if (run["returncode"] or run["timed_out"] or data.get("ok") is not True
            or run.get("pid_reaped") is not True or run.get("output_limit_reached") is not False
            or data.get("pid") != run["pid"] or data.get("phase") != phase
            or data.get("version") != BASE.VERSION or data.get("architecture") != "arm64"
            or data.get("interface_attempts") != 0 or data.get("interface_absent") is not True
            or data.get("steam_initialized") is not True or data.get("appid") != APPID
            or data.get("steamid64") != prepared["authorization"]["steamid64"]
            or data.get("logdir") != str(Path(prepared["state"]) / "logs/real-init" / phase / "game-logs")
            or data.get("original_game_log_present") is not True
            or data.get("active_save_locations") != [str(Path(prepared["state"]) / "saves")]):
        raise ValueError("真实初始化未通过：" + phase + ": " + str(data.get("error", "见本次原始日志")))
    if phase == "seed" and (data.get("save_call_returned") is not True or data.get("persistent_file_present") is not True):
        raise ValueError("原厂保存结果缺失")
    return data


def compare_readback(seed, precheck):
    expected = dict(seed["before"], fullscreen=False)
    canonical = json.dumps(expected, sort_keys=True, allow_nan=False)
    if any(json.dumps(value, sort_keys=True, allow_nan=False) != canonical
           for value in (seed["after"], precheck["before"], precheck["after"])):
        raise ValueError("新进程未完整恢复仅 fullscreen 变化的偏好")
    if any(seed[key] != precheck[key] for key in ("script_digest", "statement_names_sha256")):
        raise ValueError("原始编译脚本身份在两次进程间变化")


def read_statement_identity(directory, receipt):
    """只读取本次回执明列的有界 JSON 分片；不执行或重建任何 Node。"""
    directory = Path(directory)
    if directory.resolve(strict=True) != directory or not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError("namemap 回执目录不是固定普通目录")
    seen, total_bytes = set(), 0

    def read_part(meta):
        nonlocal total_bytes
        name = meta.get("path", "")
        if name != "index.json" and not re.fullmatch(r"[0-9]{6}[.]json", name):
            raise ValueError("namemap 分片路径无效")
        if name in seen:
            raise ValueError("namemap 分片重复")
        seen.add(name)
        path = directory / name
        data = path.lstat()
        if (not stat.S_ISREG(data.st_mode) or type(meta.get("bytes")) is not int
                or data.st_size != meta["bytes"]
                or data.st_size > NAMEMAP_LIMITS["index_bytes" if name == "index.json" else "part_bytes"]):
            raise ValueError("namemap 分片类型或大小不符")
        total_bytes += data.st_size
        if total_bytes > NAMEMAP_LIMITS["total_bytes"]:
            raise ValueError("namemap 回读超过总量")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != meta.get("sha256"):
            raise ValueError("namemap 分片摘要不符")
        return json.loads(content)

    def decode_key(key, depth=0):
        if depth > NAMEMAP_LIMITS["key_depth"] or type(key) is not dict:
            raise ValueError("namemap key 结构不符")
        kind = key.get("type")
        if kind == "tuple" and type(key.get("items")) is list:
            result = tuple(decode_key(item, depth + 1) for item in key["items"])
        elif kind == "none" and key.get("value") is None:
            result = None
        elif kind == "bool" and type(key.get("value")) is bool:
            result = key["value"]
        elif kind == "str" and type(key.get("value")) is str:
            result = key["value"]
        elif kind == "int" and type(key.get("value")) is str:
            result = int(key["value"])
        elif kind == "float" and type(key.get("value")) is str:
            result = float.fromhex(key["value"])
        else:
            raise ValueError("namemap key 原类型未知")
        if nm_json(_namemap_helpers["nm_key"](result)) != nm_json(key):
            raise ValueError("namemap key 不是类型保真 canonical 表示")
        return result

    index = read_part(receipt["index"])
    fields = _namemap_helpers["NAMEMAP_FIELDS"]
    if (any(index.get(key) != receipt.get(key) for key in fields) or index.get("protocol") != 2
            or index.get("encoding") != "typed-scalar-tuple-v1"
            or index.get("aggregation") != _namemap_helpers["NAMEMAP_ALGORITHM"]
            or index.get("limits") != NAMEMAP_LIMITS
            or type(index.get("files")) is not list or len(index["files"]) > NAMEMAP_LIMITS["files"]):
        raise ValueError("namemap 索引协议或计数不符")
    edge_scan = index.get("edge_scan", {})
    if (set(edge_scan) != {"source", "list_count", "unique_node_count", "edge_count"}
            or edge_scan["source"] != "all_stmts"
            or any(type(edge_scan[key]) is not int or not 0 <= edge_scan[key] <= NAMEMAP_LIMITS["entries"]
                   for key in ("list_count", "unique_node_count", "edge_count"))
            or edge_scan["unique_node_count"] > edge_scan["list_count"]
            or edge_scan["edge_count"] > edge_scan["unique_node_count"]):
        raise ValueError("namemap 入边扫描分母不符")
    by_file, key_bytes, count, detail_count = {}, set(), 0, 0
    filenames = [item.get("filename") for item in index["files"]]
    if (any(type(name) is not str or len(name.encode("utf-8")) > 4096 for name in filenames)
            or filenames != sorted(set(filenames))):
        raise ValueError("namemap 文件重复或次序不符")
    for item in index["files"]:
        filename = item["filename"]
        if (set(item) != set(_namemap_helpers["SUMMARY_FIELDS"]) | {"detail_reason", "detail_count", "parts"}
                or any(type(item.get(key)) is not int or not 0 <= item[key] <= NAMEMAP_LIMITS["entries"]
                       for key in ("count", "non_early_count", "detail_count"))
                or not 0 < item["count"] or item["non_early_count"] > item["count"]
                or any(type(item[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item[key])
                       for key in ("sha256", "key_sha256", "non_early_sha256"))
                or type(item["parts"]) is not list):
            raise ValueError("namemap 文件摘要结构不符")
        detailed = item["detail_reason"] is not None
        has_early = item["non_early_count"] < item["count"]
        if (item["detail_reason"] not in (None, "early_python_file", "explicit_file")
                or has_early != (item["detail_reason"] == "early_python_file")
                or item["detail_count"] != (item["count"] if detailed else 0)
                or bool(item["parts"]) != detailed
                or (not has_early and item["sha256"] != item["non_early_sha256"])):
            raise ValueError("namemap 文件明细范围不符")
        count += item["count"]
        detail_count += item["detail_count"]
        if count > NAMEMAP_LIMITS["entries"] or detail_count > NAMEMAP_LIMITS["detail_entries"]:
            raise ValueError("namemap 回读条数超限")
        rows, records, keys, non_early = [], bytearray(), bytearray(), bytearray()
        previous_key = None
        for meta in item["parts"]:
            part = read_part(meta)
            if part.get("protocol") != 2 or part.get("filename") != filename or type(part.get("entries")) is not list:
                raise ValueError("namemap 分片文件归属不符")
            if len(part["entries"]) != meta.get("count"):
                raise ValueError("namemap 分片条数不符")
            for row in part["entries"]:
                if (set(row) != {"key", "node_type", "filename", "linenumber", "next_present", "next_key",
                                 "incoming_next_count", "early_python"} or row["filename"] != filename
                        or type(row["node_type"]) is not str
                        or type(row["next_present"]) is not bool
                        or type(row["incoming_next_count"]) is not int
                        or not 0 <= row["incoming_next_count"] <= edge_scan["unique_node_count"]
                        or (row["linenumber"] is not None and type(row["linenumber"]) is not int)):
                    raise ValueError("namemap 节点元数据不符")
                decode_key(row["key"])
                if row["next_present"]:
                    decode_key(row["next_key"])
                elif row["next_key"] is not None:
                    raise ValueError("namemap 空 next 携带了 key")
                early = row["early_python"]
                if row["node_type"] == "renpy.ast.EarlyPython":
                    if (type(early) is not dict or set(early) != {"source_type", "source_bytes", "source_sha256", "hide", "store"}
                            or early["source_type"] != "str" or type(early["source_bytes"]) is not int
                            or not 0 <= early["source_bytes"] <= 1024 * 1024
                            or type(early["source_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", early["source_sha256"])
                            or type(early["hide"]) is not bool or type(early["store"]) is not str):
                        raise ValueError("namemap EarlyPython 元数据不符")
                elif early is not None:
                    raise ValueError("namemap 非 EarlyPython 携带了专有字段")
                key = nm_json(row["key"])
                if key in key_bytes or (previous_key is not None and key <= previous_key):
                    raise ValueError("namemap key 重复或次序不符")
                previous_key = key
                key_bytes.add(key)
                digest = hashlib.sha256(nm_json(row)).digest()
                records.extend(digest)
                keys.extend(hashlib.sha256(key).digest())
                if early is None:
                    non_early.extend(digest)
                rows.append(row)
                if len(rows) > item["detail_count"]:
                    raise ValueError("namemap 分片条数超限")
        if detailed:
            if (len(rows) != item["count"] or len(non_early) // 32 != item["non_early_count"]
                    or any(_namemap_helpers["nm_digest"](packed) != item[key] for key, packed in
                           (("sha256", records), ("key_sha256", keys), ("non_early_sha256", non_early)))):
                raise ValueError("namemap 文件条数或摘要不符")
            by_file[filename] = rows
    # 普通文件没有全量 JSON；其摘要由本次 receipt 中 index SHA 绑定，不伪称逐节点回读。
    totals = _namemap_helpers["nm_totals"](index["files"])
    if (count != index["count"] or len(index["files"]) != index["file_count"]
            or detail_count != index["detail_count"] or any(index.get(key) != value for key, value in totals.items())
            or {item.name for item in directory.iterdir()} != seen):
        raise ValueError("namemap 摘要与明细回读不符")
    return {"index": index, "by_file": by_file}


def compare_statement_identities(before, after, legacy_before, legacy_after):
    """只定位变化，不删除时间、路径或重新命名 key。"""
    left, right = before["index"], after["index"]
    same_keys = left["canonical_key_sha256"] == right["canonical_key_sha256"]
    same_records = left["canonical_sha256"] == right["canonical_sha256"]
    same_edges = left["edge_scan"] == right["edge_scan"]
    classification = ("edge_scan_counts_changed" if same_records and not same_edges else
                      "same" if same_records and legacy_before == legacy_after else
                      "legacy_repr_only" if same_records else "node_metadata_only" if same_keys else "key_values_changed")

    def time_signature(row, epoch):
        key = row["key"]
        items = key.get("items", [])
        if (key.get("type") != "tuple" or len(items) != 3
                or [item.get("type") for item in items] != ["str", "int", "int"]):
            return None
        second = int(items[1]["value"])
        if not int(epoch[0]) <= second <= int(epoch[1]):
            return None
        # 只把形状和实测初始化时间窗一致的 pair 标为候选；原值仍全部保留并判不相同。
        return nm_json([items[0], items[2], row["filename"], row["linenumber"], row["node_type"]])

    changes = []
    left_files = {item["filename"]: item for item in left["files"]}
    right_files = {item["filename"]: item for item in right["files"]}
    for filename in sorted(left_files.keys() | right_files.keys()):
        a_summary, b_summary = left_files.get(filename), right_files.get(filename)
        if (a_summary is not None and b_summary is not None
                and all(a_summary[key] == b_summary[key] for key in _namemap_helpers["SUMMARY_FIELDS"])):
            continue
        change = {"filename": filename, "before": a_summary, "after": b_summary,
                  "before_count": a_summary["count"] if a_summary else 0,
                  "after_count": b_summary["count"] if b_summary else 0,
                  "details_available": ((a_summary is None or filename in before["by_file"])
                                        and (b_summary is None or filename in after["by_file"]))}
        if change["details_available"]:
            a = {nm_json(row["key"]): row for row in before["by_file"].get(filename, [])}
            b = {nm_json(row["key"]): row for row in after["by_file"].get(filename, [])}
            removed, added = [a[key] for key in sorted(a.keys() - b.keys())], [b[key] for key in sorted(b.keys() - a.keys())]
            changed_meta = [b[key] for key in sorted(a.keys() & b.keys()) if nm_json(a[key]) != nm_json(b[key])]
            timed_added = {time_signature(row, right["capture_epoch"]): row for row in added
                           if time_signature(row, right["capture_epoch"]) is not None}
            time_pairs = [{"before": row["key"], "after": timed_added[signature]["key"],
                           "node_type": row["node_type"], "linenumber": row["linenumber"]}
                          for row in removed
                          if (signature := time_signature(row, left["capture_epoch"])) in timed_added]
            change.update(removed_count=len(removed), added_count=len(added), changed_node_metadata_count=len(changed_meta),
                          removed_samples=removed[:16], added_samples=added[:16], changed_node_metadata_samples=changed_meta[:16],
                          samples_truncated=any(len(rows) > 16 for rows in (removed, added, changed_meta)),
                          time_name_candidate_count=len(time_pairs), time_name_candidate_samples=time_pairs[:16])
        changes.append(change)
    same_non_early = (left["non_early_count"] == right["non_early_count"]
                      and left["non_early_sha256"] == right["non_early_sha256"])
    return {"classification": classification, "same_canonical_keys": same_keys,
            "same_canonical_records": same_records, "legacy_repr_same": legacy_before == legacy_after,
            "same_non_early_records": same_non_early, "same_edge_scan": same_edges,
            "only_early_python_records_changed": not same_records and same_non_early and same_edges,
            "before_count": left["count"], "after_count": right["count"], "changed_file_count": len(changes),
            "files": changes, "capture_epoch": [left["capture_epoch"], right["capture_epoch"]],
            "edge_scan": [left["edge_scan"], right["edge_scan"]],
            "time_fields_preserved": True,
            "boundary": "摘要覆盖 namemap 全节点元数据与原类型 key；明细仅含 EarlyPython 的文件或显式文件。入边另扫 all_stmts；不证明剧情存档兼容。"}


def run(prepared_file, timeout=30):
    if not 1 <= timeout <= 30:
        raise ValueError("真实进程截止只允许 1–30 秒")
    prepared_file = Path(os.path.abspath(prepared_file))
    if not stat.S_ISREG(prepared_file.lstat().st_mode):
        raise ValueError("准备回执不是普通文件")
    prepared_file = prepared_file.resolve(strict=True)
    prepared = json.loads(prepared_file.read_text())
    if prepared.get("protocol") != 1 or prepared.get("kind") != "gun_dog_realinit_prepared" or prepared.get("appid") != APPID:
        raise ValueError("不是本工具的准备材料")
    assert_integrity(prepared)
    assert_state(prepared, prepared_file)
    state = Path(prepared["state"])
    # run 只执行一次；失败保留原现场，不重用半成品存档。
    run_dir = state / "logs/real-init"
    run_dir.mkdir()
    if list((state / "saves").iterdir()):
        raise ValueError("本次保存目录已非空，保留并停止")
    result = {"protocol": 1, "kind": "real_game_headless_initialization", "appid": APPID,
              "ok": False, "saving_verified": False, "runs": [], "prepared": str(prepared_file),
              "boundary": "仅本游戏初始化与新状态偏好；不是画面、旧存档兼容、持续禁全屏或 Steam 外部副作用隔离。"}
    result["host_preferences_before"] = preference_files(prepared["host_preference_root"])
    identity_snapshots = []
    try:
        for phase in ("seed", "precheck"):
            auth = authorization()
            if auth["steamid64"] != prepared["authorization"]["steamid64"]:
                raise ValueError("当前 Steam 账号变化，未执行下一进程")
            assert_integrity(prepared)
            assert_state(prepared, prepared_file)
            logdir = run_dir / phase
            logdir.mkdir()
            (logdir / "game-logs").mkdir()
            env = environment(state, Path(prepared["rpe"]))
            env.update(SN_REAL_PHASE=phase, SN_REAL_SAVE=str(state / "saves"),
                       SN_REAL_AUTORUN=prepared["autorun"], SN_REAL_RECEIPT=str(logdir / "receipt.json"),
                       SN_REAL_EVENTS=str(logdir / "events.jsonl"), SN_REAL_STEAMID=auth["steamid64"],
                       SN_REAL_LOGDIR=str(logdir / "game-logs"))
            binary_dir = Path(prepared["runtime"]) / "lib/py3-mac-universal"
            args = BASE.sandbox(state, [Path(item["path"]) for item in prepared["trees"].values()]) + [
                "/usr/bin/arch", "-arm64", str(binary_dir / "pythonw"), str(Path(prepared["autorun"]) / "gundog.py"),
                "--savedir", str(state / "saves"), prepared["autorun"], COMMAND]
            item = POST.run_owned(args, env=env, cwd=binary_dir, logs=logdir, name="game", timeout=timeout)
            item["phase"] = phase
            try:
                os.kill(item["pid"], 0)
                item["pid_reaped"] = False
            except ProcessLookupError:
                item["pid_reaped"] = True
            item["output_limit_reached"] = any(Path(item[key]).stat().st_size >= POST.OUTPUT_LIMIT
                                                for key in ("stdout_file", "stderr_file"))
            receipt = logdir / "receipt.json"
            metadata = receipt.lstat() if os.path.lexists(receipt) else None
            item["receipt"] = (json.loads(receipt.read_text()) if metadata and stat.S_ISREG(metadata.st_mode)
                               and metadata.st_size <= 256 * 1024 else None)
            result["runs"].append(item)
            validate_run(item, phase, prepared)
            assert_integrity(prepared)
            identity_snapshots.append(read_statement_identity(logdir / "statement-identities", item["receipt"]["statement_identity"]))
        result["statement_identity_comparison"] = compare_statement_identities(*identity_snapshots,
            *(item["receipt"]["statement_names_sha256"] for item in result["runs"]))
        if not result["statement_identity_comparison"]["same_canonical_keys"]:
            raise ValueError("运行时语句 key 原值跨进程变化，见逐文件身份回执")
        compare_readback(*(item["receipt"] for item in result["runs"]))
        if authorization()["steamid64"] != prepared["authorization"]["steamid64"]:
            raise ValueError("结束时 Steam 账号变化")
        result.update(ok=True, saving_verified=True, verified_scope="本次独立状态中偏好新进程回读")
    except Exception as error:
        result["error"] = str(error)
    finally:
        try:
            assert_integrity(prepared)
            assert_state(prepared, prepared_file)
            result["all_original_and_runtime_trees_unchanged"] = True
        except Exception as error:
            result.update(ok=False, saving_verified=False, all_original_and_runtime_trees_unchanged=False,
                          integrity_error=str(error))
        result["state_inventory"] = BASE.inventory(state / "saves")
        result["host_preferences_after"] = preference_files(prepared["host_preference_root"])
        before, after = result["host_preferences_before"], result["host_preferences_after"]
        result["host_preference_files_changed"] = [key for key in sorted(before.keys() | after.keys())
                                                    if before.get(key) != after.get(key)]
        result["host_preferences_scope"] = "本用户 Preferences/ByHost 的 plist 文件；可能并发变化，不证明 CFPreferences 内存或其他服务无副作用。"
        write_json(prepared_file.parent / "real-init-evidence.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparing = commands.add_parser("prepare", help="核验并准备，不启动真实游戏")
    for name in ("materials", "game", "output"):
        preparing.add_argument("--" + name, required=True)
    running = commands.add_parser("run", help="经操作者授权后执行两次有截止的无窗口初始化")
    running.add_argument("--prepared", required=True)
    running.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    try:
        result = prepare(args.materials, args.game, args.output) if args.command == "prepare" else run(args.prepared, args.timeout)
        print(json.dumps({key: value for key, value in result.items() if key in ("ok", "prepared", "appid", "game_started", "error")}, ensure_ascii=False))
        return 0 if args.command == "prepare" or result.get("ok") else 1
    except Exception as error:
        print("初始化实验未完成：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
