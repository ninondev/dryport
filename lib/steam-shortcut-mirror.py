#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""维护非 Steam 快捷方式的 shortcuts.vdf。

**只动一个账号**: 默认是 Steam 当前设为自动登录的那个 (loginusers.vdf 里 AutoLogin="1",
registry.vdf 的 AutoLoginUser 作旁证)。你在用哪个账号, 工具就服务哪个账号; 多账号的机器上
不该擅自往你没在用的账号写东西。这是尊重意图, 不是哪个账号更安全: 任何合法拥有游戏的
账号用这套工具都一样正当。要写别的账号显式 --account 就行。

Steam 的 CDP AddShortcut 本来就只写当前登录的账号, 这个脚本补的是 CDP 没覆盖到的场景
(改名、去重、删除, 以及显式指定账号时的镜像)。写之前先备份, 写完重新解析核对。
没登录的账号 Steam 不会回写它的文件, 这时候改是安全的; 当前登录的账号 Steam 可能随时
回写, 改它之前最好先退出 Steam。

用法:
  steam-shortcut-mirror.py <appid> <名字> <.app 路径> [图标路径]   写入目标账号
  steam-shortcut-mirror.py --list                                 列出每个账号里的条目
  steam-shortcut-mirror.py --active                               打印当前登录账号的 uid
  steam-shortcut-mirror.py --find    <.app 路径>                  只读查重, 不写任何文件
  steam-shortcut-mirror.py --remove  <.app 路径>                  删掉这条快捷方式
  steam-shortcut-mirror.py --tidy                                 去掉死条目与重复条目
  steam-shortcut-mirror.py --purge   <uid|all>                    清空某账号的全部快捷方式

账号选择 (对上面每条都生效):
  --account <uid>    只动这个 userdata 目录 (uid 是目录名, 不是 steamid64)
  --all-accounts     动所有账号; 不给就只动当前登录的那个
  环境变量 SN_STEAM_ACCOUNT 等同于 --account, 用于长期钉死到某个账号。

