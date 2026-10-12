#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""官方增删快捷方式前备份原始字节，之后只读确认；不代写 Steam 的安装记录。"""
import base64
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import sys
import struct
import stat
import tempfile
import time

from paths import SN_GAMES, STEAM

spec = importlib.util.spec_from_file_location("shortcut_mirror", Path(__file__).with_name("steam-shortcut-mirror.py"))
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


def parse_exact(raw):
    if raw is None:
        return []
    if not raw.startswith(b"\0shortcuts\0"):
        raise ValueError("快捷方式不是可识别的二进制 VDF")
    try:
        entries, offset = mirror.parse_map(raw, len(b"\0shortcuts\0"))
    except (IndexError, struct.error) as error:
        raise ValueError("快捷方式文件尚未完整落盘") from error
    rebuilt = b"\0shortcuts\0" + mirror.dump_map(entries) + b"\x08"
    if offset != len(raw) - 1 or raw != rebuilt:
        raise ValueError("快捷方式字节往返验证失败")
    if any(t != 0 or not key.isdigit() for t, key, _ in entries):
        raise ValueError("快捷方式条目类型无效")
    def unique_keys(fields):
        keys = [key.casefold() for _, key, _ in fields]
        if len(keys) != len(set(keys)):
            raise ValueError("快捷方式存在重复字段或索引")
        for t, _, value in fields:
            if t == 0:
                unique_keys(value)
    unique_keys(entries)
    return entries


def write_backup(receipt, prefix):
    backup_dir = SN_GAMES / "shortcut-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=prefix, suffix=".json", dir=backup_dir, delete=False) as f:
        json.dump(receipt, f)
        backup = Path(f.name)
    if json.loads(backup.read_text()) != receipt:
        raise ValueError("快捷方式备份回读失败")
    return backup


def prepare(exe):
    uid = mirror.active_account()
    if uid is None:
        raise ValueError("无法唯一确认当前自动登录账号，未添加快捷方式")
    override = os.environ.get("SN_STEAM_ACCOUNT", "").strip()
    if override and override != uid:
        raise ValueError("CDP 只服务当前登录账号，指定账号与当前账号不同")
    cfg = Path(STEAM) / "userdata" / uid / "config"
    if not cfg.is_dir():
        raise ValueError("当前账号的 config 目录不存在")
    path = cfg / "shortcuts.vdf"
    raw = path.read_bytes() if path.exists() else None
    parse_exact(raw)
    receipt = dict(protocol=1, steam=str(Path(STEAM).resolve()), uid=uid,
                   exe=str(Path(exe).resolve()), raw=None if raw is None else base64.b64encode(raw).decode("ascii"))
    return write_backup(receipt, "before-add-")


def verify(backup, appid):
    receipt = json.loads(Path(backup).read_text())
    if receipt.get("protocol") != 1 or receipt["steam"] != str(Path(STEAM).resolve()):
        raise ValueError("备份与当前 Steam 目录不一致")
    uid = receipt["uid"]
    if mirror.active_account() != uid:
        raise ValueError("添加期间登录账号发生变化")
    path = Path(STEAM) / "userdata" / uid / "config" / "shortcuts.vdf"
    raw = path.read_bytes()
    entries = parse_exact(raw)
    old = parse_exact(None if receipt["raw"] is None else base64.b64decode(receipt["raw"], validate=True))
    before = Counter(mirror.dump_map(fields) for _, _, fields in old)
    after = Counter(mirror.dump_map(fields) for _, _, fields in entries)
    if before - after:
        raise ValueError("添加后已有快捷方式发生变化，保留备份供核对")
    hits = [f for _, _, f in entries if mirror.field(f, "appid") == appid
            and mirror.field(f, "Exe") in (receipt["exe"], '"' + receipt["exe"] + '"')]
    if len(hits) != 1 or path.read_bytes() != raw:
        raise ValueError("Steam 尚未落盘唯一的对应快捷方式，或文件仍在变化")


