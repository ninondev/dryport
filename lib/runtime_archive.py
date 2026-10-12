#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""校验固定原厂 ZIP 和解包后的每个条目；只向新的版本目录原子发布。"""
import argparse
import ctypes
import hashlib
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
import zipfile

from paths import SN_RUNTIMES

LOVE_VERSION = "11.5"
LOVE_ARCHIVE = "love-11.5-macos.zip"
LOVE_URL = "https://github.com/love2d/love/releases/download/11.5/" + LOVE_ARCHIVE
LOVE_SHA256 = "6795bb3a1656af6a2fdfe741e150787b481886d3a280327a261a3fdded586913"

# Steam 客户端下载器需要内建 WebSocket 与 fetch 的 Node；22 LTS 两者都有。
# 校验值取自 https://nodejs.org/dist/v22.11.0/SHASUMS256.txt（2026-09-13 核对）。
# 干净机器上不假定有 Homebrew，取法与 LÖVE、NW.js 一致：官方包、固定版本、校验后才用。
NODE_VERSION = "22.11.0"
NODE_ARCHIVE = "node-v22.11.0-darwin-arm64.tar.xz"
NODE_URL = "https://nodejs.org/dist/v22.11.0/" + NODE_ARCHIVE
NODE_SHA256 = "c379a90c6aa605b74042a233ddcda4247b347ba5732007d280e44422cc8f9ecb"


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def archive_manifest(archive, expected_sha, top):
    """先核验发布物，再允许 ZIP 条目变成文件路径。"""
    if archive.is_symlink() or not archive.is_file():
        raise ValueError(f"运行时 ZIP 不存在或是软链：{archive}")
    with archive.open("rb") as source:
        if digest(source) != expected_sha:
            raise ValueError(f"运行时 ZIP SHA-256 不符，文件已保留：{archive}")
        source.seek(0)
        result, folded = {}, set()
        with zipfile.ZipFile(source) as package:
            for info in package.infolist():
                name = info.filename.rstrip("/")
                path = PurePosixPath(name)
                if (not name or "\\" in name or "\x00" in name or path.is_absolute()
                        or any(p in ("", ".", "..") for p in name.split("/"))
                        or path.parts[0] != top):
                    raise ValueError(f"运行时 ZIP 含越界或非规范路径：{info.filename}")
                key = unicodedata.normalize("NFD", name).casefold()
                if key in folded:
                    raise ValueError(f"运行时 ZIP 含重复或大小写冲突路径：{name}")
                folded.add(key)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    target = package.read(info).decode("utf-8")
                    link = PurePosixPath(target)
                    if (not target or link.is_absolute() or ".." in link.parts
                            or "\\" in target or "\x00" in target):
                        raise ValueError(f"运行时 ZIP 含越界链接：{name}")
                    result[name] = ("link", target, mode & 0o777)
                elif info.is_dir():
                    result[name] = ("directory", None, mode & 0o777 or 0o755)
                elif stat.S_IFMT(mode) in (0, stat.S_IFREG):
                    with package.open(info) as payload:
                        result[name] = ("file", digest(payload), mode & 0o777 or 0o644)
                else:
                    raise ValueError(f"运行时 ZIP 含特殊文件：{name}")
    for name in list(result):
        for parent in PurePosixPath(name).parents:
            if str(parent) == ".":
                continue
            existing = result.setdefault(str(parent), ("directory", None, 0o755))
            if existing[0] != "directory":
                raise ValueError(f"运行时 ZIP 路径穿过非目录：{name}")
    return result