写入按 exe 路径判重, 已有条目的账号跳过。所有写操作都先备份 + 字节级往返验证。
"""
import os, re, struct, sys, time
from pathlib import Path
from paths import STEAM as STEAM_ROOT

STEAM = str(STEAM_ROOT)
STEAMID64_BASE = 76561197960265728


def active_account():
    """当前设为自动登录的账号的 userdata 目录名。取不到返回 None。

    AutoLogin="1" 必须唯一；registry 只能作旁证。文件时间不能代表用户选择。
    """
    p = os.path.join(STEAM, "config", "loginusers.vdf")
    users = {}
    if os.path.exists(p):
        t = Path(p).read_text(errors="replace")
        for m in re.finditer(r'"(\d{17})"\s*\{(.*?)\n\t\}', t, re.S):
            sid, body = m.group(1), m.group(2)
            g = lambda k: (re.search(r'"%s"\s+"([^"]*)"' % k, body, re.I) or [None, ""])[1]
            users[sid] = {"account": g("AccountName"), "persona": g("PersonaName"),
                          "autologin": g("AutoLogin"), "ts": g("Timestamp")}
        selected = [(sid, u) for sid, u in users.items() if u["autologin"] == "1"]
        if len(selected) != 1:
            return None
        sid, user = selected[0]
        pr = os.path.join(STEAM, "registry.vdf")
        if os.path.exists(pr):
            m = re.search(r'"AutoLoginUser"\s+"([^"]*)"', Path(pr).read_text(errors="replace"))
            if m:
                if user["account"] != m.group(1):
                    return None
        uid = int(sid) - STEAMID64_BASE
        return str(uid) if 0 <= uid <= 0xffffffff else None
    return None


def account_label(uid):
    """给 uid 配上账号名和昵称, 用于打印。"""
    p = os.path.join(STEAM, "config", "loginusers.vdf")
    if os.path.exists(p):
        sid = str(int(uid) + STEAMID64_BASE)
        t = Path(p).read_text(errors="replace")
        m = re.search(r'"%s"\s*\{(.*?)\n\t\}' % sid, t, re.S)
        if m:
            g = lambda k: (re.search(r'"%s"\s+"([^"]*)"' % k, m.group(1), re.I) or [None, "?"])[1]
            return f"{uid} ({g('AccountName')} / {g('PersonaName')})"
    return uid


def all_accounts():
    base = os.path.join(STEAM, "userdata")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if d.isdigit())


def target_accounts(argv):
    """按命令行与环境变量决定要动哪些账号。副作用: 从 argv 里摘掉这些开关。"""
    every = "--all-accounts" in argv
    if every:
        argv.remove("--all-accounts")
    pick = os.environ.get("SN_STEAM_ACCOUNT", "").strip() or None
    if "--account" in argv:
        i = argv.index("--account")
        if i + 1 >= len(argv):
            raise ValueError("--account 后需要 userdata 目录编号")
        pick = argv[i + 1]
        del argv[i:i + 2]
    if pick:
        if not pick.isdigit() or not 0 <= int(pick) <= 0xffffffff:
            raise ValueError("账号必须是 userdata 的数字目录编号")
        return [pick]
    if every:
        return all_accounts()
    act = active_account()
    if not act:
        print("  认不出当前登录的账号, 不动任何文件。要指定请用 --account <uid>")
        return []
    return [act]


def read_cstr(b, i):
    j = b.index(b"\x00", i)
    return b[i:j].decode("utf-8", "replace"), j + 1


def parse_map(b, i):
    """读一个 map, 返回 (有序字段列表, 下一个偏移)。字段是 (类型, 键, 值)。"""
    out = []
    while True:
        t = b[i]; i += 1
        if t == 0x08:
            return out, i
        k, i = read_cstr(b, i)
        if t == 0x00:
            v, i = parse_map(b, i)
        elif t == 0x01:
            v, i = read_cstr(b, i)
        elif t == 0x02:
            v = struct.unpack_from("<I", b, i)[0]; i += 4
        else:
            raise ValueError(f"未知字段类型 {t} at {i-1}")
        out.append((t, k, v))


def dump_map(fields):
    out = bytearray()
    for t, k, v in fields:
        out += bytes([t]) + k.encode() + b"\x00"
        if t == 0x00:
            out += dump_map(v)
        elif t == 0x01:
            out += v.encode() + b"\x00"
        elif t == 0x02:
            out += struct.pack("<I", v & 0xFFFFFFFF)
    return bytes(out) + b"\x08"


def load(path):
    if not os.path.exists(path):
        return []
    b = open(path, "rb").read()
    assert b[0] == 0x00, "不是二进制 vdf"
    k, i = read_cstr(b, 1)
    assert k == "shortcuts", f"顶层键是 {k}, 不是 shortcuts"
    entries, _ = parse_map(b, i)
    return entries


def save(path, entries):
    body = bytearray(b"\x00shortcuts\x00")
    for n, (_t, _k, fields) in enumerate(entries):
        body += b"\x00" + str(n).encode() + b"\x00" + dump_map(fields)
    body += b"\x08\x08"
    tmp = path + ".tmp"
    Path(tmp).write_bytes(bytes(body))
    os.replace(tmp, path)


def field(fields, name):
    for _t, k, v in fields:
        if k.lower() == name.lower():
            return v
    return None


def make_entry(appid, name, exe, icon):
    return [
        (0x02, "appid", appid),
        (0x01, "appname", name),
        (0x01, "Exe", f'"{exe}"'),
        (0x01, "StartDir", os.path.dirname(exe.rstrip("/")) + "/"),
        (0x01, "icon", icon),
        (0x01, "ShortcutPath", ""),
        (0x01, "LaunchOptions", ""),
        (0x02, "IsHidden", 0),
        (0x02, "AllowDesktopConfig", 1),
        (0x02, "AllowOverlay", 1),
        (0x02, "OpenVR", 0),
        (0x02, "Devkit", 0),
        (0x01, "DevkitGameID", ""),
        (0x02, "DevkitOverrideAppID", 0),
        (0x02, "LastPlayTime", 0),
        (0x01, "FlatpakAppID", ""),
        (0x01, "sortas", ""),
        (0x00, "tags", []),
    ]


def find_existing(exe, uids=None):
    """只读查找；省略 uids 时供审计使用，命令行遵守账号选择。"""
    hits = []
    base = os.path.join(STEAM, "userdata")
    if not os.path.isdir(base):
        return hits
    for uid in sorted(os.listdir(base)):
        if not uid.isdigit():
            continue
        if uids is not None and uid not in uids:
            continue
        path = os.path.join(base, uid, "config", "shortcuts.vdf")
        try:
            entries = load(path)
        except Exception:
            continue
        for _t, _k, f in entries:
            if field(f, "Exe") in (exe, f'"{exe}"'):
                hits.append((uid, field(f, "appid"), field(f, "appname")))
    return hits


def _rewrite(path, uid, entries, keep, what):
    """备份 + 字节级往返验证 + 写入 + 回读。keep 是要留下的条目列表。

    动手之前先把原文件解析出来再原样序列化, 和磁盘上的原始字节逐字节比对。对不上就说明
    本解析器对这个文件不忠实, 立刻放弃、一个字节都不写。
    """
    raw = open(path, "rb").read()
    body = bytearray(b"\x00shortcuts\x00")
    for n, (_t, _k, fields) in enumerate(entries):
        body += b"\x00" + str(n).encode() + b"\x00" + dump_map(fields)
    body += b"\x08\x08"
    if bytes(body) != raw:
        print(f"  {account_label(uid)}: 往返验证不过(原 {len(raw)} 字节 / 重建 {len(body)} 字节), 不动它")
        return 1
    bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    open(bak, "wb").write(raw)
    save(path, keep)
    back = load(path)
    print(f"  {account_label(uid)}: {what}, 回读剩 {len(back)} 条; 备份 {os.path.basename(bak)}")
    return 0


def list_entries(uids):
    """只读: 列出每个账号里的条目, 标出死条目与重复。"""
    for uid in uids:
        path = os.path.join(STEAM, "userdata", uid, "config", "shortcuts.vdf")
        if not os.path.exists(path):
            print(f"{account_label(uid)}: 没有 shortcuts.vdf"); continue
        try:
            entries = load(path)
        except Exception as ex:
            print(f"{account_label(uid)}: 解析失败 ({ex})"); continue
        print(f"{account_label(uid)}: {len(entries)} 条")
        seen = set()
        for i, (_t, _k, f) in enumerate(entries):
            exe = (field(f, "Exe") or "").strip('"')
            flags = []
            if not os.path.exists(exe):
                flags.append("路径已不存在")
            if exe in seen:
                flags.append("重复")
            seen.add(exe)
            tail = ("  <- " + "、".join(flags)) if flags else ""
            print(f"  [{i}] {field(f, 'appname')}  appid={field(f, 'appid')}{tail}")
            print(f"      {exe}")
    return 0


def tidy(uids):
    """去掉指向已不存在路径的条目, 以及同一 exe 的重复条目(留第一条)。"""
    rc = 0
    for uid in uids:
        path = os.path.join(STEAM, "userdata", uid, "config", "shortcuts.vdf")
        if not os.path.exists(path):
            print(f"  {account_label(uid)}: 没有 shortcuts.vdf, 跳过"); continue
        try:
            entries = load(path)
        except Exception as ex:
            print(f"  {account_label(uid)}: 解析失败, 不动它 ({ex})"); rc = 1; continue
        keep, seen, dropped = [], set(), []
        for e in entries:
            exe = (field(e[2], "Exe") or "").strip('"')
            if not os.path.exists(exe):
                dropped.append(f"{field(e[2], 'appname')}(路径已不存在)"); continue
            if exe in seen:
                dropped.append(f"{field(e[2], 'appname')}(重复)"); continue
            seen.add(exe); keep.append(e)
        if not dropped:
            print(f"  {account_label(uid)}: 没有死条目或重复, 不动它"); continue
        rc |= _rewrite(path, uid, entries, keep, f"去掉 {len(dropped)} 条 [{', '.join(dropped)}]")
    return rc


def purge(uids):
    """清空这些账号的全部非 Steam 快捷方式。"""
    rc = 0
    for uid in uids:
        path = os.path.join(STEAM, "userdata", uid, "config", "shortcuts.vdf")
        if not os.path.exists(path):
            print(f"  {account_label(uid)}: 没有 shortcuts.vdf, 跳过"); continue
        try:
            entries = load(path)
        except Exception as ex:
            print(f"  {account_label(uid)}: 解析失败, 不动它 ({ex})"); rc = 1; continue
        if not entries:
            print(f"  {account_label(uid)}: 本来就是空的"); continue
        rc |= _rewrite(path, uid, entries, [], f"清空 {len(entries)} 条")
    return rc


def remove_entry(exe, uids):
    """删掉指定账号里 Exe 指向 exe 的快捷方式。

    动手之前先做字节级往返验证: 把原文件解析出来再原样序列化, 和磁盘上的
    原始字节逐字节比对。对不上就说明本解析器对这个文件不忠实, 立刻放弃、
    一个字节都不写。这条和 09-11 那次镜像写入前的做法是同一条。
    """
    rc = 0
    for uid in uids:
        path = os.path.join(STEAM, "userdata", uid, "config", "shortcuts.vdf")
        if not os.path.exists(path):
            continue
        try:
            entries = load(path)
        except Exception as ex:
            print(f"  {account_label(uid)}: 解析失败, 不动它 ({ex})"); rc = 1; continue
        hit = [e for e in entries if field(e[2], "Exe") in (exe, f'"{exe}"')]
        if not hit:
            print(f"  {account_label(uid)}: 没有这条, 跳过"); continue
        kept = [e for e in entries if e not in hit]
        rc |= _rewrite(path, uid, entries, kept, f"删掉 {len(hit)} 条")
    return rc


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__); return 2
    uids = target_accounts(argv)          # 摘掉 --account / --all-accounts
    if not argv:
        print(__doc__); return 2
    cmd = argv[0]

    if cmd == "--active":                  # 给 shell 用: 打印当前登录账号的 uid
        a = active_account()
        if not a:
            return 1
        print(a); return 0
    if cmd == "--list":
        return list_entries(uids or all_accounts())
    if cmd == "--find":
        if len(argv) < 2:
            print(__doc__); return 2
        if not uids:
            return 2
        hits = find_existing(argv[1].rstrip("/"), uids)
        for uid, appid, name in hits:
            print(f"{uid} {appid} {name}")
        return 0 if hits else 1
    if not uids:
        return 1
    if cmd == "--remove":
        if len(argv) < 2:
            print(__doc__); return 2
        return remove_entry(argv[1].rstrip("/"), uids)
    if cmd == "--tidy":
        return tidy(uids)
    if cmd == "--purge":
        if len(argv) >= 2:                 # --purge 显式点名账号, 不受默认收敛约束
            uids = all_accounts() if argv[1] == "all" else [argv[1]]
        return purge(uids)

    if len(argv) < 3:
        print(__doc__); return 2
    appid = int(argv[0]); name = argv[1]; exe = argv[2].rstrip("/")
    icon = argv[3] if len(argv) > 3 else ""
    rc = 0
    for uid in uids:
        cfg = os.path.join(STEAM, "userdata", uid, "config")
        if not os.path.isdir(cfg):
            continue
        path = os.path.join(cfg, "shortcuts.vdf")
        try:
            entries = load(path)
        except Exception as ex:
            print(f"  {account_label(uid)}: 解析失败, 不动它 ({ex})"); rc = 1; continue
        if any(field(f, "Exe") in (exe, f'"{exe}"') for _t, _k, f in entries):
            print(f"  {account_label(uid)}: 已有条目, 跳过"); continue
        if os.path.exists(path):
            bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            open(bak, "wb").write(open(path, "rb").read())
        entries.append((0x00, str(len(entries)), make_entry(appid, name, exe, icon)))
        save(path, entries)
        back = load(path)                  # 写完立刻回读核对
        ok = any(field(f, "appname") == name for _t, _k, f in back)
        print(f"  {account_label(uid)}: 写入 {'成功' if ok else '失败'}, 现共 {len(back)} 条")
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
