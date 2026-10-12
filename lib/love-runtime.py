# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""LÖVE 探针和生产启动层共用的清单、进程边界与写入保护。"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time


def inventory(root):
    result = {}
    for base, dirs, files in os.walk(root):
        for name in sorted(dirs + files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = ["link", os.readlink(path)]
            elif path.is_dir():
                result[relative] = ["directory"]
            elif stat.S_ISREG(path.lstat().st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                result[relative] = ["file", path.stat().st_size, digest.hexdigest()]
            else:
                raise ValueError(f"目录含不支持的特殊文件：{path}")
    return result


def inventory_digest(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run_bounded(args, *, env=None, cwd=None, timeout=15):
    started = time.monotonic()
    process = subprocess.Popen(args, env=env, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        # 仅清理本次独立进程组，包含主进程正常退出后仍遗留的自有子进程。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return dict(args=list(map(str, args)), pid=process.pid, returncode=process.returncode,
                timed_out=timed_out, elapsed_seconds=time.monotonic() - started,
                stdout=stdout, stderr=stderr)


def sandbox(state, protected):
    args = ["/usr/bin/sandbox-exec", "-D", "STATE=" + str(state)]
    excludes = []
    ancestors = set()
    for index, directory in enumerate(protected):
        key = "PROTECTED_" + str(index)
        args += ["-D", key + "=" + str(directory)]
        excludes.append('(require-not (subpath (param "' + key + '")))')
        ancestors.update(directory.parents)
    filters = []
    for index, directory in enumerate(sorted(ancestors)):
        key = "ANCESTOR_" + str(index)
        args += ["-D", key + "=" + str(directory)]
        filters.append('(literal (param "' + key + '"))')
    # STATE 内故意放原件 fixture，验证可写祖先不会变成原件的改名出口。
    policy = ('(version 1)\n(allow default)\n(deny file-write*)\n(deny file-link)\n'
              '(allow file-write* (require-all (require-any (subpath (param "STATE")) '
              '(literal "/dev/null")) ' + " ".join(excludes) + '))\n'
              '(deny file-write-unlink ' + " ".join(filters) + ')\n')
    return args + ["-p", policy]


def publish_new(source, destination):
    """提交本工具暂存目录，连已有空目录和悬空链接也不覆盖。"""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
