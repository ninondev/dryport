#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""静态检查 LÖVE 11.5 输入；不导入 Lua、不运行 Windows EXE。"""
import argparse
from collections import deque
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

MAX_LUA = 8 * 1024 * 1024
MAX_LUA_TOTAL = 64 * 1024 * 1024
WINDOWS_RUNTIME_FILES = {"love.dll", "lua51.dll", "sdl2.dll", "openal32.dll", "mpg123.dll",
                         "msvcp120.dll", "msvcr120.dll", "vcruntime140.dll", "vcruntime140_1.dll",
                         "msvcp140.dll", "ucrtbase.dll"}
LONG_TOKEN = re.compile(r"\[(=*)\[")
WORD_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
NATIVE_SUFFIXES = {".dll", ".so", ".dylib", ".node", ".pyd", ".luac"}


def iter_lua_tokens(source):
    """只分词；注释和字符串里的代码字样不成为版本或动态库证据。"""
    index = 0
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        comment = source.startswith("--", index)
        start = index + 2 if comment else index
        long = LONG_TOKEN.match(source, start)
        if long:
            endmark = "]" + long[1] + "]"
            end = source.find(endmark, start + len(long[0]))
            if end < 0:
                raise ValueError("Lua 长字符串或注释未闭合，不能静态确认输入。")
            if not comment:
                yield ("string", source[start + len(long[0]):end])
            index = end + len(endmark)
            continue
        if comment:
            newline = source.find("\n", start)
            index = len(source) if newline < 0 else newline + 1
            continue
        char = source[index]
        if char in "\"'":
            quote, value = char, []
            index += 1
            while index < len(source) and source[index] != quote:
                char = source[index]
                if char == "\\":
                    index += 1
                    if index >= len(source):
                        raise ValueError("Lua 字符串未闭合。")
                    escaped = source[index]
                    # 路径/版本只接受普通转义；数字或十六进制转义不冒充可确认文本。
                    value.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"'}.get(escaped, "\\" + escaped))
                else:
                    value.append(char)
                index += 1
            if index >= len(source):
                raise ValueError("Lua 字符串未闭合。")
            index += 1
            yield ("string", "".join(value))
            continue
        word = WORD_TOKEN.match(source, index)
        if word:
            yield ("name", word[0]); index += len(word[0])
        else:
            yield ("symbol", char); index += 1


def lua_tokens(source):
    return list(iter_lua_tokens(source))


def configuration(tokens):
    # 只接收配置函数中的直接标量赋值；Lua 是动态语言，分支/调用/第二次定义均不能靠文本猜结果。
    start = [("name", "function"), ("name", "love"), ("symbol", "."),
             ("name", "conf"), ("symbol", "(")]
    if tokens[:5] != start or len(tokens) < 8 or tokens[5][0] != "name" or tokens[6] != ("symbol", ")"):
        raise ValueError("conf.lua 需为唯一的 function love.conf(t) 和直接配置赋值，当前不执行它探版本。")
    argument = tokens[5][1]
    fields, index = {}, 7
    while index < len(tokens) and tokens[index] != ("name", "end"):
        if tokens[index] == ("symbol", ";"):
            index += 1
            continue
        if tokens[index] != ("name", argument):
            raise ValueError("conf.lua 含动态控制或调用，版本和存档身份尚不能静态确认。")
        index += 1
        path = []
        while index + 1 < len(tokens) and tokens[index] == ("symbol", ".") and tokens[index+1][0] == "name":
            path.append(tokens[index+1][1]); index += 2
        if not path or index >= len(tokens) or tokens[index] != ("symbol", "="):
            raise ValueError("conf.lua 含非直接字段赋值，当前已停止。")
        index += 1
        if index >= len(tokens):
            raise ValueError("conf.lua 赋值缺值。")
        kind, value = tokens[index]
        if kind == "string" or (kind == "name" and value in ("true", "false", "nil")):
            scalar = (kind, value); index += 1
        else:
            number = ""
            while index < len(tokens) and tokens[index][0] == "symbol" and tokens[index][1] in "0123456789.-":
                number += tokens[index][1]; index += 1
            if not re.fullmatch(r"-?\d+(?:\.\d+)?", number):
                raise ValueError("conf.lua 含计算或非标量配置，当前不猜测其值。")
            scalar = ("number", number)
        key = ".".join(path)
        if key in fields and fields[key] != scalar:
            raise ValueError("conf.lua 对同一字段有冲突赋值：" + key)
        fields[key] = scalar
    if tokens[index:] not in ([("name", "end")], [("name", "end"), ("symbol", ";")]):
        raise ValueError("conf.lua 需为单个已结束的配置函数；不支持额外执行语句。")
    if fields.get("window.fullscreen") == ("name", "true") or fields.get("window.fullscreentype") in (("string", "desktop"), ("string", "exclusive")):
        raise ValueError("游戏明确要求全屏，尚无已验证的外置窗口策略，当前已停止。")
    version = fields.get("version")
    if version not in (("string", "11.5"), ("string", "11.5.0")):
        raise ValueError("缺少明确 t.version = \"11.5\" 或版本与运行时冲突。")
    identity = fields.get("identity")
    if identity is None or identity[0] != "string":
        raise ValueError("缺少明确 t.identity；当前不猜测原始容器的默认存档身份。")
    identity = identity[1]
    if not identity or identity in (".", "..") or any(c in identity for c in "/\\:\0\r\n"):
        raise ValueError("conf.lua 的存档 identity 不是单个安全目录名。")
    return {"version": "11.5", "version_evidence": [version[1]], "identity": identity}


