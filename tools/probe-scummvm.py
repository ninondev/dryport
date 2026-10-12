#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只用 ScummVM 2026.3.0 检测原始 AGI 数据；不添加、不启动游戏。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import plistlib
import re
import resource
import signal
import stat
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_HOME

SHARED = importlib.import_module("love-runtime")
RUNTIME = importlib.import_module("scummvm-runtime")
VERSION = "2026.3.0"
SOURCE_COMMIT = "fed42f2068dcafc6aafa1c28c77e4c88def74b66"
ARCHIVE_BYTES = 163659840
ARCHIVE_SHA256 = "d483f41a67d389a3bf50832b9a1a054fbeded56fc148216e4f84a06d330d2172"
ARCHIVE_URL = "https://downloads.scummvm.org/frs/scummvm/2026.3.0/scummvm-2026.3.0-macosx.dmg"
EXPECTED_ID = "agi:lsl1"
OUTPUT_LIMIT = 1024 * 1024
UNKNOWN_REPORT = re.compile(r"The game in '.+?' seems to be an unknown game variant\.")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plain_path(path):
    value = str(path)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("路径含控制字符，无法可靠核对检测输出：" + repr(value))
    return value


def parse_detection(stdout, stderr, game):
    """固定版表格可以有超长描述；完整路径必须匹配，空结果的 exit 0 不是成功。"""
    expected = plain_path(Path(game))
    for stream in (stdout, stderr):
        if not isinstance(stream, str) or len(stream.encode("utf-8")) >= OUTPUT_LIMIT:
            raise ValueError("检测输出缺失或超过 1 MiB，未确认游戏")
        if UNKNOWN_REPORT.search(" ".join(stream.split())):
            raise ValueError("官方检测器报告未知游戏变体，未确认版本")
        if "\x1b" in stream or re.search(r"(?im)^\s*(?:ERROR|FATAL|WARNING)\s*:", stream):
            raise ValueError("检测器报告错误、警告或控制序列，请查看外部日志")
    lines = stdout.splitlines()
    headings = [index for index, line in enumerate(lines)
                if " ".join(line.split()) == "GameID Description Full Path"]
    if len(headings) != 1:
        raise ValueError("没有唯一的官方检测结果表，未确认游戏")
    following = [line for line in lines[headings[0] + 1:] if line.strip()]
    if (len(following) != 2 or not re.fullmatch(r"-+ +-+ +-+", following[0])):
        raise ValueError("检测结果为空、重复或含额外记录，未确认唯一游戏")
    line = following[1]
    game_id = line.split(maxsplit=1)[0]
    if game_id != EXPECTED_ID or not line.startswith(game_id.ljust(30) + " "):
        raise ValueError("检测到的游戏不是唯一的 " + EXPECTED_ID)
    # 绝对路径从首个独立 '/' 开始。描述若也含该歧义则停止，不把路径前缀吞成描述。
    match = re.fullmatch(r"(.+?) +(/.*)", line[31:])
    if not match or match[2] != expected:
        raise ValueError("检测目录与本次原始目录不一致，未确认结果")
    description = match[1].rstrip()
    # printf 的最小字段宽度仍可核对；名称末尾空白与填充无法区分，返回规范化描述。
    if not description or len(line[31:31 + match.start(2)].encode("utf-8")) < 59:
        raise ValueError("检测记录格式异常，未确认游戏")
    return {"game_id": game_id, "description": description, "path": expected,
            "unique": True, "unknown_variant": False}


def architectures(binary):
    """只读 Mach-O 头，不执行未知运行时探版本。"""
    size = binary.stat().st_size
    with binary.open("rb") as stream:
        header = stream.read(4096)
    if len(header) < 8:
        raise ValueError("运行时可执行文件缺少 Mach-O 头")
    magic = header[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        count = struct.unpack_from(">I", header, 4)[0]
        stride = 32 if magic[-1] == 191 else 20
        if not 1 <= count <= 32 or len(header) < 8 + count * stride:
            raise ValueError("运行时通用架构表异常")
        cpus = []
        for index in range(count):
            offset = 8 + index * stride
            cpu = struct.unpack_from(">I", header, offset)[0]
            file_offset, length = struct.unpack_from(">QQ" if stride == 32 else ">II", header, offset + 8)
            if not length or file_offset + length > size:
                raise ValueError("运行时架构条目超出文件范围")
            cpus.append(cpu)
    elif magic == b"\xcf\xfa\xed\xfe":
        cpus = [struct.unpack_from("<I", header, 4)[0]]
    else:
        raise ValueError("运行时不是受支持的 Mach-O 文件")
    return sorted({{0x0100000c: "arm64", 0x01000007: "x86_64"}.get(cpu, str(cpu)) for cpu in cpus})


def _limit_files():
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))


