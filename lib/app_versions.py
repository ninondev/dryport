# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Compare release versions without assuming an order for arbitrary labels."""
from pathlib import Path
import plistlib
import re


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value):
        raise ValueError("Unsupported version: use one to four dotted numeric components")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def bundle_version(app):
    path = Path(app) / "Contents/Info.plist"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Could not determine installed version: bundle metadata is missing or linked")
    try:
        value = plistlib.loads(path.read_bytes())["CFBundleShortVersionString"]
        version_tuple(value)
        return value
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException) as error:
        raise ValueError("Could not determine installed version: invalid bundle metadata") from error
