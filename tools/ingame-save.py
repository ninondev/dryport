#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""ingame-save：验**游戏内存档**——在游戏里存一个新槽，经一次正常退出和一次强杀之后它还在、读得回来。

  tools/ingame-save.py up   <game>             # 经 run-guard 拉起，等窗口，截图，落状态文件
  tools/ingame-save.py keys <game> <序列>       # 往窗口 pid 投键，记号：z, z@300, z*3, cmd+q, wait:1.5, click:X:Y
  tools/ingame-save.py shot <game> [名]         # 按窗口 id 截图进本次运行证据目录
  tools/ingame-save.py down <game> [--hard]    # cmd+q 正常退；--hard 对记录的各组发 SIGKILL（内层在前）
  tools/ingame-save.py run  <game>             # 全自动：存→正常退→读→强杀→读→恢复盘面，出 result.json/RESULT.md

为什么要这个：save-matrix.py 验的是盘上既有存档「三种操作后还能读」，没验「游戏里存档真的写进盘」。
写盘这一步只能往游戏里投键，而键注入不可靠（见 save-matrix.py 头注释），所以判据全部落在机器能查的
东西上：存档目录的 SHA 清单（写没写盘）、截图的缩略图距离（进没进存档画面）、进程组死没死。
键序列逐游戏配在 tools/ingame-save-games.json，没配的 run 拒跑，绝不猜键位。表里另有可选字段：
save_again_keys（第 5 步的第二次存档，缺省沿用 save_keys）、load_latest_keys（第 6 步强杀后读档，
缺省沿用 load_keys）、folder（游戏原始目录，缺省查 installed.json 的 appid 条目）、needs_focus
（true 则每步投键/点击前先把游戏置前再等 0.3s——合成键只落进前台应用，Wine 实测不置前就丢键）、
click_mode（pid=默认，keysend --click 往窗口进程投；hid=keysend --click-hid 让真光标走系统事件流，
NW.js/Chromium 的窗口对 --click 不理不睬，实测只有 hid 点得动）。

Wine 的启动壳还会自己再起一层 run-guard（标签是游戏名），把游戏本体放进新的会话，外层记录的
pgid 因此罩不到它。拉起之后按进程表认亲：凡本会话新写、guard_pid 沿 ppid 一路连得到本工具守护
进程的记录，它的 pgid 也算游戏的组（状态记在 pgids，pgid 仍留外层那一个作兼容）；找窗口、判退、
强杀都对着整组来，强杀内层在前。Wine 起游戏的后续进程（比如 UE 的 *-Shipping.exe）更是 double
fork + setsid：ppid 1、自成一组、没有任何守护记录，记录组一个都罩不到它们——这些只能按命令行里
app 的 Windows 路径认（Wine 把每个进程的 argv 改写成 Z:\\ 开头的 Windows 命令行），down --hard
先杀它们，再对游戏的 WINEPREFIX 补一发 wineserver -k 收掉 Wine 自己的系统进程。up 拉起之后另有
caffeinate -d -u 钉在外层守护上：整局游戏期间显示器都醒着——合成输入不算用户活动，屏幕睡了
截图就全黑。

有的 app 壳带存档云同步帮手（Contents/MacOS/savebridge，启动壳会在游戏前后调它）：测试写进存档
目录的东西绝不能被它传上云端。所以每次拉起都给 run-guard 的环境带 SN_SAVEBRIDGE=0（新版启动壳
见到 0 就跳过帮手）；preflight 与 up 还会先查，壳里有 savebridge 而启动壳文本不认 SN_SAVEBRIDGE
的直接拒跑。

