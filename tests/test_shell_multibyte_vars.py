# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""bash 里 `$var` 后面紧跟非 ASCII 字符（中文标点、全角括号）时，C locale 下高位字节会被吞进变量名，
2026-09-13 归档/卸载与 self-update 各出过一次（the runtime notes）。这里把仓库里所有 shell 脚本扫一遍，出现即失败。"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")


def shell_scripts():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    for name in out.decode().split("\0"):
        if not name:
            continue
        path = ROOT / name
        if name.endswith(".sh") or name.startswith("bin/") or name == "app/build.sh":
            try:
                head = path.open("rb").read(64)
            except OSError:
                continue
            if name.endswith(".sh") or head.startswith(b"#!/bin/bash") or head.startswith(b"#!/usr/bin/env bash"):
                yield name, path


class ShellMultibyteVariableTests(unittest.TestCase):
    def test_no_variable_is_immediately_followed_by_non_ascii(self):
        offenders = []
        for name, path in shell_scripts():
            in_heredoc = None; heredoc_is_bash = False; first_in_heredoc = False
            for lineno, line in enumerate(path.read_text(encoding="utf8", errors="replace").splitlines(), 1):
                # 引号 heredoc（<<'X'）里通常是别的语言，不算；但生成出来的 bash 启动壳（首行 #!/bin/bash）运行时同样会踩坑，照样扫
                m = re.search(r"<<-?\s*'([A-Za-z_]+)'", line)
                if in_heredoc is None and m:
                    in_heredoc = m.group(1); heredoc_is_bash = False; first_in_heredoc = True; continue
                if in_heredoc is not None:
                    if line.strip() == in_heredoc:
                        in_heredoc = None; continue
                    if first_in_heredoc:
                        heredoc_is_bash = line.startswith("#!/bin/bash"); first_in_heredoc = False
                    if not heredoc_is_bash:
                        continue
                if PATTERN.search(line):
                    offenders.append(f"{name}:{lineno}: {line.strip()[:90]}")
        self.assertEqual(offenders, [], "变量后紧跟非 ASCII 要写成 ${var}：\n" + "\n".join(offenders))

    def test_scanner_sees_the_main_scripts(self):
        names = {name for name, _ in shell_scripts()}
        self.assertIn("bin/dryport", names); self.assertIn("bin/self-update.sh", names); self.assertIn("lib/paths.sh", names)


if __name__ == "__main__":
    unittest.main()
