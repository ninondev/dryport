#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""离线 Win64 ABI 探针；只运行自有程序、新 prefix 和 DLL 副本，不启动游戏或 helper。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from paths import SN_HOME, SN_RUNTIMES

# 自己写的三参数调用者，不把第三方 SDK 头文件带进仓库。
# 固定 Win64 的未定义 R9 为不可读值，区分正常 ABI 与误取第四参数；不依赖随机寄存器。
CLIENT = r'''
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
static volatile LONG active;
static LONG CALLBACK fault(EXCEPTION_POINTERS *p) {
    if (active && p->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        printf("abi_fault address=0x%llx\n",
            (unsigned long long)p->ExceptionRecord->ExceptionInformation[1]);
        fflush(stdout);
        ExitProcess(86);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
extern void *abi_call(void *fn, void *self, int pipe, const char *version);
__asm__(".text\n.globl abi_call\nabi_call:\n"
        "mov %rcx,%rax\nmov %rdx,%rcx\nmov %r8d,%edx\nmov %r9,%r8\n"
        "mov $0x30,%r9\njmp *%rax\n");
int main(int argc, char **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    AddVectoredExceptionHandler(1, fault);
    if (argc != 2) return 2;
    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { printf("load_error=%lu\n", GetLastError()); return 3; }
    void *fn = (void *)GetProcAddress(dll, "SteamAPI_ISteamClient_GetISteamUtils");
    if (!fn) return 4;
    for (int i = 0; i < 5; i++) {
        printf("before_call=%d\n", i); fflush(stdout);
        active = 1;
        void *result = abi_call(fn, (void *)(uintptr_t)0x5B0000F0, 0, "SteamUtils010");
        active = 0;
        /* 没有 helper，正确结果只能是不可用，不能伪称拿到了真实接口。 */
        if (result != NULL) { puts("unexpected_interface"); return 5; }
        printf("after_call=%d unavailable_as_expected\n", i); fflush(stdout);
    }
    FreeLibrary(dll);
    puts("abi_ok calls=5 steam_contacted=false");
    return 0;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded(argv, env, directory, log, timeout):
    start = time.monotonic()
    with log.open("wb") as output:
        p = subprocess.Popen(argv, env=env, cwd=directory, stdout=output,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
            return {"rc": rc, "seconds": time.monotonic() - start, "timeout": False}
        except subprocess.TimeoutExpired:
            # 只终止这个探针自己的进程组，prefix 服务由 finally 中自己的 wineserver 收口。
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=3)
            return {"rc": p.returncode, "seconds": time.monotonic() - start, "timeout": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", required=True, type=Path)
    parser.add_argument("--wine", type=Path, default=SN_RUNTIMES / "wine-cx/bin/wine")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 15 <= args.timeout <= 180:
        parser.error("--timeout 须为 15 至 180 秒；超时只说明本次未完成，不代表死锁。")
    dll, wine = args.dll.resolve(strict=True), args.wine.resolve(strict=True)
    runtime = SN_RUNTIMES.resolve(strict=True)
    if runtime == SN_HOME.resolve() or SN_HOME.resolve() in runtime.parents:
        parser.error("探针的 SN_RUNTIMES 不能位于源码仓库内。")
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not gcc:
        parser.error("缺少 x86_64-w64-mingw32-gcc。")
    base = Path(tempfile.mkdtemp(prefix="sn-wine-steamutils-", dir=runtime))
    os.chmod(base, 0o700)
    before = sha(dll)
    (base / "probe.c").write_text(CLIENT)
    shutil.copyfile(dll, base / "steam_api64.dll")
    prefix = base / "prefix"
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all", WINEARCH="win64",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d")
    # 占住一个没有监听的本机端口，保证无法误连正在运行的真实 helper。
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        env["STEAMBRIDGE_PORT"] = str(reserved.getsockname()[1])
        report = {"protocol": 1, "ok": False, "scope": "offline_win64_abi",
                  "dll_sha256": before, "wine": str(wine), "prefix": str(prefix),
                  "original_dll_unchanged": False, "steam_contacted": False}
        print(base, flush=True)
        try:
            report["compile"] = bounded([gcc, "-O2", "-Wall", "-o", str(base / "probe.exe"),
                                          str(base / "probe.c")], env, base, base / "compile.log", 30)
            if report["compile"]["rc"] != 0:
                return 1
            # wine-cx 的 Wineboot 源码禁用了更新等待窗口；同时关闭 Mono/HTML 安装器。
            report["initialize"] = bounded([str(wine), "wineboot", "--init"], env, base,
                                             base / "initialize.log", args.timeout)
            if report["initialize"]["rc"] != 0 or report["initialize"]["timeout"]:
                return 1
            report["probe"] = bounded([str(wine), str(base / "probe.exe"),
                                        "Z:" + str(base / "steam_api64.dll").replace("/", "\\")],
                                       env, base, base / "probe.log", args.timeout)
            log = (base / "probe.log").read_text(errors="replace")
            report["calls_completed"] = log.count("unavailable_as_expected")
            report["abi_ok"] = report["probe"]["rc"] == 0 and not report["probe"]["timeout"] and \
                report["calls_completed"] == 5 and "abi_ok calls=5" in log
        finally:
            server = wine.parent / "wineserver"
            report["cleanup_kill"] = bounded([str(server), "-k"], env, base, base / "cleanup-kill.log", 5)
            report["cleanup_wait"] = bounded([str(server), "-w"], env, base, base / "cleanup-wait.log", 8)
            report["original_dll_unchanged"] = sha(dll) == before
            report["ok"] = bool(report.get("abi_ok") and report["original_dll_unchanged"] and
                                report["cleanup_kill"]["rc"] == 0 and not report["cleanup_kill"]["timeout"] and
                                report["cleanup_wait"]["rc"] == 0 and not report["cleanup_wait"]["timeout"])
            (base / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
