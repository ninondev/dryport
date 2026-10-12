#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""准备并校验固定原厂 Ren'Py 8.1.3 依赖；只下载运行时，不下载或运行游戏。"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from paths import SN_HOME, SN_RUNTIMES, STEAM
import runtime_archive as ARCHIVE

VERSION = "8.1.3.23091805"
SDK_TOP = "renpy-8.1.3-sdk"
SDK_NAME = "renpy-8.1.3-sdk.zip"
STEAM_NAME = "renpy-8.1.3-steam.zip"
SDK_SHA256 = "9e5a0763dd15ebc24c4a08d9766bc826676d1dd35a727610f5746ba6c6e568ec"
STEAM_SHA256 = "d7470f561fcdc277087a0d86b01d6f73caced8a8d302e66dc494060f536d145c"
SDK_BYTES = 136589334
STEAM_BYTES = 420020
RELEASE_URL = "https://github.com/renpy/renpy/releases/download/" + VERSION + "/"
SELECTED_ROOTS = ("lib/python3.9", "lib/py3-mac-universal")
STEAM_LIBRARY = "lib/py3-mac-universal/libsteam_api.dylib"
SDK_BINARIES = ("librenpython.dylib", "python", "pythonw", "renpy", "zsync", "zsyncmake")
DEFAULT_OUTPUT = SN_RUNTIMES / "renpy" / VERSION


def archive_spec(kind):
    if kind not in ("sdk", "steam"):
        raise ValueError("未知的 Ren'Py 原厂归档种类。")
    name, digest, size = ((SDK_NAME, SDK_SHA256, SDK_BYTES) if kind == "sdk"
                          else (STEAM_NAME, STEAM_SHA256, STEAM_BYTES))
    return name, digest, size


def cache_path(kind):
    return SN_RUNTIMES / "build" / "renpy" / VERSION / archive_spec(kind)[0]


def fixed_archive(path, kind):
    path = plain_file(path)
    _, expected_sha, expected_size = archive_spec(kind)
    if path.stat().st_size != expected_size or sha(path) != expected_sha:
        raise ValueError(f"原厂 ZIP SHA-256 或字节数不符，文件已保留：{path}")
    return path


def sha(path):
    with Path(path).open("rb") as stream:
        return ARCHIVE.digest(stream)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    Path(path).chmod(0o600)


def plain_file(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"归档不存在或是软链：{path}")
    return path.resolve(strict=True)