run 的每一步记 pass/fail/unknown 连同证据文件名，证据落
$SN_GAMES/dryport-deliveries/<日期>/ingame-save/<game>/<HHMMSS>/；
结论写 result.json 与 RESULT.md（中文、结论先行）。测完把存档目录原样放回：
只挪旁、拷回，不删用户的任何东西。
"""
import argparse
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_GAMES, SN_RUNTIMES, SN_HOME  # noqa: E402
from evidence import windows, shoot, guard_records, pid_alive, reap_record, file_sha256  # noqa: E402

RUN_GUARD = SN_HOME / "tools" / "run-guard.py"
TABLE = SN_HOME / "tools" / "ingame-save-games.json"
KEYSEND = SN_RUNTIMES / "bin" / "keysend"
STATE_DIR = SN_GAMES / ".ingame-save"
RUNNING = SN_GAMES / ".running"
EVID_ROOT = SN_GAMES / "dryport-deliveries"

DEFAULT_BOOT_TIMEOUT = 60
DEFAULT_BOOT_WAIT = 8.0
DEFAULT_HOLD_MS = 80
QUIT_WAIT = 20.0   # cmd+q 之后等进程组消失的秒数
KILL_WAIT = 10.0   # SIGKILL 之后等消失的秒数
LOOKALIKE_DIST = 8.0  # 两份存档画面缩略图距离低于它就算「几乎一样」：机器分不出读回的是哪份，交给人看

# keysend 认的键名（tools/keysend.swift 的 keyCodes 表）。表变了这里要跟着改；
# 在这里先拦一层，是为了把「键名打错」挡在启动游戏之前，而不是投进游戏里才发现。
KEY_NAMES = frozenset("return enter space escape tab delete up down left right".split()) | \
    frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
MODS = ("cmd", "shift", "ctrl", "alt")
KEY_RE = re.compile(r"([a-z0-9]+)(?:@(\d+))?(?:\*(\d+))?\Z")
# click:X:Y / click:X:Y@120 的坐标部分：整数即可（正负都收，点没点对有截图判据兜着）
CLICK_RE = re.compile(r"(-?\d+):(-?\d+)(?:@(\d+))?\Z")

# 表字段 click_mode 认的取值：pid=往窗口进程投（默认，老行为）；hid=真光标走系统事件流
CLICK_MODES = ("pid", "hid")

# run 需要的表字段；advance_keys / quit_keys / save_again_keys / load_latest_keys 有缺省，不算必需
RUN_NEEDS = ("appid", "app", "saves", "save_keys", "load_keys")


class KeysendError(RuntimeError):
    pass


class KillRefused(RuntimeError):
    pass


class RunAbort(Exception):
    """run 里前置步骤塌了（比如窗口没出来），后面没有意义，直接跳去收场、写报告。"""


def parse_keys(text):
    """把按键序列文本解成步骤列表（纯函数，方便单测）。

    记号：逗号分隔；裸键名（return/z/down 等 keysend 认的名字）；key@300 按住 300ms
    （缺省 80）；key*3 重复三次；cmd+q 加修饰键（cmd/shift/ctrl/alt，可叠：shift+ctrl+z）；
    wait:1.5 等 1.5 秒；click:X:Y / click:X:Y@120 在窗口 (X,Y) 处点一下鼠标（窗口左上角为原点，
    按住缺省 80，send_steps 投递时按窗口 bounds 换算成屏幕坐标）。不认识的记号报错并点名那个
    token，绝不悄悄跳过——键序列错了打进的键会落在游戏里，错一步整轮证据都是脏的。
    """
    steps = []
    for raw in text.split(","):
        tok = raw.strip()
        if not tok:
            raise ValueError(f"空的按键记号（原文：{text!r}）")
        if tok.startswith("wait:"):
            try:
                secs = float(tok[len("wait:"):])
            except ValueError:
                raise ValueError(f"wait: 后面要是秒数，不认识 {tok!r}") from None
            if not math.isfinite(secs) or secs < 0:
                raise ValueError(f"等待秒数得是有限非负数，不认识 {tok!r}")
            steps.append({"wait": secs})
            continue
        if tok.startswith("click:"):
            m = CLICK_RE.match(tok[len("click:"):])
            if not m:
                raise ValueError(f"不认识的点击记号 {tok!r}（要 click:X:Y 或 click:X:Y@毫秒，X/Y 是窗口内的整数坐标）")
            hold = int(m.group(3)) if m.group(3) else DEFAULT_HOLD_MS
            if hold < 1:
                raise ValueError(f"点击按住毫秒数得 ≥1（记号 {tok!r}）")
            steps.append({"click": (int(m.group(1)), int(m.group(2))), "hold_ms": hold})
            continue
        *mods, keypart = tok.split("+")
        unknown = [m for m in mods if m not in MODS]
        if unknown:
            raise ValueError(f"不认识的修饰键 {unknown}（记号 {tok!r}）")
        m = KEY_RE.match(keypart)
        if not m or m.group(1) not in KEY_NAMES:
            raise ValueError(f"不认识的按键记号 {tok!r}（keysend 认的名字见 tools/keysend.swift）")
        hold = int(m.group(2)) if m.group(2) else DEFAULT_HOLD_MS
        repeat = int(m.group(3)) if m.group(3) else 1
        if hold < 1 or repeat < 1:
            raise ValueError(f"按住毫秒数和重复次数都得 ≥1（记号 {tok!r}）")
        steps.append({"key": m.group(1), "hold_ms": hold, "repeat": repeat, "mods": list(mods)})
    return steps


def keysend_check():
    """keysend --check 只报 AXIsProcessTrusted；退出码 3 = 没有辅助功能权限。"""
    if not KEYSEND.is_file():
        return False, f"没有 {KEYSEND}，先按 tools/keysend.swift 头注释编译"
    try:
        r = subprocess.run([str(KEYSEND), "--check"], capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"keysend --check 跑不起来：{type(e).__name__}: {e}"
    if r.returncode == 3:
        return False, "keysend 没有辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能，给终端/Python 勾上）"
    if r.returncode != 0:
        return False, f"keysend --check 退出码 {r.returncode}：{r.stderr.strip()}"
    return True, "辅助功能权限在"


def to_screen(bounds, x, y):
    """窗口相对点 → 屏幕坐标。bounds 是 window-list 给的窗口边框，X/Y 是其左上角在屏幕全局
    坐标系（左上原点、Y 向下）里的位置——跟窗口内偏移一加就是屏幕点。纯函数，方便单测。"""
    try:
        return int(bounds["X"]) + int(x), int(bounds["Y"]) + int(y)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"窗口 bounds 的 X/Y 不是能用的数，没法换算屏幕坐标：{bounds!r}") from None


def click_bounds(state):
    """click 步要的窗口 bounds。窗口会被拖动/换屏，up 时记进状态的那份可能已过时：
    点击前按 window_id 从系统窗口列表重取一次；id 已不在列表里（启动画面关了、窗口换了一个）
    就按会话进程组重认一次窗口、用新窗口的 bounds；再取不到才退回状态里记的；
    两头都没有就没法换算。"""
    if not state:
        raise ValueError("序列里有 click 记号，但没给状态文件（先 up 拿到窗口 bounds）")
    wid = state.get("window_id")
    if wid is not None:
        for w in windows():
            if w.get("id") == wid and w.get("bounds"):
                return w["bounds"]
    w = refresh_window(state)
    if w is not None and w.get("bounds"):
        return w["bounds"]
    b = state.get("bounds")
    if not b:
        raise ValueError(f"窗口 {wid} 的 bounds 系统里取不到、状态里也没记，click 没法换算屏幕坐标")
    return b


def focus_window(pid):
    """把游戏窗口拽到最前再投键：合成键只落进前台应用，游戏不在最前时键就打空了（Wine 实测）。
    System Events 按 unix id 认进程，别按名字猜——Wine 的进程名都撞在 wine 上，名字靠不住。"""
    script = (f'tell application "System Events" to set frontmost of '
              f'(first process whose unix id is {int(pid)}) to true')
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise KeysendError(f"置前失败，osascript 跑不起来：{type(e).__name__}: {e}") from None
    if r.returncode != 0:
        raise KeysendError(f"置前失败（osascript 退出 {r.returncode}）：{r.stderr.strip()}")


def send_steps(pid, steps, state=None):
    """把 parse_keys 的结果逐条投给窗口 pid；wait 步就地睡。返回每步一句说明。
    click 步按 click_bounds 取到的最新窗口 bounds 换算屏幕坐标再投。
    state 里 needs_focus 为真时，每步投键/点击前先置前再等 0.3s；click_mode:hid 的点击没有
    --pid 可指，认的是此刻的前台焦点，更要先置前——等那一拍是给窗口管理器缓口气，免得点进
    刚被挤上来的别家窗口。置前失败就整段不投：键落进前台那家不知谁的窗口，证据就是脏的。"""
    state = state or {}
    needs_focus = bool(state.get("needs_focus"))
    hid = state.get("click_mode") == "hid"
    notes = []
    for st in steps:
        if "wait" in st:
            time.sleep(st["wait"])
            notes.append(f"等 {st['wait']:g}s")
            continue
        if "click" in st:
            x, y = st["click"]
            sx, sy = to_screen(click_bounds(state), x, y)
            if hid or needs_focus:
                focus_window(pid)
                time.sleep(0.3)
                notes.append("已置前")
            if hid:
                cmd = [str(KEYSEND), "--click-hid", f"{sx},{sy}", "--hold-ms", str(st["hold_ms"])]
            else:
                cmd = [str(KEYSEND), "--pid", str(pid), "--click", f"{sx},{sy}", "--hold-ms", str(st["hold_ms"])]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 3:
                raise KeysendError("keysend 报没有辅助功能权限（退出码 3）")
            if r.returncode != 0:
                way = "--click-hid" if hid else "--click"
                raise KeysendError(f"keysend {way} {sx},{sy} 退出 {r.returncode}：{r.stderr.strip()}")
            notes.append(f"click {x},{y}→屏({sx},{sy})" + ("（hid）" if hid else ""))
            continue
        if needs_focus:
            focus_window(pid)
            time.sleep(0.3)
            notes.append("已置前")
        cmd = [str(KEYSEND), "--pid", str(pid), "--key", st["key"], "--hold-ms", str(st["hold_ms"])]
        for mod in st["mods"]:
            cmd += ["--mod", mod]
        for i in range(st["repeat"]):
            if i:
                time.sleep(0.12)  # 两次按下之间留个缝，逐帧轮询的引擎才认得出是两次
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 3:
                raise KeysendError("keysend 报没有辅助功能权限（退出码 3）")
            if r.returncode != 0:
                raise KeysendError(f"keysend --key {st['key']} 退出 {r.returncode}：{r.stderr.strip()}")
        notes.append(st["key"] + (f"×{st['repeat']}" if st["repeat"] > 1 else ""))
    return notes


def try_send(pid, sequence, state=None):
    """投一组键，把异常折成 (False, 原因)；窗口 pid 不在就直说没处投。"""
    if not pid or not pid_alive(pid):
        return False, f"窗口 pid {pid} 不在，键没处投"
    try:
        notes = send_steps(int(pid), parse_keys(sequence), state)
        return True, "投了 " + "，".join(notes)
    except (KeysendError, ValueError) as e:
        return False, str(e)


def pgids_txt(pgids):
    """进程组列表的一行写法，报错和报告里都用它。"""
    return "、".join(str(int(p)) for p in pgids)


def state_pgids(state):
    """状态里记的全部进程组，外层在前。老状态文件只有 pgid（升级前 up 写的），也认——
    down 得能收拾升级前的残局，不能因为缺了新字段就杀不得。"""
    pgids = []
    for p in (state.get("pgids") or []):
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if p and p not in pgids:
            pgids.append(p)
    pgid = state.get("pgid")
    try:
        pgid = int(pgid) if pgid else None
    except (TypeError, ValueError):
        pgid = None
    if pgid and pgid not in pgids:
        pgids.append(pgid)
    return pgids


def group_gone(pgids, timeout):
    """进程组里还有人吗：列出的每组都探没了才算没（Wine 内层启动壳的组也算游戏的组）。
    pgids 收列表也收单个 int（老状态文件只有一组）。killpg 探 0 号信号，ESRCH 才算没；
    PermissionError 是组还在只是发不进。列表为空算没人在（调用方各处都先判了有没有组）。"""
    if isinstance(pgids, (int, float)):
        pgids = [pgids]
    wanted = []
    for p in pgids or []:
        try:
            wanted.append(int(p))
        except (TypeError, ValueError):
            continue
    deadline = time.time() + max(0.0, timeout)
    while True:
        gone = True
        for p in wanted:
            try:
                os.killpg(p, 0)
                gone = False
            except ProcessLookupError:
                pass
            except PermissionError:
                gone = False
            if not gone:
                break
        if gone or time.time() >= deadline:
            return gone
        time.sleep(0.5)


def process_table():
    """ps -axo pid=,ppid= 一把读全系统的 pid→ppid 表，认亲只认这一张——逐个 ps -o ppid= 往上追
    既慢，中途进程一死还会追断。读不来返回空表：认不出亲就宁可少算一组，也不把别人误算进来。"""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    table = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            table.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return table


def process_rows():
    """ps 一把读全系统的 pid/pgid/命令行三列。要 -axww：Wine 把每个进程的 argv 改写成它的
    Windows 命令行，app 路径动辄上百字符，不带 ww 会被截到终端宽度，截掉的就永远认不出来。
    读不来返回空表——认不出就宁可少算一组，也不把别人误算进来。"""
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,pgid=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def app_windows_path(app_dir):
    """app 目录在 Wine 命令行里的模样：Z: 盘就是 Unix 根，目录分隔符换成反斜杠。结尾也补一个
    反斜杠——不补的话 `A B.app` 会连 `A B.app2` 一起认进来。纯函数，方便单测。"""
    return "Z:" + str(app_dir).replace("/", "\\") + "\\"


def app_process_pgids(rows, app_dir, own_pgid):
    """纯函数：从 process_rows 的行里认出这个 app 的 Wine 野组（游戏本体 + 它的子进程）。

    Wine double fork + setsid 起的进程没有守护记录，只能靠命令行认：Wine 把 argv 改写成
    Z:\\ 开头的 Windows 命令行，游戏进程带的是这个 app 的路径，Wine 自己的系统进程
    （services.exe、winedevice.exe 之类）是 C:\\windows 路径，天然分得开。比对不分大小写——
    Windows 路径本就不分。app_dir 是符号链接时（拉起用 realpath、进程表用实路径），解析后的
    实路径也算；本工具自己所在的组和 0/1 号组永远不算——前者是自杀，后者是 launchd/内核的。"""
    needles = [app_windows_path(app_dir).lower()]
    resolved = Path(app_dir).resolve()
    if resolved != Path(app_dir):
        needles.append(app_windows_path(resolved).lower())
    out = []
    for _pid, pgid, command in rows:
        if pgid == own_pgid or pgid <= 1:
            continue
        low = command.lower()
        if not any(n in low for n in needles):
            continue
        if pgid not in out:
            out.append(pgid)
    return out


def fresh_records(since=0.0):
    """$SN_GAMES/.running/ 里 started 晚于 since 的守护记录，不分标签。Wine 启动壳自起的第二层
    run-guard 用的标签是游戏名，跟外层记录的 label 对不上，只能先按时间粗筛，再靠进程表认亲。"""
    rows = []
    if not RUNNING.is_dir():
        return rows
    for f in sorted(RUNNING.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if float(rec.get("started", 0)) < since:
            continue
        rec["_file"] = str(f)
        rows.append(rec)
    return rows


def descendant_records(proctable, guard_pid, records):
    """纯函数：从 records 里认出 guard_pid 是本工具守护进程后代的那些（Wine 启动壳落的内层记录）。

    认亲法：进程表整理成 pid→ppid，从每条记录的 guard_pid 沿 ppid 往上走——走到守护 pid 就是亲的；
    先走到 pid 1（launchd）都没碰到 → 别人家的，不收；guard_pid 不在表里 → 那层守护已经死了
    （多半是上轮崩溃留下的陈账，pgid 甚至可能已被系统回收给无辜进程），无从认亲，不收。
    外层记录自己的 guard_pid 就是守护 pid 本尊，不算「后代」，由调用方单加上。"""
    try:
        guard_pid = int(guard_pid)
    except (TypeError, ValueError):
        return []
    parent = {}
    for pair in proctable:
        try:
            parent[int(pair[0])] = int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
    out = []
    for rec in records:
        cur = rec.get("guard_pid")
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            continue
        walked = set()
        while cur in parent and cur > 1 and cur not in walked:
            walked.add(cur)
            cur = parent[cur]
            if cur == guard_pid:
                out.append(rec)
                break
    return out


def reap_inner_records(pgids, label, since=0.0):
    """收内层残留记录：Wine 启动壳自起的第二层 run-guard 坐在外层进程组里，down --hard 对组发
    SIGKILL 时它跟着整组陪葬，自己 finally 里的 record.unlink() 没机会跑，于是每硬杀一次就留一条
    .running/<pgid>.json。这里只动 pgid 落在本会话记下的组（state_pgids(state)）里、且 name 不是
    本会话标签（外层自己的记录归 reap_record 按标签收）的记录；pid 和 guard_pid 都死了才替它删，
    还有一个活着就不删——那层守护还在干活，pgid 也随时可能被系统回收，删了等于销别人的守护记录。
    比本会话早的记录一概不碰：那是上轮的陈账，本会话没资格替它收拾。返回说明文字列表。"""
    wanted = set()
    for p in pgids or []:
        try:
            wanted.add(int(p))
        except (TypeError, ValueError):
            continue
    notes = []
    for rec in fresh_records(since):
        try:
            p = int(rec.get("pgid"))
        except (TypeError, ValueError):
            continue  # pgid 认不出来是哪一组的，宁可漏删也不瞎删
        if p not in wanted:
            continue  # 不在本会话的组里：别人家的记录，不碰
        if rec.get("name") == label:
            continue  # 外层自己的记录，reap_record 按标签收，这里不重复出手
        alive = [rec[k] for k in ("pid", "guard_pid") if rec.get(k) and pid_alive(rec[k])]
        if alive:
            notes.append(f"内层记录 {Path(rec['_file']).name} 里还有活进程 {alive}，不删")
            continue
        try:
            Path(rec["_file"]).unlink()
            notes.append(f"清掉了内层残留记录 {Path(rec['_file']).name}（启动壳那层守护随组被杀，没来得及自己删）")
        except OSError as error:
            notes.append(f"删不掉记录 {rec['_file']}: {error}")
    return notes


def launch_pgids(rec, since, app_dir=None):
    """本次运行游戏所在的全部进程组：外层记录的 pgid 打头，后面跟上认到亲的内层组。Wine 的启动壳
    自起第二层 run-guard 把游戏放进新会话，光看外层 pgid 找不到窗口。内层记录两关都过才算：
    started 晚于 since（本会话新写的，陈账不认）、guard_pid 沿进程表连得到外层守护（别人的不认）。
    给了 app_dir 再补一类：Wine double fork 出去的游戏进程（UE 的 *-Shipping.exe 就是）没有
    守护记录，只能按命令行里的 app Windows 路径认（见 app_process_pgids），不补就找不到窗口。"""
    pgids = [int(rec["pgid"])]
    for r in descendant_records(process_table(), rec.get("guard_pid"), fresh_records(since)):
        try:
            p = int(r.get("pgid"))
        except (TypeError, ValueError):
            continue
        if p not in pgids:
            pgids.append(p)
    if app_dir is not None:
        for p in app_process_pgids(process_rows(), app_dir, os.getpgid(0)):
            if p not in pgids:
                pgids.append(p)
    return pgids


def already_running(entry):
    """这个 app 现在已有 Wine 进程在跑吗：up 与 preflight 靠它挡住重复拉起——第二份游戏进程
    和第一份抢窗口、抢存档，证据就全脏了。只认命令行里带这个 app Windows 路径的进程，Wine
    自己的系统进程是 C:\\windows 路径，不会误伤。"""
    return app_process_pgids(process_rows(), SN_GAMES / entry["app"], os.getpgid(0))


def wine_server_of(app_dir):
    """Wine app 的 wineserver 与 WINEPREFIX：<app>/Contents/Resources/wine-launch.json 的
    wine 键是 Wine 根目录（wineserver 在其 bin/ 下）、state 键是 state 目录（prefix 在其下）。
    文件不在、读不了、不是 JSON、键缺或空都返回 None——那它就不是这种 Wine app，调用方别硬杀。"""
    try:
        data = json.loads((Path(app_dir) / "Contents" / "Resources" / "wine-launch.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    wine, state = data.get("wine"), data.get("state")
    if not wine or not state:
        return None
    return Path(wine) / "bin" / "wineserver", Path(state) / "prefix"


def stop_wineserver(app_dir):
    """对游戏的 WINEPREFIX 发一发 wineserver -k。Wine 自己的系统进程（services.exe、
    winedevice.exe、explorer.exe、wineserver 本尊……）和游戏进程一样是 setsid 出去的野组，
    逐组 killpg 既认不全也误伤不得，按前缀收才收得干净、且只收这一瓶的。非 Wine app
    返回 False，什么都不跑。"""
    pair = wine_server_of(app_dir)
    if pair is None:
        return False
    wineserver, prefix = pair
    try:
        subprocess.run([str(wineserver), "-k"], env=dict(os.environ, WINEPREFIX=str(prefix)),
                       capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return True


def resolve_kill_pgids(state):
    """从状态文件算出可以安全 killpg 的目标组（内层在前、外层垫后）；不安全就抛 KillRefused，绝不猜。

    拒绝规矩沿袭 save-matrix 的 stop()，多组时逐组都过，有一组不过就一组都不杀：
      1. 状态文件里得有 pgid（外层锚点）；
      2. 每个目标组都得有本会话（started 晚于状态文件记的时刻）写的、pgid 相符的守护记录——
         记录比本会话旧，多半是上轮崩溃的残留，pgid 可能已被系统回收给无辜进程；
      3. 外层组认标签，内层组认亲：外层记录的 name 得对得上本会话的 label；内层记录是启动壳落的
         （name 是游戏名），对不上标签，改看它的 guard_pid 此刻沿进程表还连不连得到本工具的守护
         进程——连不到的都是失联陈账，杀不得；
      4. 目标里不能有本工具自己所在的进程组——守护者给子进程开新组之前，守护者自己的 pgid 就是
         调用方的组，2026-09-13 按它 killpg 差点打到自己。

    带 app_dir 的状态（up 新写）另认一类目标：Wine double fork 出去的游戏进程没有守护记录，
    规矩 2/3 对它们天然失灵，改按此刻进程表里命令行带 app Windows 路径来认——进程此刻活着，
    本身就是证据；中途才冒出来的排在最前先杀。只在状态 app_pgids 里记过、此刻进程表里已不见的
    组静默跳过：组死了或 pgid 已被系统回收，照旧 pgid 动手就是乱杀。没有 app_dir 的老状态
    文件完全按老规矩走。"""
    outer = state.get("pgid")
    if not outer:
        raise KillRefused("状态文件里没有 pgid，无从杀起")
    outer = int(outer)
    own = os.getpgid(0)
    base = list(reversed(state_pgids(state)))
    label = state.get("label", "")
    since = float(state.get("started", 0))
    fresh = {}
    for r in fresh_records(since):
        try:
            fresh.setdefault(int(r.get("pgid")), r)
        except (TypeError, ValueError):
            continue
    table = None
    guard_pid = state.get("guard_pid")

    def check_record(p):
        """规矩 2/3：得有本会话的守护记录，外层组对标签、内层组认亲。"""
        nonlocal table
        rec = fresh.get(p)
        if rec is None:
            raise KillRefused(f".running 里没有本会话（label {label!r} started>={state.get('started')} "
                              f"pgid={p}）的守护记录：记录比本会话旧或已消失，按旧 pgid 动手会打到 "
                              "pid 被回收的无辜进程，拒绝")
        if p == outer:
            if rec.get("name") != label:
                raise KillRefused(f"pgid {p} 的守护记录标签是 {rec.get('name')!r}，"
                                  f"不是本会话的 {label!r}，拒绝")
        else:
            if not guard_pid:
                raise KillRefused(f"状态文件里没有 guard_pid，内层组 pgid {p} 认不了亲，拒绝")
            if table is None:
                table = process_table()
            if not descendant_records(table, guard_pid, [rec]):
                raise KillRefused(f"内层组 pgid {p} 的守护记录（guard_pid {rec.get('guard_pid')}）"
                                  "沿进程表认不到本工具的守护进程：那层守护已死或不是本会话的，拒绝")

    app_dir = state.get("app_dir")
    if not app_dir:
        targets = []
        for p in base:
            if p == own:
                raise KillRefused(f"pgid {p} 就是本工具所在的进程组，拒绝自杀")
            check_record(p)
            targets.append(p)
        return targets
    app_now = app_process_pgids(process_rows(), app_dir, own)
    recorded_app = set()
    for p in state.get("app_pgids") or []:
        try:
            recorded_app.add(int(p))
        except (TypeError, ValueError):
            continue
    targets = []
    for p in [p for p in app_now if p not in base] + base:
        if p == own:
            raise KillRefused(f"pgid {p} 就是本工具所在的进程组，拒绝自杀")
        if p in app_now:
            targets.append(p)  # 此刻进程表里活着、命令行就是这个 app——这就是全部证据
            continue
        if p in recorded_app:
            continue  # 记过但此刻不在：组已死或 pgid 已被回收，绝不照它动手
        check_record(p)
        targets.append(p)
    return targets


def hard_kill_groups(state):
    """过了解析关就逐组发 SIGKILL，内层在前——先送走游戏本体，再收启动壳所在的组，免得壳先死、
    触发它自己的善后（重启、清理）把现场搅乱。带 app_dir 的状态杀完组再按前缀补一发
    wineserver -k：游戏进程一死，Wine 自己的系统进程只会跟着这一声退。组已死/发不进都吞掉，
    真正的判据是后面等它消失。"""
    targets = resolve_kill_pgids(state)
    for pgid in targets:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if state.get("app_dir"):
        stop_wineserver(state["app_dir"])
    return targets


def force_kill_step(state):
    """强杀一步的判据：10 秒内组全消失才算过。"""
    pgids = state_pgids(state)
    if pgids and group_gone(pgids, 0):
        return True, "进程组已经不在，无需杀"
    try:
        targets = hard_kill_groups(state)
    except KillRefused as e:
        return False, str(e)
    if group_gone(targets, KILL_WAIT):
        return True, f"已对 pgid {pgids_txt(targets)} 发 SIGKILL（内层在前），组全消失"
    return False, f"对 pgid {pgids_txt(targets)} 发了 SIGKILL，{KILL_WAIT:.0f}s 后还有组活着"


def window_of_groups(pgids):
    """在被守护的几个进程组里找主窗口：按窗口属主 pid 的进程组比对。绝不按 owner 名猜——
    Wine 的窗口 owner 一律叫 wine，原生壳又各有各的名字，名字靠不住（the runtime notes）。
    Wine 的启动壳会把游戏放进新的进程组，要比对的是一组 pgid 而不是区区一个。"""
    wanted = {int(p) for p in pgids}
    for w in windows():
        try:
            if os.getpgid(int(w.get("pid", -1))) in wanted:
                return w
        except (ProcessLookupError, PermissionError, TypeError, ValueError):
            continue
    return None


def session_pgids(state):
    """此刻属于这局游戏的全部进程组：状态里记的（外层+内层），加上带 app_dir 的状态按此刻
    进程表新认到的 Wine 野组。up 落状态只是当时那一刻的快照——启动壳随后还会再起进程、
    新的 pgid 还会冒出来，找窗口前每次重认一遍才找得全。没有 app_dir 的老状态绝不跑 ps：
    认不了野组，也不该为一次窗口查询去动进程表。"""
    pgids = state_pgids(state)
    if not state.get("app_dir"):
        return pgids
    for p in app_process_pgids(process_rows(), state["app_dir"], os.getpgid(0)):
        if p not in pgids:
            pgids.append(p)
    return pgids


def refresh_window(state):
    """投键/截图/点击前重认一次游戏窗口。Unreal 这类游戏先弹一个小启动画面、再出真窗口，
    两个窗口同属游戏进程：up 若把启动画面记成了游戏窗口，它一关，按旧 window_id 的截图全失败，
    点击还按它的旧 bounds 换算、点进屏幕上别的位置。所以这里每次按会话的进程组重找，
    窗口列表大者在前，取到的第一个就是主窗口——记录里那个窗口还活着也会被更大的真窗口顶掉。
    找不到（游戏退了/窗口还没出来）就什么都不改、返回 None，让调用方按老记录走。"""
    w = window_of_groups(session_pgids(state))
    if w is None:
        return None
    state["window_id"] = w["id"]
    state["window_pid"] = w["pid"]
    state["bounds"] = w.get("bounds")
    return w


def shoot_window(state, dest):
    """先重认窗口再截图：拍的永远是此刻的主窗口，不是 up 那一刻记下的（可能是启动画面的）
    窗口 id。会话里已认不出任何窗口时没有 id 可拍，返回 False，由调用方决定这笔怎么记。"""
    refresh_window(state)
    if not state.get("window_id"):
        return False
    return shoot(state["window_id"], dest)


def wait_window(rec, since, timeout, app_dir=None):
    """等窗口出现；游戏自己退了（组全没了）就别傻等满超时。每轮都重新认一遍亲——
    内层启动壳的守护记录是拉起后过几秒才落的，第一轮多半认不全，得边等边收；给了 app_dir
    连 Wine 野组的进程也一并认（它们没有守护记录，只能按命令行认），否则 UE 那类游戏的窗口
    永远等不到。返回 (窗口, 认到的全部进程组)：组不管等到没等到都照记，down 靠它收拾残局。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pgids = launch_pgids(rec, since, app_dir)
        w = window_of_groups(pgids)
        if w is not None:
            return w, pgids
        if group_gone(pgids, 0):
            return None, pgids
        time.sleep(0.5)
    return None, launch_pgids(rec, since, app_dir)


