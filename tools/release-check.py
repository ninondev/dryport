#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""检查当前源码或 Git 历史中的发布阻塞项，可导出无历史的源码预览包。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from paths import SN_HOME

PRIVATE_FILES = {
    "owned-windows.json", "final-routes.json", "route-all.json", "risk-all.json",
    "launch-exes.json", "appinfo-signals.json", "manifest-oracle-results.jsonl",
    "installed.json", "loginusers.vdf", "shortcuts.vdf", "appinfo.vdf", "registry.vdf",
    "config.vdf", "compat.vdf", "anticheat-db.json", "windows-library.json", "catalog.json",
}
SOURCE_JSON = {"app/benchmarks/api-startup-2026-09-12.json", "docs/benchmarks/unity-shader-cache-2026-09-12.json", "SOURCE-MANIFEST.json", "lib/steambridge/exports.json", "lib/unity-retarget/fmod_pinvokes.json"}
SOURCE_JSON.add("docs/benchmarks/appinfo-selection-2026-09-12.json")
SOURCE_JSON.add("docs/benchmarks/route-classification-2026-09-12.json")
SOURCE_JSON.add("lib/runtimes.json")   # 预编译运行时清单：产物路径、许可证与来源，本项目自己写的
SOURCE_JSON.add("lib/route-tiers.json")   # 路线分层表：open/experimental 的承诺标注，本项目自己写的，CLI、api.py 与 app 共用
SOURCE_JSON.add("lib/steambridge/argsizes-i386.json")   # x86 thiscall 栈字节数：从 Valve 32 位 DLL 的机器码推演出的接口元数据，只有数字与名字
SOURCE_JSON.update({"docs/compatibility.json", "docs/compatibility-evidence.json"})   # Public ledger and recorded summaries
def exclusions(root):
    """Read exact paths and directory prefixes from the repository's export policy."""
    policy = root / ".publicignore"
    if not policy.is_file():
        return set(), ()
    files, prefixes = set(), []
    for line in policy.read_text().splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Invalid export exclusion")
        if name.endswith("/"):
            prefixes.append(name)
        else:
            files.add(name)
    return files, tuple(prefixes)


INTERNAL_FILES, INTERNAL_PREFIXES = exclusions(SN_HOME)
SOURCE_JSON_PREFIXES = ("lib/steambridge/vtables/",)   # C++ 虚表槽位表：只有名字与槽号，README 说明来源
PATTERNS = [
    # Wine 前缀里的 drive_c/users/<名> 是 Windows 布局，不是本机主目录（2026-09-13 的桥日志测试用 users/fixture 被误拦过）。
    ("个人主目录", re.compile(r"(?i)(?<!drive_c)(?:/users/|[A-Z]:\\Users\\)(?!example(?:[/\\]|\b)|fixture(?:[/\\]|\b)|<)[\w.-]+")),
    ("个人临时目录", re.compile(r"/private/tmp/claude-\d+/-Users-(?!example/)[\w.-]+")),
    ("Steam 账号标识", re.compile(r"\b7656119[0-9xX]{10}\b")),
    ("私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("访问令牌", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{32,})\b")),
]
PUBLIC_CONSTANTS = {"76561197960265728"}  # SteamID64 的公开基数，不是用户账号。


# Images for the README and docs are the only binary files the source export may carry.
DOC_IMAGES = {".png", ".jpg", ".gif"}


