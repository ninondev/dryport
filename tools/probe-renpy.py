#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只运行自有 Ren'Py 8.1.3 样例；不接受游戏输入，不下载或调用 Steam。"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
_shared = importlib.import_module("love-runtime")
inventory = _shared.inventory
inventory_digest = _shared.inventory_digest
run_bounded = _shared.run_bounded
sandbox = _shared.sandbox

VERSION = "8.1.3.23091805"
HASHES = {
    "renpy-8.1.3-sdk.zip": "9e5a0763dd15ebc24c4a08d9766bc826676d1dd35a727610f5746ba6c6e568ec",
    "renpy-8.1.3-steam.zip": "d7470f561fcdc277087a0d86b01d6f73caced8a8d302e66dc494060f536d145c",
}
CHECKSUMS_URL = "https://github.com/renpy/renpy/releases/download/8.1.3.23091805/checksums.txt"
CANARY = b"dryport owned protection fixture\n"
PAYLOAD = b"dryport original Ren'Py fixture\n"

# 自有样例没有 Steam 身份；这项设置不能用于真实游戏或被当作 Steam 兼容证据。
SCRIPT = r'''
define config.name = "dryport RenPy Mechanism"
define config.version = "1"
define config.save_directory = "dryport-renpy-probe"
define config.screen_width = 640
define config.screen_height = 360
define config.default_fullscreen = False
define config.developer = False
define config.has_sound = False
define config.has_music = False
define config.has_voice = False

# 官方 Steam 初始化在 -1499；普通 define 的优先级太晚。
init -1500 python:
    config.enable_steam = False

init python:
    import os
    import sys
    import json
    import platform
    import ctypes

    def sn_stage(value):
        path = os.path.join(os.environ["SN_RENPY_STATE"], "stages-" + os.environ["SN_RENPY_ROUND"] + ".json")
        try:
            with open(path) as stream:
                stages = json.load(stream)
        except IOError:
            stages = []
        if len(stages) < 32:
            stages.append(value)
            with open(path, "w") as stream:
                json.dump(stages, stream)

    def sn_denied(operation):
        try:
            operation()
            return 0
        except OSError as error:
            return error.errno

    def sn_attempt_write(path):
        with open(path, "wb") as stream:
            stream.write(b"UNSAFE self-owned canary write")

    def sn_protected(label, root, state):
        canary = os.path.join(root, "protected.txt")
        result = {}
        result["direct"] = sn_denied(lambda: sn_attempt_write(canary))
        result["symlink"] = sn_denied(lambda: sn_attempt_write(os.path.join(state, label + "-alias", "protected.txt")))
        result["hardlink"] = sn_denied(lambda: os.link(canary, os.path.join(state, label + "-hardlink")))
        parent = os.path.dirname(root)
        grandparent = os.path.dirname(parent)
        result["parent_rename"] = sn_denied(lambda: os.rename(parent, os.path.join(state, label + "-parent-moved")))
        result["grandparent_rename"] = sn_denied(lambda: os.rename(grandparent, os.path.join(state, label + "-grandparent-moved")))
        return result

    def sn_run_probe():
        result = {"ok": False}
        try:
            round_number = int(os.environ["SN_RENPY_ROUND"])
            state = os.environ["SN_RENPY_STATE"]
            result.update(round=round_number, pid=os.getpid(), architecture=platform.machine(),
                          executable=sys.executable, version=renpy.version_only,
                          macapp=renpy.macapp, savedir=config.savedir,
                          steam_enabled=config.enable_steam,
                          fullscreen=bool(_preferences.fullscreen),
                          restored=probe_restored, saved_value=probe_value,
                          renderer=renpy.get_renderer_info())
            value = ctypes.c_int(-1)
            size = ctypes.c_size_t(ctypes.sizeof(value))
            rc = ctypes.CDLL(None).sysctlbyname(b"sysctl.proc_translated", ctypes.byref(value), ctypes.byref(size), None, 0)
            result["translation_status_rc"] = rc
            result["translated"] = value.value if rc == 0 else None
            assert result["architecture"] == "arm64"
            assert result["translated"] in (None, 0)
            assert renpy.macapp and not result["fullscreen"]
            assert config.enable_steam is False
            assert config.savedir == os.environ["SN_RENPY_SAVE"]
            with renpy.file("payload.txt") as stream:
                assert stream.read() == b"dryport original Ren'Py fixture\n"
            assert probe_value == 314159
            assert probe_restored == (round_number == 2)
            previous = persistent.sn_counter or 0
            result["persistent_before"] = previous
            assert previous == round_number - 1
            persistent.sn_counter = round_number
            renpy.save_persistent()
            result["persistent_after"] = persistent.sn_counter
            result["protection"] = {}
            for label in ("source", "bundle", "view"):
                attempts = sn_protected(label, os.environ["SN_RENPY_" + label.upper()], state)
                result["protection"][label] = attempts
                assert all(code == 1 for code in attempts.values()), (label, attempts)
            result["ok"] = True
        except Exception as error:
            result["error"] = repr(error)
        with open(os.environ["SN_RENPY_RECEIPT"], "x") as stream:
            json.dump(result, stream, sort_keys=True)
        return result["ok"]

default probe_value = 0
default probe_restored = False

screen probe_checkpoint():
    timer 0.05 action Return()

label start:
    $ sn_stage("start")
    scene expression Solid("#25384b")
    pause 0.05
    if int(os.environ["SN_RENPY_ROUND"]) == 2 and not probe_restored:
        $ sn_stage("load_requested")
        $ renpy.load("probe-slot")
    if int(os.environ["SN_RENPY_ROUND"]) == 1:
        $ probe_value = 314159
    # timed pause 默认不建检查点；call screen 按正常交互路径记录硬检查点。
    call screen probe_checkpoint
    $ sn_stage("save_requested")
    if int(os.environ["SN_RENPY_ROUND"]) == 1:
        $ renpy.save("probe-slot")
    jump probe_finish

label probe_finish:
    $ sn_stage("checking_restored_state")
    $ sn_ok = sn_run_probe()
    $ renpy.quit(status=0 if sn_ok else 1)

label after_load:
    $ probe_restored = True
    $ sn_stage("after_load")
    return
'''