MAX_SHORTCUT_BYTES = 16 * 1024 * 1024


def read_remove_snapshot(path):
    if not os.path.lexists(path):
        return None
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SHORTCUT_BYTES:
        raise ValueError("快捷方式文件不是普通文件或超过 16 MiB，未确认删除")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SHORTCUT_BYTES + 1)
    after = path.lstat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if len(raw) > MAX_SHORTCUT_BYTES or identity(before) != identity(after):
        raise ValueError("快捷方式读取期间发生变化，未确认删除")
    return raw


def remove_context():
    steam = Path(STEAM).resolve(strict=True)
    info = steam.stat()
    if not steam.is_dir():
        raise ValueError("Steam 根目录不是普通目录，未确认删除")
    uid = mirror.active_account()
    if uid is None:
        raise ValueError("无法唯一确认当前自动登录账号，未确认删除")
    override = os.environ.get("SN_STEAM_ACCOUNT", "").strip()
    if override and override != uid:
        raise ValueError("CDP 只服务当前登录账号，指定账号与当前账号不同")
    cfg = steam / "userdata" / uid / "config"
    if not cfg.is_dir() or cfg.resolve() != cfg:
        raise ValueError("当前账号的 config 目录不存在或含路径别名，未确认删除")
    return str(steam), [info.st_dev, info.st_ino], uid, cfg / "shortcuts.vdf"


def shortcut_identity(fields):
    values = {key.casefold(): (kind, value) for kind, key, value in fields}
    id_type, appid = values.get("appid", (None, None))
    exe_type, exe = values.get("exe", (None, None))
    if (id_type != 2 or type(appid) is not int or not 0 < appid <= 0xffffffff
            or exe_type != 1 or not isinstance(exe, str) or not exe):
        raise ValueError("快捷方式缺少有效的 ID 或 Exe 字段，未确认删除")
    # 只去除完整包裹 Exe 的双引号；不解析别名、大小写或参数来扩大删除范围。
    return appid, exe[1:-1] if len(exe) >= 2 and exe[0] == exe[-1] == '"' else exe


def remove_targets(entries, exe):
    identities, targets = {}, []
    for _, _, fields in entries:
        appid, path = shortcut_identity(fields)
        if appid in identities and identities[appid] != path:
            raise ValueError("同一快捷方式 ID 对应不同路径，不能按 ID 安全删除")
        identities[appid] = path
        if path == exe and appid not in targets:
            targets.append(appid)
    return targets


