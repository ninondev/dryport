#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""打一个发布目录：独立 app 包 + 预编译运行时包 + 无历史源码快照 + release-manifest.json。

  tools/release-pack.py --out 目录 [--version 0.1.YYYYMMDD] [--skip-runtimes] [--skip-source]

产出：
  Dryport-<version>.app.tar.xz     app/build.sh --standalone 的产物打包（工具链在 app 内，含 LICENSE 与 docs/LEGAL.md）
  runtime-manifest.json + *.tar.xz      tools/runtime-bundle.py pack（可跳过；归档内带各运行时的许可证原文）
  THIRD-PARTY-NOTICES.md               从 runtime-manifest.json 渲染的第三方声明索引（--skip-runtimes 时只留本项目一行）
  source-<version>.tar.gz               tools/release-check.py --archive（检查器过了才产出，可跳过）
  release-manifest.json                 版本、git 提交、各文件 SHA256 与字节数；bin/self-update.sh 按它升级
本工具只生成本地发布目录。所有 SHA256 由本次执行算出。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_HOME, DISPLAY_APP_NAME
from app_versions import version_tuple

TOOLS = Path(__file__).resolve().parent


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"失败（{r.returncode}）：{' '.join(map(str, cmd))}\n{r.stdout[-800:]}{r.stderr[-800:]}")
    return r.stdout


# 桥自己编出来的 PE 在 0x40 处有这 16 个字节；Valve 的 steam_api.dll 是普通 DOS stub，没有。
WINE_BUILTIN = b"Wine builtin DLL"
STEAM_API_NAMES = ("steam_api.dll", "steam_api64.dll")


def assert_no_valve_binaries(out: Path):
    """发布包里凡是叫 steam_api* 的，必须是我们自己编的桥；Valve 的 macOS 库一个都不许有。

    这是项目法律主张的核心（docs/LEGAL.md：桥既不链接也不复制 Valve 的库），
    在 2026-09-13 之前只靠人记得去查。那天为了读 32 位 ABI 一次性取了 5 份 Valve 的
    steam_api.dll 放在本机，仓库靠 .gitignore 的 `*.dll` 挡住了，但发布包这一侧没有闸——
    运行时包本来就该带 DLL（我们自己的），所以按扩展名一刀切的办法在这里用不了，
    只能按内容认。
    """
    bad = []
    for archive in sorted(out.iterdir()):
        if not archive.name.endswith((".tar.xz", ".tar.gz", ".tar.bz2")):
            continue
        with tarfile.open(archive) as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = PurePosixPath(member.name).name
                low = name.lower()
                if low.startswith("libsteam_api") and low.endswith(".dylib"):
                    bad.append(f"{archive.name}: {member.name}（Valve 的 macOS 库不该进发布包）")
                    continue
                if low not in STEAM_API_NAMES:
                    continue
                handle = tar.extractfile(member)
                head = handle.read(0x50) if handle else b""
                if head[0x40:0x50] != WINE_BUILTIN:
                    bad.append(f"{archive.name}: {member.name}（0x40 处不是 Wine builtin 标记，"
                               f"疑似 Valve 原件而不是本项目编的桥）")
    if bad:
        sys.exit("发布包里有不该出现的 Steam 二进制：\n  " + "\n  ".join(bad))
    return True


APP_LICENSE_FILES = ("Contents/Resources/toolchain/LICENSE", "Contents/Resources/toolchain/docs/LEGAL.md")


def assert_app_license_files(app: Path):
    """独立 app 的工具链里必须有本项目的 LICENSE 与 docs/LEGAL.md。

    build.sh --standalone 用 git ls-files 复制这两个文件，这里在打包前再核一遍：
    若哪天复制清单被改漏，宁可停在这里，也不能发一个没有法律文本的 app。
    """
    missing = [rel for rel in APP_LICENSE_FILES if not (app / rel).is_file()]
    if missing:
        sys.exit("独立 app 缺法律文件（app/build.sh --standalone 应复制 LICENSE 与 docs/LEGAL.md）：\n  "
                 + "\n  ".join(missing))
    return True


def license_skip_failures(runtime_manifest: dict):
    """pack 因许可证原文缺失跳过的运行时（清单没登记 / 登记的原文不存在）。

    这类跳过在单独重打某个运行时时是合理的提醒，但在发布目录里是致命的：
    每个随包运行时都带原文，缺一个就整批停下来补齐。
    运行时没构建（缺主程序之类）不算，保持今天的行为。
    """
    return [s for s in runtime_manifest.get("skipped", [])
            if (s.get("why") or "") == "清单没登记许可证原文" or (s.get("why") or "").startswith("缺许可证原文：")]