def run_owned(args, *, env, cwd, logs, name, timeout, on_start=None):
    started = time.monotonic()
    stdout_file, stderr_file = logs / (name + ".stdout"), logs / (name + ".stderr")
    with stdout_file.open("xb") as out, stderr_file.open("xb") as err:
        process = subprocess.Popen(args, env=env, cwd=cwd, stdout=out, stderr=err,
                                   start_new_session=True, preexec_fn=_limit_files)
        timed_out = False
        try:
            if on_start is not None:
                on_start(process.pid)
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
            # 启动回执也可能抛错；仍须回收本次子进程，不能只发信号留下僵尸。
            cleanup_deadline = time.monotonic() + 5
            process.wait(timeout=5)
            # 回收 PID 后进程组可能尚未消失；查询被拒不能当作清理成功。
            # 确认也计入原来的五秒收尾预算，超时继续报错。
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                except PermissionError:
                    pass
                if time.monotonic() >= cleanup_deadline:
                    raise subprocess.TimeoutExpired("进程组清理未确认", 5)
                time.sleep(0.01)
    return {"args": list(map(str, args)), "pid": process.pid, "returncode": process.returncode,
            "timed_out": timed_out, "elapsed_seconds": time.monotonic() - started,
            "output_limit_reached": max(stdout_file.stat().st_size, stderr_file.stat().st_size) >= OUTPUT_LIMIT,
            "stdout": stdout_file.read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stderr": stderr_file.read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stdout_file": str(stdout_file), "stderr_file": str(stderr_file)}


def run_failed(run):
    return bool(run["timed_out"] or run["returncode"] or run.get("output_limit_reached"))


def prepare_state(state, binary, game):
    for name in ("home", "tmp", "saves", "screenshots", "logs", "mount"):
        (state / name).mkdir()
    config = state / "scummvm.ini"
    config.write_text("[scummvm]\nfullscreen=false\ngui_language=en\nupdates_check=0\n"
                      + "savepath=" + plain_path(state / "saves") + "\n"
                      + "screenshotpath=" + plain_path(state / "screenshots") + "\n"
                      + "logfile=" + plain_path(state / "logs/scummvm.log") + "\n")
    env = RUNTIME.environment(state)
    # --detect 在固定版命令行设置归并之前返回；配置先写入独立 ini，日志靠外部捕获。
    args = ["/usr/bin/arch", "-arm64", str(binary), "--config=" + str(config),
            "--path=" + plain_path(game), "--detect"]
    return args, env


CANARY = r'''
import errno,json,os,sys
state,protected=sys.argv[1:]
with open(os.path.join(state,"allowed-canary"),"xb") as f: f.write(b"allowed")
try:
    with open(protected,"wb") as f: f.write(b"unexpected")
except OSError as error:
    print(json.dumps({"write_denied":error.errno==errno.EPERM}))
    raise SystemExit(0 if error.errno==errno.EPERM else 2)
raise SystemExit(3)
'''


def reserve_output(output, protected):
    output = Path(os.path.abspath(output))
    plain_path(output)
    if os.path.lexists(output):
        raise FileExistsError("输出已存在，已保留：" + str(output))
    output = output.parent.resolve(strict=True) / output.name
    forbidden = [ROOT.resolve(), SN_HOME.resolve(), *protected]
    if any(output == root or root in output.parents or output in root.parents for root in forbidden):
        raise ValueError("证据输出不能位于仓库或与原始输入重叠")
    output.mkdir(mode=0o700)
    return output


def verify_factory(runtime, archive, state, environment, timeout):
    if archive.stat().st_size != ARCHIVE_BYTES or sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("DMG 大小或 SHA-256 与固定官方 2026.3.0 不符，未执行运行时")
    mount = state / "mount"
    logs = state / "logs"
    result = {"archive_sha256": ARCHIVE_SHA256, "archive_bytes": ARCHIVE_BYTES,
              "archive_url": ARCHIVE_URL, "source_commit": SOURCE_COMMIT}
    try:
        attach = run_owned(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist",
                            "-mountpoint", str(mount), str(archive)], env=environment, cwd=state,
                           logs=logs, name="archive-attach", timeout=timeout)
        result["attach"] = attach
        if run_failed(attach):
            raise ValueError("原厂 DMG 只读挂载失败，未执行运行时")
        info = plistlib.loads(attach["stdout"].encode())
        if not any(item.get("mount-point") == str(mount) for item in info.get("system-entities", [])):
            raise ValueError("原厂 DMG 没有挂载到本次私有目录")
        apps = [path for path in mount.glob("*.app") if path.is_dir() and not path.is_symlink()]
        if len(apps) != 1:
            raise ValueError("原厂 DMG 中没有唯一 app，未猜测运行时")
        official = SHARED.inventory(apps[0])
        if not official or SHARED.inventory(runtime) != official:
            raise ValueError("传入运行时与固定官方归档不一致，未执行")
        result.update(bundle_matches_archive=True, bundle_inventory_sha256=SHARED.inventory_digest(official))
    finally:
        if os.path.ismount(mount):
            detached = run_owned(["/usr/bin/hdiutil", "detach", str(mount)], env=environment,
                                 cwd=state, logs=logs, name="archive-detach", timeout=timeout)
            result["detach"] = detached
            if run_failed(detached) or os.path.ismount(mount):
                raise ValueError("本次只读映像未卸载，请按私有日志处理；未执行运行时")
    return result