# 固定 True，不能拿原样例本来就是 False 的结果充当外置偏好证据。
WINDOW_SCRIPT = r'''
define config.name = "dryport RenPy Window Preference"
define config.save_directory = "dryport-renpy-window-probe"
define config.screen_width = 640
define config.screen_height = 360
define config.default_fullscreen = True
define config.developer = False
define config.has_sound = False
define config.has_music = False
define config.has_voice = False

init -1500 python:
    config.enable_steam = False

init python:
    import os
    import json
    import platform
    import pygame

    def sn_window_receipt(stage, **extra):
        result = dict(stage=stage, pid=os.getpid(), architecture=platform.machine(),
                      version=renpy.version_only, default_fullscreen=config.default_fullscreen,
                      fullscreen=_preferences.fullscreen,
                      preferences_initialized=persistent._set_preferences,
                      seed=persistent.sn_window_seed, savedir=config.savedir,
                      macapp=renpy.macapp, steam_enabled=config.enable_steam,
                      interface_absent=renpy.game.interface is None,
                      video_driver=os.environ.get("SDL_VIDEODRIVER"))
        result.update(extra)
        with open(os.environ["SN_RENPY_RECEIPT"] + "." + stage, "x") as stream:
            json.dump(result, stream, sort_keys=True)
        return result

    def sn_window_before_display():
        phase = os.environ["SN_RENPY_WINDOW_PHASE"]
        if phase == "generation":
            return
        if renpy.game.interface is not None or config.default_fullscreen is not True:
            sn_window_receipt("guard", ok=False)
            raise renpy.game.QuitException(status=2)
        if phase == "seed":
            # 只有全新、自有试验的 seed 进程设置偏好；precheck/window 都只观察。
            before = _preferences.fullscreen
            _preferences.fullscreen = False
            persistent._set_preferences = True
            persistent.sn_window_seed = "official-engine-window-v1"
            renpy.save_persistent()
            sn_window_receipt("guard", ok=True, before_fullscreen=before)
        else:
            wanted = phase == "control"
            ok = (_preferences.fullscreen is wanted and
                  (wanted or persistent.sn_window_seed == "official-engine-window-v1"))
            sn_window_receipt("guard", ok=ok)
            if not ok:
                raise renpy.game.QuitException(status=2)
        if phase != "window":
            raise renpy.game.QuitException(status=0)

    # 官方 main 在完整 init 后调用它，随后才创建 Interface。
    renpy.game.post_init.append(sn_window_before_display)
    # 不使用 run 命令，避免 bootstrap 在 init 前尝试显示 presplash。
    renpy.arguments.register_command("sn_window", lambda: True, uses_display=True)

label start:
    scene expression Solid("#25384b")
    pause 0.05
    python:
        flags = pygame.display.get_window().get_window_flags()
        fullscreen_bits = pygame.WINDOW_FULLSCREEN | pygame.WINDOW_FULLSCREEN_DESKTOP
        ok = _preferences.fullscreen is False and not (flags & fullscreen_bits)
        sn_window_receipt("display", ok=ok, window_flags=flags,
                          fullscreen_bits=fullscreen_bits, renderer=renpy.get_renderer_info())
        renpy.quit(status=0 if ok else 2)
'''


