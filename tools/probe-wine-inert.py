#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""离线 Win64 惰性句柄探针；只运行自有程序、新 prefix 和 DLL 副本，不启动游戏或 helper。

证明四件事：拒绝面的取句柄函数返回非空且互不相同的惰性句柄；其上的扁平调用与 C++ 虚表调用都返回 0；
SteamInternal_ContextInit 只跑一次初始化并返回存储地址；这一切发生时 DLL 没有向 helper 发过任何请求
（读 DLL 自己的日志数 "-> fid" 行）。最后再证明没有 helper 时 served 家族仍然是不可用，行为未变。
"""
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

# 自己写的调用者，不把第三方 SDK 头文件带进仓库。
CLIENT = r'''
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static volatile LONG active;
static LONG CALLBACK fault(EXCEPTION_POINTERS *p) {
    if (active && p->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        printf("abi_fault address=0x%llx\n", (unsigned long long)p->ExceptionRecord->ExceptionInformation[1]);
        fflush(stdout);
        ExitProcess(86);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
typedef void *(*getter_t)(void *, int, int, const char *);
typedef void *(*create_t)(const char *);
typedef void *(*accessor_t)(void);
typedef unsigned long long (*lobby_t)(void *, int, int);
typedef float (*volume_t)(void *);
typedef void *(*analog_t)(void *, void *, unsigned long long, unsigned long long);
typedef void *(*ctxinit_t)(void *);
typedef void *(*findorcreate_t)(int, const char *);
typedef void *(*utils_t)(void *, int, const char *);
static findorcreate_t find_or_create;
static int ctx_calls;
static void ctx_init(void *slot) { *(void **)slot = find_or_create(0, "SteamMatchMaking009"); ctx_calls++; }
static int fails;
static void check(int ok, const char *what) { printf("%s: %s\n", ok ? "PASS" : "FAIL", what); fflush(stdout); if (!ok) fails++; }
static int count_helper_requests(void) {
    char path[MAX_PATH]; DWORD n = GetEnvironmentVariableA("TEMP", path, sizeof path);
    if (!n || n >= sizeof path - 20) strcpy(path, "C:\\windows\\temp");
    strcat(path, "\\steambridge.log");
    FILE *f = fopen(path, "rb"); if (!f) return -1;
    char line[1024]; int hits = 0;
    while (fgets(line, sizeof line, f)) if (strstr(line, "-> fid")) hits++;
    fclose(f); return hits;
}
int main(int argc, char **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    AddVectoredExceptionHandler(1, fault);
    if (argc != 2) return 2;
    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { printf("load_error=%lu\n", GetLastError()); return 3; }
#define SYM(type, name) type name = (type)GetProcAddress(dll, #name); if (!name) { printf("missing " #name "\n"); return 4; }
    SYM(create_t, SteamInternal_CreateInterface)
    SYM(getter_t, SteamAPI_ISteamClient_GetISteamMatchmaking)
    SYM(getter_t, SteamAPI_ISteamClient_GetISteamGenericInterface)
    SYM(accessor_t, SteamAPI_SteamMatchmaking_v009)
    SYM(accessor_t, SteamAPI_SteamInput_v006)
    SYM(lobby_t, SteamAPI_ISteamMatchmaking_CreateLobby)
    SYM(volume_t, SteamAPI_ISteamMusic_GetVolume)
    SYM(analog_t, SteamAPI_ISteamInput_GetAnalogActionData)
    SYM(ctxinit_t, SteamInternal_ContextInit)
    SYM(findorcreate_t, SteamInternal_FindOrCreateUserInterface)
    SYM(findorcreate_t, SteamInternal_FindOrCreateGameServerInterface)
    SYM(utils_t, SteamAPI_ISteamClient_GetISteamUtils)
    find_or_create = SteamInternal_FindOrCreateUserInterface;
    active = 1;
    void *mm1 = SteamInternal_CreateInterface("SteamMatchMaking009");
    void *mm2 = SteamAPI_ISteamClient_GetISteamMatchmaking((void *)1, 0, 0, "SteamMatchMaking009");
    void *mm3 = SteamAPI_SteamMatchmaking_v009();
    void *nu = SteamAPI_ISteamClient_GetISteamGenericInterface((void *)1, 0, 0, "SteamNetworkingUtils004");
    void *inp = SteamAPI_SteamInput_v006();
    void *gs = SteamInternal_FindOrCreateGameServerInterface(0, "SteamGameServer015");
    void *unknown = SteamInternal_CreateInterface("STEAMTIMELINE_INTERFACE_V004");
    check(mm1 && mm1 == mm2 && mm2 == mm3, "matchmaking: three getters, one inert handle");
    check(nu && nu != mm1 && inp && inp != nu && gs && gs != mm1, "distinct inert handles per family");
    check(unknown != NULL, "unknown family still gets an inert handle");
    check(SteamAPI_ISteamMatchmaking_CreateLobby(mm1, 0, 4) == 0, "flat CreateLobby -> 0");
    void **vt = *(void ***)mm1;
    check(((long long (*)(void *))vt[7])(mm1) == 0, "vtable slot 7 -> 0");
    check(((double (*)(void *))vt[9])(mm1) == 0.0, "vtable slot 9 as double -> 0.0");
    check(((long long (*)(void *, int, int, int, int))vt[300])(mm1, 1, 2, 3, 4) == 0, "vtable slot 300 with args -> 0");
    check(SteamAPI_ISteamMusic_GetVolume(mm1) == 0.0f, "typed float body -> 0.0f");
    unsigned char analog[16]; memset(analog, 0xAB, sizeof analog);
    void *ret = SteamAPI_ISteamInput_GetAnalogActionData(analog, inp, 1, 2);
    int zero = 1; for (int i = 0; i < 16; i++) zero &= analog[i] == 0;
    check(ret == analog && zero, "typed aggregate body zeroes 16 bytes and returns the pointer");
    void *ctx[3] = { (void *)ctx_init, 0, 0 };
    void *c1 = SteamInternal_ContextInit(ctx), *c2 = SteamInternal_ContextInit(ctx);
    check(c1 == &ctx[2] && c2 == c1 && ctx_calls == 1 && ctx[2] == mm1, "ContextInit: storage, init once, handle stored");
    int requests = count_helper_requests();
    printf("helper_requests_during_inert=%d\n", requests);
    check(requests == 0, "no request left the DLL during the inert checks");
    /* Served family without a helper stays unavailable: the old behaviour, unchanged. */
    void *utils = SteamAPI_ISteamClient_GetISteamUtils((void *)1, 0, "SteamUtils010");
    check(utils == NULL, "served family without helper -> unavailable");
    active = 0;
    FreeLibrary(dll);
    printf("inert_ok fails=%d steam_contacted=false\n", fails);
    return fails ? 5 : 0;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded(argv, env, directory, log, timeout):
    start = time.monotonic()
    with log.open("wb") as output:
        p = subprocess.Popen(argv, env=env, cwd=directory, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
            return {"rc": rc, "seconds": time.monotonic() - start, "timeout": False}
        except subprocess.TimeoutExpired:
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
    base = Path(tempfile.mkdtemp(prefix="sn-wine-inert-", dir=runtime))
    os.chmod(base, 0o700)
    before = sha(dll)
    (base / "probe.c").write_text(CLIENT)
    shutil.copyfile(dll, base / "steam_api64.dll")
    prefix = base / "prefix"
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all", WINEARCH="win64",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d")
    with socket.socket() as reserved:   # 占住一个没有监听的本机端口，保证无法误连正在运行的真实 helper。
        reserved.bind(("127.0.0.1", 0))
        env["STEAMBRIDGE_PORT"] = str(reserved.getsockname()[1])
        report = {"protocol": 1, "ok": False, "scope": "offline_win64_inert_handles", "dll_sha256": before,
                  "wine": str(wine), "prefix": str(prefix), "original_dll_unchanged": False, "steam_contacted": False}
        print(base, flush=True)
        try:
            report["compile"] = bounded([gcc, "-O2", "-Wall", "-o", str(base / "probe.exe"), str(base / "probe.c")],
                                        env, base, base / "compile.log", 30)
            if report["compile"]["rc"] != 0:
                return 1
            report["initialize"] = bounded([str(wine), "wineboot", "--init"], env, base, base / "initialize.log", args.timeout)
            if report["initialize"]["rc"] != 0 or report["initialize"]["timeout"]:
                return 1
            report["probe"] = bounded([str(wine), str(base / "probe.exe"), "Z:" + str(base / "steam_api64.dll").replace("/", "\\")],
                                      env, base, base / "probe.log", args.timeout)
            log = (base / "probe.log").read_text(errors="replace")
            report["checks_passed"] = log.count("PASS:")
            report["checks_failed"] = log.count("FAIL:")
            for line in log.splitlines():
                if line.startswith("helper_requests_during_inert="):
                    report["helper_requests_during_inert"] = int(line.split("=", 1)[1])
            report["inert_ok"] = report["probe"]["rc"] == 0 and not report["probe"]["timeout"] and \
                report["checks_failed"] == 0 and "inert_ok fails=0" in log and report.get("helper_requests_during_inert") == 0
        finally:
            server = wine.parent / "wineserver"
            report["cleanup_kill"] = bounded([str(server), "-k"], env, base, base / "cleanup-kill.log", 5)
            report["cleanup_wait"] = bounded([str(server), "-w"], env, base, base / "cleanup-wait.log", 8)
            report["original_dll_unchanged"] = sha(dll) == before
            report["ok"] = bool(report.get("inert_ok") and report["original_dll_unchanged"] and
                                report["cleanup_kill"]["rc"] == 0 and not report["cleanup_kill"]["timeout"] and
                                report["cleanup_wait"]["rc"] == 0 and not report["cleanup_wait"]["timeout"])
            (base / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
