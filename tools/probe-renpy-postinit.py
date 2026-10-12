#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自有 Ren'Py 8.1.3 无窗口实验：原默认偏好后仅改变 fullscreen。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sn_existing_renpy_probe", ROOT / "tools/probe-renpy.py")
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
VERSION = BASE.VERSION
OUTPUT_LIMIT = 1024 * 1024

# 仅作者新写的样例。原游戏永远不是这个工具的输入。
FIXTURE = r'''
define config.name = "Owned post-init preference fixture"
define config.save_directory = "sn-owned-postinit-v1"
define config.default_fullscreen = True
define config.default_language = "sn_fixture_language"
define config.enable_language_autodetect = False
define config.default_text_cps = 37
define config.quadratic_volumes = True
define config.default_music_volume = 0.3
define config.default_sfx_volume = 0.5
define config.default_voice_volume = 0.7
define config.developer = False
define config.has_sound = False
define config.has_music = False
define config.has_voice = False

init -1500 python:
    config.enable_steam = False

init python:
    import os
    import sn_owned_postinit

    def sn_original_late_second():
        sn_owned_postinit.note("original_late_second")
        if not persistent.sn_owned_ready:
            persistent.sn_owned_ready = "owned-fixture-v1"
            _preferences.fullscreen = True

    def sn_original_late_first():
        sn_owned_postinit.note("original_late_first")
        if not persistent.sn_owned_ready:
            _preferences.text_cps = 41
        # 动态追加，暴露简单 append 一次就退出造成的漏回调。
        renpy.game.post_init.append(sn_original_late_second)

    renpy.game.post_init.append(sn_original_late_first)
    if os.environ["SN_POSTINIT_PHASE"] == "extra":
        config.extra_savedirs = [os.environ["SN_POSTINIT_OUTSIDE"]]

label start:
    $ raise RuntimeError("自有实验不准执行游戏 label")
'''