def file_identity(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def subset(manifest, roots):
    result = {key: value for key, value in manifest.items()
              if any(key == root or key.startswith(root + "/") for root in roots)}
    if any(root not in result for root in roots):
        raise ValueError("固定原厂归档缺少运行时目录或许可文件。")
    if any(value[0] == "link" for value in result.values()):
        raise ValueError("所选运行依赖含链接，未解包；归档已保留。")
    for key in list(result):
        for parent in PurePosixPath(key).parents:
            if str(parent) != ".":
                result[str(parent)] = manifest[str(parent)]
    return result


def manifests(sdk_archive, steam_archive):
    for kind, path in (("sdk", sdk_archive), ("steam", steam_archive)):
        # archive_manifest 随后在同一个打开的 ZIP 上核 SHA 和条目，不重复散列整个 SDK。
        if plain_file(path).stat().st_size != archive_spec(kind)[2]:
            raise ValueError(f"原厂 ZIP SHA-256 或字节数不符，文件已保留：{path}")
    sdk = subset(ARCHIVE.archive_manifest(sdk_archive, SDK_SHA256, SDK_TOP),
                 tuple(SDK_TOP + "/" + path for path in SELECTED_ROOTS) + (SDK_TOP + "/LICENSE.txt",))
    steam = subset(ARCHIVE.archive_manifest(steam_archive, STEAM_SHA256, "lib"), (STEAM_LIBRARY,))
    # Runtime 精确包含两个完整目录与原厂 macOS Steam 库；SDK 许可留在包外层。
    combined = {"Runtime": ("directory", None, 0o755)}
    for key, value in sdk.items():
        if key.startswith(SDK_TOP + "/lib"):
            combined["Runtime/" + key[len(SDK_TOP) + 1:]] = value
    for key, value in steam.items():
        destination = "Runtime/" + key
        if destination in combined and combined[destination] != value:
            raise ValueError("两个原厂归档的运行依赖互相冲突，未合并。")
        combined[destination] = value
    for name in SDK_BINARIES:
        if combined.get("Runtime/lib/py3-mac-universal/" + name, (None,))[0] != "file":
            raise ValueError("固定原厂归档缺少 macOS 程序：" + name)
    return sdk, steam, combined


def source_records(sdk, steam):
    return {"sdk": {"path": str(sdk), "url": RELEASE_URL + SDK_NAME,
                    "sha256": SDK_SHA256, "bytes": sdk.stat().st_size},
            "steam": {"path": str(steam), "url": RELEASE_URL + STEAM_NAME,
                      "sha256": STEAM_SHA256, "bytes": steam.stat().st_size},
            "checksums_url": RELEASE_URL + "checksums.txt"}


def run_owned(argv, logs, label, *, env=None, cwd=None, timeout=15):
    """进程在自有组中执行；超时、回执异常和正常退出都回收该组，不碰其他进程。"""
    started = time.monotonic()
    stdout_path, stderr_path = logs / (label + ".stdout"), logs / (label + ".stderr")
    process = None
    timed_out = False
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(list(map(str, argv)), env=env if env is not None else {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, cwd=cwd, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        if max(stdout_path.stat().st_size, stderr_path.stat().st_size) > 1024 * 1024:
            raise ValueError("运行时检查输出超过 1 MiB；原始日志已保留。")
        result = {"argv": list(map(str, argv)), "pid": process.pid, "returncode": process.returncode,
                  "timed_out": timed_out, "elapsed_seconds": time.monotonic() - started,
                  "stdout": stdout_path.read_text(errors="replace"),
                  "stderr": stderr_path.read_text(errors="replace"), "reaped": process.poll() is not None}
        write_json(logs / (label + ".json"), result)
        return result
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def require_success(result, message):
    if result["returncode"] != 0 or result["timed_out"]:
        raise ValueError(message + "；检查日志已保留。")


STDLIB_PROBE = r'''
import ctypes, encodings, hashlib, importlib.util, json, math, os, pickle, platform, ssl, sys, zlib
raw = b"dryport-stdlib-only"
assert zlib.decompress(zlib.compress(raw)) == raw
assert pickle.loads(pickle.dumps((1, "stdlib"))) == (1, "stdlib")
images = ctypes.CDLL(None)
images._dyld_image_count.restype = ctypes.c_uint32
images._dyld_get_image_name.argtypes = [ctypes.c_uint32]
images._dyld_get_image_name.restype = ctypes.c_char_p
print(json.dumps({"architecture": platform.machine(), "python_version": list(sys.version_info[:3]),
 "executable": sys.executable, "path": sys.path, "renpy_absent": importlib.util.find_spec("renpy") is None,
 "stdlib": {m.__name__: getattr(m, "__file__", None) for m in (ctypes, encodings, hashlib, json, math, pickle, ssl, zlib)},
 "loaded_images": [images._dyld_get_image_name(i).decode() for i in range(images._dyld_image_count())],
 "sha256": hashlib.sha256(raw).hexdigest(), "openssl_version": ssl.OPENSSL_VERSION}))
'''


def validate_origin(data, runtime, script):
    expected = runtime / "lib/py3-mac-universal/pythonw"
    paths = data.get("path")
    if (data.get("architecture") != "arm64" or data.get("python_version", [])[:2] != [3, 9]
            or data.get("executable") != str(expected) or data.get("renpy_absent") is not True
            or not isinstance(paths, list) or len(paths) < 2 or paths[0] != str(script.parent)):
        raise ValueError("原厂 Python 身份或搜索路径不符，未准备运行时。")
    for path in paths[1:]:
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).resolve().is_relative_to(runtime / "lib"):
            raise ValueError("原厂 Python 搜索路径越出所选运行依赖。")
    modules = data.get("stdlib", {})
    if set(modules) != {"ctypes", "encodings", "hashlib", "json", "math", "pickle", "ssl", "zlib"}:
        raise ValueError("原厂 Python 标准库导入结果不完整。")
    for path in modules.values():
        if path is not None and not Path(path).resolve().is_relative_to(runtime / "lib"):
            raise ValueError("标准库从所选运行依赖之外加载。")
    images = data.get("loaded_images")
    if not isinstance(images, list) or not images:
        raise ValueError("缺少原厂 Python 动态库加载记录。")
    for path in images:
        if (not isinstance(path, str) or not Path(path).is_absolute()
                or not (Path(path).resolve().is_relative_to(runtime)
                        or path.startswith(("/System/Library/", "/usr/lib/", "/System/Volumes/Preboot/Cryptexes/OS/usr/lib/")))):
            raise ValueError("原厂 Python 加载了所选依赖和系统库之外的动态库。")


def validate_native(runtime, state):
    if sys.platform != "darwin":
        raise ValueError("准备原厂 arm64 运行依赖需要 macOS。")
    state.mkdir()
    for name in ("home", "tmp", "logs"):
        (state / name).mkdir()
    logs, binaries = state / "logs", runtime / "lib/py3-mac-universal"
    signatures, architectures = {}, {}
    for name in (*SDK_BINARIES, "libsteam_api.dylib"):
        arch = run_owned(["/usr/bin/lipo", binaries / name, "-archs"], logs, "arch-" + name, timeout=10)
        require_success(arch, "原厂架构检查失败：" + name)
        if not {"arm64", "x86_64"}.issubset(arch["stdout"].split()):
            raise ValueError("原厂 universal 架构不完整：" + name)
        architectures[name] = arch["stdout"].strip().split()
        # 官方 SDK 的 x86_64 slice 未签名；本安装器只执行并严格核验 arm64，不重签原件。
        checked = run_owned(["/usr/bin/codesign", "--verify", "--strict", "--architecture", "arm64", binaries / name],
                            logs, "signature-" + name, timeout=10)
        require_success(checked, "原厂 arm64 签名检查失败：" + name)
        signatures[name] = checked
    script = state / "stdlib.py"
    script.write_text(STDLIB_PROBE, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(state / "home"),
           "TMPDIR": str(state / "tmp") + "/", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"}
    # 不初始化引擎或 Steam；额外限制只作用于本次检查子进程，不是透明的偏好隔离方案。
    policy = importlib.import_module("love-runtime").sandbox(state, [runtime])
    policy[-1] += '\n(deny network*)\n(deny mach-lookup (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon"))\n'
    origin = run_owned(policy + ["/usr/bin/arch", "-arm64", binaries / "pythonw", "-B", "-s", script],
                       logs, "stdlib", env=env, cwd=state, timeout=15)
    require_success(origin, "原厂 Python 无游戏标准库检查失败")
    try:
        data = json.loads(origin["stdout"])
    except (ValueError, TypeError) as error:
        raise ValueError("原厂 Python 检查没有返回完整 JSON；原始日志已保留。") from error
    validate_origin(data, runtime, script)
    return {"architecture": "arm64", "architectures": architectures, "signatures": signatures,
            "signature_scope": "original arm64 slices; SDK x86_64 slices are unsigned",
            "stdlib": origin, "stdlib_data": data, "environment": env, "game_started": False,
            "steam_api_initialized": False}


def package_summary(package, combined):
    files = [package / path for path, value in combined.items() if value[0] == "file"]
    return {"files": len(files), "bytes": sum(path.stat().st_size for path in files),
            "manifest_sha256": hashlib.sha256(json.dumps(combined, sort_keys=True).encode()).hexdigest()}


def read_receipt(output):
    output = Path(output).expanduser().absolute()
    if output.is_symlink() or not output.is_dir():
        raise ValueError(f"运行时目录不存在或是软链：{output}")
    output = output.resolve(strict=True)
    receipt_path = output / "runtime.json"
    if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size > 1024 * 1024:
        raise ValueError("运行时来源回执缺失、过大或是软链。")
    receipt = json.loads(receipt_path.read_text())
    if (not isinstance(receipt, dict) or type(receipt.get("protocol")) is not int
            or receipt.get("protocol") != 1 or receipt.get("version") != VERSION):
        raise ValueError("运行时来源回执版本不符。")
    try:
        sources = receipt["sources"]
        if sources["checksums_url"] != RELEASE_URL + "checksums.txt":
            raise ValueError("运行时回执的固定原厂来源不符。")
        for kind in ("sdk", "steam"):
            name, digest, size = archive_spec(kind)
            item = sources[kind]
            if (not isinstance(item["path"], str) or not Path(item["path"]).is_absolute()
                    or item["url"] != RELEASE_URL + name or item["sha256"] != digest
                    or type(item["bytes"]) is not int or item["bytes"] != size):
                raise ValueError("运行时回执的固定原厂来源不符。")
    except (KeyError, TypeError) as error:
        raise ValueError("运行时来源回执缺少固定原厂来源。") from error
    return output, receipt


def resolve_archive(receipt, kind, explicit=None):
    if explicit is not None:
        return plain_file(explicit)
    recorded = Path(receipt["sources"][kind]["path"])
    # 只补临时材料已消失这一种情况。存在但损坏或是链接的原件仍须报错，不能藏在缓存后面。
    return plain_file(recorded if os.path.lexists(recorded) else cache_path(kind))


def check(output=DEFAULT_OUTPUT, sdk_archive=None, steam_archive=None):
    """只读核对；清单重新来自固定 SHA 的归档，不信任安装回执自报的文件哈希。"""
    output, receipt = read_receipt(output)
    sdk_archive = resolve_archive(receipt, "sdk", sdk_archive)
    steam_archive = resolve_archive(receipt, "steam", steam_archive)
    sdk, _, combined = manifests(sdk_archive, steam_archive)
    expected_sources = source_records(sdk_archive, steam_archive)
    if (receipt.get("sources", {}).get("checksums_url") != expected_sources["checksums_url"]
            or any(receipt.get("sources", {}).get(kind, {}).get(field) != expected_sources[kind][field]
                   for kind in ("sdk", "steam") for field in ("url", "sha256", "bytes"))):
        raise ValueError("运行时回执的固定原厂来源不符。")
    ARCHIVE.verify_tree(output, combined, "Runtime")
    license_path = output / "LICENSE.txt"
    if (license_path.is_symlink() or not license_path.is_file()
            or sha(license_path) != sdk[SDK_TOP + "/LICENSE.txt"][1]):
        raise ValueError("随附原厂许可文件缺失或内容不符。")
    expected = json.loads(json.dumps(combined))
    summary = package_summary(output, combined)
    if receipt.get("manifest") != expected or receipt.get("payload") != summary or receipt.get("selected_roots") != list(SELECTED_ROOTS):
        raise ValueError("运行时回执与固定原厂清单不一致。")
    return {"protocol": 1, "version": VERSION, "package": str(output), "runtime": str(output / "Runtime"),
            "pythonw": str(output / "Runtime/lib/py3-mac-universal/pythonw"), "payload": summary,
            "game_started": False, "check_scope": "archive SHA and exact runtime bytes; no game execution"}


def cache_archive(kind, *, source=None, package=DEFAULT_OUTPUT):
    """保留固定 ZIP 到仓库外缓存；已有缓存不覆盖，失败暂存保留以便检查。"""
    configured = cache_path(kind).expanduser().absolute()
    parent = configured.parent.resolve()
    target = parent / configured.name
    protected_paths = (SN_HOME, STEAM, Path(package))
    protected = tuple(path.resolve() for path in protected_paths)
    existing = parent
    missing = []
    while not existing.exists():
        missing.append(existing)
        existing = existing.parent
    identity = {existing: file_identity(existing)[:2]}

    def guard():
        if configured.parent.resolve() != parent:
            raise ValueError("运行时缓存父目录的规范路径变化，未发布缓存。")
        roots = protected + tuple(path.resolve() for path in protected_paths)
        if any(target.is_relative_to(path) or path.is_relative_to(target) for path in roots):
            raise ValueError("运行时缓存不能进入代码、Steam 或运行时包内；请选择仓库外的 SN_RUNTIMES。")
        if any(path.resolve(strict=True) != path or not path.is_dir()
               or file_identity(path)[:2] != value for path, value in identity.items()):
            raise ValueError("运行时缓存父目录的身份变化，未发布缓存。")

    guard()
    origin = fixed_archive(source, kind) if source is not None else None
    origin_identity = file_identity(origin) if origin is not None else None
    if os.path.lexists(target):
        return origin if origin == target else fixed_archive(target, kind)
    for directory in reversed(missing):
        guard()
        directory.mkdir()
        identity[directory] = file_identity(directory)[:2]
        guard()
    stage = Path(tempfile.mkdtemp(prefix=".renpy-download-", dir=parent))
    temporary = stage / target.name
    name, _, expected_size = archive_spec(kind)
    try:
        if origin is None:
            request = urllib.request.Request(RELEASE_URL + name, headers={"User-Agent": "dryport-source-preview"})
            stream = urllib.request.build_opener(ARCHIVE.HTTPSOnly()).open(request, timeout=15)
        else:
            stream = origin.open("rb")
        with stream, temporary.open("xb") as output:
            deadline = time.monotonic() + 180
            size = 0
            while True:
                block = stream.read(1024 * 1024)
                if time.monotonic() > deadline:
                    raise ValueError("固定原厂 ZIP 获取超过 180 秒，请稍后重试。")
                if not block:
                    break
                size += len(block)
                if size > expected_size:
                    raise ValueError("原厂 ZIP 超过固定字节数，下载已停止。")
                output.write(block)
        guard()
        if origin is not None and (origin.resolve(strict=True) != origin or file_identity(origin) != origin_identity):
            raise ValueError("原厂 ZIP 在复制期间变化，未发布缓存。")
        fixed_archive(temporary, kind)
        guard()
        ARCHIVE.publish(temporary, target)
        guard()
        fixed_archive(target, kind)
        stage.rmdir()
        return target
    except Exception as error:
        raise ValueError(f"Ren'Py 归档缓存未完成，已有文件未覆盖，现场保留在 {stage}：{error}") from error


def install(output=DEFAULT_OUTPUT):
    """复用持久缓存或正确的既有原厂 ZIP，缺失才联网；已有 Runtime 与回执不改写。"""
    receipt = read_receipt(output)[1] if os.path.lexists(output) else None
    archives = []
    for kind in ("sdk", "steam"):
        original = Path(receipt["sources"][kind]["path"]) if receipt else None
        if original is not None and not os.path.lexists(original):
            original = None
        # cache_archive 仍检查存在的原路径，不能用好缓存掩盖被改过的源文件。
        archives.append(cache_archive(kind, source=original, package=output))
    return prepare(*archives, output=output)


def prepare(sdk_archive, steam_archive, output=DEFAULT_OUTPUT):
    sdk_archive, steam_archive = plain_file(sdk_archive), plain_file(steam_archive)
    output = Path(os.path.abspath(Path(output).expanduser()))
    if os.path.lexists(output):
        # 不为复用路径再执行程序，也不修补、合并或覆盖已有内容。
        return check(output, sdk_archive, steam_archive)
    protected_sources = (SN_HOME, STEAM, sdk_archive, steam_archive)
    protected = tuple(path.resolve() for path in protected_sources)
    canonical = output.parent.resolve() / output.name
    if any(canonical.is_relative_to(path) or path.is_relative_to(canonical) for path in protected):
        raise ValueError("运行时输出不能位于源码、Steam 或归档的路径内，也不能是它们的祖先。")
    canonical.parent.mkdir(parents=True, exist_ok=True)
    output = canonical.parent.resolve(strict=True) / canonical.name
    identity = file_identity(output.parent)[:2]
    archive_ids = {path: file_identity(path) for path in (sdk_archive, steam_archive)}

    def guard():
        if output.parent.resolve(strict=True) != output.parent or file_identity(output.parent)[:2] != identity:
            raise ValueError("输出父目录的规范路径或身份变化，运行时尚未发布。")
        if os.path.lexists(output):
            raise FileExistsError(f"输出已存在，原内容已保留：{output}")
        roots = protected + tuple(path.resolve() for path in protected_sources)
        if any(output.is_relative_to(path) or path.is_relative_to(output) for path in roots):
            raise ValueError("输出进入保护路径，运行时尚未发布。")
        if any(path.resolve(strict=True) != path or file_identity(path) != value for path, value in archive_ids.items()):
            raise ValueError("原厂归档在准备期间变化，运行时尚未发布。")

    guard()
    sdk, steam, combined = manifests(sdk_archive, steam_archive)
    guard()
    stage = Path(tempfile.mkdtemp(prefix=".renpy-prepare-", dir=output.parent))
    try:
        guard()
        ARCHIVE.extract_verified(sdk_archive, stage / "sdk", sdk)
        ARCHIVE.verify_tree(stage / "sdk", sdk, SDK_TOP)
        guard()
        ARCHIVE.extract_verified(steam_archive, stage / "steam", steam)
        ARCHIVE.verify_tree(stage / "steam", steam, "lib")
        guard()
        package = stage / "package"
        package.mkdir()
        original = stage / "sdk" / SDK_TOP
        (original / "LICENSE.txt").rename(package / "LICENSE.txt")
        original.rename(package / "Runtime")
        (stage / "steam" / STEAM_LIBRARY).rename(package / "Runtime" / STEAM_LIBRARY)
        runtime = ARCHIVE.verify_tree(package, combined, "Runtime")
        native = validate_native(runtime, stage / "validation")
        guard()
        ARCHIVE.verify_tree(package, combined, "Runtime")
        receipt = {"protocol": 1, "version": VERSION, "sources": source_records(sdk_archive, steam_archive),
                   "selected_roots": list(SELECTED_ROOTS), "manifest": combined,
                   "payload": package_summary(package, combined), "native_validation": native,
                   "validation_location": str(stage / "validation"), "game_started": False,
                   "source_sha256": sha(Path(__file__)), "shared_archive_code_sha256": sha(Path(ARCHIVE.__file__))}
        guard()
        write_json(package / "runtime.json", receipt)
        check(package, sdk_archive, steam_archive)
        guard()
        ARCHIVE.publish(package, output)
        return {"protocol": 1, "version": VERSION, "package": str(output), "runtime": str(output / "Runtime"),
                "pythonw": str(output / "Runtime/lib/py3-mac-universal/pythonw"), "payload": receipt["payload"],
                "validation_location": str(stage / "validation"), "game_started": False}
    except Exception as error:
        raise ValueError(f"Ren'Py 运行依赖准备失败，已有内容未覆盖，现场保留在 {stage}：{error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "install"))
    parser.add_argument("--sdk-archive", type=Path)
    parser.add_argument("--steam-archive", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.action == "prepare" and (args.sdk_archive is None or args.steam_archive is None):
        parser.error("prepare 需要 --sdk-archive 和 --steam-archive；请提供已有的固定原厂 ZIP。")
    if args.action == "install" and (args.sdk_archive is not None or args.steam_archive is not None):
        parser.error("install 只使用固定缓存、已有回执的原厂 ZIP 或官方 URL；自有归档路径请用 prepare。")
    try:
        if args.action == "install":
            result = install(args.output)
        elif args.action == "prepare":
            result = prepare(args.sdk_archive, args.steam_archive, args.output)
        else:
            result = check(args.output, args.sdk_archive, args.steam_archive)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
