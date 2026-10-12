# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Shared confinement checks for local application and runtime bundles."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import posixpath
import secrets
import stat
import tarfile


def relative_violation(value, label):
    if (not isinstance(value, str) or not value or value.startswith(("/", "~"))
            or "\\" in value or "\0" in value or ".." in PurePosixPath(value).parts):
        return f"{label} 不是安全的相对路径：{value!r}"
    return None


def output_violation(root, target):
    # Callers supply the root selected at configuration time; never retarget it here.
    root = Path(root).absolute()
    target = Path(target).absolute()
    if root.is_symlink():
        return f"输出根目录含符号链接：{root}"
    if root.exists() and not root.is_dir():
        return f"输出根路径不是目录：{root}"
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return f"输出越出运行时目录：{target}"
    current = root
    for index, part in enumerate(parts):
        if part in (".", ".."):
            return f"输出路径不规范：{target}"
        current /= part
        if current.is_symlink():
            return f"输出路径含符号链接：{current}"
        if current.exists():
            if index < len(parts) - 1 and not current.is_dir():
                return f"输出父路径不是目录：{current}"
            if not (current.is_file() or current.is_dir()):
                return f"输出路径不是普通文件或目录：{current}"
    return None


def require_output(root, target):
    why = output_violation(root, target)
    if why:
        raise ValueError(why)


def directory_fd(path):
    """Open from the filesystem anchor, refusing links in every component."""
    path = Path(path).absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                raise ValueError(f"目录路径不规范：{path}")
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def verify_parent_fd(root, target, parent):
    """Refuse publication if the pinned parent is no longer at its checked path."""
    require_output(root, target)
    current = directory_fd(Path(target).absolute().parent)
    try:
        expected, actual = os.fstat(parent), os.fstat(current)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError(f"输出父目录已被替换：{target}")
    finally:
        os.close(current)


def output_parent_fd(root, target):
    """Open each parent without following links, including parents created here."""
    root = Path(root).absolute()
    target = Path(target).absolute()
    require_output(root, target)
    parts = target.relative_to(root).parts[:-1]
    fd = directory_fd(root)
    try:
        for part in parts:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        verify_parent_fd(root, target, fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def atomic_output(root, target):
    """Write a fresh regular file and replace its directory entry, never its link target."""
    target = Path(target)
    parent = output_parent_fd(root, target)
    temporary = ".bundle-part-" + secrets.token_hex(12)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as output:
            yield output
        verify_parent_fd(root, target, parent)
        try:
            mode = os.stat(target.name, dir_fd=parent, follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f"输出不是普通文件：{target}")
        except FileNotFoundError:
            pass
        os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def archive_violation(archive):
    with tarfile.open(archive, "r:*") as package:
        members = package.getmembers()
        links = {str(PurePosixPath(m.name)) for m in members if m.issym() or m.islnk()}
        link_members = {str(PurePosixPath(m.name)): m for m in members if m.issym() or m.islnk()}

        def resolve_link_path(parts, stack=()):
            resolved = []
            for part in parts:
                if part in ("", "."):
                    continue
                if part == "..":
                    if not resolved:
                        raise ValueError("链接目标越界")
                    resolved.pop()
                    continue
                resolved.append(part)
                name = "/".join(resolved)
                if name in link_members:
                    if name in stack or len(stack) >= 40:
                        raise ValueError("链接循环或链过长")
                    link = link_members[name]
                    value = link.linkname
                    if not value or value.startswith("/") or "\\" in value or "\0" in value:
                        raise ValueError("链接目标不合法")
                    prefix = resolved[:-1] if link.issym() else []
                    resolved = resolve_link_path(prefix + value.split("/"), stack + (name,))
            return resolved

        for member in members:
            name = member.name.rstrip("/")
            why = relative_violation(name, "归档成员")
            if why:
                return why
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                return f"归档含特殊文件：{name}"
            if any(str(parent) in links for parent in PurePosixPath(name).parents):
                return f"归档成员穿过链接：{name}"
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname)
                                            if member.issym() else member.linkname)
                if (not member.linkname or "\\" in member.linkname or posixpath.isabs(target)
                        or target == ".." or target.startswith("../")):
                    return f"归档成员 {name} 的链接目标越界：{member.linkname}"
                try:
                    # Resolve link components before processing any following '..'.
                    prefix = name.split("/")[:-1] if member.issym() else []
                    resolve_link_path(prefix + member.linkname.split("/"), (name,))
                except ValueError as error:
                    return f"归档成员 {name} 的链接被拒绝：{error}"
    return None