# .rpe 是固定版官方 main.load_rpe 的输入，不进入 Script.scan_script_files。
# Interface/FileLocation 哨兵仅用于本自有实验，不是生产引擎补丁或兼容层。
RPE = r'''
import copy
import hashlib
import json
import os
import platform
import sys
import types
import renpy

phase = os.environ["SN_POSTINIT_PHASE"]
receipt_path = os.environ["SN_POSTINIT_RECEIPT"]
savedir = os.path.realpath(os.environ["SN_POSTINIT_SAVE"])
state = os.path.realpath(os.environ["SN_POSTINIT_STATE"])
events = ["rpe_loaded"]
locations_seen = []
deferrals = 0
interface_attempts = 0
report = {"protocol": 1, "phase": phase, "pid": os.getpid(), "ok": False,
          "architecture": platform.machine(), "version": renpy.version_only,
          "saving_verified": False}

def write_receipt():
    report.update(events=list(events), file_locations_seen=list(locations_seen),
                  interface_attempts=interface_attempts)
    with open(receipt_path, "x") as stream:
        json.dump(report, stream, sort_keys=True)

def stop(reason, status=3):
    report["error"] = reason
    write_receipt()
    # SystemExit 不会落入 Ren'Py 的 Exception 错误对话框路径。
    raise SystemExit(status)

def note(value):
    if len(events) >= 64:
        stop("callback_event_limit")
    events.append(value)

if phase not in {"generation", "control", "seed", "precheck", "denied_seed", "denied_precheck", "extra"}:
    stop("unknown_phase")
if os.environ.get("SDL_VIDEODRIVER") != "dummy" or os.environ.get("SDL_AUDIODRIVER") != "dummy":
    stop("dummy_drivers_missing")
if renpy.game.args.command != "sn_owned_postinit":
    stop("non_owned_command")
if renpy.game.interface is not None:
    stop("interface_exists_before_rpe")
if renpy.config.extra_savedirs:
    stop("extra_savedirs_before_loading")
if os.path.commonpath([state, savedir]) != state or savedir == state:
    stop("savedir_outside_owned_state")

def reject_interface(self, *args, **kwargs):
    global interface_attempts
    interface_attempts += 1
    stop("interface_constructor_attempted", 79)

renpy.display.core.Interface.__init__ = reject_interface
original_location_init = renpy.savelocation.FileLocation.__init__
initial_locations = {savedir}
if phase == "generation":
    # 唯一可编译目录也是全新自有 state；随后冻结其 .rpyc。
    initial_locations.add(os.path.realpath(os.path.join(renpy.config.gamedir, "saves")))
# 官方 00sync.rpy init1100 添加此子目录，不能把它清空伪装成路径隔离。
syncdir = os.path.join(savedir, "sync")
allowed_locations = initial_locations | {syncdir}
for path in allowed_locations:
    if os.path.commonpath([state, path]) != state:
        stop("generation_save_outside_owned_state")

def guarded_location_init(self, directory):
    resolved = os.path.realpath(directory)
    if resolved not in allowed_locations:
        stop("unexpected_save_location_before_write")
    if len(locations_seen) >= 16:
        stop("save_location_limit")
    locations_seen.append(resolved)
    original_location_init(self, directory)

renpy.savelocation.FileLocation.__init__ = guarded_location_init
module = types.ModuleType("sn_owned_postinit")
module.note = note
if module.__name__ in sys.modules:
    stop("owned_module_collision")
sys.modules[module.__name__] = module
renpy.arguments.register_command("sn_owned_postinit", lambda: False, uses_display=False)

def normalize(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: normalize(item) for key, item in value.items()}
        # 官方 pad_bindings 含 None key；JSON 的 "null" key 会与原字符串冲突。
        entries = [[type(key).__name__, normalize(key), normalize(item)] for key, item in value.items()]
        return {"__typed_mapping__": sorted(entries, key=lambda item: json.dumps(item[:2], sort_keys=True))}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, tuple):
        return {"__tuple__": [normalize(item) for item in value]}
    raise TypeError("unsupported preference type: " + type(value).__name__)

def preferences():
    return normalize(vars(renpy.game.preferences))

def finalize():
    global deferrals
    # main.py 遍历的是实时 list；晚期回调仍有追加时有界后移。
    if renpy.game.post_init[-1] is not finalizer:
        deferrals += 1
        note("defer_finalizer")
        if deferrals > 8:
            stop("post_init_did_not_settle")
        renpy.game.post_init.append(finalizer)
        return
    note("finalizer")
    if renpy.game.interface is not None:
        stop("interface_exists_at_finalizer")
    report["extra_savedirs"] = list(renpy.config.extra_savedirs)
    if renpy.config.extra_savedirs != [syncdir]:
        stop("unexpected_extra_savedirs")
    actual = [os.path.realpath(item.directory) for item in renpy.loadsave.location.locations]
    active = [os.path.realpath(item.directory) for item in renpy.loadsave.location.active_locations()]
    # 第二次 savelocation.init 在 post_init 之后；sync 尚未创建。
    if set(actual) != initial_locations or set(active) != initial_locations:
        stop("save_locations_mismatch")
    if os.path.realpath(renpy.config.savedir) != savedir:
        stop("savedir_changed")
    if phase != "generation" and not renpy.macapp:
        stop("macapp_missing")
    if not renpy.game.persistent._set_preferences:
        stop("original_defaults_not_initialized")
    if renpy.config.enable_steam is not False or "steamapi" in sys.modules:
        stop("owned_fixture_steam_boundary")
    if events.count("original_late_first") != 1 or events.count("original_late_second") != 1:
        stop("original_callbacks_incomplete")
    before = preferences()
    report.update(before=before, preferences_initialized=True,
                  persistent_marker=renpy.game.persistent.sn_owned_ready,
                  savedir=savedir, extra_savedirs=list(renpy.config.extra_savedirs),
                  active_save_locations=active, macapp=bool(renpy.macapp),
                  interface_absent=True, steam_enabled=False,
                  deferrals=deferrals, script_digest=renpy.game.script.digest.hexdigest(),
                  statement_names_sha256=hashlib.sha256(json.dumps(sorted(map(repr, renpy.game.script.namemap)),
                                                       sort_keys=True).encode()).hexdigest())
    if phase in ("seed", "denied_seed"):
        renpy.game.preferences.fullscreen = False
        after = preferences()
        unchanged = copy.deepcopy(after)
        unchanged["fullscreen"] = before["fullscreen"]
        if unchanged != before or before["fullscreen"] is not True:
            stop("changed_more_than_fullscreen")
        # .rpy 中的 renpy 名指向 exports；RPE import 得到的是引擎 package。
        renpy.exports.save_persistent()
        report["save_call_returned"] = True
        report["persistent_file_present"] = os.path.isfile(os.path.join(savedir, "persistent"))
        report["ok"] = report["persistent_file_present"]
        if not report["ok"]:
            report["error"] = "save_returned_without_persistent_file"
    elif phase in ("precheck", "denied_precheck"):
        report["ok"] = before["fullscreen"] is False
        if not report["ok"]:
            report["error"] = "saved_fullscreen_not_restored"
    else:
        report["ok"] = before["fullscreen"] is True
    report["after"] = preferences()
    write_receipt()
    raise renpy.game.QuitException(status=0 if report["ok"] else 2)

def finalizer():
    try:
        finalize()
    except renpy.game.QuitException:
        raise
    except Exception as error:
        stop("post_init_error: " + repr(error)[:300])

def queue_finalizer():
    note("queue_finalizer")
    # 分开记录官方默认与原样例的晚期回调，避免文本 41 掩盖默认 37 未生效。
    report["after_original_init"] = preferences()
    renpy.game.post_init.append(finalizer)

renpy.game.post_init.append(queue_finalizer)
'''

