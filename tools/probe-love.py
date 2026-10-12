#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""用自有无窗口样例验证原厂 LÖVE 11.5；不接受游戏输入，也不下载运行时。"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from paths import SN_RUNTIMES
import importlib
_shared = importlib.import_module("love-runtime")
inventory = _shared.inventory
inventory_digest = _shared.inventory_digest
run_bounded = _shared.run_bounded
sandbox = _shared.sandbox

OFFICIAL_URL = "https://github.com/love2d/love/releases/download/11.5/love-11.5-macos.zip"
ARCHIVE_SHA256 = "6795bb3a1656af6a2fdfe741e150787b481886d3a280327a261a3fdded586913"
IDENTITY = "dryport-love-probe"
CONF = '''function love.conf(t)
    t.version = "11.5"
    t.identity = "dryport-love-probe"
    t.window = false
    t.modules.window = false
    t.modules.graphics = false
    t.modules.audio = false
    t.modules.joystick = false
    t.modules.physics = false
end
'''
MAIN = r'''
local ffi = require("ffi")
ffi.cdef[[int link(const char *, const char *); int rename(const char *, const char *);]]
local result = {}
local function put(k, v) result[k] = v end
local function protected(label, source, state)
    local file, _, err = io.open(source .. "/protected.txt", "wb")
    if file then file:write("UNSAFE WRITE"); file:close() end
    put(label .. "_direct_errno", err or 0)
    local alias = state .. "/" .. label .. "-alias/protected.txt"
    file, _, err = io.open(alias, "wb")
    if file then file:write("UNSAFE ALIAS WRITE"); file:close() end
    put(label .. "_alias_errno", err or 0)
    local rc = ffi.C.link(source .. "/protected.txt", state .. "/" .. label .. "-hardlink")
    put(label .. "_hardlink_errno", rc == -1 and ffi.errno() or 0)
    local parent = source:match("^(.*)/[^/]+$")
    local grandparent = parent:match("^(.*)/[^/]+$")
    for name, old in pairs({parent=parent, grandparent=grandparent}) do
        rc = ffi.C.rename(old, state .. "/" .. label .. "-moved-" .. name)
        put(label .. "_" .. name .. "_rename_errno", rc == -1 and ffi.errno() or 0)
    end
end
local function probe()
    local round = tonumber(os.getenv("SN_LOVE_ROUND"))
    local state = assert(os.getenv("SN_LOVE_STATE"))
    put("version", love.getVersion())
    put("version_string", tostring(select(1, love.getVersion())) .. "." .. tostring(select(2, love.getVersion())) .. "." .. tostring(select(3, love.getVersion())))
    put("arch", ffi.arch)
    put("fused", love.filesystem.isFused())
    put("window_module", love.window ~= nil)
    put("graphics_module", love.graphics ~= nil)
    put("source", love.filesystem.getSource())
    put("save_directory", love.filesystem.getSaveDirectory())
    assert(result.save_directory == os.getenv("SN_LOVE_EXPECTED_SAVE"), "save directory escaped isolated HOME")
    put("payload", assert(love.filesystem.read("payload.txt")))
    assert(result.payload == "immutable fixture payload\n", "resource read mismatch")
    local previous = love.filesystem.getInfo("counter.txt") and love.filesystem.read("counter.txt") or "0"
    assert(previous == tostring(round - 1), "saved content did not survive restart")
    assert(love.filesystem.write("counter.txt", tostring(round)))
    assert(love.filesystem.read("counter.txt") == tostring(round), "save readback mismatch")
    put("round", round)
    protected("source", assert(os.getenv("SN_LOVE_SOURCE")), state)
    protected("bundle", assert(os.getenv("SN_LOVE_BUNDLE")), state)
    for key, value in pairs(result) do
        if key:match("_errno$") then assert(value == 1, key .. " was not EPERM: " .. tostring(value)) end
    end
end
local ok, err = pcall(probe)
put("ok", ok)
if not ok then put("error", tostring(err)) end
local function quoted(value)
    return '"' .. value:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t') .. '"'
end
local rows = {}
for k, v in pairs(result) do
    rows[#rows+1] = quoted(k) .. ":" .. (type(v) == "string" and quoted(v) or tostring(v))
end
table.sort(rows)
local out = assert(io.open(assert(os.getenv("SN_LOVE_RECEIPT")), "wb"))
out:write("{" .. table.concat(rows, ",") .. "}\n"); out:close()
-- 覆盖自有样例的 run，初始化结束即退出；不创建窗口或进入默认错误画面。
function love.run() return function() return ok and 0 or 1 end end
'''




