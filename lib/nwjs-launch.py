#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""原文件只读映射到独立运行目录；不改写游戏脚本或游戏 manifest。"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def directory(path):
    if path.is_symlink():
        raise ValueError(f"运行数据目录不能是链接，原路径保留：{path}")
    path.mkdir(exist_ok=True)


def publish_new(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def save_directory(saves, relative, content, original, config):
    destination = saves / relative
    parent = saves
    for component in relative.parts[:-1]:
        parent = parent / component
        directory(parent)
    if destination.is_symlink():
        raise ValueError(f"存档目录不能是链接，原路径保留：{destination}")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"存档目录已被其他文件占用：{destination}")
        return destination  # 包括空目录；已有用户状态不补写、不覆盖。
    stage = Path(tempfile.mkdtemp(prefix=".save-seed-", dir=parent))
    seed = content / relative
    if seed.exists():
        for source in seed.rglob("*"):
            target = stage / source.relative_to(seed)
            if source.is_symlink():
                raise ValueError(f"随包初始存档含链接，已停止：{source}")
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            provenance = config["bundled_save_provenance"].get(source.relative_to(original).as_posix())
            if not provenance or digest(source) != provenance["sha256"]:
                raise ValueError(f"随包初始存档与构建时的 depot 校验不符：{source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    publish_new(stage, destination)
    return destination


def prepare(app):
    app = Path(app).resolve(strict=True)
    resources = app / "Contents/Resources"
    config = json.loads((resources / "nwjs-launch.json").read_text(encoding="utf-8"))
    source = Path(config["source"]).resolve()
    original = resources / "OriginalGame"
    # package.nw（ZIP）打包的游戏在构建时解到 UnpackedNW/，视图镜像它；原件仍在 OriginalGame 完整保留
    content = resources / config.get("content_base", "OriginalGame") / config["content_root"]
    state_root = Path(os.environ.get("NWJS_STATE_ROOT") or
                      Path.home() / "Library/Application Support/dryport/nwjs").resolve()
    state = state_root / config["appid"]
    if inside(state.resolve(), app) or inside(state.resolve(), source):
        raise ValueError("运行数据目录不能位于游戏或 app 内；请设置独立的 NWJS_STATE_ROOT。")
    if state.is_symlink():
        raise ValueError(f"状态目录不能是链接，原路径保留：{state}")
    identity = {"protocol": 1, "appid": config["appid"], "save_layout": 2}
    owner = state / ".dryport-nwjs-state.json"
    if state.exists():
        existing = json.loads(owner.read_text()) if owner.is_file() and not owner.is_symlink() else None
        if existing == {"protocol": 1, "appid": config["appid"]}:
            raise ValueError(f"发现旧格式存档，已保留并停止；当前不自动迁移：{state}")
        if existing != identity:
            raise ValueError(f"状态目录归属无法确认，原目录保留：{state}")
    else:
        state_root.mkdir(parents=True, exist_ok=True)
        new_state = Path(tempfile.mkdtemp(prefix=".state-" + config["appid"] + "-", dir=state_root))
        (new_state / owner.name).write_text(json.dumps(identity))
        publish_new(new_state, state)
    key = hashlib.sha256(("view-layout-2|" + str(app) + config["content_id"]).encode()).hexdigest()[:24]
    views = state / "views"
    directory(views)
    container = views / key
    view = container / "app"
    saves = state / "saves"
    directory(saves)
    view_identity = {"protocol": 2, "app": str(app), "content_id": config["content_id"]}
    # 回执与游戏视图分层，既不撞游戏文件名，又能一次原子提交。
    view_owner = container / "receipt.json"
    if not os.path.lexists(container):
        stage_container = Path(tempfile.mkdtemp(prefix=".view-", dir=views))
        stage = stage_container / "app"
        stage.mkdir()
        root_rel = Path(config["content_root"])
        save_paths = {Path("save"), Path(config["main"]).parent / "save"}
        # 镜像目录保持 mainModule、相对 require、XHR 的原有目录语义。
        for rel, entry in config["inventory"].items():
            if str(root_rel) == ".":
                local = Path(rel)
            else:
                try:
                    local = Path(rel).relative_to(root_rel)
                except ValueError:
                    continue
            if str(local) == "." or local == Path("package.json"):
                continue
            # 初始存档由独立状态目录承接；原件仍完整保存在 OriginalGame。
            if any(inside(local, save) for save in save_paths):
                continue
            target = stage / local
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry[0] == "directory":
                target.mkdir(exist_ok=True)
            else:
                target.symlink_to(content / local)
        for relative in save_paths:
            save = stage / relative
            if save.exists():
                save.rmdir()  # 构建入口已拒绝非空的既有存档目录。
            save.parent.mkdir(parents=True, exist_ok=True)
            destination = save_directory(saves, relative, content, original, config)
            save.symlink_to(destination, target_is_directory=True)
        package = json.loads((content / "package.json").read_text(encoding="utf-8-sig"))
        window = package.get("window") if isinstance(package.get("window"), dict) else {}
        allowed = ("width", "height", "min_width", "min_height", "max_width", "max_height", "resizable")
        window = {key: window[key] for key in allowed if key in window}
        window.update({"fullscreen": False, "kiosk": False, "frame": True,
                       "title": config["name"], "position": "center"})
        window.setdefault("width", 1280)
        window.setdefault("height", 720)
        manifest = {"name": "dryport-nwjs-" + config["appid"], "main": config["main"],
                    "window": window}
        # 游戏自带的 Steam 原生模块（greenworks / Steam4C2 的 .node）在 arm64 新版 NW.js 里加载不了，包装脚本会抛未捕获异常
        # 把游戏卡在黑屏；注入一段只在加载失败时兜底的桩（见 lib/nwjs-steam-stub.js），游戏照常跑但没有 Steam 功能。
        stub_src = Path(__file__).resolve().parent / "nwjs-steam-stub.js"
        if not stub_src.is_file():
            stub_src = resources / "nwjs-steam-stub.js"
        if stub_src.is_file():
            shutil.copyfile(stub_src, stage / "dryport-stub.js")
            manifest["inject_js_start"] = "dryport-stub.js"
        if package.get("nodejs") is False:
            manifest["nodejs"] = False
        # 仅保留已确认用于 MV 的 V8 开关；不携带游戏里的 Chromium 安全覆盖参数。
        if package.get("js-flags") == "--expose-gc":
            manifest["js-flags"] = "--expose-gc"
        (stage / "package.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with (stage_container / "receipt.json").open("x", encoding="utf-8") as record:
            json.dump(view_identity, record)
        publish_new(stage_container, container)
    elif container.is_symlink() or not container.is_dir():
        raise ValueError(f"运行目录不是本工具创建的目录：{view}")
    if view.is_symlink() or not view.is_dir():
        raise ValueError(f"运行视图目录不完整，原路径保留：{view}")
    if (view_owner.is_symlink() or not view_owner.is_file()
            or json.loads(view_owner.read_text()) != view_identity):
        raise ValueError(f"运行目录归属无法确认，原目录保留：{view}")
    for folder in (state / "profile", state / "tmp", state / "crashes"):
        directory(folder)
    return app, source, state, view


def sandbox_prefix(app, source, state, system_temp, profile):
    args = ["/usr/bin/sandbox-exec", "-D", "STATE=" + str(state),
            "-D", "TEMP=" + str(system_temp), "-D", "BUNDLE=" + str(app),
            "-D", "SOURCE=" + str(source)]
    ancestors = sorted(set(app.parents) | set(source.parents))
    filters = []
    for index, ancestor in enumerate(ancestors):
        key = "ANCESTOR_" + str(index)
        args.extend(["-D", key + "=" + str(ancestor)])
        filters.append('(literal (param "' + key + '"))')
    # 临时目录可写时，原件的父目录也不能被改名；否则静态 subpath 会失去目标。
    policy = profile.read_text(encoding="utf-8") + "\n(deny file-write-unlink " + " ".join(filters) + ")\n"
    return args + ["-p", policy]


def main():
    app, source, state, view = prepare(sys.argv[1])
    # 不接受用户透传 Chromium 开关，以免覆盖数据目录或关闭这层写入保护。
    if len(sys.argv) > 2:
        raise ValueError("当前 NW.js 启动入口不接受额外命令参数。")
    system_temp = Path(tempfile.gettempdir()).resolve()
    runtime = app / "Contents/Frameworks/NWJS.app/Contents/MacOS/nwjs"
    args = sandbox_prefix(app, source, state, system_temp, app / "Contents/Resources/nwjs-runtime.sb")
    args.extend([str(runtime), "--nwapp=" + str(view), "--user-data-dir=" + str(state / "profile"),
                 "--crash-dumps-dir=" + str(state / "crashes")])
    env = {**os.environ, "TMPDIR": str(state / "tmp") + "/"}
    env.pop("NW_PRE_ARGS", None)
    env.pop("NODE_OPTIONS", None)
    os.chdir(view)
    os.execve(args[0], args, env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"游戏尚未启动：{error}", file=sys.stderr)
        sys.exit(1)
