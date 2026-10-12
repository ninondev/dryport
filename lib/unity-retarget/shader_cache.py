# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""HLSLcc 纯计算缓存：失败绕过、内容校验、原子落盘，同进程合并相同请求。"""
import collections
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import threading

PROTOCOL = 1
MAX_ENTRY_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf8")


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _macho_dependencies(compiler):
    """把实际 HLSLcc 动态库也纳入身份；解析不全时不使用缓存。"""
    executable = compiler.resolve()
    visited = set()
    aliases = set()

    def inspect(path, inherited):
        aliases.add(path.absolute())
        path = path.resolve(strict=True)
        if path in visited:
            return
        with path.open("rb") as stream:
            if stream.read(4) not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
                raise OSError("无法确定非 Mach-O 编译器的依赖")
        visited.add(path)
        libs = subprocess.check_output(["otool", "-L", str(path)], text=True, stderr=subprocess.DEVNULL, timeout=5)
        commands = subprocess.check_output(["otool", "-l", str(path)], text=True, stderr=subprocess.DEVNULL, timeout=5)

        def expand(value):
            return value.replace("@loader_path", str(path.parent)).replace("@executable_path", str(executable.parent))

        rpaths = [expand(value) for value in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", commands)] + inherited
        for line in libs.splitlines()[1:]:
            name = line.strip().split(" (", 1)[0]
            if not name or name.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if name.startswith("@rpath/"):
                candidates = [Path(base) / name[len("@rpath/"):] for base in rpaths]
                # 多个搜索路径的优先级还会受后来出现的文件影响；身份无法稳定确认时直接编译。
                if len(set(candidates)) != 1:
                    raise OSError("编译器有多个动态库搜索路径")
                dependency = next((value for value in candidates if value.is_file()), None)
                if dependency is None:
                    raise OSError("无法定位编译器动态库：" + name)
            else:
                dependency = Path(expand(name))
                if not dependency.is_absolute() or "@" in str(dependency):
                    raise OSError("无法确定编译器动态库：" + name)
            inspect(dependency, rpaths)

    inspect(executable, [])
    # 保留加载路径本身：只记录 resolve 后的旧目标会漏掉动态库软链切换。
    return sorted((visited | aliases) - {executable}, key=str)


class CompilerIdentity:
    def __init__(self, dependency_resolver=None):
        self._resolve = dependency_resolver or _macho_dependencies
        self._lock = threading.Lock()
        self._known = {}
        self._unavailable = {}

    def fingerprint(self, compiler):
        # DYLD 可重定向加载路径；不猜测其效果，也不把不确定的身份写入持久缓存。
        if any(key.startswith("DYLD_") for key in os.environ):
            return None
        compiler = Path(compiler)
        try:
            compiler = compiler.resolve(strict=True)
            with self._lock:
                signature = _signature(compiler)
                if self._unavailable.get(compiler) == signature:
                    return None
                previous = self._known.get(compiler)
                if previous and previous[1] == tuple(_signature(path) for path in previous[0]):
                    return previous[2]
                files = [compiler, *self._resolve(compiler)]
                before = tuple(_signature(path) for path in files)
                hashes = [_digest(path) for path in files]
                if before != tuple(_signature(path) for path in files):
                    return None
                value = hashlib.sha256(_json({"executable": hashes[0], "libraries": sorted(hashes[1:]),
                                             "system": platform.platform(), "machine": platform.machine()})).hexdigest()
                self._known[compiler] = files, before, value
                self._unavailable.pop(compiler, None)
                return value
        except (OSError, ValueError, subprocess.SubprocessError):
            # 依赖不明时每个进程只探一次；编译器变化会重探。直接编译始终可继续。
            try:
                with self._lock:
                    self._unavailable[compiler] = _signature(compiler)
            except OSError:
                pass
            return None


def _valid(result):
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return False
    source, lines = result
    fields = {
        "input": {"name": str, "bind": int},
        "cb": {"name": str, "size": int, "members": int},
        "const": {"name": str, "offset": int, "type": int, "rows": int, "cols": int,
                  "isMatrix": bool, "arraySize": int, "used": bool},
        "cbbind": {"name": str, "bind": int},
        "tex": {"name": str, "bind": int, "sampler": int, "ms": bool, "dim": int, "uav": bool},
        "buf": {"name": str, "bind": int, "uav": bool},
        "vsout": {"name": str, "sem": str, "idx": int},
        "fsout": {"comps": int, "index": int},
    }
    return (isinstance(source, str) and bool(source.strip()) and "\0" not in source
            and isinstance(lines, list)
            and all(isinstance(line, dict) and isinstance(line.get("k"), str) and line["k"] in fields
                    and all(type(line.get(name)) is kind for name, kind in fields[line["k"]].items())
                    for line in lines))


class ShaderCache:
    def __init__(self, directory, enabled=True, max_bytes=DEFAULT_MAX_BYTES, dependency_resolver=None):
        self.directory = Path(directory) / ("v%d" % PROTOCOL)
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.identity = CompilerIdentity(dependency_resolver)
        self._lock = threading.Lock()
        self._trim_lock = threading.Lock()
        self._inflight = {}
        self._stats = collections.Counter()

    def stats(self):
        with self._lock:
            return dict(self._stats)

    def _count(self, name):
        with self._lock:
            self._stats[name] += 1
            return self._stats[name]

    def key(self, dxbc, fingerprint, lang, flags):
        h = hashlib.sha256(_json({"protocol": PROTOCOL, "compiler": fingerprint, "lang": lang, "flags": flags}))
        h.update(b"\0")
        h.update(dxbc)
        return h.hexdigest()

    def _path(self, key):
        return self.directory / key[:2] / (key + ".json")

    def _read(self, key):
        path = self._path(key)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_ENTRY_BYTES:
                raise ValueError("缓存文件无效")
            entry = json.loads(path.read_bytes())
            payload = entry["payload"]
            if (entry["protocol"] != PROTOCOL or entry["key"] != key
                    or entry["sha256"] != hashlib.sha256(_json(payload)).hexdigest()
                    or not _valid(payload)):
                raise ValueError("缓存校验失败")
            return tuple(payload)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            self._count("invalid")
            return None

    def _write(self, key, result):
        temporary = None
        try:
            payload = _json(result)
            encoded = _json({"protocol": PROTOCOL, "key": key, "sha256": hashlib.sha256(payload).hexdigest(), "payload": result})
            if len(encoded) > min(MAX_ENTRY_BYTES, self.max_bytes):
                return
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
            # 缓存可丢失；关闭后原子替换，不为可重算结果强制刷盘。
            os.replace(temporary, path)
            temporary = None
            writes = self._count("writes")
            if writes == 1 or writes % 64 == 0:
                self._trim()
        except (OSError, ValueError, TypeError, UnicodeError):
            self._count("write_errors")
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _trim(self):
        # 软上限，定期清理最早写入的完整结果；并发写入可暂时超出，不阻塞编译。
        if not self._trim_lock.acquire(blocking=False):
            return
        try:
            entries = []
            for path in self.directory.glob("??/*.json"):
                if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                    continue
                try:
                    stat = path.stat()
                    entries.append((stat.st_mtime_ns, stat.st_size, path))
                except OSError:
                    continue
            total = sum(size for _, size, _ in entries)
            for _, size, path in sorted(entries):
                if total <= self.max_bytes:
                    break
                try:
                    path.unlink()
                    total -= size
                    self._count("evictions")
                except OSError:
                    pass
        finally:
            self._trim_lock.release()

    def run(self, dxbc, compiler, lang, flags, compile_fn):
        self._count("requests")
        fingerprint = self.identity.fingerprint(compiler) if self.enabled else None
        if fingerprint is None:
            self._count("bypasses")
            self._count("compiles")
            return compile_fn()
        key = self.key(dxbc, fingerprint, lang, flags)
        with self._lock:
            future = self._inflight.get(key)
            leader = future is None
            if leader:
                future = concurrent.futures.Future()
                self._inflight[key] = future
            else:
                self._stats["waits"] += 1
        if not leader:
            return copy.deepcopy(future.result())
        try:
            result = self._read(key)
            if result is not None and self.identity.fingerprint(compiler) == fingerprint:
                self._count("hits")
            else:
                self._count("compiles")
                result = compile_fn()
                if _valid(result) and self.identity.fingerprint(compiler) == fingerprint:
                    self._write(key, result)
            future.set_result(result)
            return copy.deepcopy(result)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                del self._inflight[key]