def archive_inventory(archive):
    expected = {}
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if not item.filename.startswith("love.app/"):
                continue
            relative = item.filename[len("love.app/"):].rstrip("/")
            if not relative:
                continue
            if ".." in Path(relative).parts or relative.startswith("/") or relative in expected:
                raise ValueError("官方压缩包含无效或重复路径。")
            if stat.S_ISLNK(item.external_attr >> 16):
                expected[relative] = ["link", package.read(item).decode()]
            elif item.is_dir():
                expected[relative] = ["directory"]
            else:
                data = package.read(item)
                expected[relative] = ["file", len(data), hashlib.sha256(data).hexdigest()]
    return expected








def make_fixture(state, shape):
    source = state / "source-grandparent/source-parent/OriginalGame"
    bundle = state / "bundle-grandparent/bundle-parent/Fixture.app"
    for path in (source, bundle):
        path.mkdir(parents=True)
        (path / "protected.txt").write_text("untouched original\n")
    files = {"conf.lua": CONF.encode(), "main.lua": MAIN.encode(), "payload.txt": b"immutable fixture payload\n"}
    if shape == "directory":
        entry = source / "game"
        entry.mkdir()
        for name, content in files.items():
            (entry / name).write_bytes(content)
    else:
        entry = source / ("game.love" if shape == "love" else "game.exe")
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with entry.open("wb") as out:
            if shape == "exe":
                # 自有 MZ 前缀只验证容器读取，不冒充有效 Windows 程序或真实游戏。
                out.write(b"MZ" + b"dryport synthetic prefix\0" * 16)
            # 先生成 ZIP 再拼接，目录偏移仍相对 ZIP 开头，与实际合并包一致。
            out.write(data.getvalue())
    for label, target in (("source", source), ("bundle", bundle)):
        (state / (label + "-alias")).symlink_to(target)
    return source, bundle, entry