def validate_remove_receipt(receipt):
    if (not isinstance(receipt, dict) or type(receipt.get("protocol")) is not int
            or receipt["protocol"] != 1 or receipt.get("action") != "remove"):
        raise ValueError("删除备份的版本或 action 不符")
    for key in ("steam", "exe"):
        value = receipt.get(key)
        if (not isinstance(value, str) or not Path(value).is_absolute()
                or os.path.normpath(value) != value or any(char in value for char in "\0\r\n")):
            raise ValueError("删除备份的路径无效：" + key)
    uid, identity, ids = receipt.get("uid"), receipt.get("steam_identity"), receipt.get("ids")
    if (not isinstance(uid, str) or not uid.isascii() or not uid.isdigit() or not 0 <= int(uid) <= 0xffffffff
            or not isinstance(identity, list) or len(identity) != 2
            or any(type(value) is not int or value < 0 for value in identity)
            or not isinstance(ids, list) or any(type(value) is not int or not 0 < value <= 0xffffffff for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("删除备份的账号、Steam 身份或目标 IDs 无效")
    encoded = receipt.get("raw", False)
    if encoded is not None and (not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_SHORTCUT_BYTES + 2) // 3)):
        raise ValueError("删除备份缺少有效的原始字节")
    old = parse_exact(None if encoded is None else base64.b64decode(encoded, validate=True))
    if remove_targets(old, receipt["exe"]) != ids:
        raise ValueError("删除备份的目标 IDs 与原始快捷方式不符")
    return old


def validate_remove_snapshot(receipt, raw):
    """纯快照核对；调用方另核当前账号/Steam 根。只允许原目标消失，索引可由 Steam 重排。"""
    old = validate_remove_receipt(receipt)
    if raw is not None and (not isinstance(raw, bytes) or len(raw) > MAX_SHORTCUT_BYTES):
        raise ValueError("删除后的快捷方式快照无效或过大")
    current = parse_exact(raw)
    targets = remove_targets(current, receipt["exe"])
    if targets or any(shortcut_identity(fields)[0] in receipt["ids"] for _, _, fields in current):
        raise ValueError("目标快捷方式仍存在，或原 ID 已指向其他条目，未确认删除")
    before = Counter(mirror.dump_map(fields) for _, _, fields in old
                     if shortcut_identity(fields)[1] != receipt["exe"])
    after = Counter(mirror.dump_map(fields) for _, _, fields in current)
    if before != after:
        raise ValueError("删除后非目标快捷方式的字段或数量变化，保留备份供核对")


def prepare_remove(exe):
    if not str(exe) or any(char in str(exe) for char in "\0\r\n"):
        raise ValueError("快捷方式目标路径无效")
    steam, identity, uid, path = remove_context()
    target = str(Path(exe).resolve())
    raw = read_remove_snapshot(path)
    entries = parse_exact(raw)
    receipt = dict(protocol=1, action="remove", steam=steam, steam_identity=identity, uid=uid, exe=target,
                   ids=remove_targets(entries, target), raw=None if raw is None else base64.b64encode(raw).decode("ascii"))
    validate_remove_receipt(receipt)
    if remove_context()[:3] != (steam, identity, uid) or read_remove_snapshot(path) != raw:
        raise ValueError("删除准备期间账号、Steam 根或快捷方式变化，未开始删除")
    backup = write_backup(receipt, "before-remove-")
    if remove_context()[:3] != (steam, identity, uid) or read_remove_snapshot(path) != raw:
        raise ValueError(f"备份后账号、Steam 根或快捷方式变化，未开始删除；备份保留：{backup}")
    return backup


def verify_remove(backup):
    backup = Path(backup)
    if backup.is_symlink() or not backup.is_file() or backup.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("删除备份不是普通文件或超过大小限制")
    receipt = json.loads(backup.read_text())
    validate_remove_receipt(receipt)
    steam, identity, uid, path = remove_context()
    if (steam, identity, uid) != (receipt["steam"], receipt["steam_identity"], receipt["uid"]):
        raise ValueError("删除期间登录账号或 Steam 根发生变化")
    raw = read_remove_snapshot(path)
    validate_remove_snapshot(receipt, raw)
    if remove_context()[:3] != (steam, identity, uid) or read_remove_snapshot(path) != raw:
        raise ValueError("删除核对期间账号、Steam 根或快捷方式仍在变化")


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "prepare":
            print(prepare(sys.argv[2]))
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "prepare-remove":
            print(prepare_remove(sys.argv[2]))
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "verify-remove":
            for attempt in range(11):
                try:
                    verify_remove(sys.argv[2])
                    return 0
                except (OSError, ValueError):
                    if attempt == 10:
                        raise
                    time.sleep(0.5)
        if len(sys.argv) == 4 and sys.argv[1] == "verify":
            appid = int(sys.argv[3])
            if not 0 < appid <= 0xffffffff:
                raise ValueError("快捷方式 appid 无效")
            # Steam 的官方写入可能稍后落盘；只等待，不用手工 VDF 写入补救。
            for attempt in range(11):
                try:
                    verify(sys.argv[2], appid)
                    return 0
                except (OSError, ValueError):
                    if attempt == 10:
                        raise
                    time.sleep(0.5)
        raise ValueError("用法: steam-shortcut-check.py prepare <app> | verify <备份> <appid> | prepare-remove <app> | verify-remove <备份>")
    except Exception as error:
        print(f"快捷方式检查失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
