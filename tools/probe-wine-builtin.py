#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自有 DLL/EXE 验证 Wine 外置 builtin；不运行游戏、helper 或 Steam 调用。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
from paths import SN_HOME, SN_RUNTIMES

spec = importlib.util.spec_from_file_location("abi_probe", HERE / "probe-wine-steamutils.py")
abi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(abi)

CLIENT = r'''
#include <windows.h>
#include <psapi.h>
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (argc != 3) return 2;
    HMODULE module = LoadLibraryA(argv[1]);
    if (!module) { printf("loaded=0\nerror=%lu\n", GetLastError()); return 23; }
    char path[4096];
    printf("loaded=1\n");
    if (GetModuleFileNameA(module, path, sizeof path)) printf("logical_path=%s\n", path);
    if (GetMappedFileNameA(GetCurrentProcess(), module, path, sizeof path)) printf("mapped_path=%s\n", path);
    printf("builtin=%d\n", !memcmp((char *)module + sizeof(IMAGE_DOS_HEADER), "Wine builtin DLL", 16));
    printf("fixture=%d\n", GetProcAddress(module, "FixtureOriginal") != NULL);
    printf("bridge_export=%d\n", GetProcAddress(module, "SteamAPI_ISteamClient_GetISteamUtils") != NULL);
    puts("ready=1"); fflush(stdout);
    /* 让宿主读取这个自有进程的物理映射；超时退出，不能遗留常驻进程。 */
    for (int i = 0; i < 2000; i++) {
        if (GetFileAttributesA(argv[2]) != INVALID_FILE_ATTRIBUTES) {
            FreeLibrary(module); return 0;
        }
        Sleep(10);
    }
    return 24;
}
'''

ORIGINAL = r'''
#include <windows.h>
__declspec(dllexport) int FixtureOriginal(void) { return 734; }
BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID p) { return TRUE; }
'''


def windows(path):
    return "Z:" + str(path).replace("/", "\\")


def fields(text):
    keys = {"loaded", "error", "logical_path", "mapped_path", "builtin", "fixture", "bridge_export", "ready"}
    return {key: value.rstrip("\r") for line in text.splitlines()
            if "=" in line for key, value in [line.split("=", 1)] if key in keys}