def exe_of(app_dir):
    """CFBundleExecutable 从 Info.plist 读——别猜可执行名，各游戏的壳名不一样。"""
    with open(Path(app_dir) / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    name = info.get("CFBundleExecutable")
    if not name:
        raise RuntimeError(f"{app_dir}/Contents/Info.plist 里没有 CFBundleExecutable")
    return Path(app_dir) / "Contents" / "MacOS" / name


def launch_env():
    """run-guard 及其后代（游戏本体、启动壳）的环境：SN_SAVEBRIDGE=0 让带云同步帮手的启动壳
    跳过帮手——测试写进存档目录的东西绝不能被传上云端。拷一份 os.environ，别污染本工具自己。"""
    env = dict(os.environ)
    env["SN_SAVEBRIDGE"] = "0"
    return env


_KEPT_AWAKE = []  # 持着 caffeinate 的 Popen 引用，免得它被垃圾回收时叨叨；进程本身靠 -w 自己收


def keep_display_awake(pid=None, detach=False):
    """长跑期间把显示器摁住：合成键和 screencapture 都不算用户活动，屏幕一睡截图就全黑、
    游戏也可能跟着停帧。caffeinate -d 只压显示睡眠，-u 顺手声明一次用户活动把将睡的屏幕拽回来；
    -w 指着 pid（缺省本进程），被指的进程一退它就跟着退，不用专门收尸。detach=True 时另起
    会话——up 用它把 caffeinate 钉在外层守护上，游戏整局（可能几十分钟）显示器都醒着，
    本工具自己先退也不影响。起不来不挡流程，只把后果说在明处。"""
    try:
        _KEPT_AWAKE.append(subprocess.Popen(
            ["caffeinate", "-d", "-u", "-w", str(pid if pid else os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=detach))
        return True
    except OSError as e:
        print(f"caffeinate 起不来，长跑中途显示器可能会睡、截图会黑：{e}")
        return False


def wake_display():
    """投键/截图前把屏幕叫醒：-u 声明一次用户活动，-t 1 只占一秒不拖进程。睡着的显示器对
    screencapture 给的是黑帧，键也白投，先叫醒再动手才拍得到东西。起不来就算了——判据还有
    缩略图距离兜着。"""
    try:
        subprocess.run(["caffeinate", "-u", "-t", "1"], capture_output=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        pass


def launch_guarded(entry, label, evd, boot_timeout):
    """经 run-guard 拉起游戏本体。守护者保证会话断了也能把整组收掉；
    上限 boot_timeout+900s 是挂死兜底，正常流程早该退了。返回 (Popen, 记录, 起始时刻)。"""
    exe = exe_of(SN_GAMES / entry["app"])
    if not exe.is_file():
        raise RuntimeError(f"可执行文件不在：{exe}")
    evd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, str(RUN_GUARD), "--name", label, "--max-seconds", str(boot_timeout + 900),
         "--log", str(evd / f"{label}.log"), "--", str(exe)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=launch_env())
    rec = None
    for _ in range(30):  # 记录是 run-guard Popen 之后才写的，轮到它出现；pgid 只认记录
        time.sleep(0.5)
        rows = guard_records(label, since=t0)
        if rows:
            rec = rows[0]
            break
    if rec is None:
        try:
            proc.terminate()  # 记录没落成，先把守护者收掉，别留失联
        except ProcessLookupError:
            pass
        raise RuntimeError(f"run-guard 没落记录，{label} 大概没起来")
    return proc, rec, t0


def do_launch(entry, name, idx, evd, boot_timeout):
    """起第 idx 次游戏并等窗口。窗口没等到也照写状态文件——down 靠它收拾残局。状态里另记
    app_dir/app_pgids：Wine 野组的进程没有守护记录，down --hard 只能靠这对键认它们。"""
    label = f"ingamesave-{name}-{idx}"
    proc, rec, t0 = launch_guarded(entry, label, evd, boot_timeout)
    app_dir = SN_GAMES / entry["app"]
    win, pgids = wait_window(rec, t0, boot_timeout, app_dir=app_dir)
    app_pgids = app_process_pgids(process_rows(), app_dir, os.getpgid(0))
    for p in app_pgids:  # 等窗口最后一轮之后才冒头的野组也别漏记
        if p not in pgids:
            pgids.append(p)
    state = {"game": name, "label": label, "started": t0,
             "guard_pid": rec.get("guard_pid"), "pgid": int(rec["pgid"]), "pgids": pgids,
             "needs_focus": bool(entry.get("needs_focus")), "click_mode": click_mode_of(entry),
             "app_dir": str(app_dir), "app_pgids": app_pgids,
             "window_id": win["id"] if win else None,
             "window_pid": win["pid"] if win else None,
             "bounds": win.get("bounds") if win else None,
             "evidence_dir": str(evd)}
    write_state(name, state)
    return state, proc, win


def reap_guard_proc(proc):
    """守护者在游戏死后会自己退；等它，超时补一刀 terminate（它的信号处理器会收组）。"""
    if proc is None:
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


def quit_via_keys(state, entry, wait_s=QUIT_WAIT):
    """quit_keys（默认 cmd+q）走应用自己的退出路径——和用户按的一样，SIGTERM 不算正常退出。"""
    pgids = state_pgids(state)
    keys = quit_keys_of(entry)
    if pgids and group_gone(pgids, 0):
        return True, "进程组已不在（多半自己退了）"
    refresh_window(state)  # 记下的窗口可能是启动画面、早换了真窗口：按旧 pid 投键会投给不存在的进程
    pid = state.get("window_pid")
    if not pid or not pid_alive(pid):
        return False, f"窗口 pid {pid} 不在了，{keys} 没处投"
    ok, why = keysend_check()
    if not ok:
        return False, f"不投：{why}"
    try:
        notes = send_steps(int(pid), parse_keys(keys), state)
    except (KeysendError, ValueError) as e:
        return False, f"键没投出去：{e}"
    focused = sum(1 for n in notes if n == "已置前")
    extra = f"，期间置前 {focused} 次" if focused else ""
    gone = bool(pgids) and group_gone(pgids, wait_s)
    return gone, (f"{keys} 投出{extra}，进程组 {wait_s:.0f}s 内退出" if gone
                  else f"{keys} 投了{extra}，进程组 {wait_s:.0f}s 还活着")


def state_path(name):
    return STATE_DIR / f"{name}.json"


def write_state(name, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(name).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def read_state(name):
    p = state_path(name)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def clear_state(name):
    """只清本工具自己的状态文件——这是规则允许删的两样东西之一。"""
    try:
        state_path(name).unlink()
    except FileNotFoundError:
        pass


def new_evidence_dir(name):
    d = EVID_ROOT / time.strftime("%Y-%m-%d") / "ingame-save" / name / time.strftime("%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_table():
    if not TABLE.is_file():
        return {}
    return json.loads(TABLE.read_text())


def entry_missing(entry):
    """run 需要的字段有一个空着就列出来拒绝；null 的键序列拿去盲跑等于瞎按。"""
    return [f for f in RUN_NEEDS if not entry.get(f)]


def under_games(p):
    """~/ 开头就展开；相对路径按 SN_GAMES 算——配置里不写死机器路径。"""
    p = Path(p).expanduser()
    return p if p.is_absolute() else SN_GAMES / p


def installed_folder(entry):
    """原始游戏目录，originals 清单对着它算——游戏本体每一字节都不该被测试动过。
    表里带 folder 就用它（绝对、~/、相对 SN_GAMES 都行），优先于 installed.json：
    登记是安装器写的、会缺（appid 42 就没登记 folder），不该反过来挡住 run；
    表里没配才退回查 installed.json 里 appid 条目的 folder 字段。"""
    if entry.get("folder"):
        return under_games(entry["folder"])
    p = SN_GAMES / "installed.json"
    appid = entry.get("appid")
    if not appid or not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    folder = (data.get(str(appid)) or {}).get("folder")
    return under_games(folder) if folder else None


def saves_dir_of(entry):
    return under_games(entry["saves"])


def boot_timeout_of(entry):
    return int(entry.get("boot_timeout") or DEFAULT_BOOT_TIMEOUT)


def boot_wait_of(entry):
    v = entry.get("boot_wait")
    return float(v) if v is not None else DEFAULT_BOOT_WAIT


def quit_keys_of(entry):
    return entry.get("quit_keys") or "cmd+q"


def click_mode_of(entry):
    """表字段 click_mode：pid（默认）=keysend --click 往窗口进程投；hid=--click-hid 让真光标
    走系统事件流——NW.js/Chromium 的窗口对 --click 不理不睬，实测只有 hid 点得动。
    写了别的值就在拉起游戏前炸出来，别等游戏起来了才发现点不动。"""
    mode = entry.get("click_mode") or "pid"
    if mode not in CLICK_MODES:
        raise ValueError(f"click_mode 只认 {'/'.join(CLICK_MODES)}，表里写的是 {mode!r}")
    return mode


def cloud_sync_problem(app):
    """app 壳里带 savebridge 云同步帮手时，启动壳必须认 SN_SAVEBRIDGE=0（新版壳见到 0 就跳过
    帮手）；壳文本里没有这串就拒跑——不然测试写进存档目录的东西会被帮手原样传上云端。
    没问题返回 None。"""
    app = Path(app)
    if not (app / "Contents" / "MacOS" / "savebridge").is_file():
        return None
    try:
        text = (app / "Contents" / "MacOS" / "launcher").read_text(errors="replace")
    except OSError:
        text = ""  # 连启动壳都读不到，谈不上认 SN_SAVEBRIDGE
    if "SN_SAVEBRIDGE" not in text:
        return "这个 app 带存档云同步，但启动壳不认 SN_SAVEBRIDGE=0；先更新它的启动壳，免得测试存档传进云端。"
    return None


def preflight_problems(entry):
    """run 的第 0 步：上轮没收干净、游戏不在、游戏已有进程在跑、启动壳会传云存档、keysend 没权限，
    任一有就别开跑。"""
    problems = []
    live = []
    if RUNNING.is_dir():
        for f in sorted(RUNNING.glob("*.json")):
            try:
                rec = json.loads(f.read_text())
            except Exception:
                continue
            alive = [rec[k] for k in ("pid", "guard_pid") if rec.get(k) and pid_alive(rec[k])]
            if alive:
                live.append(f"{f.name}（pid {alive}）")
    if live:
        problems.append(f"$SN_GAMES/.running 里还有活着的守护记录：{'；'.join(live)}，先收干净再跑")
    app = SN_GAMES / entry["app"]
    if not app.is_dir():
        problems.append(f"游戏不在：{app}")
    else:
        try:
            exe_of(app)
        except Exception as e:
            problems.append(f"读 {app} 的可执行名失败：{e}")
        cloud = cloud_sync_problem(app)
        if cloud:
            problems.append(cloud)
        busy = already_running(entry)
        if busy:
            problems.append(f"这个游戏已有进程在跑（pgid {pgids_txt(busy)}），先收掉再来")
    ok, why = keysend_check()
    if not ok:
        problems.append(why)
    return problems


def manifest(root):
    """目录里每个文件的 SHA-256，键是相对路径；目录不存在返回 {}（调用方据此知道原本没有存档）。"""
    root = Path(root)
    out = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = file_sha256(p)
    return out


def manifest_diff(before, after):
    """清单差分：(新增, 改动, 缺失)，各自排好序。"""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    return added, changed, removed


def split_old_vs_slots(before, after, slots):
    """把测试槽之外的变动分出来：旧文件被改/没了 → changed_preexisting（待人看，不算失败，
    游戏本来就会写自己的日志设置）；槽外新增单列，只作信息。"""
    changed_pre = sorted(f for f in before if f not in slots and before[f] != after.get(f))
    added_other = sorted(f for f in after if f not in before and f not in slots)
    return changed_pre, added_other


def thumb_dist(a, b):
    """d(a,b)：64x36 灰度缩略图的平均绝对差（0–255）。用缩略图是因为全尺寸逐像素又慢
    又会被一行状态栏文字左右；64x36 足以分辨「停在标题还是进了存档画面」。"""
    from PIL import Image
    ta = Image.open(a).convert("L").resize((64, 36))
    tb = Image.open(b).convert("L").resize((64, 36))
    da, db = list(ta.getdata()), list(tb.getdata())
    return sum(abs(x - y) for x, y in zip(da, db)) / float(len(da))


def load_latest_verdict(d_loaded_saved2, d_loaded_saved1, d_loaded_title, d_saved1_saved2):
    """load_after_kill 的机器判据（纯函数，方便单测）。强杀前存了两次，读回来的画面得
    既比标题像存档画面、又更靠近第二份存档——只比标题近证明不了「读到的是强杀前那份」，
    覆盖同槽/另存新槽两种配法都要经得起这一条。两份存档画面本身几乎一样时机器断不了，
    记 unknown 交给人对着截图看。"""
    if d_saved1_saved2 < LOOKALIKE_DIST:
        return "unknown", "两次存档画面几乎一样，分不出读到的是哪一次"
    good = d_loaded_saved2 < d_loaded_title and d_loaded_saved2 < d_loaded_saved1
    if good:
        return "pass", (f"d(载入,二存)={d_loaded_saved2:.1f} < d(载入,标题)={d_loaded_title:.1f}"
                        f" 且 < d(载入,一存)={d_loaded_saved1:.1f}，读到的是强杀前那份")
    return "fail", (f"d(载入,二存)={d_loaded_saved2:.1f}，d(载入,标题)={d_loaded_title:.1f}，"
                    f"d(载入,一存)={d_loaded_saved1:.1f}，读回来的不是强杀前那份")


def load_after_quit_step(send_ok, send_note, save_status, shots_exist, d_saved, d_title,
                         dist_error=None):
    """load_after_quit 的判法（纯函数，方便单测）。键没投出去照旧是 fail；但第一次存档没写成
    （save_written 不是 pass）就绝不再拿截图距离说话——盘上根本没有那份存档时，读到的多半还是
    主菜单，与「存档画面」（其实也是主菜单）距离极小，会被误判成 pass（2026-09-28 实测翻车）。
    截图在、距离算得出时把距离照记进说明：判不了，但人还能对着截图看。"""
    if not send_ok:
        return "fail", f"键没投出去：{send_note}"
    if save_status != "pass":
        dist = (f"；距离照记 d(载入,存档)={d_saved:.1f}、d(载入,标题)={d_title:.1f}"
                if shots_exist and d_saved is not None and d_title is not None else "")
        return "unknown", f"第一次存档没写成（save_written={save_status}），读档比对无从谈起" + dist
    if not shots_exist:
        return "unknown", "截图缺了，距离比不了"
    if d_saved is None or d_title is None:
        return "unknown", f"距离比不了：{dist_error}"
    good = d_saved < d_title
    return ("pass" if good else "fail",
            f"d(载入,存档)={d_saved:.1f} {'<' if good else '>='} d(载入,标题)={d_title:.1f}")


def load_after_kill_step(send_ok, send_note, second_written, shots_exist,
                         d_saved2, d_saved1, d_title, d_two, dist_error=None):
    """load_after_kill 的判法（纯函数，方便单测）。键没投出去照旧是 fail；第二次存档压根没写盘
    时，强杀前那份与第一份无从区分，「读到的是强杀前那份」就无从验起，只能记 unknown 交给人
    看——照距离硬判，读到什么都会被说成强杀前那份。其余照 load_latest_verdict 的老判据。"""
    if not send_ok:
        return "fail", f"键没投出去：{send_note}"
    if not second_written:
        return "unknown", "第二次存档没有写盘（强杀前那份与第一份无从区分），强杀后读档无从验"
    if not shots_exist:
        return "unknown", "截图缺了，距离比不了"
    if d_saved2 is None or d_saved1 is None or d_title is None or d_two is None:
        return "unknown", f"距离比不了：{dist_error}"
    return load_latest_verdict(d_saved2, d_saved1, d_title, d_two)


def restore_saves(saves, backup, evd, stamp, had_saves):
    """把测后的存档挪进证据目录（saves-after/），再把备份原样放回。只挪旁、不删：
    那是用户的真存档，任何一步失败都要在盘上留看得见的东西。返回说明。"""
    saves, backup, evd = Path(saves), Path(backup), Path(evd)
    notes = []
    after_dir = evd / "saves-after"
    if saves.is_dir():
        try:
            os.rename(saves, after_dir)  # 同卷改名瞬时完成
        except OSError:
            # 跨卷 rename 会 EXDEV：先拷贝留证，再把原目录整个挪到旁边改名，绝不原地删
            shutil.copytree(saves, after_dir, symlinks=True)
            aside = saves.with_name(saves.name + f".ingame-test-{stamp}")
            os.rename(saves, aside)
            notes.append(f"跨卷：测后存档拷去证据目录，原目录旁置为 {aside.name}")
    else:
        notes.append("测后存档目录不存在（游戏没写盘或被游戏自己删了）")
    if had_saves:
        shutil.copytree(backup, saves, symlinks=True)
        notes.append("备份已放回原位")
    return notes


def write_report(evd, result):
    """RESULT.md：结论先行，一步一行带状态与证据名，给赶时间的人扫一眼。"""
    lines = [f"# ingame-save {result['game']}（appid {result.get('appid')}）", "",
             f"## 结论：{result['conclusion']}", ""]
    if result.get("why"):
        lines += [f"{result['why']}。", ""]
    if result.get("error"):
        lines += [f"中途异常（已收场并走完盘面与报告步骤）：{result['error']}", ""]
    for n, s in result["steps"].items():
        ev = "、".join(s.get("evidence") or []) or "无"
        detail = f" —— {s['detail']}" if s.get("detail") else ""
        machine = "；机器判据，请人对着截图确认" if s.get("machine") else ""
        lines.append(f"- {n}：**{s['status']}**{detail}（证据：{ev}）{machine}")
    if result.get("distances"):
        lines += ["", "截图距离（64x36 灰度缩略图平均绝对差，0–255）："]
        lines += [f"- {k}：" + "，".join(f"{dk}={dv}" for dk, dv in v.items())
                  for k, v in result["distances"].items()]
    lines += ["", f"证据目录：{result['evidence_dir']}", ""]
    (Path(evd) / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_up(name):
    table = load_table()
    if name not in table:
        print(f"表 {TABLE.name} 里没有 {name}")
        return 2
    entry = table[name]
    cloud = cloud_sync_problem(SN_GAMES / entry["app"])
    if cloud:
        print(f"拒绝 up {name}：{cloud}")
        return 2
    state = read_state(name)
    live = state_pgids(state) if state else []
    if live and not group_gone(live, 0):
        print(f"{name} 有一条还活着的会话（label {state.get('label')} pgid {pgids_txt(live)}），先 down 再 up")
        return 2
    busy = already_running(entry)
    if busy:
        print(f"拒绝 up {name}：这个游戏已有进程在跑（pgid {pgids_txt(busy)}），先收掉再来")
        return 2
    keep_display_awake()  # up 之后是等窗口、截图，屏幕中途睡了截图就全黑
    evd = new_evidence_dir(name)
    try:
        state, _proc, win = do_launch(entry, name, 1, evd, boot_timeout_of(entry))
    except Exception as e:
        print(f"起不来：{type(e).__name__}: {e}")
        return 1
    if state.get("guard_pid"):
        # 整局把显示器钉在外层守护上：它退了游戏必然也收场，caffeinate 跟着它退正好
        keep_display_awake(state["guard_pid"], detach=True)
    if win is None:
        print(f"等了 {boot_timeout_of(entry)}s 没等到窗口（进程组 {pgids_txt(state['pgids'])}）；"
              f"状态已写 {state_path(name)}，可 down --hard 收拾")
        return 1
    ok = shoot(win["id"], evd / "up-window.png")
    print(f"已拉起：label={state['label']} pgid={pgids_txt(state['pgids'])} guard_pid={state['guard_pid']} "
          f"窗口 {win['id']}（pid {win['pid']}）")
    print(f"截图{'已存' if ok else '失败'}：{evd / 'up-window.png'}；状态：{state_path(name)}")
    return 0


def cmd_keys(name, sequence):
    state = read_state(name)
    if not state:
        print(f"没有 {name} 的状态文件（{state_path(name)}），先 up")
        return 2
    if not state.get("window_pid"):
        print("状态里没有窗口 pid（上次 up 没等到窗口），先重新 up")
        return 2
    if refresh_window(state):  # 重认窗口：记下的 pid 若是启动画面的，键会投给已死的进程
        write_state(name, state)
    wake_display()  # 屏幕睡了投键也白投，先声明一次用户活动
    ok, why = keysend_check()
    if not ok:
        print(f"不投：{why}")
        return 3
    try:
        steps = parse_keys(sequence)
    except ValueError as e:
        print(f"序列不认识：{e}")
        return 2
    try:
        notes = send_steps(int(state["window_pid"]), steps, state)
    except (KeysendError, ValueError) as e:
        print(f"没投成：{e}")
        return 3
    print("；".join(notes))
    return 0


def cmd_shot(name, shot_name=None):
    state = read_state(name)
    if not state:
        print(f"没有 {name} 的状态文件（{state_path(name)}），先 up")
        return 2
    if not state.get("window_id"):
        print("状态里没有窗口 id（上次 up 没等到窗口），先重新 up")
        return 2
    if refresh_window(state):  # 重认窗口：启动画面换成真窗口后，旧 id 已拍不到东西
        write_state(name, state)
    wake_display()  # 睡着的屏幕拍出来是黑帧，先叫醒再拍
    dest = Path(state["evidence_dir"]) / (shot_name or f"shot-{time.strftime('%H%M%S')}.png")
    ok = shoot(state["window_id"], dest)
    print(f"截图{'已存' if ok else '失败'}：{dest}")
    return 0 if ok else 1


def cmd_down(name, hard):
    state = read_state(name)
    if not state:
        print(f"没有 {name} 的状态文件（{state_path(name)}），先 up")
        return 2
    entry = load_table().get(name) or {}
    if hard:
        ok, note = force_kill_step(state)
    else:
        ok, note = quit_via_keys(state, entry)
    print(f"[{'成' if ok else '败'}] {note}")
    pgids = state_pgids(state)
    if not (pgids and group_gone(pgids, 0)):
        if hard:
            print(f"强杀没成（原因见上，多半是守护记录已失效）；状态文件留着（{state_path(name)}），"
                  "重新 up 拿到本会话的新记录后再收")
        else:
            print(f"进程组还活着，状态文件留着（{state_path(name)}），可再 down --hard")
        return 1
    time.sleep(1.0)  # 给守护者一点时间自己退、自己删记录
    for note in reap_record(state["label"], float(state.get("started", 0))):
        print(f"  {note}")
    # 硬杀把内层守护连组一起带走，它的记录没机会自删；这里只认本会话的组和时刻，替它扫尾
    for note in reap_inner_records(state_pgids(state), state["label"], float(state.get("started", 0))):
        print(f"  {note}")
    clear_state(name)
    print(f"状态文件已清：{state_path(name)}")
    return 0 if ok else 1


def cmd_run(name):
    table = load_table()
    if name not in table:
        print(f"表 {TABLE.name} 里没有 {name}；run 只跑表里键序列配齐的游戏")
        return 2
    entry = table[name]
    missing = entry_missing(entry)
    folder = installed_folder(entry)
    if folder is None:
        missing.append(f"folder（表里没配，installed.json[appid={entry.get('appid')!r}] 里也没有）")
    if missing:
        print(f"拒绝 run {name}：这些字段还空着 —— {'、'.join(missing)}")
        print(f"补好 {TABLE} 再来（键序列记号见 parse_keys）")
        return 2
    problems = preflight_problems(entry)
    for p in problems:
        print(f"  拒开跑：{p}")
    if problems:
        return 2

    keep_display_awake()  # 一跑几分钟到几十分钟，合成输入不算用户活动，不压着屏幕中途就睡
    evd = new_evidence_dir(name)
    print(f"=== {name} ingame-save run {time.strftime('%H:%M:%S')} 证据 {evd} ===", flush=True)
    boot_timeout = boot_timeout_of(entry)
    boot_wait = boot_wait_of(entry)
    saves_dir = saves_dir_of(entry)
    started_wall = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    steps = {}
    distances = {}
    slots = set()
    changed_preexisting = []
    emergency = []
    labels = []   # (label, since, pgids)：收尾时按会话收守护记录（含内层残留），别人的不碰
    procs = []
    state = None
    error = None

    def step(n, status, detail="", evidence=(), machine=False):
        steps[n] = {"status": status, "detail": detail, "evidence": [str(x) for x in evidence]}
        if machine:
            steps[n]["machine"] = True
        print(f"  [{status}] {n} {detail}", flush=True)

    def retire(proc, label, since, pgids=()):
        """一次起落收尾：等守护者自己退（它退出时会删记录），超时补刀，再收残留记录。
        强杀过的那轮还要替内层守护扫尾：它随组被杀，记录没机会自删（见 reap_inner_records）。"""
        reap_guard_proc(proc)
        emergency.extend(reap_record(label, since))
        emergency.extend(reap_inner_records(pgids, label, since))

    try:
        # ── 1. 盘面留底：既有存档整份拷走，存档与游戏本体各写一份 SHA 清单 ──
        had_saves = saves_dir.is_dir()
        if had_saves:
            shutil.copytree(saves_dir, evd / "saves-before", symlinks=True)
        saves_before = manifest(saves_dir)
        (evd / "saves-before.json").write_text(
            json.dumps(saves_before, ensure_ascii=False, indent=2, sort_keys=True))
        originals_before = manifest(folder)
        (evd / "originals-before.json").write_text(
            json.dumps(originals_before, ensure_ascii=False, indent=2, sort_keys=True))
        step("before", "pass",
             f"{'有' if had_saves else '没有'}既有存档目录（{len(saves_before)} 个文件）；游戏目录 {len(originals_before)} 个文件",
             ["saves-before/", "saves-before.json", "originals-before.json"])

        # ── 2. 第一次起：在游戏里存一个测试槽 ──
        state, proc, win = do_launch(entry, name, 1, evd, boot_timeout)
        procs.append(proc)
        labels.append((state["label"], state["started"], state_pgids(state)))
        if win is None:
            step("launch_1", "fail", f"{boot_timeout}s 没等到窗口（进程组 {pgids_txt(state['pgids'])}）")
            raise RunAbort("第一次起没等到窗口")
        step("launch_1", "pass", f"窗口 {win['id']} pid {win['pid']}")
        time.sleep(boot_wait)
        refresh_window(state)  # 启动画面此刻多半已换成真窗口：重认并落盘，后面的投键/截图才对着它
        write_state(name, state)
        title1 = evd / "1-title.png"
        shoot_window(state, title1)
        send_ok, send_note = try_send(state["window_pid"], entry["save_keys"], state)
        time.sleep(2)
        saved1 = evd / "1-saved.png"
        shoot_window(state, saved1)
        saves_after_first = manifest(saves_dir)
        added, changed, _ = manifest_diff(saves_before, saves_after_first)
        slots |= set(added) | set(changed)
        if not send_ok:
            step("save_written", "fail", f"键没投出去：{send_note}", ["1-title.png", "1-saved.png"])
        elif slots:
            step("save_written", "pass", f"存档新写/改了 {len(slots)} 个（测试槽）：{sorted(slots)}",
                 ["1-title.png", "1-saved.png"])
        else:
            step("save_written", "fail", "键投了但存档清单没有新文件也没有变化，save_keys 多半没起作用",
                 ["1-title.png", "1-saved.png"])

        # ── 3. 正常退出；20s 不走就按矩阵第 2 列的语义强杀收尾 ──
        # 判据只看 20s 内退没退；强杀是善后，成不成写进说明，不改判这一步
        ok, note = quit_via_keys(state, entry)
        if not ok:
            _, k_note = force_kill_step(state)
            note += f"；强杀收尾：{k_note}"
        step("quit_normal", "pass" if ok else "fail", note)
        retire(proc, state["label"], state["started"], state_pgids(state))

        # ── 4. 第二次起：读档，和本次标题画面比、和上次存档画面比 ──
        state, proc, win = do_launch(entry, name, 2, evd, boot_timeout)
        procs.append(proc)
        labels.append((state["label"], state["started"], state_pgids(state)))
        if win is None:
            step("launch_2", "fail", f"{boot_timeout}s 没等到窗口")
            raise RunAbort("第二次起没等到窗口")
        time.sleep(boot_wait)
        refresh_window(state)  # 第二次起同样先重认：记录里的窗口 id 可能又是启动画面的
        write_state(name, state)
        title2 = evd / "2-title.png"
        shoot_window(state, title2)
        step("launch_2", "pass", f"窗口 {win['id']} pid {win['pid']}", ["2-title.png"])
        send_ok, send_note = try_send(state["window_pid"], entry["load_keys"], state)
        time.sleep(2)
        loaded2 = evd / "2-loaded.png"
        shoot_window(state, loaded2)
        shots = ["2-title.png", "1-saved.png", "2-loaded.png"]
        shots_ok = saved1.is_file() and title2.is_file() and loaded2.is_file()
        d_saved = d_title = None
        dist_error = None
        if shots_ok:
            try:
                d_saved = thumb_dist(loaded2, saved1)
                d_title = thumb_dist(loaded2, title2)
                distances["load_after_quit"] = {"d(loaded,saved)": round(d_saved, 2),
                                                "d(loaded,title)": round(d_title, 2)}
            except Exception as e:
                dist_error = f"{type(e).__name__}: {e}"
        status, detail = load_after_quit_step(send_ok, send_note,
                                              steps.get("save_written", {}).get("status"),
                                              shots_ok, d_saved, d_title, dist_error)
        step("load_after_quit", status, detail, shots, machine=send_ok and status != "unknown")

        # ── 5. 推进游戏状态再存一次，然后强杀（断电语义） ──
        # save_again_keys 没配就沿用 save_keys；配了它游戏可以覆盖第一槽或另存新槽
        if entry.get("advance_keys"):
            try_send(state["window_pid"], entry["advance_keys"], state)
        send_ok, send_note = try_send(state["window_pid"], entry.get("save_again_keys") or entry["save_keys"], state)
        time.sleep(2)
        saved2 = evd / "2-saved.png"
        shoot_window(state, saved2)
        saves_mid = manifest(saves_dir)
        a2, c2, _ = manifest_diff(saves_after_first, saves_mid)
        second_written = bool(set(a2) | set(c2))  # 第二次存档真的写盘了吗：没写就验不了「强杀前那份」
        slots |= set(a2) | set(c2)
        step("save_again", "pass" if send_ok else "fail",
             f"第二次存：{send_note}；相对第一次又新写/改 {sorted(set(a2) | set(c2)) or '无'}",
             ["2-saved.png"])
        k_ok, k_note = force_kill_step(state)
        step("force_kill", "pass" if k_ok else "fail", k_note)
        retire(proc, state["label"], state["started"], state_pgids(state))

        # ── 6. 第三次起：强杀后的盘面还能读吗，读到的是不是强杀前那份 ──
        state, proc, win = do_launch(entry, name, 3, evd, boot_timeout)
        procs.append(proc)
        labels.append((state["label"], state["started"], state_pgids(state)))
        if win is None:
            step("launch_3", "fail", f"{boot_timeout}s 没等到窗口")
            raise RunAbort("第三次起没等到窗口")
        time.sleep(boot_wait)
        refresh_window(state)  # 第三次起同样先重认，截图/投键都对着此刻的真窗口
        write_state(name, state)
        title3 = evd / "3-title.png"
        shoot_window(state, title3)
        step("launch_3", "pass", f"窗口 {win['id']} pid {win['pid']}", ["3-title.png"])
        # load_latest_keys 没配就沿用 load_keys；配了它游戏读最新的槽——强杀前那份才算数
        send_ok, send_note = try_send(state["window_pid"], entry.get("load_latest_keys") or entry["load_keys"], state)
        time.sleep(2)
        loaded3 = evd / "3-loaded.png"
        shoot_window(state, loaded3)
        shots = ["3-title.png", "1-saved.png", "2-saved.png", "3-loaded.png"]
        shots_ok = saved1.is_file() and saved2.is_file() and title3.is_file() and loaded3.is_file()
        d_saved2 = d_saved1 = d_title = d_two = None
        dist_error = None
        if shots_ok:
            try:
                d_saved2 = thumb_dist(loaded3, saved2)
                d_saved1 = thumb_dist(loaded3, saved1)
                d_title = thumb_dist(loaded3, title3)
                d_two = thumb_dist(saved1, saved2)
                distances["load_after_kill"] = {"d(loaded,saved2)": round(d_saved2, 2),
                                                "d(loaded,saved1)": round(d_saved1, 2),
                                                "d(loaded,title)": round(d_title, 2),
                                                "d(saved1,saved2)": round(d_two, 2)}
            except Exception as e:
                dist_error = f"{type(e).__name__}: {e}"
        status, verdict = load_after_kill_step(send_ok, send_note, second_written, shots_ok,
                                               d_saved2, d_saved1, d_title, d_two, dist_error)
        step("load_after_kill", status, verdict, shots, machine=send_ok and status != "unknown")
        ok, note = quit_via_keys(state, entry)
        if not ok:
            _, k_note = force_kill_step(state)
            note += f"；强杀收尾：{k_note}"
        step("quit_final", "pass" if ok else "fail", note)
        retire(proc, state["label"], state["started"], state_pgids(state))
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        step("exception", "fail", f"步骤 2–6 抛了 {error}；先收本会话的场，再继续盘面步骤")
        # 收场只收本会话的东西：还活着的组强杀，自己这轮的守护记录收掉
        live = state_pgids(state) if state else []
        if live and not group_gone(live, 0):
            k_ok, k_note = force_kill_step(state)
            emergency.append(f"强杀本会话 pgid {pgids_txt(live)}：{k_note if not k_ok else '组已消失'}")
        for p in procs:
            reap_guard_proc(p)
        for lbl, since, pg in labels:
            emergency.extend(reap_record(lbl, since))
            emergency.extend(reap_inner_records(pg, lbl, since))

    # ── 7. 原始目录没动、测试槽之外的旧存档没动 ──
    try:
        originals_after = manifest(folder)
        (evd / "originals-after.json").write_text(
            json.dumps(originals_after, ensure_ascii=False, indent=2, sort_keys=True))
        o_add, o_chg, o_rem = manifest_diff(originals_before, originals_after)
        bad = o_add + o_chg + o_rem
        step("originals_unchanged", "pass" if not bad else "fail",
             "游戏目录逐字节未变" if not bad else f"游戏目录变了：新增 {o_add} 改动 {o_chg} 缺失 {o_rem}",
             ["originals-before.json", "originals-after.json"])
    except Exception as e:
        step("originals_unchanged", "unknown", f"比不了：{type(e).__name__}: {e}")

    try:
        saves_after = manifest(saves_dir)
        (evd / "saves-after.json").write_text(
            json.dumps(saves_after, ensure_ascii=False, indent=2, sort_keys=True))
        touched, extra = split_old_vs_slots(saves_before, saves_after, slots)
        changed_preexisting = touched
        if touched:
            step("old_saves_unchanged", "unknown",
                 f"测试槽外的旧文件变了（游戏自己的日志/设置之类，待人看）：{touched}"
                 + (f"；另有槽外新增 {extra}" if extra else ""),
                 ["saves-before.json", "saves-after.json"])
        else:
            step("old_saves_unchanged", "pass",
                 "测试槽外的旧存档逐字节未变" + (f"；另有槽外新增 {extra}" if extra else ""),
                 ["saves-before.json", "saves-after.json"])
    except Exception as e:
        step("old_saves_unchanged", "unknown", f"比不了：{type(e).__name__}: {e}")

    # ── 8. 恢复盘面：测后存档挪进证据目录，备份放回原位，再对一遍清单 ──
    try:
        notes = restore_saves(saves_dir, evd / "saves-before", evd, time.strftime("%H%M%S"), had_saves)
        if had_saves:
            r_add, r_chg, r_rem = manifest_diff(saves_before, manifest(saves_dir))
            good = not (r_add or r_chg or r_rem)
            detail = ("备份已原样放回" if good else
                      f"放回后与 saves-before.json 对不上：新增 {r_add} 改动 {r_chg} 缺失 {r_rem}")
        else:
            good = True
            detail = "原来没有存档目录：只把游戏新建的挪进证据目录，不往回放"
        step("restored", "pass" if good else "fail", "；".join([detail] + notes),
             ["saves-before/", "saves-after/"])
    except Exception as e:
        step("restored", "fail",
             f"恢复抛了异常（只挪旁不删，盘上留有可查的东西）：{type(e).__name__}: {e}")

    # ── 9. 结论与报告 ──
    statuses = [s["status"] for s in steps.values()]
    if "fail" in statuses:
        conclusion = "有失败"
    elif "unknown" in statuses:
        conclusion = "待人看截图"
    else:
        conclusion = "机器判据全过"
    why = ""
    if conclusion == "有失败":
        why = "失败步骤：" + "、".join(n for n, s in steps.items() if s["status"] == "fail")
    elif conclusion == "待人看截图":
        why = "拿不准、要人看的步骤：" + "、".join(n for n, s in steps.items() if s["status"] == "unknown")
    result = {
        "game": name, "appid": entry.get("appid"),
        "started": started_wall, "finished": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "evidence_dir": str(evd), "conclusion": conclusion, "why": why,
        "error": error, "steps": steps, "distances": distances,
        "slots": sorted(slots), "changed_preexisting": changed_preexisting,
        "emergency_cleanup": emergency,
    }
    (evd / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    write_report(evd, result)
    print(f"=== 结论：{conclusion}{'，' + why if why else ''} ===", flush=True)
    print(f"证据与报告：{evd}", flush=True)
    return 0 if conclusion != "有失败" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("up", help="拉起游戏、等窗口、截图、落状态文件")
    p.add_argument("game")
    p = sub.add_parser("keys", help="往窗口 pid 投一段按键序列")
    p.add_argument("game")
    p.add_argument("sequence")
    p = sub.add_parser("shot", help="按窗口 id 截图进本次运行证据目录")
    p.add_argument("game")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("down", help="cmd+q 正常退；--hard 对记录的 pgid 发 SIGKILL")
    p.add_argument("game")
    p.add_argument("--hard", action="store_true")
    p = sub.add_parser("run", help="全自动：存→正常退→读→强杀→读→恢复盘面")
    p.add_argument("game")
    a = ap.parse_args(argv)
    if a.cmd == "up":
        return cmd_up(a.game)
    if a.cmd == "keys":
        return cmd_keys(a.game, a.sequence)
    if a.cmd == "shot":
        return cmd_shot(a.game, a.name)
    if a.cmd == "down":
        return cmd_down(a.game, a.hard)
    return cmd_run(a.game)


if __name__ == "__main__":
    raise SystemExit(main())