CANARY_PROGRAM = r'''
import errno,json,os,sys
plan=json.loads(sys.argv[1]); result={"denied":{},"allowed":False}
with open(plan["allowed"],"xb") as stream: stream.write(b"owned writable canary")
result["allowed"]=True
for label,path in plan["deny_writes"].items():
    try:
        with open(path,"wb") as stream: stream.write(b"unexpected write")
        result["denied"][label]=0
    except OSError as error: result["denied"][label]=error.errno
try: os.rename(plan["rename_source"],plan["rename_destination"]); result["denied"]["ancestor_rename"]=0
except OSError as error: result["denied"]["ancestor_rename"]=error.errno
print(json.dumps(result,sort_keys=True))
raise SystemExit(0 if result["allowed"] and set(result["denied"].values())=={errno.EPERM} else 2)
'''


def _limit_child_files():
    # 此工具只产生小型自有 fixture；限制 stdout 文件和任何意外大写入。
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))


def run_owned(args, *, env, cwd, logs, name, timeout):
    started = time.monotonic()
    stdout_path, stderr_path = logs / (name + ".stdout"), logs / (name + ".stderr")
    with stdout_path.open("xb") as out, stderr_path.open("xb") as err:
        process = subprocess.Popen(args, env=env, cwd=cwd, stdout=out, stderr=err,
                                   start_new_session=True, preexec_fn=_limit_child_files)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return {"args": list(map(str, args)), "pid": process.pid, "returncode": process.returncode,
            "timed_out": timed_out, "elapsed_seconds": time.monotonic() - started,
            "stdout": stdout_path.read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stderr": stderr_path.read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stdout_file": str(stdout_path), "stderr_file": str(stderr_path),
            "per_file_write_limit_bytes": OUTPUT_LIMIT}


def policy(state, protected, denied_savedir=None):
    result = BASE.sandbox(state, protected)
    if denied_savedir is not None:
        expression = "^" + re.escape(str(denied_savedir)) + "/persistent([.].*)?$"
        # 参数传递，不能把含引号的用户路径直接拼进 SBPL。
        result[1:1] = ["-D", "DENY_PERSISTENT=" + expression]
        result[-1] += '(deny file-write* (regex (param "DENY_PERSISTENT")))\n'
    return result