def run_case(base, name, wine, exe, request, env, timeout):
    log = base / (name + ".log")
    ack = base / (name + ".ack")
    argv = [str(wine), str(exe), request, windows(ack)]
    report = {"command": argv, "override": env["WINEDLLOVERRIDES"], "dllpath": env["WINEDLLPATH"]}
    start = time.monotonic()
    with log.open("wb") as output:
        process = subprocess.Popen(argv, env=env, cwd=exe.parent, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None and time.monotonic() - start < timeout:
                if fields(log.read_text(errors="replace")).get("ready") == "1":
                    # lsof 的 txt 映射名是宿主观察；不以 Wine 的逻辑模块名代替物理来源。
                    report["host_pid"] = process.pid
                    report["lsof"] = abi.bounded(["/usr/sbin/lsof", "-a", "-p", str(process.pid), "-Fn"],
                                                  env, base, base / (name + ".lsof"), 5)
                    ack.touch(exist_ok=False)
                    break
                time.sleep(0.05)
            remaining = max(0.1, timeout - (time.monotonic() - start))
            report["rc"] = process.wait(timeout=remaining)
            report["timeout"] = False
        except subprocess.TimeoutExpired:
            report["timeout"] = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            report["rc"] = process.returncode
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
    report["seconds"] = time.monotonic() - start
    report["observed"] = fields(log.read_text(errors="replace"))
    lsof = base / (name + ".lsof")
    report["physical_dll_paths"] = [line[1:] for line in lsof.read_text(errors="replace").splitlines()
                                     if line.startswith("n") and line.lower().endswith("/steam_api64.dll")] if lsof.exists() else []
    return report


def accepted(case, expected, overlay, local):
    got = case["observed"]
    if case["timeout"]:
        return False
    if expected == "missing":
        return case["rc"] == 23 and got.get("loaded") == "0" and got.get("error") == "126"
    if case["rc"] != 0 or got.get("loaded") != "1":
        return False
    wanted = overlay if expected == "bridge" else local
    identity = (got.get("builtin"), got.get("fixture"), got.get("bridge_export"))
    expected_identity = ("1", "0", "1") if expected == "bridge" else ("0", "1", "0")
    observer = case.get("lsof", {})
    return (identity == expected_identity and observer.get("rc") == 0 and not observer.get("timeout", True)
            and set(case["physical_dll_paths"]) == {str(wanted)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", required=True, type=Path)
    parser.add_argument("--wine", type=Path, default=SN_RUNTIMES / "wine-cx/bin/wine")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 15 <= args.timeout <= 180:
        parser.error("每阶段超时须为 15 至 180 秒。")
    runtime, home = SN_RUNTIMES.resolve(strict=True), SN_HOME.resolve()
    if runtime == home or home in runtime.parents:
        parser.error("SN_RUNTIMES 不能位于源码仓库内。")
    dll, wine = args.dll.resolve(strict=True), args.wine.resolve(strict=True)
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not gcc:
        parser.error("缺少 MinGW 编译器。")
    base = Path(tempfile.mkdtemp(prefix="sn-wine-builtin-", dir=runtime))
    os.chmod(base, 0o700)
    print(base, flush=True)
    game = base / "fake game" / "plugins"
    game.mkdir(parents=True)
    overlay = base / "overlay"
    target = overlay / "x86_64-windows" / "steam_api64.dll"
    target.parent.mkdir(parents=True)
    shutil.copyfile(dll, target)
    source_hash = abi.sha(dll)
    (base / "original.c").write_text(ORIGINAL)
    (base / "loader.c").write_text(CLIENT)
    local = game / "steam_api64.dll"
    exe = game / "loader.exe"
    env = dict(os.environ, WINEPREFIX=str(base / "prefix"), WINEDEBUG="-all,+loaddll", WINEARCH="win64",
               WINEDLLPATH=str(overlay), WINEDLLOVERRIDES="steam_api64=b;mscoree,mshtml,winemenubuilder.exe=d")
    report = {"protocol": 1, "ok": False, "scope": "offline_builtin_loader", "source_dll_sha256": source_hash,
              "wine": str(wine), "prefix": env["WINEPREFIX"], "cases": {}, "steam_contacted": False,
              "source_references": ["dlls/ntdll/unix/loader.c:set_dll_path/find_builtin_dll/load_builtin",
                                     "dlls/ntdll/unix/virtual.c:virtual_map_builtin_module",
                                     "tools/winebuild/spec32.c:make_builtin_files"]}
    local_hash = None
    try:
        for name, command in [
            ("compile-original", [gcc, "-shared", "-O2", "-o", str(local), str(base / "original.c")]),
            ("compile-loader", [gcc, "-O2", "-o", str(exe), str(base / "loader.c"), "-lpsapi"]),
            ("mark-builtin", [str(wine.parent / "winebuild"), "--builtin", str(target)]),
            ("initialize", [str(wine), "wineboot", "--init"]),
        ]:
            report[name] = abi.bounded(command, env, base, base / (name + ".log"), args.timeout)
            if report[name]["rc"] != 0 or report[name]["timeout"]:
                return 1
        local_hash = abi.sha(local)
        report["local_dll_sha256"] = local_hash
        report["overlay_dll_sha256"] = abi.sha(target)
        for mode, expected in [("native", "original"), ("builtin", "bridge"), ("missing", "missing")]:
            current_env = dict(env)
            if mode == "native":
                current_env["WINEDLLOVERRIDES"] = "steam_api64=n;mscoree,mshtml,winemenubuilder.exe=d"
            if mode == "missing":
                # 只移动自己创建的 overlay；保留字节，证明确实没有可回退的 builtin。
                overlay.rename(base / "overlay-preserved")
            for kind, request in [("basename", "steam_api64.dll"), ("absolute", windows(local))]:
                name = mode + "-" + kind
                result = run_case(base, name, wine, exe, request, current_env, args.timeout)
                result["accepted"] = accepted(result, expected, target, local)
                report["cases"][name] = result
                print(name, "PASS" if result["accepted"] else "FAIL", flush=True)
    finally:
        server = wine.parent / "wineserver"
        report["cleanup_kill"] = abi.bounded([str(server), "-k"], env, base, base / "cleanup-kill.log", 5)
        report["cleanup_wait"] = abi.bounded([str(server), "-w"], env, base, base / "cleanup-wait.log", 8)
        report["source_dll_unchanged"] = abi.sha(dll) == source_hash
        report["local_dll_unchanged"] = local_hash is not None and abi.sha(local) == local_hash
        report["ok"] = (len(report["cases"]) == 6 and all(c["accepted"] for c in report["cases"].values()) and
                        report["source_dll_unchanged"] and report["local_dll_unchanged"] and
                        all(report[name]["rc"] == 0 and not report[name]["timeout"]
                            for name in ("cleanup_kill", "cleanup_wait")))
        (base / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
