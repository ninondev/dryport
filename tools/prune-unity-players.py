#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""给 $SN_RUNTIMES/unity-players/<版本>/ 瘦身：装配只用一个 mac 非开发版 mono 变体（arm64 → universal → x64），其余变体、dSYM、
开发版、解包后的 .pkg/.exe 原件都可以删。2026-09-14 一批 Unity 冒烟把这个目录从 5.9 GB 撑到 25 GB（每版 2 GB 多），磁盘跌破 15 GiB。

  tools/prune-unity-players.py [--dry-run] [<版本>...]
只删这几类，别的不碰；每版打印删了多少字节。
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from paths import SN_RUNTIMES   # noqa: E402

PREFER = ("macos_arm64_player_nondevelopment_mono", "macos_arm64_nondevelopment_mono", "macos_x64arm64_player_nondevelopment_mono",
          "macos_x64_player_nondevelopment_mono", "macos_x64_nondevelopment_mono", "macosx64_nondevelopment_mono",
          "macosxintel64_nondevelopment_mono", "macosx32_nondevelopment_mono", "macosxintel_nondevelopment_mono")


def du(p):
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def prune_version(vdir, dry):
    freed, removed = 0, []
    targets = []
    for pkg in list(vdir.glob("*.pkg")) + list(vdir.glob("*.exe")):
        if (vdir / "expanded").is_dir():
            targets.append(pkg)
    vars_dirs = [d for d in (vdir / "expanded").rglob("Variations") if d.is_dir()] if (vdir / "expanded").is_dir() else []
    for vars_dir in vars_dirs:
        present = [d.name for d in vars_dir.iterdir() if d.is_dir()]
        keep = next((n for n in PREFER if n in present), None)
        for d in vars_dir.iterdir():
            if not d.is_dir():
                continue
            if d.name == "mono":          # Variations/mono/Managed：mac 版 UnityEngine*.dll，装配要用
                continue
            if d.name != keep and (d.name.startswith("macos") or "development" in d.name or d.name.startswith("win") or d.name.startswith("linux")):
                targets.append(d)
        for dsym in vars_dir.rglob("*.dSYM"):
            if dsym.is_dir():
                targets.append(dsym)
    seen = set()
    for t in targets:
        if str(t) in seen or not t.exists():      # dSYM 可能在已删的变体里，rglob 时列到、删变体时一起没了
            continue
        seen.add(str(t))
        size = du(t) if t.is_dir() else t.stat().st_size
        freed += size; removed.append(f"{t.relative_to(vdir)} ({size / 1e6:.0f} MB)")
        if not dry:
            shutil.rmtree(t) if t.is_dir() else t.unlink()
    return freed, removed


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("versions", nargs="*")
    a = ap.parse_args(argv)
    root = SN_RUNTIMES / "unity-players"
    total = 0
    for vdir in sorted(root.iterdir()) if root.is_dir() else []:
        if not vdir.is_dir() or (a.versions and vdir.name not in a.versions):
            continue
        freed, removed = prune_version(vdir, a.dry_run)
        total += freed
        if removed:
            print(f"{vdir.name}: {'将' if a.dry_run else '已'}删 {len(removed)} 项，{freed / 1e9:.2f} GB")
    print(f"合计 {total / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
