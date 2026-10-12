# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""复用文件清单分类，不保存账号、拥有关系或风险结论。

lookup/fingerprint 不创建目录。只有 store 会写 caller 指定的受控目录；
损坏、权限不足或目录归属不符均按缓存未命中处理。扫描前保存 fingerprint，
store 时传 expected，可防止把扫描期间变化的文件配给旧分类。
条目内容摘要只检测意外损坏，不防止能同时改写内容和摘要的本地攻击者。
"""
import ctypes
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import types


_OWNER = ".manifest-route-cache.json"
_IDENTITY = {"kind": "dryport-manifest-route-cache", "protocol": 1}
_MAX_ENTRY = 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MISSING = object()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _execution_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _execution_value(value):
    """按值绑定已加载代码，不把驻留字符串引用计数或集合迭代顺序变成缓存身份。"""
    if value is None:
        return ["none"]
    if value is Ellipsis:
        return ["ellipsis"]
    if type(value) in (bool, int, str):
        return [type(value).__name__, value]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, complex):
        return ["complex", value.real.hex(), value.imag.hex()]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, slice):
        # Python 3.14 会把 [:80] 等切片编译成常量；边界和步长都参与身份。
        return ["slice", _execution_value(value.start), _execution_value(value.stop),
                _execution_value(value.step)]
    if isinstance(value, types.CodeType):
        # 新版行表已包含旧 co_lnotab 的信息；3.9 保留原字段，避免无关缓存失效。
        ignored = {"co_lnotab"} if hasattr(value, "co_linetable") else set()
        # 包含字节码、嵌套 code、常量、参数、名称和异常表等代码字段。
        return ["code", {name: _execution_value(getattr(value, name)) for name in dir(value)
                         if name.startswith("co_") and name not in ignored
                         and not callable(getattr(value, name))}]
    if isinstance(value, re.Pattern):
        return ["regex", _execution_value(value.pattern), value.flags]
    if isinstance(value, dict):
        pairs = [[_execution_value(key), _execution_value(item)] for key, item in value.items()]
        return ["dict", sorted(pairs, key=_execution_json)]
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_execution_value(item) for item in value]
        return [type(value).__name__, sorted(items, key=_execution_json)
                if isinstance(value, (set, frozenset)) else items]
    raise TypeError(f"分类器含无法稳定绑定的值类型：{type(value).__name__}")


def _execution_function(function):
    closure = []
    for cell in function.__closure__ or ():
        try:
            value = cell.cell_contents
        except ValueError:
            closure.append(["empty-cell"])
        else:
            closure.append(_execution_value(value))
    return {"code": _execution_value(function.__code__),
            "defaults": _execution_value(function.__defaults__),
            "kwdefaults": _execution_value(function.__kwdefaults__), "closure": closure}


def _number(value, maximum, field):
    if (isinstance(value, bool) or not isinstance(value, (int, str))
            or not str(value).isascii() or not str(value).isdecimal()
            or not 0 < int(value) <= maximum):
        raise ValueError(f"无效的 {field}")
    return str(int(value))


def _stat(value):
    return [value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]


def _directory(path, create=False):
    """逐层拒绝软链；dir_fd 让随后打开缓存条目不重新追随父路径。"""
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name):
    item = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(item, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_ENTRY:
            raise ValueError("缓存条目不是有限大小的普通文件")
        data = stream.read(_MAX_ENTRY + 1)
        if len(data) > _MAX_ENTRY:
            raise ValueError("缓存条目过大")
        return json.loads(data)


def _rename_directory(parent, source, destination):
    # 原子提交归属标记；不能用会覆盖并发空目录的普通 rename。
    libc = ctypes.CDLL(None, use_errno=True)
    name, flag = ("renameatx_np", 4) if hasattr(libc, "renameatx_np") else ("renameat2", 1)
    function = getattr(libc, name, None)
    if function is None:
        raise OSError(errno.ENOSYS, "当前系统不支持独占目录提交")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent, os.fsencode(source), parent, os.fsencode(destination), flag):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class ManifestRouteCache:
    def __init__(self, steam, cache_root, *, classifier_path=None, classifier_module=None):
        """一个实例代表一轮固定的已加载分类器；更换函数或规则后创建新实例。"""
        self.steam = Path(os.path.abspath(steam))
        self.cache_root = Path(os.path.abspath(cache_root))
        self.classifier_path = Path(classifier_path) if classifier_path is not None else Path(__file__).with_name("reclassify.py")
        self._classifier_stat = self._classifier_hash = None
        module = classifier_module if classifier_module is not None else importlib.import_module("reclassify")
        constants = ("NAME", "RULES", "COMPILED", "_FILTER_RULES", "_FILTER_RULE_HASH",
                     "_CANDIDATE_EXTENSIONS", "_CANDIDATE_PATH")
        # 格式版本参与摘要；旧 marshal 键保留但不迁移为未经新实现验证的命中。
        execution = {"format": 2, "python": sys.implementation.cache_tag,
                     "functions": {name: _execution_function(value) for name, value in vars(module).items()
                                   if isinstance(value, types.FunctionType)},
                     "constants": {name: _execution_value(getattr(module, name)) for name in constants
                                   if hasattr(module, name)}}
        self._execution_hash = hashlib.sha256(_execution_json(execution)).hexdigest()

    def fingerprint(self, depots):
        """非法 depot/gid 抛 ValueError；无法读取身份时返回 None，绝不确证缓存。"""
        if not isinstance(depots, (list, tuple)):
            raise ValueError("depot 清单必须是列表")
        checked = []
        for row in depots:
            if not isinstance(row, dict):
                raise ValueError("depot 记录必须是对象")
            depot = _number(row.get("depot"), 2 ** 32 - 1, "depot")
            gid = None if row.get("gid") is None else _number(row["gid"], 2 ** 64 - 1, "manifest gid")
            checked.append((depot, gid))
        try:
            identity = _stat(self.classifier_path.stat())
            if identity != self._classifier_stat:
                content = self.classifier_path.read_bytes()
                if _stat(self.classifier_path.stat()) != identity:
                    return None
                self._classifier_hash = hashlib.sha256(content).hexdigest()
                self._classifier_stat = identity
            entries = []
            for depot, gid in checked:
                entry = {"depot": depot, "gid": gid, "path": None, "identity": None}
                if gid is not None:
                    path = self.steam / "depotcache" / f"{depot}_{gid}.manifest.txt"
                    entry["path"] = str(path)
                    try:
                        link = path.lstat()
                        entry["identity"] = _stat(link)
                        if stat.S_ISLNK(link.st_mode):
                            # 链接本身和实际目标都要绑定；普通文件的 lstat 已包含目标身份。
                            entry["resolved"] = str(path.resolve())
                            try:
                                entry["target"] = _stat(path.stat())
                            except FileNotFoundError:
                                entry["target"] = None
                        else:
                            entry["target"] = entry["identity"]
                    except FileNotFoundError:
                        pass
                entries.append(entry)
            # 提升键协议，让没有内容摘要的旧记录失效；目录归属仍保持协议 1。
            return {"protocol": 2, "classifier_sha256": self._classifier_hash,
                    "classifier_execution_sha256": self._execution_hash, "depots": entries}
        except (OSError, RuntimeError):
            return None

    def _root(self, create):
        parent = _directory(self.cache_root.parent, create=create)
        try:
            try:
                fd = os.open(self.cache_root.name, _DIR_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                temporary = ".manifest-cache-" + secrets.token_hex(12)
                os.mkdir(temporary, 0o700, dir_fd=parent)
                stage = os.open(temporary, _DIR_FLAGS, dir_fd=parent)
                try:
                    marker = os.open(_OWNER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage)
                    with os.fdopen(marker, "wb") as stream:
                        stream.write(_json(_IDENTITY))
                    _rename_directory(parent, temporary, self.cache_root.name)
                except BaseException:
                    os.unlink(_OWNER, dir_fd=stage)
                    os.rmdir(temporary, dir_fd=parent)
                    raise
                finally:
                    os.close(stage)
                fd = os.open(self.cache_root.name, _DIR_FLAGS, dir_fd=parent)
            try:
                if _read(fd, _OWNER) != _IDENTITY:
                    raise ValueError("缓存目录归属不符")
                return fd
            except BaseException:
                os.close(fd)
                raise
        finally:
            os.close(parent)

    @staticmethod
    def _result(classification, files, found, total):
        if (not isinstance(classification, (list, tuple)) or len(classification) != 4
                or not all(isinstance(value, str) for value in classification)
                or any(type(value) is not int or value < 0 for value in (files, found, total))
                or found > total or (files > 0 and found == 0)):
            raise ValueError("文件分类缓存结果无效")
        return {"classification": tuple(classification), "files": files, "found": found, "total": total}

    def lookup(self, depots):
        fingerprint = self.fingerprint(depots)
        if fingerprint is None:
            return None
        key = hashlib.sha256(_json(fingerprint)).hexdigest() + ".json"
        try:
            fd = self._root(False)
            try:
                record = _read(fd, key)
            finally:
                os.close(fd)
            if (not isinstance(record, dict) or set(record) != {"fingerprint", "result", "content_sha256"}
                    or record["fingerprint"] != fingerprint or not isinstance(record["result"], dict)
                    or set(record["result"]) != {"classification", "files", "found", "total"}):
                return None
            content = {"fingerprint": record["fingerprint"], "result": record["result"]}
            if record["content_sha256"] != hashlib.sha256(_json(content)).hexdigest():
                return None
            result = self._result(**record["result"])
            if result["total"] != len(fingerprint["depots"]) or self.fingerprint(depots) != fingerprint:
                return None
            return result
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def store(self, depots, classification, files, found, *, expected=_MISSING):
        """不传 expected 时，调用方负责保证结果对应此刻的文件；不覆盖任何已有条目。"""
        fingerprint = self.fingerprint(depots)
        if fingerprint is None or (expected is not _MISSING and fingerprint != expected):
            return False
        result = self._result(classification, files, found, len(fingerprint["depots"]))
        record = {"fingerprint": fingerprint, "result": result}
        record["content_sha256"] = hashlib.sha256(_json(record)).hexdigest()
        payload = _json(record)
        if len(payload) > _MAX_ENTRY:
            return False
        key = hashlib.sha256(_json(fingerprint)).hexdigest() + ".json"
        try:
            fd = self._root(True)
            temporary = ".entry-" + secrets.token_hex(12)
            try:
                item = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    with os.fdopen(item, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if self.fingerprint(depots) != fingerprint:
                        return False
                    try:
                        os.link(temporary, key, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    except FileExistsError:
                        return _read(fd, key) == json.loads(payload)
                    return True
                finally:
                    os.unlink(temporary, dir_fd=fd)
            finally:
                os.close(fd)
        except (OSError, ValueError, TypeError, RecursionError):
            return False
