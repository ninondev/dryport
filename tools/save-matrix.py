#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自动跑存档可靠性矩阵三列，验的是**既有存档**（多数是游戏自己的自动存档）。

为什么是这个形状：矩阵原本要求「进游戏 → 存新槽」，但合成键盘事件在 mkxp-z 与 Ren'Py 上
都不可靠，因此只检查已有存档。此检查不验证手动存入新槽。
存档已经在盘上，要验的其实是「它在三种操作之后还能不能读」，这条不需要任何输入注入。

比截图更硬的判据：存档文件按各自格式真解一遍。Ren'Py 的 .save 是 zip，能列出条目就是完整的；
plist 能被 plutil 解析；sqlite 能 `PRAGMA integrity_check`。SHA 只证「没变」，解析才证「能读」。

  tools/save-matrix.py --game gun-dog --column 1
  tools/save-matrix.py --game gun-dog --column all

游戏定义在 GAMES 里。每格证据落到
~/Games/dryport-deliveries/<日期>/save-matrix/<游戏>/0<列>-<名>/。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import zipfile

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent
RUNTIMES = HOME / "Games" / "runtimes"
TODAY = time.strftime("%Y-%m-%d")
EVID = HOME / "Games" / "dryport-deliveries" / TODAY / "save-matrix"
SN_RUNNING = HOME / "Games" / ".running"

# owner 名是窗口在 WindowServer 里的名字，不一定等于游戏名（mkxp-z 的就是 "mkxp-z"，Wine 的一律是 "wine"）。
GAMES = {
    "gun-dog": {
        "appid": "2118420",
        "app": HOME / "Games" / "Gun-Dog.app",
        "owner": "Gun-Dog",
        "saves": HOME / "Games" / ".renpy-state" / "2118420" / "saves",
        "engine": "renpy",
    },
    "a-space": {
        "appid": "1201270",
        "app": HOME / "Games" / "A Space for the Unbound.app",
        "owner": "A Space for the Unbound",
        "saves": HOME / "Library" / "Application Support" / "Mojiken" / "A Space for the Unbound",
        "engine": "unity",
    },
}

# Private presets are optional; public presets contain only named examples.
_private_presets = REPO / "docs/internal/save-matrix-games.json"
if _private_presets.is_file():
    for key, entry in json.loads(_private_presets.read_text()).items():
        GAMES[key] = {**entry, "app": HOME / entry["app"], "saves": HOME / entry["saves"]}

# 这些是运行时产物，每次跑都会变，拿它们算指纹会让每一格都「失败」
VOLATILE = ("Player.log", "Player-prev.log", ".savebridge.log", ".savebridge-last.json",
            "output_log.txt", "unity.player_sessionid")

# Unity 的遥测目录不是存档，里面本来就有空文件（Analytics/config），
# 拿它当存档校验会让好端端的一格判成失败。整个子树排除。
EXCLUDE_PARTS = ("Analytics", "logs", "Unity")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest()


def fingerprint(root: Path) -> dict:
    """存档目录里每个非易变文件的 SHA。目录不存在返回空字典，调用方据此判「存档没了」。"""
    out = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name in VOLATILE:
            continue
        if any(part in EXCLUDE_PARTS for part in p.relative_to(root).parts):
            continue
        out[str(p.relative_to(root))] = sha(p)
    return out


def readable(path: Path) -> tuple:
    """按格式真解一遍。返回 (能不能读, 说明)。SHA 只证没变，这里才证能读。"""
    n = path.name.lower()
    try:
        if n.endswith(".save") or zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                bad = z.testzip()
                if bad:
                    return False, f"zip 条目 {bad} 损坏"
                return True, f"zip {len(names)} 条目: {','.join(names[:4])}"
        if n.endswith(".plist"):
            with open(path, "rb") as f:
                d = plistlib.load(f)
            return True, f"plist {len(d)} 键"
        if n.endswith((".sqlite3", ".sqlite", ".db")):
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            r = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            return r == "ok", f"sqlite integrity_check={r}"
        data = path.read_bytes()
        if not data:
            return False, "空文件"
        # entry-03 的 .sav 是裸 JSON
        if data.lstrip()[:1] in (b"{", b"["):
            import json as _j
            d = _j.loads(data)
            return True, f"JSON {len(d)} 键: {','.join(list(d)[:4])}" if isinstance(d, dict) else f"JSON 数组 {len(d)} 项"
        # A Space 的 MainData.sav 是 .NET BinaryFormatter：头 SerializationHeaderRecord，尾 MessageEnd(0x0B)
        if data[:5] == b"\x00\x01\x00\x00\x00" and data[-1:] == b"\x0b":
            return True, f"BinaryFormatter 流 {len(data)} 字节，头尾记录完整"
        # Ren'Py 的 persistent 是 zlib 压缩的 pickle
        if data[:2] == b"\x78\x9c":
            import zlib
            return True, f"zlib 解出 {len(zlib.decompress(data))} 字节"
        return True, f"{len(data)} 字节（无格式校验器，只证非空）"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def integrity(root: Path) -> list:
    rows = []
    if not root.is_dir():
        return rows
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name in VOLATILE:
            continue
        if any(part in EXCLUDE_PARTS for part in p.relative_to(root).parts):
            continue
        ok, why = readable(p)
        rows.append({"file": str(p.relative_to(root)), "readable": ok, "detail": why})
    return rows


