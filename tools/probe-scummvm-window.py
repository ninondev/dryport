#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""固定 ScummVM 的受限窗口实验；画面、交互与存档恢复须另行人工验收。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEC = importlib.util.spec_from_file_location("scummvm_detection", ROOT / "tools/probe-scummvm.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
RUNTIME = importlib.import_module("scummvm-runtime")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def physical_preferences(path):
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size >= P.OUTPUT_LIMIT:
        raise ValueError("实际偏好路径不是受支持的普通小文件，已保留且未继续")
    return {"bytes": info.st_size, "sha256": P.sha256(path), "device": info.st_dev,
            "inode": info.st_ino, "mtime_ns": info.st_mtime_ns, "birthtime": info.st_birthtime}


def preferences_snapshot(domain, host_home, logs, name):
    """只读实际 bundle 域；原有内容可以非空，绝不清空或修复用户偏好。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,254}", domain):
        raise ValueError("原厂 bundle id 无法安全用于精确偏好域检查")
    path = host_home / "Library/Preferences" / (domain + ".plist")
    before = physical_preferences(path)
    host_env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(host_home),
                "LANG": "C", "LC_ALL": "C"}
    result = P.run_owned(["/usr/bin/defaults", "export", domain, "-"], env=host_env,
                         cwd=logs, logs=logs, name=name, timeout=10)
    if result["timed_out"] or result.get("output_limit_reached"):
        raise ValueError("读取实际偏好域超时或输出过大，未确认原状态")
    if result["returncode"] == 0:
        values = plistlib.loads(result["stdout"].encode())
        if not isinstance(values, dict):
            raise ValueError("实际偏好域导出格式异常")
        present = True
    elif result["returncode"] == 1 and "does not exist" in result["stderr"]:
        values, present = {}, False
    else:
        raise ValueError("无法读取实际偏好域，未将查询失败当作空域")
    after = physical_preferences(path)
    if before != after:
        raise ValueError("读取偏好期间实际 plist 已变化，请保留日志；本工具不清理该域")
    canonical = plistlib.dumps(values, fmt=plistlib.FMT_BINARY, sort_keys=True)
    return {"domain": domain, "physical_path": str(path), "physical": after,
            "export_present": present, "value_sha256": hashlib.sha256(canonical).hexdigest(),
            "key_count": len(values), "export": result}


def preferences_equal(before, after):
    keys = ("domain", "physical_path", "physical", "export_present", "value_sha256", "key_count")
    return all(before[key] == after[key] for key in keys)


def input_integrity(original, archive, archive_before):
    result = {}
    for label, (path, before) in original.items():
        try:
            after = P.SHARED.inventory(path)
            result[label] = {"unchanged": path.is_dir() and before == after,
                             "entries": len(before), "before_sha256": P.SHARED.inventory_digest(before),
                             "after_sha256": P.SHARED.inventory_digest(after)}
        except (OSError, ValueError) as error:
            result[label] = {"unchanged": False, "error": str(error)}
    try:
        result["archive"] = {"unchanged": P.sha256(archive) == archive_before}
    except OSError as error:
        result["archive"] = {"unchanged": False, "error": str(error)}
    return result


def all_unchanged(integrity):
    return all(item["unchanged"] for item in integrity.values())


def prepare_state(state):
    for name in ("home", "tmp", "saves", "screenshots", "logs", "mount", "protected-canary"):
        (state / name).mkdir()
    (state / "protected-canary/original").write_bytes(b"owned protection fixture")
    (state / "scummvm.ini").write_text(
        "[scummvm]\nfullscreen=false\ngui_language=en\nupdates_check=0\n"
        "macos_savepath_migrated=true\nautosave_period=0\n"
        + "savepath=" + P.plain_path(state / "saves") + "\n"
        + "screenshotpath=" + P.plain_path(state / "screenshots") + "\n"
        + "logfile=" + P.plain_path(state / "logs/scummvm.log") + "\n")


def window_arguments(binary, game, state, logs, round_number):
    result = ["/usr/bin/arch", "-arm64", str(binary), "--config=" + str(state / "scummvm.ini"),
              "--path=" + str(game), "--no-fullscreen", "--savepath=" + str(state / "saves"),
              "--screenshotpath=" + str(state / "screenshots"), "--logfile=" + str(logs / "scummvm.log")]
    if round_number == 1:
        result.append("--window-size=800,600")
    return result + [P.EXPECTED_ID]


def probe(runtime_app, archive, game, output, timeout=60, runs=1):
    if sys.platform != "darwin" or not 10 <= timeout <= 180 or type(runs) is not int or runs not in (1, 2):
        raise ValueError("需要 macOS；每轮截止 10–180 秒，轮数只能为 1 或 2")
    runtime, archive, game = [Path(value).resolve(strict=True) for value in (runtime_app, archive, game)]
    for path in (runtime, archive, game):
        P.plain_path(path)
    binary = runtime / "Contents/MacOS/scummvm"
    if (not runtime.is_dir() or not game.is_dir() or not archive.is_file()
            or binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK)):
        raise ValueError("需要完整原厂 app、DMG 和无修改原游戏目录")
    info = plistlib.loads((runtime / "Contents/Info.plist").read_bytes())
    domain = info.get("CFBundleIdentifier")
    if (info.get("CFBundleShortVersionString") != P.VERSION or "arm64" not in P.architectures(binary)
            or not isinstance(domain, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,254}", domain)):
        raise ValueError("需要固定原厂版本、arm64 与明确的 bundle id")
    original = {"runtime": (runtime, P.SHARED.inventory(runtime)), "game": (game, P.SHARED.inventory(game))}
    if not original["game"][1] or any(item[0] == "link" for item in original["game"][1].values()):
        raise ValueError("游戏为空或含软链，本受限实验未支持")
    archive_before = P.sha256(archive)
    # CFPreferences 的物理域采用系统账号 home，不能拿实验 HOME 猜真实偏好位置。
    host_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    prefs_path = host_home / "Library/Preferences" / (domain + ".plist")
    output = P.reserve_output(output, [runtime, archive, game, prefs_path])
    state = output / "state"
    report = {"protocol": 1, "kind": "scummvm_window_experiment", "ok": False,
              "created_utc": datetime.now(timezone.utc).isoformat(), "output": str(output),
              "source_sha256": P.sha256(Path(__file__)), "runtime_helper_sha256": P.sha256(ROOT / "lib/scummvm-runtime.py"),
              "detection_helper_sha256": P.sha256(Path(P.__file__)),
              "shared_runtime_sha256": P.sha256(ROOT / "lib/love-runtime.py"),
              "requested_runs": runs, "timeout_seconds": timeout, "rounds": [], "manual_acceptance": False,
              "boundary": "自动结果只确认限时退出、指定原件与实际偏好域保持。画面、输入、音频、存档恢复和低端性能须另验。禁止访问两个 cfprefsd 服务不保证读隔离或同步返回失败，也不是透明重定向。"}
    baseline = None
    try:
        state.mkdir(mode=0o700)
        prepare_state(state)
        headless = RUNTIME.environment(state)
        report["headless_environment"] = headless
        report["window_environment"] = RUNTIME.environment(state, headless=False)
        report["factory"] = P.verify_factory(runtime, archive, state, headless, 15)
        baseline = preferences_snapshot(domain, host_home, state / "logs", "preferences-before")
        report["preferences_before"] = baseline
        policy = RUNTIME.preferences_guard(state, [runtime, game, archive, state / "protected-canary", prefs_path])
        check = P.run_owned(policy + [sys.executable, "-I", "-S", "-B", "-c", P.CANARY,
                                     str(state), str(state / "protected-canary/original")],
                            env=headless, cwd=state, logs=state / "logs", name="write-preflight", timeout=10)
        report["write_preflight"] = check
        if (P.run_failed(check) or not json.loads(check["stdout"]).get("write_denied")
                or (state / "protected-canary/original").read_bytes() != b"owned protection fixture"):
            raise ValueError("自有写入闸未通过，未启动游戏窗口")
        # 不能只因运行时认识某个游戏 ID，就对任意目录直接启动同一引擎。
        detected = P.run_owned(policy + ["/usr/bin/arch", "-arm64", str(binary),
                              "--config=" + str(state / "scummvm.ini"), "--path=" + str(game), "--detect"],
                              env=headless, cwd=state, logs=state / "logs", name="detect", timeout=15)
        report["detection_process"] = detected
        if P.run_failed(detected):
            raise ValueError("原数据检测失败，未启动游戏窗口")
        report["detected"] = P.parse_detection(detected["stdout"], detected["stderr"], game)
        for number in range(1, runs + 1):
            logs = state / "logs" / ("round-" + str(number))
            logs.mkdir()
            current = {"number": number, "ok": False, "environment": report["window_environment"]}
            report["rounds"].append(current)
            try:
                current["integrity_before"] = input_integrity(original, archive, archive_before)
                current["preferences_before"] = preferences_snapshot(domain, host_home, logs, "preferences-before")
                if (not all_unchanged(current["integrity_before"])
                        or not preferences_equal(baseline, current["preferences_before"])):
                    raise ValueError("窗口启动前原件或实际偏好域已变化，已停止")
                arguments = policy + window_arguments(binary, game, state, logs, number)
                def started(pid):
                    value = {"event": "started", "round": number, "pid": pid,
                             "timeout_seconds": timeout, "logs": str(logs), "manual_acceptance": False}
                    current["pid"] = pid
                    write_json(logs / "started.json", value)
                    emit(value)
                current["process"] = P.run_owned(arguments, env=current["environment"],
                                                   cwd=state, logs=logs, name="game", timeout=timeout, on_start=started)
                if P.run_failed(current["process"]):
                    raise ValueError("本次运行超时、异常退出或输出到达上限，已停止后续轮次")
                current["ok"] = True
            finally:
                current["integrity_after"] = input_integrity(original, archive, archive_before)
                current["preferences_after"] = preferences_snapshot(domain, host_home, logs, "preferences-after")
                current["preferences_unchanged"] = preferences_equal(baseline, current["preferences_after"])
                current["ok"] = current["ok"] and all_unchanged(current["integrity_after"]) and current["preferences_unchanged"]
                write_json(logs / "evidence.json", current)
            if not current["ok"]:
                raise ValueError("本次运行原件或实际偏好域变化，已保留证据且停止重开")
        report["ok"] = len(report["rounds"]) == runs and all(item["ok"] for item in report["rounds"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        report["integrity_final"] = input_integrity(original, archive, archive_before)
        report["ok"] = report["ok"] and all_unchanged(report["integrity_final"])
        if baseline is not None:
            try:
                report["preferences_final"] = preferences_snapshot(domain, host_home, state / "logs", "preferences-final")
                report["preferences_unchanged"] = preferences_equal(baseline, report["preferences_final"])
                report["ok"] = report["ok"] and report["preferences_unchanged"]
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                report["ok"] = False
                report["preferences_error"] = str(error)
        write_json(output / "evidence.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime-app", "archive", "game", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--timeout", type=float, default=60, help="每轮 10–180 秒，默认 60")
    parser.add_argument("--runs", type=int, default=1, help="1 或 2；第二轮沿用 state 且不传窗口大小")
    args = parser.parse_args(argv)
    try:
        result = probe(args.runtime_app, args.archive, args.game, args.output, args.timeout, args.runs)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        emit({"event": "failed", "ok": False, "error": str(error)})
        return 1
    emit({"event": "finished", "ok": result["ok"], "report": str(Path(result["output"]) / "evidence.json"),
          "manual_acceptance": False})
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