def probe(runtime_app, archive, game, output, timeout=15):
    if sys.platform != "darwin" or not 1 <= timeout <= 20:
        raise ValueError("需要 macOS；每个自有进程截止须为 1–20 秒")
    runtime, archive, game = [Path(value).resolve(strict=True) for value in (runtime_app, archive, game)]
    for path in (runtime, archive, game):
        plain_path(path)
    binary = runtime / "Contents/MacOS/scummvm"
    if not runtime.is_dir() or not game.is_dir() or not archive.is_file():
        raise ValueError("需要现有完整 .app、官方 DMG 与原始游戏目录")
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("运行时缺少普通、可执行的 Contents/MacOS/scummvm")
    with (runtime / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleShortVersionString") != VERSION or "arm64" not in architectures(binary):
        raise ValueError("需要固定 2026.3.0 且含 arm64 的原厂运行时")
    original = {"runtime": (runtime, SHARED.inventory(runtime)), "game": (game, SHARED.inventory(game))}
    if not original["game"][1] or any(value[0] == "link" for value in original["game"][1].values()):
        raise ValueError("游戏目录为空或含软链，本探针尚不接受这类输入")
    archive_before = sha256(archive)
    output = reserve_output(output, [runtime, game, archive])
    report = {"protocol": 1, "ok": False, "kind": "scummvm_detect_only", "version": VERSION,
              "created_at": datetime.now(timezone.utc).isoformat(), "output": str(output),
              "expected_game_id": EXPECTED_ID, "game_started": False,
              "source_sha256": sha256(Path(__file__)),
              "boundary": "只验证固定检测器识别原数据，不证明游戏运行、音画、存档或低端性能；SDL dummy 不代表没有 Cocoa/AppKit 初始化。"}
    try:
        state = output / "state"
        state.mkdir(mode=0o700)
        args, environment = prepare_state(state, binary, game)
        report["factory"] = verify_factory(runtime, archive, state, environment, timeout)
        guard = state / "protected-canary"
        guard.mkdir()
        canary_file = guard / "original"
        canary_file.write_bytes(b"owned protection fixture")
        policy = RUNTIME.preferences_guard(state, [runtime, game, archive, guard])
        check = run_owned(policy + [sys.executable, "-I", "-S", "-B", "-c", CANARY, str(state), str(canary_file)],
                          env=environment, cwd=state, logs=state / "logs", name="write-preflight", timeout=timeout)
        report["write_preflight"] = check
        if (run_failed(check) or not json.loads(check["stdout"]).get("write_denied")
                or canary_file.read_bytes() != b"owned protection fixture"):
            raise ValueError("自有写入闸预检失败，未执行检测器")
        run = run_owned(policy + args, env=environment, cwd=state, logs=state / "logs",
                        name="detect", timeout=timeout)
        report["detection_process"] = run
        if run_failed(run):
            raise ValueError("检测器超时、失败或输出到达上限，请查看外部 stdout/stderr")
        report["detected"] = parse_detection(run["stdout"], run["stderr"], game)
        try:
            os.kill(run["pid"], 0)
        except ProcessLookupError:
            run["pid_reaped"] = True
        else:
            raise ValueError("检测器进程仍存在，未确认完成")
        report["ok"] = True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        report["integrity"] = {}
        for label, (path, before) in original.items():
            try:
                after = SHARED.inventory(path)
                same = path.is_dir() and before == after
                report["integrity"][label] = {"unchanged": same, "entries": len(before),
                                               "before_sha256": SHARED.inventory_digest(before),
                                               "after_sha256": SHARED.inventory_digest(after)}
            except (OSError, ValueError) as error:
                report["integrity"][label] = {"unchanged": False, "error": str(error)}
        try:
            report["integrity"]["archive"] = {"unchanged": sha256(archive) == archive_before}
        except OSError as error:
            report["integrity"]["archive"] = {"unchanged": False, "error": str(error)}
        if not all(item["unchanged"] for item in report["integrity"].values()):
            report["ok"] = False
            report.setdefault("error", "最终回读发现原始输入变化，检测结果不能通过")
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-app", required=True, help="固定官方 ScummVM 2026.3.0 的完整 .app")
    parser.add_argument("--archive", required=True, help="已下载的固定原厂 DMG；本工具不下载")
    parser.add_argument("--game", required=True, help="只读的原始游戏数据目录")
    parser.add_argument("--output", required=True, help="仓库和原始输入之外、尚不存在的私有目录")
    parser.add_argument("--timeout", type=float, default=15, help="每个进程截止秒数，1–20，默认15")
    args = parser.parse_args(argv)
    try:
        report = probe(args.runtime_app, args.archive, args.game, args.output, args.timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("检测未启动：" + str(error), file=sys.stderr)
        return 1
    print(str(Path(report["output"]) / "evidence.json"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