def windows(owner: str) -> list:
    exe = RUNTIMES / "bin" / "window-list"
    if not exe.exists():
        return []
    try:
        out = subprocess.run([str(exe), owner], capture_output=True, text=True, timeout=10)
        return json.loads(out.stdout or "[]")
    except Exception:
        return []


def shoot(owner: str, dest: Path) -> str:
    """按窗口 id 截图。全屏截图会被别的窗口盖住，也会把无关内容带进证据。"""
    for name in (owner, "wine", ""):
        ws = windows(name)
        if ws:
            wid = ws[0]["id"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), str(dest)],
                           capture_output=True, timeout=20)
            if dest.exists():
                return f"{dest.name}（窗口 {wid} owner={ws[0]['owner']} {ws[0]['bounds']['Width']}x{ws[0]['bounds']['Height']}）"
    return "没找到窗口，未截图"


def launch(g: dict, label: str, seconds: float, logdir: Path):
    """经 run-guard 拉起，返回 (守护者 Popen, 游戏 pid 或 None)。守护者保证会话断了也能收干净。"""
    logdir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(REPO / "tools" / "run-guard.py"),
           "--name", label, "--max-seconds", str(seconds + 60),
           "--log", str(logdir / f"{label}.log"),
           "--", str(g["app"] / "Contents" / "MacOS" / "launcher")]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = False
    for _ in range(int(seconds * 2)):
        time.sleep(0.5)
        if not got and (windows(g["owner"]) or windows("wine")):
            got = True
            break
    return proc, got


def guarded_pgid(label: str, since: float = 0.0):
    """从 run-guard 的记录里读被守护进程的真实进程组。

    这一步是 2026-09-13 09:50 踩出来的：原先用 os.getpgid(守护者.pid) 当进程组去 killpg，
    但 run-guard 给子进程开了新组，那个 pgid 其实是本脚本自己的组 ——
    既没打到游戏（Gun-Dog 活下来成了失联进程），又差点打到自己。
    记录文件是唯一可靠的来源。"""
    d = SN_RUNNING
    if not d.is_dir():
        return None
    for f in d.glob("*.json"):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if rec.get("name") != label:
            continue
        # 陈旧记录（前一次运行崩了没删）里的 pgid 可能已被系统回收给别的进程，
        # 按它去 killpg 就会打到无辜进程。只认本次启动之后写的记录。
        if rec.get("started", 0) < since:
            continue
        return rec.get("pgid")
    return None


def stop(proc, hard: bool, label: str = "", since: float = 0.0):
    """hard=True 用 SIGKILL 打游戏本体的进程组（矩阵第 2 列的断电语义），否则让守护者正常收。"""
    if hard:
        pgid = guarded_pgid(label, since)
        if pgid and pgid != os.getpgid(0):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            raise RuntimeError(f"拿不到 {label} 的子进程组（记录里是 {pgid}，本进程组 {os.getpgid(0)}），"
                               "拒绝乱杀")
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def reap_record(label: str, since: float):
    """收掉自己这轮留下的 run-guard 记录（仅限进程确已死的）。

    2026-09-13 09:52 观察到：强杀那一格结束后，守护者与子进程都已死，
    记录文件却还在 ~/Games/.running/，`dryport running` 因此报一条假的「失联」。
    run-guard 的删除动作在 finally 里，按理该执行，所以这是它的一个待查缺陷；
    这里只清自己制造的那条，不动别人的记录。"""
    if not SN_RUNNING.is_dir():
        return None
    for f in SN_RUNNING.glob("*.json"):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if rec.get("name") != label or rec.get("started", 0) < since:
            continue
        alive = []
        for key in ("pid", "guard_pid"):
            try:
                os.kill(rec[key], 0)
                alive.append(rec[key])
            except (ProcessLookupError, KeyError):
                pass
            except PermissionError:
                alive.append(rec[key])
        if alive:
            return f"记录 {f.name} 里还有活进程 {alive}，不删"
        f.unlink()
        return f"清掉了残留记录 {f.name}（进程已全死）"
    return None


def leftovers(g: dict) -> list:
    out = subprocess.run(["pgrep", "-fl", g["app"].stem], capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if "pgrep" not in l]