def render_third_party_notices(runtime_manifest: dict) -> str:
    """从 runtime-manifest.json 渲染 THIRD-PARTY-NOTICES.md：给每个运行时一节署名与原文位置索引。

    真正的法律效力在随包原文与各归档里的 NOTICE.txt；这里让人不解包也能看到谁是谁、原文去哪找。
    """
    lines = ["本项目按 GPL-3.0-only 许可证发布，下列每个运行时保留各自的许可证与版权声明；"
             "许可证原文在每个运行时归档内的 third-party-licenses/<名字>/ 下，安装后位于运行时根目录的 third-party-licenses/<名字>/。",
             ""]
    for e in runtime_manifest.get("runtimes", []):
        lines.append(f"## {e['name']} {e['version']}")
        lines.append("")
        lines.append(f"- 许可证：{e['license']}")
        lines.append(f"- 来源：{e['source']}")
        lines += [f"- {n}" for n in e.get("notice", [])]
        if e.get("license_members"):
            lines.append("- 归档内：" + "、".join(f"`{m}`" for m in e["license_members"]))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", default="0.1." + time.strftime("%Y%m%d"))
    ap.add_argument("--skip-runtimes", action="store_true")
    ap.add_argument("--skip-source", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true", help="工作树有未提交改动时也打包（清单会标 dirty）；正式发布不要用")
    a = ap.parse_args(argv)
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    revision = run(["git", "-C", str(SN_HOME), "rev-parse", "HEAD"]).strip()
    dirty = run(["git", "-C", str(SN_HOME), "status", "--porcelain", "--untracked-files=no"]).strip()
    if dirty and not a.allow_dirty:
        sys.exit("工作树有未提交改动，清单里的提交号会对不上包内容；先提交，或加 --allow-dirty（只用于试打包）：\n" + dirty[:600])
    version_tuple(a.version)
    manifest = {"manifest_version": 1, "version": a.version, "revision": revision, "dirty": bool(dirty),
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": {}}
    # 1. 独立 app
    stage = Path(tempfile.mkdtemp(prefix=".release-app.", dir=out))
    try:
        app = stage / DISPLAY_APP_NAME
        run(["bash", str(SN_HOME / "app/build.sh"), "--standalone"],
            env={**os.environ, "SN_APP": str(app), "SN_APP_VERSION": a.version})
        marker = app / "Contents/Resources/toolchain/SOURCE-REVISION.txt"
        if marker.read_text().strip() != revision:
            sys.exit("app 里记的提交与当前 HEAD 不一致")
        assert_app_license_files(app)
        stem = DISPLAY_APP_NAME[:-len(".app")]
        archive = out / f"{stem}-{a.version}.app.tar.xz"
        with open(archive, "wb") as fh:
            tar = subprocess.Popen(["tar", "-cf", "-", "-C", str(stage), DISPLAY_APP_NAME], stdout=subprocess.PIPE)
            xz = subprocess.Popen(["xz", "-T0", "-6", "-c"], stdin=tar.stdout, stdout=fh)
            tar.stdout.close(); rc_x = xz.wait(); rc_t = tar.wait()
        if rc_t or rc_x:
            sys.exit(f"app 打包失败 tar={rc_t} xz={rc_x}")
        manifest["app"] = {"archive": archive.name, "bundle": DISPLAY_APP_NAME, "sha256": sha256(archive), "bytes": archive.stat().st_size}
        print(f"app：{archive.name} {archive.stat().st_size / 1e6:.1f} MB")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    # 2. 运行时包
    rm_data = {"runtimes": []}
    if not a.skip_runtimes:
        print(run([sys.executable, "-B", str(TOOLS / "runtime-bundle.py"), "pack", "--out", str(out)]).strip())
        rm = out / "runtime-manifest.json"
        manifest["runtimes"] = {"manifest": rm.name, "sha256": sha256(rm)}
        rm_data = json.loads(rm.read_text(encoding="utf8"))
        bad = license_skip_failures(rm_data)
        if bad:
            sys.exit("这些运行时因许可证原文缺失被跳过，发布包不能没有它们的法律文本；"
                     "按 lib/runtimes.json 补齐原文后重新打包：\n  "
                     + "\n  ".join(f"{s['name']}：{s['why']}" for s in bad))
    # 第三方声明在 files 统计之前写出，才能跟其他文件一样进清单拿哈希；--skip-runtimes 时只留本项目一行
    notices = out / "THIRD-PARTY-NOTICES.md"
    notices.write_text(render_third_party_notices(rm_data), encoding="utf8")
    print(f"第三方声明：{notices.name}（{len(rm_data.get('runtimes', []))} 个运行时）")
    # 3. 源码快照（检查器过了才有）
    if not a.skip_source:
        src = out / f"source-{a.version}.tar.gz"
        if src.exists():
            src.unlink()
        r = subprocess.run([sys.executable, "-B", str(TOOLS / "release-check.py"), "--json", "--archive", str(src)], capture_output=True, text=True)
        report = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        if not report.get("passed") or not src.exists():
            sys.exit(f"源码快照未产出：发布检查器未通过（{len(report.get('issues', []))} 项）或归档失败；先修再打包")
        manifest["source"] = {"archive": src.name, "sha256": sha256(src), "bytes": src.stat().st_size}
        print(f"源码：{src.name} {src.stat().st_size / 1e6:.1f} MB")
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name != "release-manifest.json":
            manifest["files"][p.name] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    assert_no_valve_binaries(out)
    (out / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(f"发布清单：{out / 'release-manifest.json'}（版本 {a.version}，提交 {revision[:10]}，{len(manifest['files'])} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