def issues_for(name, content, symlink=False):
    issues = []
    path = PurePosixPath(name)
    if symlink:
        issues.append({"file": name, "line": 0, "reason": "软链不进入源码包，请保留实际源码"})
    if (path.name in PRIVATE_FILES or path.suffix.lower() in {".dll", ".exe", ".dylib", ".so", ".o", ".zip", ".gz", ".xz", ".app", ".pck", ".xp3", ".rpa", ".acf"}
            or any(p in {".build", "venv", "__pycache__", "data"} or p.endswith(".app") for p in path.parts)
            or path.name.startswith("oracle-queue")):
        issues.append({"file": name, "line": 0, "reason": "运行时、构建产物或个人数据"})
    if path.suffix in {".json", ".jsonl"} and name not in SOURCE_JSON and not name.startswith(SOURCE_JSON_PREFIXES):
        issues.append({"file": name, "line": 0, "reason": "JSON 数据尚未确认可分发"})
    if path.parts[0] == "docs" and path.suffix.lower() in DOC_IMAGES:
        return issues
    try:
        text = content.decode("utf-8")
        if "\0" in text:
            raise ValueError("binary")
    except (UnicodeError, ValueError):
        issues.append({"file": name, "line": 0, "reason": "非 UTF-8 源码或含二进制内容"})
        return issues
    for label, pattern in PATTERNS:
        for match in pattern.finditer(text):
            if match.group() in PUBLIC_CONSTANTS:
                continue
            issues.append({"file": name, "line": text.count("\n", 0, match.start()) + 1, "reason": label})
    return issues


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def snapshot(root):
    internal_files, internal_prefixes = exclusions(root)
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    entries = {}
    issues = []
    for name in sorted(set(x.decode() for x in names.split(b"\0") if x)):
        if name in internal_files or name.startswith(internal_prefixes):
            continue  # 内部文件与流程材料不进公开源码包，也不按公开标准检查
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue  # 已删除但尚未提交的文件不进入工作区快照。
        if path.is_symlink():
            content = str(path.readlink()).encode()
        else:
            content = path.read_bytes()
        issues.extend(issues_for(name, content, path.is_symlink()))
        entries[name] = (content, 0o755 if not path.is_symlink() and path.stat().st_mode & 0o111 else 0o644)
    return entries, issues


def history_issues(root):
    issues = []
    seen = set()
    commits = git(root, "rev-list", "--all").decode().splitlines()
    for commit in commits:
        for row in git(root, "ls-tree", "-rz", commit).split(b"\0"):
            if not row:
                continue
            header, raw_name = row.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            name = raw_name.decode()
            if kind != "blob" or (name, oid) in seen:
                continue
            seen.add((name, oid))
            for issue in issues_for(name, git(root, "cat-file", "blob", oid), mode == "120000"):
                issues.append({**issue, "commit": commit[:12]})
    return commits, issues


def archive(path, entries):
    manifest = {name: hashlib.sha256(content).hexdigest() for name, (content, _) in entries.items()}
    # 使用已检查的同一份内存快照，避免检查后文件被并行编辑而混入未检查内容。
    with path.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for name, (content, mode) in {**entries, "SOURCE-MANIFEST.json": (json.dumps(manifest, indent=2).encode() + b"\n", 0o644)}.items():
                    info = tarfile.TarInfo("source-preview/" + name)
                    info.size = len(content)
                    info.mode = mode
                    tar.addfile(info, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="同时检查全部本地 Git 历史；不修改历史")
    parser.add_argument("--json", action="store_true", help="只输出 JSON；发现项不打印匹配值")
    parser.add_argument("--archive", type=Path, help="检查通过后导出源码包，目标必须不存在，包内没有 .git")
    args = parser.parse_args()
    entries, issues = snapshot(SN_HOME)
    report = {"scope": "working-tree", "files": len(entries), "issues": issues}
    if args.history:
        commits, found = history_issues(SN_HOME)
        report.update(history_commits=len(commits), history_issues=found)
    passed = not issues and not report.get("history_issues")
    report["passed"] = passed
    if args.archive and passed:
        report["archive"] = str(args.archive.resolve())
        report["sha256"] = archive(args.archive, entries)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"源码快照: {len(entries)} 个文件，{'通过' if not issues else '发现发布阻塞项'}")
        for issue in issues + report.get("history_issues", []):
            prefix = f"[{issue['commit']}] " if "commit" in issue else ""
            print(f"  {prefix}{issue['file']}:{issue['line']} {issue['reason']}")
        if "archive" in report:
            print(f"源码包: {report['archive']}\nSHA256: {report['sha256']}")
        print("范围: 已识别的个人路径、账号、令牌、二进制和数据文件；不等于完整的隐私或许可证审计。")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"发布检查未完成: {exc}", file=sys.stderr)
        sys.exit(2)