def verify_tree(parent, manifest, top):
    root = parent / top
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"运行时 app 不存在或是软链：{root}")
    seen = {top}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            key = path.relative_to(parent).as_posix()
            seen.add(key)
            expected = manifest.get(key)
            if expected is None:
                raise ValueError(f"运行时含原厂 ZIP 外的条目：{path}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                actual = ("link", os.readlink(path))
                if not path.resolve(strict=True).is_relative_to(root.resolve()):
                    raise ValueError(f"运行时链接越出 app：{path}")
            elif stat.S_ISDIR(mode):
                actual = ("directory", None)
            elif stat.S_ISREG(mode):
                with path.open("rb") as source:
                    actual = ("file", digest(source))
                if mode & 0o111 != expected[2] & 0o111:
                    raise ValueError(f"运行时文件执行权限与原厂不符：{path}")
            else:
                raise ValueError(f"运行时含特殊文件：{path}")
            if actual != expected[:2]:
                raise ValueError(f"运行时条目与原厂 ZIP 不符：{path}")
    if seen != set(manifest):
        raise ValueError("运行时缺少原厂条目：" + ", ".join(sorted(set(manifest) - seen)))
    return root


def extract_verified(archive, destination, manifest):
    """destination 必须是本次私有暂存区，不合并已有目录。"""
    destination.mkdir()
    with zipfile.ZipFile(archive) as package:
        for name, (kind, _, mode) in sorted(manifest.items(), key=lambda item: (item[0].count("/"), item[0])):
            path = destination / name
            if kind == "directory":
                path.mkdir(mode=0o755)
            elif kind == "file":
                with package.open(name) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(mode)
        # 最后创建链接，归档内文件永远不会穿过链接写入。
        for name, (kind, target, _) in manifest.items():
            if kind == "link":
                (destination / name).symlink_to(target)


def publish(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def check_love(runtime, archive=None):
    runtime = Path(runtime).absolute()
    if archive is None:
        archive = runtime.parent / LOVE_ARCHIVE
        if not archive.exists():
            archive = SN_RUNTIMES / "build" / LOVE_ARCHIVE
    manifest = archive_manifest(Path(archive), LOVE_SHA256, "love.app")
    if runtime.name != "love.app":
        raise ValueError("LÖVE 运行时必须保留原厂 love.app 名称。")
    verify_tree(runtime.parent, manifest, "love.app")
    with (runtime / "Contents/Info.plist").open("rb") as source:
        metadata = plistlib.load(source)
    if metadata.get("CFBundleShortVersionString") != "11.5a":
        raise ValueError("LÖVE 运行时版本不是固定原厂 11.5 macOS 发布物。")
    architecture = subprocess.check_output(["/usr/bin/file", "-b", str(runtime / "Contents/MacOS/love")], text=True)
    if "arm64" not in architecture:
        raise ValueError("LÖVE 运行时缺少 arm64 主程序。")
    return runtime


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("运行时下载被重定向到非 HTTPS 地址，已停止。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def install_love(root):
    # 联网前固定真实父目录；随后调整入口软链不会把本次下载带到另一位置。
    parent = (Path(root).expanduser() / "love").resolve()
    target = parent / LOVE_VERSION
    if os.path.lexists(target):
        return check_love(target / "love.app")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".love-download-", dir=target.parent))
    archive = stage / LOVE_ARCHIVE
    # 下载和解压失败都保留本次现场；已有版本永远不覆盖。
    try:
        request = urllib.request.Request(LOVE_URL, headers={"User-Agent": "dryport-source-preview"})
        with urllib.request.build_opener(HTTPSOnly()).open(request, timeout=45) as response, archive.open("xb") as output:
            shutil.copyfileobj(response, output)
        manifest = archive_manifest(archive, LOVE_SHA256, "love.app")
        expanded = stage / "expanded"
        extract_verified(archive, expanded, manifest)
        archive.rename(expanded / LOVE_ARCHIVE)
        runtime = check_love(expanded / "love.app")
        publish(expanded, target)
        # 成功后这里只剩空的自有暂存目录；并发新增内容则保留它。
        try:
            stage.rmdir()
        except OSError:
            pass
        return target / runtime.name
    except Exception as error:
        raise ValueError(f"LÖVE 准备失败，现场保留在 {stage}：{error}") from error


def _safe_tar_members(tar, top):
    """只接受 top/ 下的普通文件、目录与指向包内的软链；绝对路径、.. 与硬链接一律拒绝（系统 python 3.9 没有 filter=）。"""
    for member in tar.getmembers():
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts or not (name == top or name.startswith(top + "/")):
            raise ValueError(f"归档里有越界路径：{name}")
        if member.islnk():
            raise ValueError(f"归档里有硬链接：{name}")
        if member.issym():
            target = (Path(name).parent / member.linkname)
            if member.linkname.startswith("/") or ".." in Path(os.path.normpath(str(target))).parts:
                raise ValueError(f"归档里有指向包外的软链：{name} -> {member.linkname}")
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError(f"归档里有不支持的条目：{name}")
        yield member


def check_node(runtime):
    """runtime = SN_RUNTIMES/node/<版本>；bin/node 必须是 arm64 Mach-O，且内建 WebSocket 与 fetch。"""
    runtime = Path(runtime)
    node = runtime / "bin/node"
    if not node.is_file() or node.is_symlink():
        raise ValueError(f"没有 Node 主程序：{node}")
    architecture = subprocess.check_output(["/usr/bin/file", "-b", str(node)], text=True)
    if "arm64" not in architecture:
        raise ValueError("Node 主程序不是 arm64")
    probe = subprocess.run([str(node), "-e", 'if(typeof WebSocket!=="function"||typeof fetch!=="function")process.exit(72)'],
                           capture_output=True, timeout=20)
    if probe.returncode:
        raise ValueError(f"Node 探针失败（退出码 {probe.returncode}）：缺 WebSocket / fetch 或无法运行")
    return node


def install_node(root, url=None, sha256=None):
    """nodejs.org 官方包、固定版本、SHA-256 核对后解到 SN_RUNTIMES/node/<版本>；干净机器不假定有 Homebrew。"""
    parent = (Path(root).expanduser() / "node").resolve()
    target = parent / NODE_VERSION
    if os.path.lexists(target):
        return check_node(target)
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".node-download-", dir=parent))
    archive = stage / NODE_ARCHIVE
    top = NODE_ARCHIVE[:-len(".tar.xz")]
    try:
        request = urllib.request.Request(url or NODE_URL, headers={"User-Agent": "dryport-source-preview"})
        with urllib.request.build_opener(HTTPSOnly()).open(request, timeout=120) as response, archive.open("xb") as output:
            shutil.copyfileobj(response, output)
        with archive.open("rb") as stream:
            actual = digest(stream)
        if actual != (sha256 or NODE_SHA256):
            raise ValueError(f"Node 归档 SHA-256 不符：{actual}")
        expanded = stage / "expanded"; expanded.mkdir()
        import tarfile
        with tarfile.open(archive) as tar:
            members = list(_safe_tar_members(tar, top))
            tar.extractall(expanded, members=members)
        runtime = expanded / top
        check_node(runtime)
        archive.rename(expanded / NODE_ARCHIVE)
        publish(runtime, target)
        try:
            (expanded / NODE_ARCHIVE).unlink(); expanded.rmdir(); stage.rmdir()
        except OSError:
            pass
        return target / "bin/node"
    except Exception as error:
        raise ValueError(f"Node 准备失败，现场保留在 {stage}：{error}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check-love", "install-love", "check-node", "install-node"))
    args = parser.parse_args()
    try:
        if args.action == "install-love":
            runtime = install_love(SN_RUNTIMES)
        elif args.action == "install-node":
            runtime = install_node(SN_RUNTIMES)
        elif args.action == "check-node":
            runtime = check_node(SN_RUNTIMES / "node" / NODE_VERSION)
        else:
            runtime = check_love(SN_RUNTIMES / "love" / LOVE_VERSION / "love.app")
        print(runtime)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