def validate_guard(run, phase, savedir):
    data = run.get("receipt")
    if run["timed_out"] or not data or data.get("pid") != run["pid"]:
        raise ValueError("缺少本进程完整回执：" + phase)
    if (data.get("phase") != phase or data.get("architecture") != "arm64"
            or data.get("version") != VERSION or data.get("interface_attempts") != 0
            or "[S_API" in run["stderr"]):
        raise ValueError("进程身份或无窗口/Steam 边界不符：" + phase)
    if phase == "extra":
        if run["returncode"] != 3 or data.get("error") != "unexpected_extra_savedirs":
            raise ValueError("extra_savedirs 负例没有拒绝")
        return data
    expected_exit = 2 if phase in ("denied_seed", "denied_precheck") else 0
    if (run["returncode"] != expected_exit or data.get("interface_absent") is not True
            or data.get("savedir") != str(savedir) or data.get("extra_savedirs") != [str(savedir / "sync")]
            or data.get("preferences_initialized") is not True or data.get("steam_enabled") is not False
            or data.get("persistent_marker") != "owned-fixture-v1"):
        raise ValueError("默认偏好、保存路径或退出边界不符：" + phase)
    if phase != "generation" and data.get("active_save_locations") != [str(savedir)]:
        raise ValueError("活动保存路径不唯一：" + phase)
    events = data.get("events", [])
    if (events != ["rpe_loaded", "queue_finalizer", "original_late_first", "defer_finalizer",
                   "original_late_second", "finalizer"] or data.get("deferrals") != 1):
        raise ValueError("晚期原回调没有完整先行：" + phase)
    if phase in ("denied_seed", "denied_precheck"):
        expected = "save_returned_without_persistent_file" if phase == "denied_seed" else "saved_fullscreen_not_restored"
        if data.get("ok") is not False or data.get("error") != expected:
            raise ValueError("保存失败负例被当成成功：" + phase)
        if phase == "denied_seed" and (data.get("save_call_returned") is not True
                                      or data.get("persistent_file_present") is not False):
            raise ValueError("没有观察到官方保存返回但文件缺失")
    elif data.get("ok") is not True:
        raise ValueError("自有偏好回执失败：" + phase)
    return data


def compare_preferences(control, seed, precheck):
    original = control["before"]
    initialized = control["after_original_init"]
    if (initialized.get("text_cps") != 37 or seed["after_original_init"] != initialized
            or dict(initialized, text_cps=41) != original):
        raise ValueError("原默认与晚期原回调的偏好顺序不符")
    if (original.get("fullscreen") is not True or original.get("language") != "sn_fixture_language"
            or original.get("text_cps") != 41):
        raise ValueError("对照没有应用原始语言/文本/全屏偏好")
    volumes = original.get("volumes", {})
    if "__typed_mapping__" in volumes:
        volumes = {key: value for kind, key, value in volumes["__typed_mapping__"] if kind == "str"}
    for name, wanted in (("music", 0.09), ("sfx", 0.25), ("voice", 0.49)):
        if abs(volumes.get(name, -1) - wanted) > 1e-9:
            raise ValueError("对照没有应用原始音量偏好")
    if seed["before"] != original:
        raise ValueError("seed 改写前与原默认对照不同")
    wanted = dict(original, fullscreen=False)
    if seed["after"] != wanted or precheck["before"] != wanted or precheck["after"] != wanted:
        raise ValueError("独立进程回读没有仅保持 fullscreen 差异")
    for key in ("script_digest", "statement_names_sha256"):
        if len({control[key], seed[key], precheck[key]}) != 1:
            raise ValueError("原编译脚本身份变化：" + key)


