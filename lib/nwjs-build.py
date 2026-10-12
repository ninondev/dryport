#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""完整保留游戏，使用原厂运行时另建启动层；不覆盖任何已有输出。"""
import ctypes
import hashlib
import json
import re
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from paths import SN_HOME, SN_LIB, SN_RUNTIMES, STEAM


def inventory(root):
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            p = Path(parent) / name
            rel = p.relative_to(root).as_posix()
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(p)
                if os.path.isabs(target) or not p.resolve().is_relative_to(root):
                    raise ValueError(f"游戏含目录外的链接，已停止：{p}")
                result[rel] = ["link", target]
            elif stat.S_ISDIR(mode):
                result[rel] = ["directory"]
            elif stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with p.open("rb") as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        digest.update(block)
                result[rel] = ["file", p.stat().st_size, digest.hexdigest()]
            else:
                raise ValueError(f"游戏含不支持的特殊文件，已停止：{p}")
    return dict(sorted(result.items()))


def clone(source, destination):
    copied = subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], capture_output=True)
    if copied.returncode:
        # 仅合并进本次 mkdtemp 创建的暂存区；不触碰已有输出或源文件。
        subprocess.run(["/usr/bin/ditto", str(source), str(destination)], check=True)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def publish(source, destination):
    # 普通 rename 会覆盖空目录；RENAME_EXCL 也拒绝并发出现的空目录和软链。
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def unpack_package_nw(archive, destination):
    """NW.js 允许把整个应用打成 ZIP 命名为 package.nw（Construct 2/3 的 Windows 导出就是这样：根目录只有 nw.exe + package.nw）。
    启动层的视图要镜像目录，所以打包时解到 app 里的 UnpackedNW/；只解常规文件与目录，拒绝绝对路径、..、软链、盘符。
    2026-09-14 Star Sky 3 / 另一款 NW.js 游戏 冒烟：原样按 www/package.json 找入口失败。"""
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts or "\\" in name:
                raise ValueError(f"package.nw 里有越界路径，拒绝解包：{name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"package.nw 里有软链，拒绝解包：{name}")
            target = destination / name
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def normalize_main(main):
    """Tyrano Builder 一类写 "main": "app://./index.html"（NW.js 老式 app: 协议，指向应用根目录）；本启动层重写清单后只认相对路径，
    这里把 app://、app://./、app:/// 前缀去掉（2026-09-14 一款 NW.js 游戏 冒烟：原样拼路径成了 ".../app:"）。
    其它形态原样返回，由后面的「必须是目录内的 HTML 文件」判据把关。"""
    app_url = re.match(r"^app://(?:\./|/)?(.*)$", main)
    if app_url:
        return app_url.group(1) or "index.html"
    return main


def build(game, name, output, appid):
    if sys.platform != "darwin":
        raise ValueError("NW.js 原生打包需要 macOS。")
    if not appid.isascii() or not appid.isdecimal() or not 0 < int(appid) <= 4294967295:
        raise ValueError("Steam appid 必须是有效的十进制数字。")
    game = Path(game).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if not game.is_dir() or output.suffix != ".app":
        raise ValueError("游戏必须是目录，输出必须以 .app 结尾。")
    output = output.parent.resolve(strict=True) / output.name
    if os.path.lexists(output):
        raise ValueError(f"输出已存在，已保留；请选一个新的 .app 路径：{output}")
    if output.is_relative_to(game):
        raise ValueError("输出不能放在原游戏目录内。")
    root = game if (game / "package.json").is_file() else game / "www"
    unpacked_stage = None
    if not (root / "package.json").is_file() and (game / "package.nw").is_file() and zipfile.is_zipfile(game / "package.nw"):
        unpacked_stage = Path(tempfile.mkdtemp(prefix=".nwjs-unpack-", dir=output.parent))
        unpack_package_nw(game / "package.nw", unpacked_stage)
        root = unpacked_stage
    package = json.loads((root / "package.json").read_text(encoding="utf-8-sig"))
    if not isinstance(package, dict):
        raise ValueError("游戏 package.json 必须是 JSON 对象。")
    main = package.get("main", "index.html")
    if not isinstance(main, str) or not main or "?" in main or "#" in main or "\\" in main:
        raise ValueError("当前启动层只支持本地 HTML main，带查询参数的入口尚未验证。")
    main = normalize_main(main)
    entry = (root / main).resolve(strict=True)
    if not entry.is_relative_to(root) or not entry.is_file() or entry.suffix.lower() not in (".html", ".htm"):
        raise ValueError("当前启动层只支持游戏目录内的 HTML main。")
    unsupported = [key for key in ("node-main", "bg-script", "inject_js_start", "inject_js_end") if package.get(key)]
    if unsupported:
        raise ValueError("游戏自带额外启动脚本，尚未验证其路径语义：" + ", ".join(unsupported))
    save_provenance = {}
    for save in {root / "save", entry.parent / "save"}:
        if not save.exists():
            continue
        if save.is_symlink() or not save.is_dir():
            raise ValueError(f"目录含既有存档，本入口不复制或迁移它们：{save}")
        for rel, item in inventory(save).items():
            if item[0] == "directory":
                continue
            source_rel = (save / rel).relative_to(game)
            depots = []
            for content in (STEAM / "Steam.AppBundle/Steam/Contents/MacOS/steamapps/content",
                            STEAM / "steamapps/content"):
                depots.extend((content / ("app_" + appid)).glob("depot_*"))
            matches = []
            for depot in depots:
                reference = depot / source_rel
                if (item[0] == "file" and reference.is_file() and not reference.is_symlink()
                        and digest(reference) == item[2]):
                    matches.append(depot.name)
            if not matches:
                raise ValueError(f"目录含既有存档，未与 Steam depot 原件核对，本入口不复制或迁移它们：{save / rel}")
            save_provenance[source_rel.as_posix()] = {"sha256": item[2], "matches_depots": matches}
    runtime_root = Path(os.environ.get("NWJS_ROOT", SN_RUNTIMES / "nwjs"))
    candidates = list(runtime_root.glob("*/nwjs-v*-osx-arm64/nwjs.app"))
    candidates.sort(key=lambda p: tuple(int(x) for x in p.parent.parent.name.split(".") if x.isdigit()))
    runtime = Path(os.environ["NWJS_APP"]) if os.environ.get("NWJS_APP") else (candidates[-1] if candidates else None)
    if runtime is None:
        raise ValueError(f"缺少 arm64 NW.js。请运行 bash {shlex.quote(str(SN_HOME.resolve() / 'bin/install.sh'))} --runtime-only --with-nwjs 准备此运行时。")
    runtime = runtime.resolve(strict=True)
    architecture = subprocess.check_output(["/usr/bin/file", "-b", str(runtime / "Contents/MacOS/nwjs")], text=True)
    if "arm64" not in architecture:
        raise ValueError(f"运行时不是 arm64：{runtime}")
    before = inventory(game)
    content_id = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    stage = Path(tempfile.mkdtemp(prefix=".nwjs-stage-", dir=output.parent))
    app = stage / output.name
    try:
        resources = app / "Contents/Resources"
        macos = app / "Contents/MacOS"
        frameworks = app / "Contents/Frameworks"
        for directory in (resources, macos, frameworks):
            directory.mkdir(parents=True, exist_ok=True)
        clone(runtime, frameworks / "NWJS.app")
        # 原厂 ZIP 的 app 资源封存尚未完整；只重签本次运行时副本的外层，不改引擎代码。
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-",
                        str(frameworks / "NWJS.app")], check=True)
        original = resources / "OriginalGame"
        clone(game, original)
        if inventory(original) != before or inventory(game) != before:
            raise ValueError("游戏在复制期间变化，或副本校验不符；已停止且保留暂存产物。")
        if unpacked_stage is not None:
            unpacked = resources / "UnpackedNW"
            shutil.move(str(unpacked_stage), str(unpacked))
            unpacked_stage = None
            config = {"protocol": 1, "appid": appid, "name": name, "source": str(game),
                      "content_id": content_id, "content_base": "UnpackedNW", "content_root": ".",
                      "package_nw_sha256": digest(game / "package.nw"),
                      "main": entry.relative_to(root).as_posix(), "inventory": inventory(unpacked),
                      "original_inventory": before, "bundled_save_provenance": save_provenance}
        else:
            config = {"protocol": 1, "appid": appid, "name": name, "source": str(game),
                      "content_id": content_id, "content_root": root.relative_to(game).as_posix(),
                      "main": entry.relative_to(root).as_posix(), "inventory": before,
                      "bundled_save_provenance": save_provenance}
        (resources / "nwjs-launch.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        (resources / "steam_appid.txt").write_text(appid, encoding="ascii")
        for filename in ("nwjs-launch.py", "nwjs-runtime.sb", "nwjs-steam-stub.js"):
            shutil.copy2(SN_LIB / filename, resources / filename)
        launcher = (SN_LIB / "nwjs-launcher.sh").read_text(encoding="utf-8")
        launcher = launcher.replace("__SN_NWJS_PYTHON__", shlex.quote(sys.executable))
        (macos / "launcher").write_text(launcher, encoding="utf-8")
        (macos / "launcher").chmod(0o755)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleName": name, "CFBundleDisplayName": name,
            "CFBundleIdentifier": "io.dryport.nwjs." + appid,
            "CFBundleExecutable": "launcher", "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1", "NSHighResolutionCapable": True,
        }))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
        if inventory(original) != before:
            raise ValueError("签名后游戏副本校验不符；已停止且保留暂存产物。")
        publish(app, output)
        stage.rmdir()
    except Exception:
        if unpacked_stage is not None:
            shutil.rmtree(unpacked_stage, ignore_errors=True)   # 解包暂存只是 package.nw 的副本，失败就删
        print(f"构建未完成；原目录与已有输出保留，暂存位置：{stage}", file=sys.stderr)
        raise
    print(f"完成：{output}")
    print(f"原游戏清单与 SHA256 一致：{len(before)} 项；Windows 件、package.json 和脚本全部保留。")
    print("运行目录、浏览器配置及新存档独立；写入原游戏或 app 会被进程级限制拒绝。")
    print("Greenworks 不作替换；缺原生模块的游戏功能仍需逐款验证。")
    print(f'启动：open "{output}"')


if __name__ == "__main__":
    try:
        if len(sys.argv) != 5:
            raise ValueError("用法：nwjs-build.py <游戏目录> <显示名> <输出.app> <appid>")
        build(*sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"NW.js 打包失败：{error}", file=sys.stderr)
        sys.exit(1)
