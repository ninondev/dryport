#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自动冒烟一款游戏：安装（不入库）→ 守护起进程 → 判窗口 / 崩溃 / 活性 → 截图与日志进证据目录 → 写回 $SN_GAMES/.smoke/<appid>.json → 删掉本次运行创建的东西。

  tools/smoke-run.py <appid> [--dwell 60] [--keep] [--input] [--no-idle-guard] [--window-wait N]
  tools/smoke-run.py <appid> --no-install --app <已装的 .app> [--keep]   # 只起、判、截图，不装不删（验判据用）

判据只有一档：verdict=alive 表示「出了窗口、活满观察期、没有崩溃信号」，即「自动冒烟通过」。它不等于可玩，
更不等于达到 Windows 水平（docs/终局共识-2026-09-14.md）；操作、存档、成就都要另外验。其它 verdict：
  install_failed:<step>  风险闸 / 下载 / 无路线 / 打包 / DRM / unity_player_missing
  launch_failed          起不来（run-guard 126）或 10 秒内退出且没出窗口
  no_window              进程活着但等到上限也没有新窗口
  crashed                winedbg 出现、日志有 Unhandled exception、macOS 崩溃报告、被信号杀、缺 DLL 秒退
  exited_early           出了窗口但观察期内正常退出（退出码 0）
  blank_window           窗口在、进程活着，但三张截图都是整幅纯色（白/黑），画面没出来
  static_window          窗口在、活满观察期，但「活着」的信号不够两个（帧数、截图变化、投键反应、画面有结构），要人看
flags（正交附注）：static（截图全同）、blank_frames（窗口服务器 0 帧且截图极小）、dialog_seen（小窗口）、fullscreen_seen、
  missing_dll、unclean_exit、input_changed|input_no_change|input_untested|input_closed_window、small_window（本体窗口 < 500x360，按标题认的）。

