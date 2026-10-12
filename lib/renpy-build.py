#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""为已核对版本的 Gun-Dog 生成独立原生 app；不启动游戏。"""
import argparse
import importlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

from paths import SN_GAMES, SN_HOME, SN_LIB, STEAM
RUNTIME = importlib.import_module("renpy-runtime")
SHARED = importlib.import_module("love-runtime")
CLONE = importlib.import_module("nwjs-build").clone
APPID = "2118420"
PAYLOAD_SHA256 = "d3646a863a82ca1a8e48fbb595d6ae2dd6a7f3fac936e884cd890db1e4fd604a"
MARKERS = {".dryport-appid", ".dryport-download.json"}


def profile(inventory, appid):
    payload = {name: item for name, item in inventory.items() if name not in MARKERS}
    if (appid != APPID or any(item[0] == "link" for item in payload.values())
            or SHARED.inventory_digest(payload) != PAYLOAD_SHA256):
        raise ValueError("这份游戏尚未匹配当前 Gun-Dog 适配版本，原文件已保留。")
    return {"appid": APPID, "version": RUNTIME.VERSION, "entry": "gundog.py",
            "payload_sha256": PAYLOAD_SHA256, "entries": len(payload)}


def check_access(game):
    commands = [
        [sys.executable, str(SN_LIB / "risk.py"), "--gate-json", APPID],
        [sys.executable, str(SN_LIB / "library_discovery.py"), "--license", APPID],
        ["node", str(SN_LIB / "steam-download.mjs"), "--verify-delivery", APPID, str(game)],
    ]
    records = []
    for command in commands:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=40)
        records.append(json.loads(result.stdout))
    gate, license_, delivery = records
    if (gate.get("code") != 0 or str(gate.get("appid")) != APPID
            or license_.get("appid") != APPID or license_.get("allowed") is not True
            or license_.get("status") != "available" or delivery.get("appid") != APPID
            or delivery.get("complete") is not True or delivery.get("scope") != "base_game"):
        raise ValueError("当前风险、Steam 许可或完整下载回读未通过。")


def compile_entry(output):
    subprocess.run(["/usr/bin/xcrun", "clang", "-arch", "arm64", "-mmacosx-version-min=11.0", "-Os", "-fobjc-arc",
                    "-Wall", "-Wextra", "-Werror", "-Wl,-export_dynamic",
                    "-framework", "Foundation", "-framework", "Security",
                    str(SN_LIB / "renpy-entry.m"), "-o", str(output)], check=True, timeout=30)


def build(game, name, output, *, package=None, state_root=None):
    if sys.platform != "darwin":
        raise ValueError("原生 Ren’Py 打包需要 macOS。")
    game = Path(game).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if os.path.lexists(output):
        raise FileExistsError("输出已存在，已保留：" + str(output))
    output = output.parent.resolve(strict=True) / output.name
    if output.suffix != ".app" or not game.is_dir():
        raise ValueError("需要完整游戏目录和新的 .app 输出路径。")
    if not name or any(char in name for char in "\0\r\n"):
        raise ValueError("显示名称不能为空或含控制字符。")
    protected = [game, SN_HOME.resolve(), STEAM.resolve()]
    if any(output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError("输出不能与游戏原件、Steam 或代码目录重叠。")
    before = SHARED.inventory(game)
    selected = profile(before, APPID)
    check_access(game)
    checked = RUNTIME.check(package or RUNTIME.DEFAULT_OUTPUT)
    runtime = Path(checked["runtime"])
    if output.is_relative_to(runtime) or runtime.is_relative_to(output):
        raise ValueError("输出不能与共享运行时重叠。")
    parent_identity = (output.parent.stat().st_dev, output.parent.stat().st_ino)
    stage = Path(tempfile.mkdtemp(prefix=".renpy-app-", dir=output.parent))
    app = stage / output.name
    try:
        resources, macos = app / "Contents/Resources", app / "Contents/MacOS"
        resources.mkdir(parents=True)
        macos.mkdir()
        original = resources / "autorun"
        CLONE(game, original)
        if SHARED.inventory(original) != before or SHARED.inventory(game) != before:
            raise ValueError("复制期间原件变化或副本字节不符。")
        config = {"protocol": 2, **selected, "name": name, "runtime": str(runtime),
                  "runtime_manifest_sha256": checked["payload"]["manifest_sha256"],
                  "source": str(game), "repo": str(SN_HOME.resolve()), "original_inventory": before,
                  "state_root": str(Path(state_root or SN_GAMES / ".renpy-state").expanduser().resolve()),
                  "game_file_changes": [], "verification": "已核对完整输入与原生依赖，真实游玩另行验收"}
        (resources / "renpy-launch.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        (resources / "steam_appid.txt").write_text(APPID + "\n")
        for filename in ("renpy-launch.py", "love-runtime.py"):
            shutil.copy2(SN_LIB / filename, resources / filename)
        extensions = resources / "extensions"
        extensions.mkdir()
        with zipfile.ZipFile(extensions / "sn-native.rpe", "x", compression=zipfile.ZIP_STORED) as archive:
            # RPE 时间头会进入资源签名；固定自产元数据，让同一输入重装后字节一致。
            entry = zipfile.ZipInfo("autorun.py", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3
            entry.create_version = entry.extract_version = 20
            entry.external_attr = 0o600 << 16
            entry.internal_attr = 0
            entry.extra = entry.comment = b""
            archive.writestr(entry, (SN_LIB / "renpy-extension.py").read_bytes())
        compile_entry(macos / "launcher")
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleName": name, "CFBundleDisplayName": name,
            "CFBundleIdentifier": "io.dryport.renpy." + APPID,
            "CFBundleExecutable": "launcher", "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1", "NSHighResolutionCapable": True,
        }))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True, timeout=30)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True, timeout=30)
        if (SHARED.inventory(original) != before or SHARED.inventory(game) != before
                or output.parent.resolve(strict=True) != output.parent
                or (output.parent.stat().st_dev, output.parent.stat().st_ino) != parent_identity):
            raise ValueError("发布前原件、副本或输出父目录变化，产物保留在暂存区。")
        SHARED.publish_new(app, output)
        stage.rmdir()
    except Exception:
        print("构建未完成；原件和已有输出保留，暂存位置：" + str(stage), file=sys.stderr)
        raise
    return {"app": str(output), "appid": APPID, "runtime": str(runtime), "game_started": False,
            "original_entries": len(before), "original_unchanged": True, "game_file_changes": []}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path)
    parser.add_argument("name")
    parser.add_argument("output", type=Path)
    parser.add_argument("--runtime-package", type=Path)
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.game, args.name, args.output, package=args.runtime_package,
                               state_root=args.state_root), ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Ren’Py 打包失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
