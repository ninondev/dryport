#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只读核对 Ren'Py 游戏与指定 SDK；不导入游戏 Python，不解包 RPA。"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_METADATA = 64 * 1024
MAX_SOURCE = 16 * 1024 * 1024
MAX_SOURCE_TOTAL = 128 * 1024 * 1024
MAX_ENGINE_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 200_000
READ_CHUNK = 1024 * 1024
NATIVE_SUFFIXES = {".dll", ".dylib", ".so", ".pyd", ".node", ".exe"}
COMPILED_SUFFIXES = {".pyc", ".pyo", ".rpyc", ".rpymc"}
ARCHIVE_SUFFIXES = {".rpa", ".zip", ".egg", ".whl"}
VERSION_FIELDS = {"branch": str, "nightly": bool, "official": bool,
                  "version": str, "version_name": str}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def fingerprint_regular(path, limit, *, collect=False):
    # 非阻塞打开后先查类型，避免管道输入把只读检查挂住。
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"不是上限内的普通文件：{path}")
        digest, size = hashlib.sha256(), 0
        chunks = [] if collect else None
        while data := stream.read(min(READ_CHUNK, limit - size + 1)):
            size += len(data)
            if size > limit:
                raise ValueError(f"读取时文件超过上限：{path}")
            digest.update(data)
            if collect:
                chunks.append(data)
        after = os.fstat(stream.fileno())
        if (size != before.st_size or identity(before) != identity(after)
                or identity(before) != identity(Path(path).lstat())):
            raise ValueError(f"读取时文件改变或超过上限：{path}")
        return {"size": size, "sha256": digest.hexdigest()}, b"".join(chunks) if collect else None


def read_regular(path, limit):
    return fingerprint_regular(path, limit, collect=True)[1]


def parse_version(data):
    if len(data) > MAX_METADATA:
        raise ValueError("vc_version.py 超过 64 KiB。")
    try:
        nodes = ast.parse(data, filename="vc_version.py").body
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ValueError("vc_version.py 不是可静态读取的 Python。") from error
    result = {}
    for index, node in enumerate(nodes):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if (not isinstance(node, ast.Assign) or len(node.targets) != 1
                or not isinstance(node.targets[0], ast.Name)
                or not isinstance(node.value, ast.Constant)):
            raise ValueError("vc_version.py 含动态语句，当前不执行它探版本。")
        key, value = node.targets[0].id, node.value.value
        if key not in VERSION_FIELDS or key in result or type(value) is not VERSION_FIELDS[key]:
            raise ValueError("vc_version.py 有重复、未知或类型不符的字段。")
        result[key] = value
    if result.keys() != VERSION_FIELDS.keys() or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", result["version"]):
        raise ValueError("vc_version.py 缺少明确的完整版本与构建字段。")
    return result


def tree_files(root):
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f"需要实际目录，不接受链接：{root}")
    result = []
    count = 0
    def fail(error):
        raise error
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            mode = path.lstat().st_mode
            count += 1
            if count > MAX_ENTRIES:
                raise ValueError("输入目录超过静态检查条目上限。")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"输入包含链接或特殊文件：{path.relative_to(root)}")
            if stat.S_ISREG(mode):
                result.append(path)
    return sorted(result)


def file_kind(path):
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python_source"
    if suffix in {".rpy", ".rpym"}:
        return "renpy_source"
    if suffix in COMPILED_SUFFIXES:
        return "compiled"
    if suffix in ARCHIVE_SUFFIXES:
        return "archive"
    if suffix in NATIVE_SUFFIXES or re.search(r"\.so(?:\.[0-9]+)+$", path.name.lower()):
        return "native"
    return "other"


def tree_snapshot(root):
    return {path: identity(path.lstat()) for path in tree_files(root)}


def engine_inventory(root):
    engine = root / "renpy"
    before = tree_snapshot(engine)
    result, sources, total, source_total = {}, {}, 0, 0
    # 编译输入和归档也影响实际行为；只流式取摘要，不尝试反序列化。
    for path in before:
        kind = file_kind(path)
        collect = path.suffix == ".py"
        limit = MAX_ENGINE_TOTAL - total
        if collect:
            limit = min(limit, MAX_SOURCE, MAX_SOURCE_TOTAL - source_total)
        info, data = fingerprint_regular(path, limit, collect=collect)
        total += info["size"]
        name = path.relative_to(root).as_posix()
        result[name] = {"kind": kind, **info}
        if collect:
            sources[name] = data
            source_total += info["size"]
    if before != tree_snapshot(engine):
        raise ValueError("引擎文件集合或身份在检查期间改变，请停止写入后重试。")
    return result, sources, before


