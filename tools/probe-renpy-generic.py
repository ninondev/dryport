#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""技术实验：用已备的原厂 Ren'Py 8.1.3 mac 二进制（librenpython + pythonw）直接跑一款非 Gun-Dog 的 Ren'Py 游戏原件，
看能不能出画面。不进产品、不改游戏原件、存档与日志落在临时状态目录。目的是给「Ren'Py 路线放开到同 major.minor 版本」提供证据。

  tools/probe-renpy-generic.py <游戏目录> [--seconds 45] [--out 目录]
输出 result.json：游戏声明的 Ren'Py 版本、入口脚本、窗口、截图统计、stderr 尾部。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_RUNTIMES, SN_GAMES   # noqa: E402
import evidence                            # noqa: E402

RUNTIME = SN_RUNTIMES / "renpy" / "8.1.3.23091805" / "Runtime"
PYTHONW = RUNTIME / "lib" / "py3-mac-universal" / "pythonw"
IMAGE_STATS = SN_RUNTIMES / "bin" / "image-stats"


def game_version(game):
    for rel in ("renpy/vc_version.py", "renpy/__init__.py"):
        p = game / rel
        if p.is_file():
            text = p.read_text(errors="replace")
            m = re.search(r"version_tuple\s*=\s*\(([^)]*)\)", text) or re.search(r'version\s*=\s*"([^"]+)"', text)
            if m:
                return m.group(1).strip()
    return None


def entry_script(game):
    for p in sorted(game.glob("*.py")):
        if (game / (p.stem + ".exe")).exists() or (game / (p.stem + ".sh")).exists() or (game / "renpy").is_dir():
            return p
    return None


def stats(png):
    try:
        return json.loads(subprocess.run([str(IMAGE_STATS), str(png)], capture_output=True, text=True, timeout=60).stdout)
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("game"); ap.add_argument("--seconds", type=float, default=45); ap.add_argument("--out")
    a = ap.parse_args(argv)
    game = Path(a.game).resolve()
    entry = entry_script(game)
    ver = game_version(game)
    out = Path(a.out) if a.out else SN_GAMES / "dryport-deliveries" / time.strftime("%Y-%m-%d") / "renpy-generic" / game.name
    out.mkdir(parents=True, exist_ok=True)
    result = {"game": str(game), "entry": str(entry) if entry else None, "declared_version": ver, "runtime": str(RUNTIME)}
    if not entry or not PYTHONW.is_file():
        result["verdict"] = "no_entry_or_runtime"
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1)); print(json.dumps(result, ensure_ascii=False)); return 1
    state = Path(tempfile.mkdtemp(prefix="sn-renpy-probe-", dir=SN_RUNTIMES))
    for d in ("home", "tmp", "saves", "renpy"):
        (state / d).mkdir()
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8", "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "RENPY_PATH_TO_SAVES": str(state / "renpy"),
           "RENPY_LOG_TO_STDOUT": "1", "RENPY_MULTIPERSISTENT": str(state / "multipersistent")}
    evidence.ensure_window_list()
    base = {w["id"] for w in evidence.windows()}
    label = f"renpy-probe-{game.name[:20]}"
    cmd = [sys.executable, str(ROOT / "tools" / "run-guard.py"), "--name", label, "--max-seconds", str(a.seconds + 60),
           "--log", str(out / "launcher.log"), "--", str(PYTHONW), str(entry), "--savedir", str(state / "saves"), str(game)]
    t0 = time.time()
    proc = subprocess.Popen(cmd, env={**os.environ, **env}, cwd=str(game), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    win = None
    while time.time() - t0 < 60:
        fresh = evidence.new_windows(base)
        if fresh:
            win = fresh[0]; break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    result["window_at"] = round(time.time() - t0, 1) if win else None
    result["shots"] = []
    if win:
        for wait, tag in ((5, "t05"), (a.seconds, "end")):
            while time.time() - t0 - result["window_at"] < wait and proc.poll() is None:
                time.sleep(0.5)
            dest = out / f"{tag}.png"
            if evidence.shoot(win["id"], dest):
                result["shots"].append({"tag": tag, "stats": stats(dest)})
    result["exit_code_before_stop"] = proc.poll()
    recs = evidence.guard_records(label, t0 - 1)
    if recs:
        import signal
        try:
            os.kill(int(recs[0]["pid"]), signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    evidence.reap_record(label, t0 - 1)
    log = out / "launcher.log"
    result["stderr_tail"] = log.read_text(errors="replace")[-1500:] if log.is_file() else ""
    blank = [s for s in result["shots"] if s["stats"] and (s["stats"]["colors"] < 30 or s["stats"]["dominant_fraction"] >= 0.97)]
    result["verdict"] = "no_window" if not win else ("blank" if result["shots"] and len(blank) == len(result["shots"]) else "picture")
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({k: result.get(k) for k in ("game", "declared_version", "verdict", "window_at", "exit_code_before_stop")}, ensure_ascii=False))
    import shutil
    shutil.rmtree(state, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