def run_column(name: str, g: dict, col: int, dwell: float) -> dict:
    colname = {1: "normal", 2: "sigkill", 3: "archive-restore"}[col]
    d = EVID / name / f"0{col}-{colname}"
    d.mkdir(parents=True, exist_ok=True)
    r = {"game": name, "column": col, "column_name": colname,
         "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "saves_root": str(g["saves"])}

    before = fingerprint(g["saves"])
    r["files_before"] = len(before)
    if not before:
        r["verdict"] = "无存档"
        r["note"] = "存档目录为空或不存在，这一格没有可验对象"
        (d / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=2))
        return r
    r["integrity_before"] = integrity(g["saves"])

    if col == 3:
        # 不起游戏，直接验归档恢复这条链
        sn = str(REPO / "bin" / "dryport")
        a = subprocess.run([sn, "archive", g["appid"], "--yes"], capture_output=True, text=True, timeout=900)
        r["archive_rc"], r["archive_tail"] = a.returncode, a.stdout[-600:] + a.stderr[-400:]
        b = subprocess.run([sn, "restore", g["appid"], "--yes"], capture_output=True, text=True, timeout=900)
        r["restore_rc"], r["restore_tail"] = b.returncode, b.stdout[-600:] + b.stderr[-400:]
    else:
        label = f"savematrix-{name}-c{col}"
        t0 = time.time()
        proc, got_window = launch(g, label, 45, d)
        r["window_appeared"] = got_window
        if got_window:
            time.sleep(dwell)
            r["screenshot_running"] = shoot(g["owner"], d / "01-running.png")
        stop(proc, hard=(col == 2), label=label, since=t0)
        r["stop_mode"] = "SIGKILL 游戏进程组" if col == 2 else "守护者正常收"
        time.sleep(3)
        r["leftovers_after_stop"] = leftovers(g)
        r["stale_record"] = reap_record(label, t0)

    after_op = fingerprint(g["saves"])
    r["files_after_op"] = len(after_op)

    # 重开，验存档还在且游戏认它
    label2 = f"savematrix-{name}-c{col}-reopen"
    t2 = time.time()
    proc2, got2 = launch(g, label2, 45, d)
    r["reopen_window"] = got2
    if got2:
        time.sleep(dwell)
        r["screenshot_reopen"] = shoot(g["owner"], d / "02-reopen.png")
    stop(proc2, hard=False, label=label2, since=t2)
    time.sleep(3)
    r["leftovers_final"] = leftovers(g)
    r["stale_record_reopen"] = reap_record(label2, t2)

    after = fingerprint(g["saves"])
    r["files_after"] = len(after)
    r["integrity_after"] = integrity(g["saves"])

    lost = sorted(set(before) - set(after))
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    added = sorted(set(after) - set(before))
    r["lost"], r["changed"], r["added"] = lost, changed, added
    unreadable = [x["file"] for x in r["integrity_after"] if not x["readable"]]
    r["unreadable_after"] = unreadable

    if lost:
        r["verdict"] = "失败"
        r["why"] = f"{len(lost)} 个存档文件不见了：{lost[:5]}"
    elif unreadable:
        r["verdict"] = "失败"
        r["why"] = f"{len(unreadable)} 个存档文件解不开：{unreadable[:5]}"
    else:
        r["verdict"] = "通过"
        r["why"] = (f"{len(before)} 个存档文件全部还在且能按格式解开"
                    + (f"；游戏改写了 {len(changed)} 个、新增 {len(added)} 个，这是正常写入" if (changed or added) else "；逐字节未变"))
    (d / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, choices=sorted(GAMES) + ["all"])
    ap.add_argument("--column", default="all", help="1 / 2 / 3 / all")
    ap.add_argument("--dwell", type=float, default=25.0, help="窗口出来后停留几秒再动作")
    a = ap.parse_args()

    names = sorted(GAMES) if a.game == "all" else [a.game]
    cols = [1, 2, 3] if a.column == "all" else [int(a.column)]
    results = []
    for n in names:
        g = GAMES[n]
        if not g["app"].is_dir():
            print(f"  跳过 {n}：{g['app']} 不在")
            continue
        for c in cols:
            print(f"\n=== {n} 第 {c} 列 {time.strftime('%H:%M:%S')} ===", flush=True)
            r = run_column(n, g, c, a.dwell)
            results.append(r)
            print(f"  判定：{r['verdict']}  {r.get('why') or r.get('note','')}", flush=True)
            if r.get("leftovers_final"):
                print(f"  !! 有残留进程：{r['leftovers_final']}", flush=True)
    print("\n=== 汇总 ===")
    for r in results:
        print(f"  {r['game']:<14} 第{r['column']}列 {r['column_name']:<16} {r['verdict']}")
    return 0 if all(r["verdict"] != "失败" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
