#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# krkr-run.py <程序> [参数...]  — 在伪终端里跑 krkrsdl2 换壳 app 并把日志打到 stdout。
# SDL2 在 Apple 平台把 SDL_Log 走 NSLog，stdout 是管道时什么都看不到；
# `script -q /dev/null` 在 stdin 不是 tty 的场合会 tcgetattr 失败，所以用 pty.spawn。
import os, pty, sys
out = []
def rd(fd):
    d = os.read(fd, 4096)
    out.append(d)
    return d
if len(sys.argv) < 2:
    sys.exit("用法: krkr-run.py <程序> [参数...]")
pty.spawn(sys.argv[1:], rd)
sys.stdout.buffer.write(b"".join(out))
