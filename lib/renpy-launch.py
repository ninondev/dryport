#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""由官方 Python 执行的独立启动层；游戏代码和 Steam 初始化仍走原入口。"""
import importlib
import ctypes
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

VERSION = "8.1.3.23091805"
APPID = "2118420"
DIRECTORIES = ("home", "tmp", "saves", "renpy", "multipersistent", "screenshots", "logs")
SHARED = importlib.import_module("love-runtime")
publish = SHARED.publish_new
sandbox = SHARED.sandbox


def native_bootstrap_verified(app):
    try:
        check = ctypes.CDLL(None).sn_renpy_bootstrap_verified
    except AttributeError:
        return False
    check.argtypes = [ctypes.c_char_p]
    check.restype = ctypes.c_int
    return check(os.fsencode(app)) == 1


def state_directory(root, protected):
    root = Path(root).expanduser().resolve()
    state = root / APPID
    for path in protected:
        if state == path or state.is_relative_to(path) or path.is_relative_to(state):
            raise ValueError("存档目录与原件、app 或运行时重叠。")
    owner = {"protocol": 1, "kind": "dryport-renpy", "appid": APPID}
    marker = state / ".dryport-renpy.json"
    root.mkdir(parents=True, exist_ok=True)
    identity = (root.stat().st_dev, root.stat().st_ino)

    def guard_parent():
        if (root.resolve(strict=True) != root
                or (root.stat().st_dev, root.stat().st_ino) != identity):
            raise ValueError("状态父目录在准备期间变化，未继续发布。")
        if any(state.is_relative_to(path.resolve()) or path.resolve().is_relative_to(state)
               for path in protected):
            raise ValueError("状态路径进入保护目录，未继续发布。")

    guard_parent()
    if os.path.lexists(state):
        if state.is_symlink() or not state.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError("存档位置已有未识别内容，已保留：" + str(state))
        if json.loads(marker.read_text()) != owner:
            raise ValueError("存档目录归属不符，已有内容保留。")
        for name in DIRECTORIES:
            path = state / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError("存档目录结构已变化：" + str(path))
    else:
        stage = Path(tempfile.mkdtemp(prefix=".renpy-state-", dir=root))
        for name in DIRECTORIES:
            guard_parent()
            (stage / name).mkdir(mode=0o700)
        (stage / marker.name).write_text(json.dumps(owner), encoding="utf-8")
        guard_parent()
        publish(stage, state)
    guard_parent()
    return state


def prepare(app, state_root=None):
    app = Path(app).resolve(strict=True)
    preverified = native_bootstrap_verified(app)
    if not preverified:
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True, timeout=15)
    resources = app / "Contents/Resources"
    config = json.loads((resources / "renpy-launch.json").read_text())
    if (config.get("protocol") != 2 or config.get("appid") != APPID
            or config.get("version") != VERSION or config.get("entry") != "gundog.py"):
        raise ValueError("启动配置不是当前支持的 Gun-Dog 版本。")
    original = resources / "autorun"
    if original.is_symlink() or not (original / "gundog.py").is_file():
        raise ValueError("完整原件副本或入口缺失。")
    runtime = Path(config["runtime"]).resolve(strict=True)
    binaries = runtime / "lib/py3-mac-universal"
    if not preverified:
        for name in ("pythonw", "librenpython.dylib", "libsteam_api.dylib"):
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--architecture", "arm64",
                            str(binaries / name)], check=True, timeout=10)
    protected = [app, runtime, Path(config["source"]).resolve(), Path(config["repo"]).resolve()]
    state = state_directory(state_root or config["state_root"], protected)
    # AppID 是逐游戏的数据；共享运行时不能存上一款游戏的身份。
    appid_file = state / "steam_appid.txt"
    if os.path.lexists(appid_file):
        if appid_file.is_symlink() or not appid_file.is_file() or appid_file.read_text().strip() != APPID:
            raise ValueError("本游戏状态中的 AppID 不符，未覆盖。")
    else:
        with appid_file.open("x") as stream:
            stream.write(APPID + "\n")
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + str(os.getpid())
    logs = state / "logs" / run_id
    logs.mkdir(mode=0o700)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": os.environ.get("LANG") or "en_US.UTF-8",
           "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "RENPY_PATH_TO_SAVES": str(state / "renpy"), "RENPY_MULTIPERSISTENT": str(state / "multipersistent"),
           "RENPY_SCREENSHOT_PATTERN": str(state / "screenshots/screenshot%04d.png"),
           "RENPY_SEARCHPATH": str(resources / "extensions"), "RENPY_LOG_TO_STDOUT": "1",
           "SN_RENPY_AUTORUN": str(original), "SN_RENPY_SAVES": str(state / "saves"),
           "SN_RENPY_LOGDIR": str(logs), "SN_RENPY_FIRST_RUN": "0" if (state / "saves/persistent").exists() else "1"}
    command = sandbox(state, protected + [appid_file, state / ".dryport-renpy.json"])
    # sandbox 前后均回到包内主映像，原厂入口在内部继续使用共享 pythonw 的路径定位标准库。
    command += [str(app / "Contents/MacOS/launcher"), "--engine"]
    return {"command": command, "env": env, "cwd": state, "logs": logs, "state": state}


def main():
    if len(sys.argv) != 2:
        raise ValueError("启动器只接受自己所属的 app 路径。")
    launch = prepare(sys.argv[1])
    os.chdir(launch["cwd"])
    # 各轮日志独立留存；应用进程直接成为引擎进程，不常驻一个 Python 轮询器。
    with (launch["logs"] / "stdout.log").open("xb") as stdout, (launch["logs"] / "stderr.log").open("xb") as stderr:
        os.dup2(stdout.fileno(), 1)
        os.dup2(stderr.fileno(), 2)
    os.execve(launch["command"][0], launch["command"], launch["env"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("Ren’Py 未启动：" + str(error), file=sys.stderr)
        raise SystemExit(1)
