# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""检查对外发布的 Markdown 里的相对文件链接。

范围与 tools/release-check.py 的源码快照同一人口：git 跟踪的加未跟踪未忽略的（--cached --others
--exclude-standard），减去点名的内部材料。只看跟踪文件会漏掉「未提交就带着坏链接的新文档」，
快照会把它带走，检查也要带走。私有文档按 .publicignore 的排除规则处理；
读不了的公开文档直接失败，不静默跳过——公开文档必须两个账户都读得了。
"""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\n]+)\)")
REFERENCE_LINK = re.compile(r"(?<!!)\[([^\]]+)\](?:\[([^\]]*)\])")
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# 内部排除直接复用 release-check 的定义（单一事实源，避免两处清单漂移）；只有 .md 会出现在这里。
_spec = importlib.util.spec_from_file_location("release_check_module", ROOT / "tools/release-check.py")
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)


def markdown_files(root=ROOT):
    """与 release-check.py 的 snapshot() 同一人口：--cached --others --exclude-standard，减内部文件；
    已删除未提交的跳过。未提交的新文档同样入册。"""
    internal_files, internal_prefixes = release_check.exclusions(root)
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"])
    for raw in names.split(b"\0"):
        name = raw.decode()
        if not name or name in internal_files or name.startswith(internal_prefixes):
            continue
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue  # 已删除但尚未提交的文件不进入快照
        yield path


def _destination(raw):
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1:raw.index(">")]
    return raw.split(None, 1)[0] if raw else ""


def _relative_file_links(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    definitions = {}
    for line in lines:
        match = REFERENCE_DEFINITION.match(line)
        if match:
            key = match.group(1).strip().lower()
            definitions[key] = match.group(2) or match.group(3)

    fence = None
    for number, line in enumerate(lines, 1):
        fence_match = re.match(r"^\s*(```+|~~~+)", line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue

        # 行内代码中的括号不是 Markdown 链接；去掉它们只影响扫描，不改原文。
        visible = re.sub(r"`[^`]*`", "", line)
        seen = set()
        for match in MARKDOWN_LINK.finditer(visible):
            raw = _destination(match.group(1))
            if raw:
                seen.add((raw, number))
                yield number, raw
        for match in REFERENCE_LINK.finditer(visible):
            label = match.group(1)
            key = (match.group(2) or label).strip().lower()
            raw = _destination(definitions.get(key, ""))
            if raw and (raw, number) not in seen:
                yield number, raw


def _is_relative_file_target(raw):
    if not raw or raw.startswith(("#", "/", "//")) or SCHEME.match(raw):
        return None
    target = raw.split("#", 1)[0]
    return target or None


def broken_links(root=ROOT):
    broken = []
    for path in markdown_files(root):
        for number, raw in _relative_file_links(path):
            target = _is_relative_file_target(raw)
            if target is None:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_file():
                broken.append(f"{path.relative_to(root)}:{number}: {raw}")
    return broken


class DocumentationLinkTests(unittest.TestCase):
    def test_relative_links_point_to_existing_files(self):
        self.assertEqual([], broken_links(), "\n".join(broken_links()))

    def test_every_public_markdown_is_reachable_and_checked(self):
        # 公开文档一份都不能少读：读不了（权限、丢失）在这里直接报错，而不是跳过
        checked = list(markdown_files())
        self.assertTrue(checked)
        for path in checked:
            self.assertTrue(path.is_file(), f"missing public document: {path}")
            path.read_text(encoding="utf-8")

    def test_an_untracked_markdown_with_a_broken_link_is_checked(self):
        # 未提交的新文档也会进入源码快照，其坏链接必须被检查。
        with tempfile.TemporaryDirectory(prefix="sn-docs-links-untracked-") as tmp:
            root = Path(tmp)

            def git(*args):
                subprocess.check_call(["git", "-C", str(root), *args],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "fixture")
            (root / "tracked.md").write_text("[good](tracked-target.md)\n", encoding="utf-8")
            (root / "tracked-target.md").write_text("fine\n", encoding="utf-8")
            git("add", "tracked.md", "tracked-target.md")
            git("commit", "-qm", "fixture")
            (root / "fresh-untracked.md").write_text("[broken](no-such-file.md)\n", encoding="utf-8")
            self.assertEqual(broken_links(root), ["fresh-untracked.md:1: no-such-file.md"])

    def test_internal_requests_are_not_public_documents(self):
        with tempfile.TemporaryDirectory(prefix="sn-internal-links-") as tmp:
            root = Path(tmp)
            subprocess.check_call(["git", "init", "-q", str(root)])
            (root / ".publicignore").write_text("private/\n")
            (root / "private").mkdir()
            (root / "private/audit.md").write_text("[private](absent.md)\n")
            (root / "README.md").write_text("Public documentation\n")
            self.assertEqual([p.name for p in markdown_files(root)], ["README.md"])


if __name__ == "__main__":
    unittest.main()