def check_lua(name, data):
    if data.startswith((b"\x1bLua", b"\x1bLJ")):
        raise ValueError(f"Lua 字节码不能静态确认原生依赖，已停止：{name}")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError(f"Lua 源码不是可检查的 UTF-8：{name}") from error
    configuration_file = name == "conf.lua"
    if configuration_file and len(data) > 64 * 1024:
        raise ValueError("conf.lua 超过 64 KiB 静态配置上限，已停止。")
    # 主脚本只保留三个相邻 token；不把几十 MB Lua 展开成数 GB Python 元组。
    recent = deque(maxlen=3)
    retained = []
    for kind, value in iter_lua_tokens(source):
        recent.append(value)
        if list(recent) in (["ffi", ".", "load"], ["package", ".", "loadlib"],
                           ["os", ".", "execute"], ["io", ".", "popen"]):
            raise ValueError(f"游戏包含未支持的本机库或外部程序调用：{name}：{''.join(recent)}")
        if kind == "string" and value.lower().endswith(tuple(NATIVE_SUFFIXES)):
            raise ValueError(f"Lua 引用了平台原生模块，尚未验证：{name}：{value}")
        if configuration_file:
            retained.append((kind, value))
    return retained if configuration_file else None



def inspect(game):
    game = Path(game).resolve(strict=True)
    if not game.is_dir():
        raise ValueError("请输入完整游戏目录；单个文件请放在独立游戏目录中。")
    paths = []
    def fail_walk(error):
        raise error
    for parent, dirs, files in os.walk(game, onerror=fail_walk):
        for name in dirs + files:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"当前 LÖVE 输入不支持链接或特殊文件：{path.relative_to(game)}")
            if stat.S_ISREG(mode):
                paths.append(path)
    candidates = []
    if (game / "main.lua").is_file():
        candidates.append(("directory", game))
    for path in paths:
        if path.parent == game and path.suffix.lower() in (".love", ".exe") and zipfile.is_zipfile(path):
            candidates.append(("exe" if path.suffix.lower() == ".exe" else "love", path))
    if len(candidates) != 1:
        raise ValueError("没有唯一 LÖVE 入口；需要根目录 main.lua、一个 .love 或带 ZIP 数据的 EXE。")
    shape, entry = candidates[0]
    total = 0
    sources = {}
    for path in paths:
        relative = path.relative_to(game).as_posix()
        if path.suffix.lower() in NATIVE_SUFFIXES and path.name.lower() not in WINDOWS_RUNTIME_FILES:
            raise ValueError(f"发现未支持的原生插件，原文件保留：{relative}")
        if path.suffix.lower() == ".lua":
            if path.stat().st_size > MAX_LUA:
                raise ValueError(f"Lua 文件超过静态检查上限：{relative}")
            data = path.read_bytes(); total += len(data)
            if total > MAX_LUA_TOTAL:
                raise ValueError("Lua 源码总量超过静态检查上限，已停止。")
            sources[relative] = check_lua(relative, data)
    checked = len(sources)
    if shape != "directory":
        sources = {}  # 外置 conf.lua 不会被归档入口读取，不能当版本证据。
        with zipfile.ZipFile(entry) as archive:
            names = set()
            for item in archive.infolist():
                name = item.filename.rstrip("/")
                parts = PurePosixPath(name)
                if (not name or name in names or any(p in ("", ".", "..") for p in name.split("/")) or parts.is_absolute() or ".." in parts.parts or "\\" in name
                        or ":" in name or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1):
                    raise ValueError(f"游戏包包含歧义、链接或加密条目：{item.filename}")
                names.add(name)
                if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError(f"游戏包使用未验证的压缩方式：{item.filename}")
                if parts.suffix.lower() in NATIVE_SUFFIXES:
                    raise ValueError(f"游戏包包含未支持的原生插件：{item.filename}")
                if parts.suffix.lower() == ".lua":
                    total += item.file_size
                    if item.file_size > MAX_LUA or total > MAX_LUA_TOTAL:
                        raise ValueError("Lua 源码超过静态检查上限，已停止。")
                    sources[name] = check_lua(name, archive.read(item))
            if "main.lua" not in names:
                raise ValueError("LÖVE 包根目录缺少 main.lua。")
    if "conf.lua" not in sources:
        raise ValueError("入口缺少 conf.lua，无法静态确认运行时版本。")
    info = configuration(sources["conf.lua"])
    return {"protocol": 1, **info, "shape": shape,
            "entry": entry.relative_to(game).as_posix(), "lua_files_checked": checked + (len(sources) if shape != "directory" else 0)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.game), ensure_ascii=False))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"LÖVE 输入尚不支持：{error}", file=sys.stderr)
        sys.exit(1)