def windowing_runs(report, environment, state, autorun, protected, timeout):
    """所有无窗口对照成功后才准许窗口进程；其自身仍在创建 display 前复查。"""
    report["windowing"] = {"ok": False, "runs": [], "default_fullscreen": True,
        "boundary": "外置已初始化偏好优先于 default；不阻断游戏随后主动改 fullscreen。"}
    control = state / "control-saves"
    control.mkdir()
    for phase in ("control", "seed", "precheck", "window"):
        savedir = control if phase == "control" else state / "saves"
        receipt = state / ("window-" + phase + ".json")
        env = {**environment, "SN_RENPY_WINDOW_PHASE": phase,
               "SN_RENPY_RECEIPT": str(receipt), "SN_RENPY_SAVE": str(savedir)}
        # 环境键的约束尚未证明；本对照不让它成为混杂因素。
        env.pop("RENPY_DISABLE_FULLSCREEN", None)
        if phase != "window":
            env.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        args = sandbox(state, protected) + ["/usr/bin/arch", "-arm64",
            str(Path(environment["SN_RENPY_VIEW"]) / "lib/py3-mac-universal/renpy"),
            "--savedir", str(savedir), str(autorun), "sn_window"]
        run = run_bounded(args, env=env, cwd=state, timeout=timeout)
        run["phase"] = phase
        run["guard"] = json.loads(Path(str(receipt) + ".guard").read_text()) if Path(str(receipt) + ".guard").is_file() else None
        report["windowing"]["runs"].append(run)
        data = run["guard"]
        if (run["timed_out"] or run["returncode"] or not data or data.get("ok") is not True
                or data.get("interface_absent") is not True or data.get("default_fullscreen") is not True
                or data.get("pid") != run["pid"] or data.get("architecture") != "arm64"
                or data.get("version") != VERSION or data.get("macapp") is not True
                or data.get("savedir") != str(savedir) or data.get("steam_enabled") is not False
                or data.get("fullscreen") is not (phase == "control") or "[S_API" in run["stderr"]):
            raise ValueError("窗口偏好 " + phase + " 对照未通过，未继续后续进程。")
        if phase == "seed":
            if data.get("before_fullscreen") is not True:
                raise ValueError("seed 进程没有先观察到真实的全屏默认值。")
            persistent = savedir / "persistent"
            if not persistent.is_file() or persistent.stat().st_size == 0:
                raise ValueError("官方引擎没有生成外置 persistent。")
            report["windowing"]["seed_persistent_sha256"] = hashlib.sha256(persistent.read_bytes()).hexdigest()
        if phase != "control" and (data.get("preferences_initialized") is not True
                or data.get("seed") != "official-engine-window-v1"):
            raise ValueError("外置偏好初始化标记与本次运行不一致。")
        if phase == "window":
            display = json.loads(Path(str(receipt) + ".display").read_text()) if Path(str(receipt) + ".display").is_file() else None
            run["display"] = display
            if (not display or display.get("ok") is not True or display.get("fullscreen") is not False
                    or display.get("pid") != run["pid"] or display.get("interface_absent") is not False
                    or display.get("window_flags", -1) & display.get("fullscreen_bits", -1)):
                raise ValueError("实际窗口状态未通过，回执已保留。")
    report["windowing"]["ok"] = True