def inventory_comparison(left, right):
    shared = sorted(left.keys() & right.keys())
    differences = []
    for name in shared:
        if (left[name]["size"], left[name]["sha256"]) == (right[name]["size"], right[name]["sha256"]):
            continue
        differences.append({"path": name, "kind": left[name]["kind"],
                            "game_size": left[name]["size"], "sdk_size": right[name]["size"],
                            "game_sha256": left[name]["sha256"], "sdk_sha256": right[name]["sha256"]})
    missing_game, missing_sdk = sorted(right.keys() - left.keys()), sorted(left.keys() - right.keys())
    return {"scope": "renpy/** 普通文件，不包括输入根目录中的 lib 或其他游戏文件",
            "comparison_method": "size+sha256", "game_count": len(left), "sdk_count": len(right),
            "hashes_equal_count": len(shared) - len(differences), "differences": differences,
            "missing_from_game": missing_game, "missing_from_sdk": missing_sdk,
            "all_hashes_equal": not differences and not missing_game and not missing_sdk,
            "game_inventory": left, "sdk_inventory": right,
            "semantic_compatibility_checked": False}


def launcher_comparisons(game, sdk, compare_ast):
    launcher = read_regular(sdk / "renpy.py", MAX_SOURCE)
    result, total = [], len(launcher)
    paths = sorted(game.glob("*.py"))
    if len(paths) > MAX_ENTRIES:
        raise ValueError("根目录 Python 文件超过静态检查条目上限。")
    for path in paths:
        data = read_regular(path, min(MAX_SOURCE, MAX_SOURCE_TOTAL - total))
        total += len(data)
        result.append({"path": path.name, **comparison(data, launcher, compare_ast)})
    return result


def comparison(left, right, compare_ast=True):
    equal = left == right
    result = {"bytes_equal": equal, "game_sha256": hashlib.sha256(left).hexdigest(),
              "sdk_sha256": hashlib.sha256(right).hexdigest()}
    if not equal and compare_ast:
        try:
            result["ast_equal"] = ast.dump(ast.parse(left), include_attributes=False) == ast.dump(ast.parse(right), include_attributes=False)
        except (SyntaxError, ValueError, RecursionError):
            result["ast_equal"] = None
    return result


def inspect(game, sdk, *, compare_ast=True):
    game, sdk = Path(game).absolute(), Path(sdk).absolute()
    for root in (game, sdk):
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("游戏与 SDK 必须是实际目录。")
    left_files, left, left_snapshot = engine_inventory(game)
    right_files, right, right_snapshot = engine_inventory(sdk)
    version_path = "renpy/vc_version.py"
    if version_path not in left or version_path not in right:
        raise ValueError("游戏或 SDK 缺少 renpy/vc_version.py。")
    versions = {"game": parse_version(left[version_path]), "sdk": parse_version(right[version_path])}
    shared = sorted(left.keys() & right.keys())
    differences = [{"path": name, **comparison(left[name], right[name], compare_ast)}
                   for name in shared if left[name] != right[name]]
    missing_game, missing_sdk = sorted(right.keys() - left.keys()), sorted(left.keys() - right.keys())
    launchers = launcher_comparisons(game, sdk, compare_ast)
    game_files = tree_files(game / "game")
    if (left_snapshot != tree_snapshot(game / "renpy")
            or right_snapshot != tree_snapshot(sdk / "renpy")):
        raise ValueError("引擎输入在对照完成前改变，请停止写入后重试。")
    return {"protocol": 1, "kind": "static_source_comparison", "versions": versions,
            "version_fields_equal": versions["game"] == versions["sdk"],
            "engine_sources": {"scope": "renpy/**/*.py，仅 Python 源码", "ast_comparison_requested": compare_ast,
                "game_count": len(left), "sdk_count": len(right),
                "bytes_equal_count": len(shared) - len(differences), "differences": differences,
                "missing_from_game": missing_game, "missing_from_sdk": missing_sdk,
                "all_bytes_equal": not differences and not missing_game and not missing_sdk},
            "engine_files": inventory_comparison(left_files, right_files),
            "root_python_vs_sdk_launcher": launchers,
            "game_content_scan": {"scope": "game/**", "detection": "仅按扩展名列出，不读取或识别文件格式与 ABI"},
            "game_native_files": [p.relative_to(game).as_posix() for p in game_files if file_kind(p) == "native"],
            "uninspected_archives": [p.relative_to(game).as_posix() for p in game_files if file_kind(p) == "archive"],
            "limits": ["构建字段来自输入文件，不代表官方来源核验。", "AST 相同不等于原字节相同，也不是运行兼容证明。",
                       ".rpy/.rpym、编译文件、原生文件和归档只比较大小与摘要，不作语义判断或解包。",
                       "编译脚本可能携带存档语句身份，不能把 .rpyc/.rpymc 差异视为可随意丢弃的缓存。",
                       "game_native_files 与 uninspected_archives 仅覆盖 game/**，不能证明其他目录或归档内无原生依赖。",
                       "lib、动态导入和原生 ABI 未验证；未执行游戏，任何差异或相同结果都不能据此标为兼容或可玩。"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game")
    parser.add_argument("--sdk", required=True)
    parser.add_argument("--no-ast", action="store_true", help="只比较字节与摘要，不追加 Python AST 对照")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.game, args.sdk, compare_ast=not args.no_ast), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        print("Ren'Py 静态检查未完成：" + str(error), file=sys.stderr)
        sys.exit(1)