def probe(materials, output, timeout=15):
    if sys.platform != "darwin" or not 1 <= timeout <= 20:
        raise ValueError("需要 macOS；单进程截止须为 1–20 秒")
    materials = Path(materials).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError("输出已存在，保留：" + str(output))
    output = output.parent.resolve(strict=True) / output.name
    if output == ROOT or ROOT in output.parents:
        raise ValueError("自有样例与运行时不能写入源码仓库")
    if output == materials or materials in output.parents or output in materials.parents:
        raise ValueError("输出不能与原始材料重叠")
    sdk, extension, checked = BASE.verify_materials(materials)
    output.mkdir(mode=0o700)
    report = {"protocol": 1, "kind": "owned headless post-init mechanism", "ok": False,
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "version": VERSION,
              "output": str(output), "official_archives": checked, "runs": [],
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "boundaries": ["只运行自有样例，没有运行真实游戏或 GUI。",
                             "Interface 与保存位置哨兵只用于本实验，不是生产方案。",
                             "修改启动偏好不能阻止游戏后来进入全屏；不构成游戏可玩或旧存档兼容证明。"]}
    snapshots = {"sdk": (sdk, BASE.inventory(sdk)), "steam_extension": (extension, BASE.inventory(extension))}
    try:
        state = output / "state"
        state.mkdir(mode=0o700)
        logs = state / "logs"
        logs.mkdir(mode=0o700)
        view = state / "view-parent/Runtime"
        source = state / "source-parent/OriginalFixture"
        source.mkdir(parents=True)
        (source / "game").mkdir()
        (source / "game/probe.rpy").write_text(FIXTURE)
        (source / "game/script_version.txt").write_text("(8, 1, 3)")
        (source / "protected.txt").write_bytes(BASE.CANARY)
        BASE.prepare_view(sdk, extension, view)
        rpe_dir = state / "extensions"
        rpe_dir.mkdir()
        with zipfile.ZipFile(rpe_dir / "sn-owned-postinit.rpe", "x", compression=zipfile.ZIP_STORED) as z:
            z.writestr("autorun.py", RPE)
        outside = output / "outside-state"
        outside.mkdir()
        (outside / "canary").write_bytes(BASE.CANARY)
        for name in ("home", "tmp", "renpy", "multipersistent", "screenshots", "generation-saves",
                     "control-saves", "saves", "denied-saves", "extra-saves"):
            (state / name).mkdir()
        alias = state / "source-alias"
        alias.symlink_to(source)
        protected = [source, view, rpe_dir, sdk, extension]
        snapshots.update({"view": (view, BASE.inventory(view)), "extension": (rpe_dir, BASE.inventory(rpe_dir)),
                          "source_before_generation": (source, BASE.inventory(source)),
                          "outside": (outside, BASE.inventory(outside))})
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
               "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
               "PYTHONDONTWRITEBYTECODE": "1", "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
               "RENPY_PATH_TO_SAVES": str(state / "renpy"), "RENPY_MULTIPERSISTENT": str(state / "multipersistent"),
               "RENPY_SCREENSHOT_PATTERN": str(state / "screenshots/shot%04d.png"),
               "RENPY_SEARCHPATH": str(rpe_dir), "SN_POSTINIT_STATE": str(state),
               "SN_POSTINIT_OUTSIDE": str(outside)}
        plan = {"allowed": str(state / "allowed-canary"),
                "deny_writes": {"source": str(source / "protected.txt"), "view": str(view / "protected.txt"),
                                "alias": str(alias / "protected.txt"), "outside": str(outside / "canary"),
                                "persistent": str(state / "denied-saves/persistent.probe")},
                "rename_source": str(source.parent), "rename_destination": str(state / "moved-source")}
        canary = run_owned(policy(state, protected, state / "denied-saves") +
                           [sys.executable, "-c", CANARY_PROGRAM, json.dumps(plan)],
                           env=env, cwd=state, logs=logs, name="write-preflight", timeout=timeout)
        report["write_preflight"] = canary
        if canary["timed_out"] or canary["returncode"] or not json.loads(canary["stdout"])["allowed"]:
            raise ValueError("写入边界预检失败，没有运行 Ren'Py")
        BASE.finish_integrity(report, snapshots)
        if not all(x["unchanged"] for x in report["integrity"].values()):
            raise ValueError("预检后原件不一致，没有运行 Ren'Py")
        report["preflight_integrity"] = report.pop("integrity")
        signatures = []
        for name in ("renpy", "pythonw", "librenpython.dylib", "libsteam_api.dylib"):
            result = BASE.run_bounded(["/usr/bin/codesign", "--verify", "--strict", "--arch", "arm64",
                                       str(view / "lib/py3-mac-universal" / name)], timeout=10)
            signatures.append(result)
            if result["returncode"] or result["timed_out"]:
                raise ValueError("官方 arm64 签名未通过")
        report["factory_signatures"] = signatures

        def invoke(phase, basedir, savedir, roots, deny=False):
            receipt = logs / (phase + ".json")
            child_env = {**env, "SN_POSTINIT_PHASE": phase, "SN_POSTINIT_RECEIPT": str(receipt),
                         "SN_POSTINIT_SAVE": str(savedir)}
            args = policy(state, roots, savedir if deny else None) + ["/usr/bin/arch", "-arm64",
                str(view / "lib/py3-mac-universal/renpy"), "--savedir", str(savedir), str(basedir), "sn_owned_postinit"]
            run = run_owned(args, env=child_env, cwd=state, logs=logs, name=phase, timeout=timeout)
            run["phase"] = phase
            run["receipt"] = json.loads(receipt.read_text()) if receipt.is_file() else None
            report["runs"].append(run)
            return validate_guard(run, phase, savedir)

        generation = state / "generation"
        import shutil
        shutil.copytree(source, generation)
        invoke("generation", generation, state / "generation-saves", protected)
        compiled = generation / "game/probe.rpyc"
        if not compiled.is_file() or not compiled.stat().st_size:
            raise ValueError("官方没有为新作者样例产生编译文件")
        # 唯一一次作者生成，完成后记录新原件基线；不编译真实游戏。
        with compiled.open("rb") as src, (source / "game/probe.rpyc").open("xb") as dst:
            shutil.copyfileobj(src, dst)
        del snapshots["source_before_generation"]
        snapshots["source"] = (source, BASE.inventory(source))
        bundle = state / "bundle-parent/Probe.app"
        autorun = bundle / "Contents/Resources/autorun"
        autorun.parent.mkdir(parents=True)
        shutil.copytree(source, autorun)
        snapshots["bundle"] = (bundle, BASE.inventory(bundle))
        protected.append(bundle)
        report["fixture_compiled_sha256"] = hashlib.sha256(compiled.read_bytes()).hexdigest()
        report["fixture_frozen_inventory_sha256"] = BASE.inventory_digest(snapshots["source"][1])
        control = invoke("control", autorun, state / "control-saves", protected)
        seed = invoke("seed", autorun, state / "saves", protected)
        precheck = invoke("precheck", autorun, state / "saves", protected)
        compare_preferences(control, seed, precheck)
        report["cross_process_preferences_verified"] = True
        denied_seed = invoke("denied_seed", autorun, state / "denied-saves", protected, deny=True)
        denied_precheck = invoke("denied_precheck", autorun, state / "denied-saves", protected)
        if denied_seed["before"] != control["before"] or denied_precheck["before"] != control["before"]:
            raise ValueError("拒绝保存的独立进程没有恢复原默认值")
        invoke("extra", autorun, state / "extra-saves", protected)
        report["negative_controls_verified"] = ["save_returned_but_not_persisted", "new_process_rejects_missing_seed",
                                                 "extra_savedirs_rejected_before_external_write"]
        for run in [canary] + report["runs"]:
            try:
                os.kill(run["pid"], 0)
            except ProcessLookupError:
                run["pid_reaped"] = True
            else:
                raise ValueError("自有进程没有退出")
        report["ok"] = True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        BASE.finish_integrity(report, snapshots)
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", required=True, help="已备官方 SDK/Steam 扩展及 ZIP 的材料根")
    parser.add_argument("--output", required=True, help="材料和仓库外、尚不存在的私有证据目录")
    parser.add_argument("--timeout", type=float, default=15, help="每个自有子进程截止，1–20 秒")
    args = parser.parse_args()
    try:
        report = probe(args.materials, args.output, args.timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("无窗口偏好实验未启动：" + str(error), file=sys.stderr)
        return 1
    print(str(Path(report["output"]) / "evidence.json"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