def archive_inventory(archive, prefix=""):
    import zipfile
    expected = {}
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if not name or name.startswith("/") or ".." in parts or "\\" in name:
                raise ValueError("官方归档含无效路径。")
            if prefix and not name.startswith(prefix):
                raise ValueError("官方归档含预期 SDK 根目录之外的文件。")
            relative = name[len(prefix):].rstrip("/")
            if not relative:
                continue
            if relative in expected or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("当前固定归档不应包含重复条目或软链。")
            if item.is_dir():
                expected[relative] = ["directory"]
            else:
                data = package.read(item)  # ZipFile 同时校验 CRC。
                expected[relative] = ["file", len(data), hashlib.sha256(data).hexdigest()]
    return expected


def verify_materials(materials):
    materials = Path(materials).resolve(strict=True)
    sdk = materials / "official-sdk/renpy-8.1.3-sdk"
    extension = materials / "official-steam"
    result = {}
    for name, root, prefix in (("renpy-8.1.3-sdk.zip", sdk, "renpy-8.1.3-sdk/"),
                               ("renpy-8.1.3-steam.zip", extension, "")):
        archive = materials / "runtime-archives" / name
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != HASHES[name]:
            raise ValueError("官方归档 SHA-256 不符，未执行：" + str(archive))
        expected = archive_inventory(archive, prefix)
        if inventory(root) != expected:
            raise ValueError("原厂目录与已核验归档不一致，未执行：" + str(root))
        result[name] = {"sha256": actual, "entries": len(expected), "files_match_archive": True}
    version = {}
    for node in ast.parse((sdk / "renpy/vc_version.py").read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            version[node.targets[0].id] = ast.literal_eval(node.value)
    if version.get("version") != VERSION or version.get("official") is not True or version.get("nightly") is not False:
        raise ValueError("需要已核验的官方 Ren'Py " + VERSION)
    return sdk, extension, result


def prepare_view(sdk, extension, destination):
    library = Path("lib/py3-mac-universal/libsteam_api.dylib")
    if os.path.lexists(destination) or os.path.lexists(sdk / library):
        raise FileExistsError("执行视图或 Steam 库目标已存在，已保留。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # APFS clone 不共享可写 inode；既有原厂目录保持原样。
    subprocess.run(["/bin/cp", "-cR", str(sdk), str(destination)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    with (extension / library).open("rb") as source, (destination / library).open("xb") as output:
        shutil.copyfileobj(source, output)
    shutil.copystat(extension / library, destination / library)
    (destination / "protected.txt").write_bytes(CANARY)
    expected = inventory(sdk)
    expected[str(library)] = inventory(extension)[str(library)]
    expected["protected.txt"] = ["file", len(CANARY), hashlib.sha256(CANARY).hexdigest()]
    if inventory(destination) != expected:
        raise ValueError("无覆盖执行视图与原厂输入不一致。")


def finish_integrity(report, snapshots):
    report["integrity"] = {}
    for label, (root, before) in snapshots.items():
        try:
            after = inventory(root)
            same = root.is_dir() and before == after
            report["integrity"][label] = {"unchanged": same,
                "before_sha256": inventory_digest(before), "after_sha256": inventory_digest(after)}
        except (OSError, ValueError) as error:
            same = False
            report["integrity"][label] = {"unchanged": False, "error": str(error)}
        if not same:
            report["ok"] = False
            report.setdefault("error", "最终回读发现原件、运行视图或样例发生变化。")


def probe(materials, output, timeout=20, *, windowing=False):
    if sys.platform != "darwin" or not 1 <= timeout <= 20:
        raise ValueError("探针需要 macOS；单轮外部超时须为 1–20 秒。")
    materials = Path(materials).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError("证据目录已存在，已保留：" + str(output))
    output = output.parent.resolve(strict=True) / output.name
    repository = Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        raise ValueError("第三方运行视图和个人证据不能写入源码仓库。")
    if output == materials or materials in output.parents or output in materials.parents:
        raise ValueError("证据目录不能与原厂材料目录重叠。")
    sdk, extension, checked = verify_materials(materials)
    output.mkdir(mode=0o700)
    report = {"protocol": 1, "kind": "owned Ren'Py mechanism; not real game acceptance", "ok": False,
              "created_at": datetime.now(timezone.utc).isoformat(), "version": VERSION,
              "probe_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "materials": str(materials), "official_archives": checked, "checksums_url": CHECKSUMS_URL,
              "steam": "official library present; self-owned fixture has no Steam identity and does not initialize Steam",
              "runs": [], "output": str(output)}
    snapshots = {"sdk": (sdk, inventory(sdk)), "steam_extension": (extension, inventory(extension))}
    try:
        state = output / "state"
        state.mkdir()
        source = state / "source-grandparent/source-parent/OriginalFixture"
        bundle = state / "bundle-grandparent/bundle-parent/Probe.app"
        view = state / "view-grandparent/view-parent/Runtime"
        source.mkdir(parents=True)
        (source / "game").mkdir()
        (source / "protected.txt").write_bytes(CANARY)
        (source / "game/payload.txt").write_bytes(PAYLOAD)
        (source / "game/probe.rpy").write_text(WINDOW_SCRIPT if windowing else SCRIPT)
        (source / "game/script_version.txt").write_text("(8, 1, 3)")
        autorun = bundle / "Contents/Resources/autorun"
        prepare_view(sdk, extension, view)
        snapshots["view"] = (view, inventory(view))
        for name in ("home", "tmp", "saves", "compile-saves", "renpy", "multipersistent", "screenshots"):
            (state / name).mkdir()
        signatures = []
        for relative in ("lib/py3-mac-universal/renpy", "lib/py3-mac-universal/pythonw", "lib/py3-mac-universal/librenpython.dylib",
                         "lib/py3-mac-universal/libsteam_api.dylib"):
            result = run_bounded(["/usr/bin/codesign", "--verify", "--strict", "--arch", "arm64", str(view / relative)])
            signatures.append(result)
            if result["returncode"] or result["timed_out"]:
                raise ValueError("原厂 arm64 签名未通过：" + relative)
        report["factory_arm64_signatures"] = signatures
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
                   "HOME": str(state / "home"), "TMPDIR": str(state / "tmp") + "/",
                   "PYTHONDONTWRITEBYTECODE": "1", "RENPY_DISABLE_FULLSCREEN": "1",
                   "RENPY_PATH_TO_SAVES": str(state / "renpy"),
                   "RENPY_MULTIPERSISTENT": str(state / "multipersistent"),
                   "RENPY_SCREENSHOT_PATTERN": str(state / "screenshots/screenshot%04d.png"),
                   "SN_RENPY_STATE": str(state), "SN_RENPY_SAVE": str(state / "saves"),
                   "SN_RENPY_SOURCE": str(source), "SN_RENPY_BUNDLE": str(bundle), "SN_RENPY_VIEW": str(view)}
        if windowing:
            environment.update(SN_RENPY_WINDOW_PHASE="generation",
                               SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        # 只编译本工具刚写的样例，随后才冻结原件。没有 .rpyc 时，官方 assign_names
        # 每次会用当前秒数生成语句 ID；禁止写回的 .app 无法跨次保留这些 ID。
        generation = state / "fixture-generation"
        shutil.copytree(source, generation)
        # 官方 quit 也先加载脚本并生成缺失的 .rpyc，再于显示前退出。
        # compile 命令会强制重写 common 缓存；此处没有重编原厂文件的必要。
        compile_args = sandbox(state, [source, view, sdk, extension]) + [
            "/usr/bin/arch", "-arm64", str(view / "lib/py3-mac-universal/renpy"),
            "--savedir", str(state / "compile-saves"), str(generation), "quit"]
        compilation = run_bounded(compile_args, env=environment, cwd=state, timeout=timeout)
        report["fixture_compilation"] = compilation
        report["fixture_generation_method"] = "official quit loads newly authored script and creates stable .rpyc before freezing; never applied to real game input"
        compiled = generation / "game/probe.rpyc"
        if (compilation["timed_out"] or compilation["returncode"] or "[S_API" in compilation["stderr"]
                or not compiled.is_file() or compiled.stat().st_size == 0):
            raise ValueError("自有样例编译未通过；没有启动游戏测试。")
        with compiled.open("rb") as reader, (source / "game/probe.rpyc").open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        autorun.parent.mkdir(parents=True)
        shutil.copytree(source, autorun)
        (bundle / "protected.txt").write_bytes(CANARY)
        for label, root in (("source", source), ("bundle", bundle), ("view", view)):
            (state / (label + "-alias")).symlink_to(root)
            if label != "view":
                snapshots[label] = (root, inventory(root))
        if windowing:
            environment.pop("SDL_VIDEODRIVER", None)
            environment.pop("SDL_AUDIODRIVER", None)
            windowing_runs(report, environment, state, autorun, [source, bundle, view, sdk, extension], timeout)
        for number in (() if windowing else (1, 2)):
            receipt = state / ("round-" + str(number) + ".json")
            env = {**environment, "SN_RENPY_RECEIPT": str(receipt), "SN_RENPY_ROUND": str(number)}
            args = sandbox(state, [source, bundle, view, sdk, extension]) + [
                "/usr/bin/arch", "-arm64", str(view / "lib/py3-mac-universal/renpy"),
                "--savedir", str(state / "saves"), str(autorun)]
            run = run_bounded(args, env=env, cwd=state, timeout=timeout)
            report["runs"].append(run)
            run["receipt"] = json.loads(receipt.read_text()) if receipt.is_file() else None
            stages = state / ("stages-" + str(number) + ".json")
            run["stages"] = json.loads(stages.read_text()) if stages.is_file() else None
            data = run["receipt"]
            if run["timed_out"] or run["returncode"] or not data or data.get("ok") is not True:
                raise ValueError("自有样例第 " + str(number) + " 轮未通过；日志与现场已保留。")
            if (data.get("architecture") != "arm64" or data.get("version") != VERSION
                    or data.get("round") != number or data.get("restored") != (number == 2)
                    or data.get("saved_value") != 314159 or data.get("persistent_before") != number - 1
                    or data.get("persistent_after") != number or data.get("savedir") != str(state / "saves")
                    or data.get("macapp") is not True or data.get("fullscreen") is not False
                    or data.get("steam_enabled") is not False or "[S_API" in run["stderr"]
                    or data.get("pid") != run["pid"]):
                raise ValueError("实际版本、架构、存档、窗口或 PID 回执与本次运行不一致。")
            protection = data.get("protection", {})
            attempts_expected = {"direct", "symlink", "hardlink", "parent_rename", "grandparent_rename"}
            if (set(protection) != {"source", "bundle", "view"}
                    or any(set(attempts) != attempts_expected or set(attempts.values()) != {1}
                           for attempts in protection.values())):
                raise ValueError("原件写闸没有返回 EPERM。")
        report["ok"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        finish_integrity(report, snapshots)
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", required=True, help="前一次运行官方 SDK、Steam 扩展与 ZIP 的材料根目录")
    parser.add_argument("--output", required=True, help="尚不存在、且在材料目录之外的证据目录")
    parser.add_argument("--timeout", type=float, default=20, help="单轮外部截止秒数，1–20，默认 20")
    parser.add_argument("--windowing", action="store_true", help="验证外置偏好优先于自有样例的全屏默认值")
    args = parser.parse_args()
    try:
        report = probe(args.materials, args.output, args.timeout, windowing=args.windowing)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Ren'Py 探针未启动：" + str(error), file=sys.stderr)
        return 1
    print(str(Path(report["output"]) / "evidence.json"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