运行约束：不用 open -a；一切经 tools/run-guard.py 起，窗口化，观察期有上限；
收尾先 TERM 壳 pid，再 wineserver -k 兜底，复核 .running 记录与流浪 Wine 进程后才删 prefix。
清理只删「开跑前不存在」的路径，用户已有的目录、存档一律不动。
"""
import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_HOME, SN_RUNTIMES, SN_GAMES   # noqa: E402
import evidence                                    # noqa: E402

CLI = SN_HOME / "bin" / "dryport"
RUN_GUARD = SN_HOME / "tools" / "run-guard.py"
FRAME_PROBE = SN_RUNTIMES / "bin" / "frame-probe"
KEYSEND = SN_RUNTIMES / "bin" / "keysend"
IMAGE_STATS = SN_RUNTIMES / "bin" / "image-stats"
SMOKE_DIR = SN_GAMES / ".smoke"
DELIVERIES = SN_GAMES / "dryport-deliveries"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "dryport"
WINE_DEBUG = "-all,+loaddll,err+seh"   # +seh 全开每轮几十 KB 的展开栈，只留错误级

# 目录 route → 冒烟怎么等窗口。Wine 首启要建 prefix（30–115 秒才出窗口），原生路线几秒。
WINE_ROUTES = {"wine", "unknown", "maybe", "vn"}


def log(msg):
    print(f"[smoke] {msg}", flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def git_rev():
    try:
        return sh(["git", "-C", str(SN_HOME), "rev-parse", "--short", "HEAD"], timeout=10).stdout.strip()
    except Exception:
        return None


def catalog_row(appid):
    """目录里的这一行（名字、route、engine）；目录读不到就退化成 appid。"""
    try:
        import api
        ctx = api.catalog_context()
        for g in api.all_routes(ctx):
            if str(g.get("appid")) == appid:
                return g
    except Exception as error:
        log(f"目录读不到（{error}），只按 appid 记")
    return {"appid": appid, "name": f"app_{appid}", "route": None, "engine": None}


def safe_name(name):
    out = re.sub(r"[/:\x00-\x1f]", " ", name).strip()
    return out[:60] or "game"


def bundle_exec(app):
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    return info.get("CFBundleExecutable"), info.get("CFBundleName") or app.stem, info.get("CFBundleIdentifier")


def wine_launch(app):
    p = app / "Contents" / "Resources" / "wine-launch.json"
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def disk_free():
    return shutil.disk_usage(str(SN_GAMES)).free


def ensure_frame_probe():
    """tools/frame-probe.swift 比 $SN_RUNTIMES/bin/frame-probe 新（或后者不存在）就重编；编不出来返回 False。"""
    src = SN_HOME / "tools" / "frame-probe.swift"
    if not src.is_file():
        return FRAME_PROBE.is_file()
    if not FRAME_PROBE.is_file() or FRAME_PROBE.stat().st_mtime < src.stat().st_mtime:
        FRAME_PROBE.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["swiftc", "-O", str(src), "-o", str(FRAME_PROBE)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            log(f"frame-probe 编译失败：{r.stderr.strip()[:200]}")
    return FRAME_PROBE.is_file()


def image_stats(png):
    """截图的主色占比 / 量化颜色数 / 平均亮度（tools/image-stats.swift）。工具缺失返回 None。"""
    if not IMAGE_STATS.is_file():
        src = SN_HOME / "tools" / "image-stats.swift"
        if src.is_file():
            subprocess.run(["swiftc", "-O", str(src), "-o", str(IMAGE_STATS)], capture_output=True, timeout=300)
    if not IMAGE_STATS.is_file():
        return None
    try:
        return json.loads(sh([str(IMAGE_STATS), str(png)], timeout=60).stdout or "null")
    except Exception:
        return None


def looks_blank(stats):
    """整幅纯色：颜色数不超过 4，或一种颜色占 99% 以上（一款 Wine 游戏 全白：2 色）。
    颜色少但有内容的画面不算（一款像素游戏 三色 11 色、一款像素游戏 灰白 9 色都是真画面），所以不能再按「<30 色」判。"""
    if not stats:
        return False
    return stats.get("colors", 999) <= 4 or stats.get("dominant_fraction", 0) >= 0.99


def _norm(text):
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def small_game_window(w, names):
    """500x360 以下的窗口默认当对话框，但像素风游戏的本体窗口可以只有 480x270（PIXELFIXTURE 2026-09-14：488x304 被当成对话框记 no_window）。
    ≥ 300x200 且标题就是游戏名/app 名（去掉大小写与标点后相等或以它开头）的，认作游戏窗口，加 small_window 标记；
    错误框标题也可能就是游戏名（RGSS），所以随后按 Return 把窗口按没了不算输入信号（input_closed_window）。"""
    b = w.get("bounds") or {}
    if b.get("Width", 0) < 300 or b.get("Height", 0) < 200:
        return False
    title = _norm(w.get("title"))
    if not title:
        return False
    for n in names:
        n = _norm(n)
        if len(n) >= 3 and (title == n or title.startswith(n)):
            return True
    return False


def liveness_signals(frames, shots, flags):
    """画面「活着」的独立信号，凑够两个才算 alive：窗口服务器帧数 ≥ 10；三张截图不全同；投键后画面变了；截图有结构
    （细节格子 ≥ 5%）。只有一个或零个：static_window（窗口在、画面不动、对 Return 无反应，要人看）。
    2026-09-14：一款 Wine 游戏 白窗（0 信号）、一款 NW.js 游戏修前黑屏（只有帧数一个信号）都落到 static_window，不再冒充 alive。"""
    got = []
    stats = [s_.get("stats") or {} for s_ in shots]
    # 画面几乎是一块纯色（主色 ≥ 95% 且细节格子 < 2%）时，帧数再高、截图字节再变都不算活（一款 NW.js 游戏：黑窗一直在重绘、噪点让哈希不同）
    has_content = any(st.get("detail_fraction", 0) >= 0.02 or st.get("dominant_fraction", 1) < 0.95 for st in stats) if stats else True
    if has_content and isinstance(frames, dict) and frames.get("frames", 0) >= 10:
        got.append("frames")
    hashes = {s_["sha256"] for s_ in shots}
    if has_content and len(shots) >= 2 and len(hashes) > 1:
        got.append("changed")
    if "input_changed" in flags:
        got.append("input")
    if any(st.get("detail_fraction", 0) >= 0.05 for st in stats):
        got.append("detail")
    return got


class Snapshot:
    """开跑前的路径快照：清理只删这里没有的东西。也记 defaults 域：Unity 老播放器的分辨率对话框会往 com.unity-retarget.<名> 写键，
    留着的话下一轮壳会以为偏好已钉过、不再钉窗口化，对话框就一直等人点（NecroWorm 2026-09-14）。"""

    def __init__(self):
        self.paths = set()
        try:
            self.domains = set(sh(["defaults", "domains"], timeout=20).stdout.replace("\n", "").split(", "))
        except Exception:
            self.domains = set()
        for root in (SN_GAMES, SN_GAMES / ".wine-state", SN_GAMES / ".renpy-state", SN_GAMES / ".unity-work",
                     SN_GAMES / ".uninstalled", APP_SUPPORT / "nwjs", APP_SUPPORT / "love", APP_SUPPORT / "firstrun"):
            if root.is_dir():
                for p in root.iterdir():
                    self.paths.add(str(p))

    def is_new(self, p):
        return str(p) not in self.paths


def install(appid, evid, env_extra):
    """dryport install <appid> --no-add；返回 (rc, stdout+stderr, app 路径或 None, 秒数)。"""
    outfile = evid / "wrap-out.txt"
    env = {**os.environ, "SN_UNITY_NO_FETCH": "1", **env_extra, "WRAP_OUT_FILE": str(outfile), "SN_WRAP_NO_CLOBBER": "1",
           "SN_WINE_NO_NOTIFY": "1"}     # --env SN_UNITY_NO_FETCH=0 允许去官方 CDN 取本机没有的 Unity 播放器（每个 28 到 480 MB）
    t0 = time.monotonic()
    try:
        r = subprocess.run([str(CLI), "install", appid, "--no-add"], env=env, capture_output=True, text=True,
                           errors="replace", timeout=3600)
        rc, text = r.returncode, r.stdout + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
    except subprocess.TimeoutExpired as error:
        rc, text = 124, f"install 超时 3600 秒\n{error.stdout or ''}\n{error.stderr or ''}"
    (evid / "install.log").write_text(text, encoding="utf8")
    app = None
    if outfile.is_file():
        cand = outfile.read_text().strip()
        if cand and Path(cand).is_dir():
            app = Path(cand)
    return rc, text, app, round(time.monotonic() - t0, 1)


def classify_install(rc, text):
    """安装没走完时归到哪一步。文案来自 bin/dryport、lib/repack-gate.sh、lib/wine-wrap.sh。"""
    t = text
    if "检出反作弊" in t:
        return "install_failed:risk_anticheat"
    if "该游戏带联机" in t:
        return "install_failed:risk_online"
    if "风险检查未完成" in t or "风险判定回执" in t:
        return "install_failed:risk_pending"
    if "DRM 包装" in t and ("不打包" in t or "拒绝" in t):
        return "install_failed:drm"
    if "unity_player_missing" in t:
        return "install_failed:unity_player_missing"
    if "客户端下载没成" in t or "尚无完整下载" in t or "下载完整性回执未通过" in t or "尚不能确认完整" in t:
        return "install_failed:download"
    if "还没有打包路线" in t or "尚未实现" in t and "打包" in t:
        return "install_failed:no_route"
    if "缺 wine-cx" in t:
        return "install_failed:runtime_missing"
    if "打包失败" in t or "失败(退出码" in t:
        return "install_failed:wrap"
    if rc == 124:
        return "install_failed:timeout"
    return f"install_failed:rc{rc}"


class Launch:
    """一轮受控启动。所有进程经 run-guard 起（子进程组 + .running 记录），观察期有上限。"""

    def __init__(self, app, appid, evid, dwell, window_wait, idle_guard, want_input):
        self.app, self.appid, self.evid, self.dwell = app, appid, evid, dwell
        self.window_wait, self.idle_guard, self.want_input = window_wait, idle_guard, want_input
        self.label = f"smoke-{appid}"
        self.exe, self.bundle_name, self.bundle_id = bundle_exec(app)
        self.wl = wine_launch(app)
        self.is_wine = bool(self.wl)
        self.state = Path(self.wl["state"]) if self.wl.get("state") else None
        self.flags = set()
        self.notes = []
        self.windows = []          # 认出来的游戏窗口
        self.shots = []
        self.frames = None
        self.exit_code = None
        self.started = None
        self.stopped_how = None

    # ---- 起 ----
    def start(self):
        cmd = [sys.executable, str(RUN_GUARD), "--name", self.label,
               "--max-seconds", str(self.window_wait + self.dwell + 90),
               "--log", str(self.evid / "launcher.log"), "--", str(self.app / "Contents" / "MacOS" / self.exe)]
        # Unity 原生重定向的 app：Resources 里有 unity default resources，壳把参数透传给播放器，-logFile 直接落证据目录
        if not self.is_wine and self.exe == "launcher" and (self.app / "Contents" / "Resources" / "unity default resources").exists():
            cmd += ["-logFile", str(self.evid / "player.log")]
        env = {**os.environ, "SN_WINE_NO_NOTIFY": "1", "SN_WINEDEBUG": os.environ.get("SN_WINEDEBUG") or WINE_DEBUG}   # --env SN_WINEDEBUG=... 可加频道
        self.baseline = {w["id"] for w in evidence.windows()}
        self.started = time.time()
        self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"起进程：{' '.join(cmd[-3:])}（守护者 pid {self.proc.pid}，窗口等待 ≤{self.window_wait}s，观察 {self.dwell}s）")

    def guard_pid(self):
        recs = evidence.guard_records(self.label, self.started - 1)
        return int(recs[0]["pid"]) if recs else None

    def own_pids(self):
        """本次运行的进程：守护记录的进程组成员 + Wine 进程（comm 判据）。"""
        pids = set()
        gp = self.guard_pid()
        if gp:
            try:
                out = sh(["ps", "-axo", "pid=,pgid="], timeout=10).stdout
                for line in out.splitlines():
                    a, b = line.split()
                    if int(b) == gp:
                        pids.add(int(a))
            except Exception:
                pass
        if self.is_wine:
            try:
                out = sh(["ps", "-axo", "pid=,comm="], timeout=10).stdout
                for line in out.splitlines():
                    pid, comm = line.strip().split(None, 1)
                    low = comm.lower()
                    if low.startswith("c:\\") or low.endswith(".exe") or "wine" in low.rsplit("/", 1)[-1]:
                        pids.add(int(pid))
            except Exception:
                pass
        return pids

    def game_processes_alive(self):
        """本 prefix 里除 wineserver / services 之外还有 .exe 进程在跑（按 comm 判，不看命令行）。"""
        try:
            out = sh(["ps", "-axo", "pid=,comm="], timeout=10).stdout
        except Exception:
            return False
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            low = parts[1].lower()
            if low.endswith(".exe") and not any(x in low for x in ("services.exe", "winedevice.exe", "explorer.exe", "plugplay.exe",
                                                                   "rpcss.exe", "svchost.exe", "conhost.exe", "wineboot.exe")):
                return True
        return False

    def user_active(self):
        if not self.idle_guard:
            return False
        idle = evidence.hid_idle_seconds()
        return idle is not None and idle < 3.0

    # ---- 等窗口 ----
    def wait_window(self):
        screen = evidence.screen_size()
        deadline = time.monotonic() + self.window_wait
        while time.monotonic() < deadline:
            rc = self.proc.poll()
            fresh = evidence.new_windows(self.baseline)
            if fresh:
                own = self.own_pids()
                # 只认本次运行进程的窗口：pid 在守护进程组里，或（Wine 路线）owner 是 wine / 游戏 exe 名。
                # 2026-09-14 Northmark 那轮把用户别的 app（Meantime）新开的窗口当成了游戏窗口，截图三张全失败。
                mine = [w for w in fresh if w["pid"] in own
                        or (self.is_wine and ((w.get("owner") or "").lower() == "wine" or (w.get("owner") or "").lower().endswith(".exe")))]
                for w in mine:
                    if evidence.is_fullscreen(w, screen):
                        self.flags.add("fullscreen_seen")
                        self.notes.append(f"窗口 {w['id']} 铺满整屏（{w['bounds']}），立即停止")
                        self.windows = [w]
                        return "fullscreen"
                    if w["bounds"]["Width"] < 500 or w["bounds"]["Height"] < 360:
                        if "dialog_seen" not in self.flags:
                            # 小窗口多半是报错框：截一张、记标题，不然 no_window 的证据目录里什么都没有（PIXELFIXTURE 2026-09-14）
                            self.notes.append(f"小窗口：{w.get('title')!r} {w['bounds']['Width']}x{w['bounds']['Height']}")
                            try:
                                evidence.shoot(w["id"], self.evid / "dialog.png")
                            except Exception as e:
                                self.notes.append(f"小窗口截图失败：{e}")
                            self._dialog_since = time.monotonic(); self._dialog_pid = w["pid"]
                        self.flags.add("dialog_seen")
                # 小窗口挂着 15 秒还没主窗口：可能是「开始游戏」一类的启动器对话框（魔导圣战 2026-09-14：441x167 一个按钮），
                # 按一次 Return 给默认按钮；报错框被按掉也无妨，反正之后照样是 no_window
                if (self.want_input and getattr(self, "_dialog_since", None) and not getattr(self, "_dialog_pressed", False)
                        and time.monotonic() - self._dialog_since >= 15 and KEYSEND.is_file()):
                    self._dialog_pressed = True; self.input_at = time.monotonic()
                    r = sh([str(KEYSEND), "--pid", str(self._dialog_pid), "--key", "return", "--hold-ms", "80"], timeout=15)
                    time.sleep(1.0)   # Return 给默认按钮，Space 给有焦点的按钮（Win32 两条路）
                    sh([str(KEYSEND), "--pid", str(self._dialog_pid), "--key", "space", "--hold-ms", "80"], timeout=15)
                    self.notes.append("小窗口挂着 15 秒，投了 Return 和 Space" + ("" if r.returncode == 0 else f"（keysend 失败：{(r.stderr or r.stdout).strip()[:120]}）"))
                    self.flags.add("dialog_return_sent")
                # RPG Maker XP/VX Ace 默认窗口 640x480 / 544x416，mkxp 报错框约 500x200：以 500x360 分界
                big = [w for w in mine if w["bounds"]["Width"] >= 500 and w["bounds"]["Height"] >= 360]
                if not big:
                    small = [w for w in mine if small_game_window(w, (self.bundle_name, self.app.stem))]
                    if small:
                        self.flags.add("small_window")
                        self.notes.append(f"小窗口按标题认作游戏本体：{small[0].get('title')!r} {small[0]['bounds']['Width']}x{small[0]['bounds']['Height']}")
                        big = small
                if big:
                    self.windows = big
                    self.window_at = time.time() - self.started
                    log(f"窗口出现：{[(w['id'], w['pid'], w['owner'], w['bounds']['Width'], w['bounds']['Height']) for w in big]}，启动后 {self.window_at:.1f}s")
                    return "window"
            if rc is not None:
                self.exit_code = rc
                # 自解压/启动器型的 exe 会先退出、再由子进程跑真正的游戏（一款自解压游戏：IEXPRESS 解到 Temp 再起）；
                # 壳退出后本 prefix 里还有活着的 .exe 就继续等窗口，直到它们也没了
                if self.is_wine and self.game_processes_alive():
                    if not getattr(self, "_orphan_noted", False):
                        self._orphan_noted = True
                        self.notes.append(f"壳退出（rc {rc}）但 Wine 里还有游戏进程在跑，继续等窗口")
                    time.sleep(0.5)
                    continue
                return "exited"
            time.sleep(0.5)
        return "timeout"

    # ---- 观察 ----
    def shoot(self, tag):
        w = self.windows[0]
        # 游戏切分辨率/全屏切换会换窗口 id（Northmark、9th Dawn 三张截图全失败）：id 不在了就按 pid 重找一次
        if not any(x["id"] == w["id"] for x in evidence.windows()):
            again = [x for x in evidence.windows() if x["pid"] == w["pid"] and x["bounds"]["Width"] >= 500 and x["bounds"]["Height"] >= 360]
            if again:
                self.windows[0] = w = again[0]
                self.notes.append(f"窗口 id 换成 {w['id']}（{w['bounds']['Width']}x{w['bounds']['Height']}）")
        dest = self.evid / f"{tag}.png"
        ok = evidence.shoot(w["id"], dest)
        if ok:
            self.shots.append((tag, dest, dest.stat().st_size, evidence.file_sha256(dest)))
        else:
            self.notes.append(f"截图 {tag} 失败（窗口 {w['id']} 可能已没了）")
        return ok

    def probe_frames(self, seconds):
        # Popen 而不是 run：本次运行提前结束（崩溃、收掉）时 stop() 要能把探针一起杀掉。2026-09-14 一批冒烟留下 22 个 frame-probe 孤儿
        # （窗口没了 stopCapture 不返回，守护线程随主进程退出、子进程留下），replayd 吃掉三个核，owner 从活动监视器看到的。
        w = self.windows[0]
        try:
            self.probe_proc = subprocess.Popen([str(FRAME_PROBE), str(w["id"]), str(seconds), "120"],
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                out, err = self.probe_proc.communicate(timeout=seconds + 30)
            except subprocess.TimeoutExpired:
                self.probe_proc.kill(); out, err = self.probe_proc.communicate(timeout=10)
                self.notes.append("frame-probe 超时被杀")
            done = [json.loads(l) for l in out.splitlines() if l.startswith("{") and '"done"' in l]
            self.frames = done[0] if done else {"error": err.strip()[:200], "rc": self.probe_proc.returncode}
        except Exception as error:
            self.frames = {"error": str(error)}

    def kill_probe(self):
        proc = getattr(self, "probe_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.kill(); proc.wait(timeout=10)
                self.notes.append("frame-probe 随本次运行收掉")
            except Exception:
                pass

    def dwell_loop(self):
        t_end = time.monotonic() + self.dwell
        shot_plan = [(5, "t05"), (30, "t30")]
        t_start = time.monotonic()
        probe_len = max(5, min(self.dwell - 8, 45))
        th = threading.Thread(target=self.probe_frames, args=(probe_len,), daemon=True)
        th.start()
        input_done = False
        while time.monotonic() < t_end:
            elapsed = time.monotonic() - t_start
            if shot_plan and elapsed >= shot_plan[0][0]:
                self.shoot(shot_plan.pop(0)[1])
            if self.want_input and not input_done and elapsed >= 12:
                input_done = True
                self.input_probe()
                self.input_at = time.monotonic()     # 投键本身会重置 HIDIdleTime，之后 15 秒内不算「有人在用机器」
            rc = self.proc.poll()
            if rc is not None:
                self.exit_code = rc
                if not (self.is_wine and self.game_processes_alive()):
                    return "exited"
            if evidence.winedbg_running():
                self.flags.add("winedbg")
                self.notes.append("winedbg.exe 出现（Wine 崩溃对话框）")
                return "crashed"
            if self.user_active() and time.monotonic() - getattr(self, "input_at", 0) > 15:
                self.flags.add("user_active_during")
            time.sleep(0.5)
        self.shoot("end")
        th.join(timeout=5)
        return "alive"

    def input_probe(self):
        """软信号：按住 80 ms 的 Return 投给窗口进程，1.5 秒后看画面变没变。探针不可用记 untested。"""
        if not KEYSEND.is_file():
            self.flags.add("input_untested")
            return
        before = self.evid / "input-before.png"
        after = self.evid / "input-after.png"
        w = self.windows[0]
        evidence.shoot(w["id"], before)
        r = sh([str(KEYSEND), "--pid", str(w["pid"]), "--key", "return", "--hold-ms", "80"], timeout=15)
        if r.returncode != 0:
            self.flags.add("input_untested")
            self.notes.append(f"keysend 失败：{(r.stderr or r.stdout).strip()[:160]}")
            return
        time.sleep(1.5)
        if not any(x["id"] == w["id"] for x in evidence.windows()):
            # 一个 Return 把窗口按没了：多半是错误框（RGSS 的错误框标题就是游戏名），不算画面有反应
            self.flags.add("input_closed_window")
            self.notes.append("按 Return 后窗口消失（错误框？）")
            return
        evidence.shoot(w["id"], after)
        if before.is_file() and after.is_file():
            same = evidence.file_sha256(before) == evidence.file_sha256(after)
            self.flags.add("input_no_change" if same else "input_changed")

    # ---- 收 ----
    def stop(self):
        """先 TERM 壳 pid（run-guard 记录里的 pid），等它按自己的顺序收；不行再 wineserver -k；最后才 KILL。"""
        self.kill_probe()
        shell_pid = self.guard_pid()
        if shell_pid and evidence.pid_alive(shell_pid):
            try:
                os.kill(shell_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.proc.wait(timeout=30)
            self.stopped_how = "clean_stop"
        except subprocess.TimeoutExpired:
            self.stopped_how = "forced"
        if self.is_wine and self.state and (self.state / "prefix").is_dir():
            ws = Path(self.wl.get("wine", SN_RUNTIMES / "wine-cx")) / "bin" / "wineserver"
            env = {**os.environ, "WINEPREFIX": str(self.state / "prefix")}
            for flag in ("-k", "-w"):
                try:
                    subprocess.run([str(ws), flag], env=env, capture_output=True, timeout=60)
                except Exception:
                    pass
        if self.proc.poll() is None:
            try:
                self.proc.terminate(); self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        # 记录之外还活着的本次运行进程（守护者被 KILL 时可能留下）：按记录的进程组收，不碰别的
        for rec in evidence.guard_records(self.label, self.started - 1):
            pg = int(rec.get("pgid") or 0)
            if pg and pg != os.getpgid(0):
                try:
                    os.killpg(pg, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        time.sleep(1.5)
        leftovers = [p for p in self.own_pids() if evidence.pid_alive(p) and p != os.getpid()]
        for p in leftovers:
            try:
                os.kill(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if leftovers:
            self.flags.add("unclean_exit")
            self.notes.append(f"收尾后仍有本次运行进程 {sorted(leftovers)}，已 KILL")
        for lab in (self.label, self.bundle_name, self.wl.get("name") or ""):
            if lab:
                self.notes.extend(evidence.reap_record(lab, self.started - 1))
        strays = evidence.stray_wine()
        if strays:
            self.flags.add("stray_wine")
            self.notes.append(f"流浪 Wine 进程（不是本次运行记录覆盖的，不动）：{strays[:3]}")
        helper = sh(["pgrep", "-f", "steambridge-helper"], timeout=10).stdout.split()
        if helper:
            self.notes.append(f"steambridge-helper 仍在（pid {helper}），15 秒无客户端会自退")

    # ---- 日志 ----
    def collect_logs(self, since):
        got = []
        cands = []
        if self.state:
            cands += [self.state / "logs" / "helper.log"]
            cands += list((self.state / "prefix").glob("drive_c/users/*/AppData/LocalLow/*/*/Player.log")) if (self.state / "prefix").is_dir() else []
            cands += list((self.state / "prefix").glob("drive_c/users/*/AppData/Local/Temp/steambridge.log")) if (self.state / "prefix").is_dir() else []
        cands += list((Path.home() / "Library" / "Logs").glob("*/*/Player.log"))
        cands += list((APP_SUPPORT / "nwjs" / self.appid / "crashes").glob("*")) if (APP_SUPPORT / "nwjs" / self.appid).is_dir() else []
        cands += list((Path.home() / "Library" / "Application Support").glob("krkrsdl2-*/krkr.console.log"))
        for c in cands:
            try:
                if c.is_file() and c.stat().st_mtime >= since - 5:
                    dest = self.evid / "logs" / c.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    n = 0
                    while dest.exists():
                        n += 1; dest = self.evid / "logs" / f"{c.stem}.{n}{c.suffix}"
                    shutil.copy2(c, dest)
                    got.append(str(c))
            except OSError:
                pass
        return got

    def crash_signals(self):
        sig = []
        launcher = self.evid / "launcher.log"
        if launcher.is_file():
            text = launcher.read_text(errors="replace")
            for pat in ("Unhandled exception", "starting debugger", "wine: Unhandled", "Assertion failed", "Segmentation fault",
                        "Abort trap", "Illegal instruction", "Bus error"):
                if pat in text:
                    sig.append(f"launcher.log: {pat}")
            if "c0000135" in text or "not found (needed by" in text.lower():
                self.flags.add("missing_dll")
        if "winedbg" in self.flags:
            sig.append("winedbg")
        reports = evidence.crash_reports(self.started - 1, needles=[self.exe or "", self.bundle_name or "", "wine", "wine64", "wine-preloader"])
        if reports:
            sig.append(f"DiagnosticReports: {[r['proc'] for r in reports][:3]}")
            for r in reports:
                try:
                    shutil.copy2(r["file"], self.evid / "logs" / Path(r["file"]).name)
                except OSError:
                    pass
        rc = self.exit_code
        if rc is not None and rc not in (0, 124, 143):
            if rc == 53:
                self.flags.add("missing_dll")
                sig.append("exit 53 (c0000135 缺 DLL)")
            elif rc > 128 or rc < 0:
                sig.append(f"被信号收掉 rc={rc}")
        return sig


def verdict_of(phase, dwell_result, launch):
    if phase == "user_active":
        return "aborted_user_active"
    if phase == "fullscreen":
        if dwell_result == "exited" and launch.exit_code not in (0, None):
            return "crashed"
        return "crashed" if launch.crash_signals() else "alive"
    if phase == "exited":
        rc = launch.exit_code
        if rc == 126:
            return "launch_failed"
        if launch.crash_signals():
            return "crashed"
        return "launch_failed" if (time.time() - launch.started) < 10 else "exited_early"
    if phase == "timeout":
        return "crashed" if launch.crash_signals() else "no_window"
    # 有窗口
    if dwell_result == "user_active":
        return "aborted_user_active"
    if dwell_result == "crashed":
        return "crashed"
    if dwell_result == "exited":
        return "crashed" if launch.crash_signals() or launch.exit_code not in (0, None) else "exited_early"
    return "crashed" if launch.crash_signals() else "alive"


def write_back(appid, run):
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    f = SMOKE_DIR / f"{appid}.json"
    data = {"protocol": 1, "appid": appid, "runs": []}
    if f.is_file():
        try:
            data = json.loads(f.read_text())
        except Exception:
            pass
    data["runs"].append(run)
    data["latest"] = {k: run.get(k) for k in ("verdict", "flags", "at", "route", "engine", "detected_engine", "evidence", "git", "env",
                                              "window_at_seconds", "liveness")}
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, f)
    return f


def cleanup(appid, app, folder, snap, evid, keep_states):
    """删本次运行创建的：下载目录、app（先挪进 .uninstalled/<appid>-smoke-* 再随归档删）、.wine-state/.renpy-state；
    再加快照里没有的 NW.js/LÖVE 状态、firstrun 标记、.unity-work。用户开跑前就有的一律不动。"""
    import uninstall_purge
    result = {"deleted": [], "refused": [], "skipped_existing": []}
    installed = {}
    if app and app.is_dir() and snap.is_new(app):
        installed["app"] = str(app)
    elif app:
        result["skipped_existing"].append(str(app))
    if folder and Path(folder).is_dir() and snap.is_new(folder):
        installed["folder"] = str(folder)
    elif folder:
        result["skipped_existing"].append(str(folder))

    def archive_hook(app_path):
        root = SN_GAMES / ".uninstalled"
        root.mkdir(exist_ok=True)
        container = root / f"{appid}-smoke-{time.strftime('%Y%m%d%H%M%S')}"
        container.mkdir()
        dest = container / Path(app_path).name
        os.rename(app_path, dest)
        return str(container)

    saves_ok = all(snap.is_new(SN_GAMES / d / appid) for d in (".wine-state", ".renpy-state")) and not keep_states
    r = uninstall_purge.apply(appid, saves=saves_ok, installed=installed, archive_hook=archive_hook, log=lambda *a: None)
    result["deleted"] += [d if isinstance(d, str) else d.get("path", str(d)) for d in r.get("deleted", [])]
    result["refused"] += [x if isinstance(x, str) else x.get("path", str(x)) for x in r.get("refused", [])]
    result["freed_bytes"] = r.get("freed_bytes")
    # 快照外的其它状态
    extra = [APP_SUPPORT / "nwjs" / appid, APP_SUPPORT / "love" / appid]
    for d in (SN_GAMES / ".unity-work").glob("*") if (SN_GAMES / ".unity-work").is_dir() else []:
        extra.append(d)
    for d in (APP_SUPPORT / "firstrun").glob("*") if (APP_SUPPORT / "firstrun").is_dir() else []:
        extra.append(d)
    # 本次运行新出现的 unity-retarget 偏好域
    try:
        now = set(sh(["defaults", "domains"], timeout=20).stdout.replace("\n", "").split(", "))
        for dom in sorted(now - snap.domains):
            if dom.startswith("com.unity-retarget."):
                sh(["defaults", "delete", dom], timeout=20)
                result["deleted"].append(f"defaults:{dom}")
    except Exception as error:
        result["refused"].append(f"defaults: {error}")
    for d in extra:
        if d.exists() and snap.is_new(d) and not d.is_symlink():
            try:
                if d.is_dir():
                    if d.parent.name == ".unity-work":
                        for lg in d.glob("*.log"):
                            shutil.copy2(lg, evid / "logs" / f"unity-work-{lg.name}") if (evid / "logs").is_dir() else None
                    shutil.rmtree(d)
                else:
                    d.unlink()
                result["deleted"].append(str(d))
            except OSError as error:
                result["refused"].append(f"{d}: {error}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("appid")
    ap.add_argument("--dwell", type=float, default=60, help="窗口出现后观察多少秒")
    ap.add_argument("--window-wait", type=float, help="等窗口的上限秒数；默认原生 60、Wine 180")
    ap.add_argument("--keep", action="store_true", help="不清理（保留下载、app、状态）")
    ap.add_argument("--input", action="store_true", help="做输入探针（软信号）")
    ap.add_argument("--no-install", action="store_true", help="不装，直接起 --app 指定的 app（也不清理）")
    ap.add_argument("--app", help="配合 --no-install：已装的 .app 路径")
    ap.add_argument("--no-idle-guard", action="store_true", help="不管有没有人在用机器")
    ap.add_argument("--out", help="证据目录（默认 dryport-deliveries/<日期>/smoke/<appid>-<名>）")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VAL",
                    help="传给安装与启动的环境变量（做对照实验用，如 SN_WINE_BRIDGE=1）；会记进结果")
    a = ap.parse_args(argv)
    env_extra = {}
    for kv in a.env:
        if "=" not in kv:
            sys.exit(f"--env 要写成 KEY=VAL：{kv}")
        k, v = kv.split("=", 1); env_extra[k] = v
    os.environ.update(env_extra)
    appid = a.appid
    if not appid.isdigit():
        sys.exit("appid 要是数字")
    row = catalog_row(appid)
    name = safe_name(row.get("name") or f"app_{appid}")
    evid = Path(a.out) if a.out else DELIVERIES / time.strftime("%Y-%m-%d") / "smoke" / f"{appid}-{name}" / time.strftime("%H%M%S")
    evid.mkdir(parents=True, exist_ok=True)
    for stale in ("launcher.log", "install.log", "player.log"):
        if (evid / stale).exists():
            (evid / stale).unlink()          # --out 指定的目录复用时也别把前一次运行的日志混进来
    (evid / "logs").mkdir(exist_ok=True)
    t0 = time.time()
    run = {"protocol": 1, "appid": appid, "name": row.get("name"), "route": row.get("route"), "engine": row.get("engine"),
           "at": evidence.now_stamp(), "git": git_rev(), "evidence": str(evid), "machine": "本机", "flags": [], "notes": [],
           "env": env_extra}
    log(f"{appid} {row.get('name')}  目录路线={row.get('route')} 引擎={row.get('engine')}  证据 {evid}")

    # 预检
    if not evidence.ensure_window_list():
        sys.exit("window-list 编不出来，取证做不了")
    import running
    live = [r for r in running.records() if r.get("alive")]
    if live and not a.no_install:
        sys.exit(f"有别的受控进程在跑（{[(r['name'], r['pid']) for r in live]}），先 dryport running 处理")
    if not a.no_install:
        lock = SN_RUNTIMES / ".dryport-install.lock"
        if lock.exists() and not sh(["pgrep", "-f", "bin/install.sh"], timeout=10).stdout.strip():
            sys.exit(f"安装锁残留 {lock}，且没有 install.sh 在跑；不自动删，请人工确认后删除")
        import api
        if not api.steam_running():
            sys.exit("Steam 没在跑")
        if not api.debug_port_enabled():
            sys.exit("Steam 调试口不可读，下载做不了")
    if not ensure_frame_probe():
        run["notes"].append("没有 frame-probe，活性只靠截图")
    # 屏幕礼貌：有人正在敲键盘动鼠标就先等（最多 10 分钟），免得游戏窗口抢走焦点；等不到也开跑，但记一笔。
    idle = evidence.hid_idle_seconds()
    if not a.no_idle_guard and idle is not None and idle < 30:
        log(f"有人在用机器（空闲 {idle:.0f}s），等空闲满 30 秒再起窗口（最多等 10 分钟）…")
        waited = 0
        while waited < 600:
            time.sleep(5); waited += 5
            idle = evidence.hid_idle_seconds()
            if idle is None or idle >= 30:
                break
        else:
            run["flags"].append("started_while_active")

    snap = Snapshot()
    app = Path(a.app) if a.app else None
    folder = None
    free_before = disk_free()
    run["disk_free_before"] = free_before
    if not a.no_install:
        rc, text, app, secs = install(appid, evid, env_extra)
        run["install"] = {"rc": rc, "seconds": secs}
        try:
            import api
            reg = api.scan_installed()
            folder = (reg.get(appid) or {}).get("folder")
            if app is None and (reg.get(appid) or {}).get("app") and snap.is_new((reg.get(appid) or {}).get("app")):
                app = Path(reg[appid]["app"])
        except Exception as error:
            run["notes"].append(f"scan_installed 失败：{error}")
        m = re.search(r"引擎: (.+)", text)
        if m:
            run["detected_engine"] = m.group(1).strip()
        run["folder"] = folder
        run["app"] = str(app) if app else None
        if rc != 0 or app is None:
            run["verdict"] = classify_install(rc, text)
            log(f"安装没走完：{run['verdict']}（rc {rc}，{secs}s）")
    else:
        if not app or not app.is_dir():
            sys.exit("--no-install 要配 --app <已装的 .app>")
        run["app"] = str(app)

    if app and run.get("verdict") is None:
        wine = bool(wine_launch(app))
        window_wait = a.window_wait or (180 if wine else 60)
        L = Launch(app, appid, evid, a.dwell, window_wait, not a.no_idle_guard, a.input)
        run["is_wine"] = wine
        L.start()
        dwell_result = None
        try:
            phase = L.wait_window()
            if phase == "window":
                dwell_result = L.dwell_loop()
            elif phase == "fullscreen":
                # 铺满整屏是产品缺陷（该窗口化没窗口化）；run-guard 兜底保证能收，所以还是观察 20 秒、截两张再收，
                # 免得把还没渲染的黑画面当成结果（Door in the Woods 09-14）。
                time.sleep(6); L.shoot("t05")
                th = threading.Thread(target=L.probe_frames, args=(12,), daemon=True); th.start()
                time.sleep(14); L.shoot("end"); th.join(timeout=5)
                dwell_result = "alive" if L.proc.poll() is None else "exited"
            run["window_at_seconds"] = round(getattr(L, "window_at", 0), 1) if L.windows else None
        finally:
            L.stop()   # 判定过程里抛了异常也要把游戏、探针收干净
        time.sleep(4)   # ReportCrash 是异步的
        run["logs"] = L.collect_logs(L.started)
        run["crash_signals"] = L.crash_signals()
        run["verdict"] = verdict_of(phase, dwell_result, L)
        run["phase"] = phase
        run["dwell_result"] = dwell_result
        run["exit_code"] = L.exit_code
        run["stopped"] = L.stopped_how
        run["windows"] = L.windows
        run["frames"] = L.frames
        run["shots"] = [{"tag": t, "file": str(p), "bytes": b, "sha256": h, "stats": image_stats(p)} for t, p, b, h in L.shots]
        hashes = {h for _, _, _, h in L.shots}
        if len(L.shots) >= 2 and len(hashes) == 1:
            L.flags.add("static")
        if L.frames and isinstance(L.frames, dict) and L.frames.get("frames", 99) < 10:
            L.flags.add("few_frames")
        signals = liveness_signals(L.frames, run["shots"], L.flags)
        run["liveness"] = signals
        if run["verdict"] == "alive":
            if L.shots and all(looks_blank(s_["stats"]) for s_ in run["shots"]):
                L.flags.add("blank")
                run["verdict"] = "blank_window"      # 窗口在、进程活着，但画面是整幅纯色
            elif len(signals) < 2:
                run["verdict"] = "static_window"     # 画面不动、投键无反应、没结构：要人看，不算冒烟通过
        run["flags"] = sorted(L.flags)
        run["notes"] += L.notes
        log(f"verdict={run['verdict']} flags={run['flags']} exit={L.exit_code} frames={L.frames and L.frames.get('frames')}")

    # 清理
    if not a.keep and not a.no_install:
        run["cleanup"] = cleanup(appid, app, folder, snap, evid, keep_states=False)
        strays = evidence.stray_wine()
        if strays:
            run["notes"].append(f"清理后仍有流浪 Wine 进程：{strays[:3]}")
    run["disk_free_after"] = disk_free()
    run["disk_delta_bytes"] = run["disk_free_after"] - free_before
    run["seconds_total"] = round(time.time() - t0, 1)
    (evid / "result.json").write_text(json.dumps(run, ensure_ascii=False, indent=1))
    if not a.no_install:
        f = write_back(appid, run)
        log(f"写回 {f}")
    log(f"结束：{run['verdict']}  用时 {run['seconds_total']}s  磁盘变化 {run['disk_delta_bytes'] / 1e9:+.2f} GB")
    print(json.dumps({k: run.get(k) for k in ("appid", "name", "verdict", "flags", "window_at_seconds", "seconds_total", "evidence")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