def probe(runtime, output):
    runtime = Path(runtime).resolve(strict=True)
    binary = runtime / "Contents/MacOS/love"
    if sys.platform != "darwin" or not binary.is_file():
        raise ValueError("需要 macOS 和完整的原厂 love.app 路径。")
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError(f"证据路径已存在，已保留：{output}")
    # 只固定父目录；末端悬空链接不能被 resolve 成另一个可写的新路径。
    output = output.parent.resolve() / output.name
    if runtime == output or runtime in output.parents:
        raise ValueError("证据目录不能位于原厂运行时内。")
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    report = dict(protocol=1, kind="synthetic mechanism only", runtime=str(runtime),
                  created_at=datetime.now(timezone.utc).isoformat(), official_url=OFFICIAL_URL,
                  archive_sha256=None, cases=[], ok=False)
    before_runtime = inventory(runtime)
    protected_inventories = {}
    try:
        archive = runtime.parent / "love-11.5-macos.zip"
        if not archive.is_file():
            raise ValueError("运行时旁缺少官方 love-11.5-macos.zip，不能记录并核对下载哈希。")
        report["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        if report["archive_sha256"] != ARCHIVE_SHA256:
            raise ValueError("运行时压缩包与已核验的官方 11.5 macOS 发布资产不一致。")
        report["runtime_matches_archive"] = before_runtime == archive_inventory(archive)
        if not report["runtime_matches_archive"]:
            raise ValueError("love.app 的文件、目录或软链与官方压缩包不一致，未启动。")
        report["architectures"] = run_bounded(["/usr/bin/lipo", "-archs", str(binary)])
        if report["architectures"]["returncode"] or "arm64" not in report["architectures"]["stdout"].split():
            raise ValueError("运行时没有已确认的 arm64 架构。")
        report["factory_signature"] = run_bounded(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(runtime)])
        for shape in ("directory", "love", "exe"):
            state = output / shape
            state.mkdir()
            source, bundle, entry = make_fixture(state, shape)
            isolated_home = state / "isolated-home"
            isolated_home.mkdir()
            temporary = state / "tmp"
            temporary.mkdir()
            expected_save = isolated_home / "Library/Application Support"
            if shape != "exe":
                expected_save /= "LOVE"
            expected_save /= IDENTITY
            before = inventory(source)
            before_bundle = inventory(bundle)
            protected_inventories[shape] = (before, before_bundle)
            case = dict(shape=shape, source=str(source), entry=str(entry), expected_save=str(expected_save), runs=[])
            report["cases"].append(case)
            for round_number in (1, 2):
                receipt = state / f"round-{round_number}.json"
                env = {**os.environ, "HOME": str(isolated_home), "TMPDIR": str(temporary) + "/",
                       "SN_LOVE_STATE": str(state), "SN_LOVE_SOURCE": str(source), "SN_LOVE_BUNDLE": str(bundle),
                       "SN_LOVE_RECEIPT": str(receipt), "SN_LOVE_ROUND": str(round_number),
                       "SN_LOVE_EXPECTED_SAVE": str(expected_save)}
                for variable in ("LUA_PATH", "LUA_CPATH", "LUA_INIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH"):
                    env.pop(variable, None)
                command = sandbox(state, [source, bundle, runtime]) + [str(binary)]
                if shape == "exe":
                    command += ["--fused"]
                command += ["--game", str(entry)]
                run = run_bounded(command, env=env, cwd=state)
                case["runs"].append(run)
                run["receipt"] = json.loads(receipt.read_text()) if receipt.is_file() else None
                if run["timed_out"] or run["returncode"] or not run["receipt"] or not run["receipt"].get("ok"):
                    raise ValueError(f"{shape} 第 {round_number} 轮未通过；保留日志和现场。")
                if run["receipt"]["arch"] != "arm64" or run["receipt"]["version_string"] != "11.5.0":
                    raise ValueError("实际运行版本或架构不符合 11.5 arm64。")
                if (run["receipt"]["source"] != str(entry)
                        or run["receipt"]["fused"] != (shape == "exe")
                        or run["receipt"]["window_module"] or run["receipt"]["graphics_module"]):
                    raise ValueError("实际输入、合并模式或无窗口设置与探针不一致。")
            case["source_unchanged"] = before == inventory(source)
            case["bundle_unchanged"] = before_bundle == inventory(bundle)
            case["saved_content"] = (expected_save / "counter.txt").read_text()
            if not case["source_unchanged"] or not case["bundle_unchanged"] or case["saved_content"] != "2":
                raise ValueError("原件变化或跨进程保存重读失败。")
        report["ok"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        for case in report["cases"]:
            before, before_bundle = protected_inventories[case["shape"]]
            after = inventory(Path(case["source"]))
            bundle = output / case["shape"] / "bundle-grandparent/bundle-parent/Fixture.app"
            case["source_unchanged"] = before == after
            case["bundle_unchanged"] = before_bundle == inventory(bundle)
            case["source_inventory_sha256_before"] = inventory_digest(before)
            case["source_inventory_sha256_after"] = inventory_digest(after)
        report["runtime_unchanged"] = before_runtime == inventory(runtime)
        report["runtime_inventory_sha256"] = inventory_digest(before_runtime)
        preserved = report["runtime_unchanged"] and all(
            case["source_unchanged"] and case["bundle_unchanged"] for case in report["cases"])
        report["ok"] = report["ok"] and preserved
        if not preserved:
            report.setdefault("error", "最终回读发现样例原件、保护目录或原厂运行时发生变化。")
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=os.environ.get("LOVE_TEST_APP") or str(SN_RUNTIMES / "love/11.5/love.app"))
    parser.add_argument("--output", help="新的证据目录；已有目录一律保留并拒绝使用")
    args = parser.parse_args()
    if args.output:
        output = Path(args.output)
    else:
        output = Path(tempfile.mkdtemp(prefix="sn-love-probe-")) / "evidence"
    try:
        result = probe(args.runtime, output)
    except (OSError, ValueError) as error:
        print(f"LÖVE 机制探针未运行：{error}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": result["ok"], "evidence": str(output / "evidence.json"),
                      "error": result.get("error")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
