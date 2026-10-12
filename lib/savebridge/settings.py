# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Persistent per-game Cloud opt-in. This command never connects to Steam."""
import argparse
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bundle_safety import atomic_output, output_parent_fd, require_output


def choice_path(root, appid):
    if not re.fullmatch(r"[0-9]+", appid) or not 0 < int(appid) <= 0xffffffff:
        raise ValueError("Invalid Steam app id")
    return root / ".savebridge-state" / appid / "choice"


def enabled(root, path):
    require_output(root, path)
    if not path.exists():
        return False
    parent = output_parent_fd(root, path)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Cloud choice must be a separate regular file")
            value = os.read(fd, 17)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    if value not in (b"enabled\n", b"disabled\n"):
        raise ValueError("Invalid Cloud choice; sync remains off")
    return value == b"enabled\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "enable", "disable", "init", "check"))
    parser.add_argument("appid")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--root", default=os.environ.get("SN_GAMES") or str(Path.home() / "Games"))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if root.exists() and not root.is_dir():
        raise ValueError("Game settings root is not a directory")
    path = choice_path(root, args.appid)
    current = enabled(root, path)
    if args.command == "check":
        return 0 if current else 1
    if args.command == "status":
        print(f"Cloud save sync: {'enabled' if current else 'disabled'} (appid {args.appid})")
        return 0
    if args.command == "enable" and not args.yes:
        print("Cloud sync uses this game's slot in your logged-in Steam account. Review the save folder and patterns first.")
        print("A first-sync conflict keeps both versions and stops synchronization. No sync happens while changing this setting.")
        if not sys.stdin.isatty():
            raise ValueError("Explicit opt-in requires --yes or a terminal confirmation")
        if input("Enable Cloud sync for this game? [y/N] ").strip().lower() != "y":
            return 1
    if args.command == "init" and path.exists():
        return 0
    root.mkdir(parents=True, exist_ok=True)
    with atomic_output(root, path) as output:
        output.write(b"enabled\n" if args.command == "enable" else b"disabled\n")
    if args.command != "init":
        print(f"Cloud save sync: {'enabled' if args.command == 'enable' else 'disabled'} (appid {args.appid}); next launch")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"Cloud setting refused: {error}", file=sys.stderr)
        sys.exit(2)
