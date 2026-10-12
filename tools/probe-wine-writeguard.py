#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""只在自有假原件和新 prefix 上验证 Wine 写入保护；不接受真实游戏目标。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
from paths import SN_HOME, SN_RUNTIMES
spec = importlib.util.spec_from_file_location("abi_probe", HERE / "probe-wine-steamutils.py")
abi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(abi)

CLIENT = r'''
#include <windows.h>
#include <stdio.h>
int main(int argc, char **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (argc != 9) return 2;
    HANDLE read = CreateFileA(argv[2], GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    char byte = 0; DWORD got = 0;
    if (read == INVALID_HANDLE_VALUE || !ReadFile(read, &byte, 1, &got, NULL) || got != 1) return 3;
    CloseHandle(read); puts("source_read=ok");
    int prelinked = argv[8][0] == '1';
    for (int i = 2; i <= (prelinked ? 5 : 4); i++) {
        HANDLE file = CreateFileA(argv[i], GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                   NULL, OPEN_EXISTING, 0, NULL);
        DWORD error = GetLastError();
        if (file != INVALID_HANDLE_VALUE) {
            DWORD written = 0;
            BOOL ok = WriteFile(file, "changed!", 8, &written, NULL);
            error = GetLastError();
            CloseHandle(file);
            if (ok && written) { printf("unexpected_write=%d bytes=%lu\n", i, written); return 4; }
        }
        printf("denied_%d=%lu\n", i, error);
        if (error != ERROR_ACCESS_DENIED) return 5;
    }
    if (!prelinked) {
        if (CreateHardLinkA(argv[5], argv[2], NULL)) { puts("unexpected_new_hardlink"); return 10; }
        DWORD link_error = GetLastError();
        printf("link_denied=%lu\n", link_error);
        if (link_error != ERROR_ACCESS_DENIED) return 11;
    }
    if (MoveFileA(argv[6], argv[7])) { puts("unexpected_ancestor_rename"); return 6; }
    DWORD rename_error = GetLastError();
    printf("rename_denied=%lu\n", rename_error);
    if (rename_error != ERROR_ACCESS_DENIED) return 7;
    HANDLE file = CreateFileA(argv[1], GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) { printf("state_write_error=%lu\n", GetLastError()); return 8; }
    DWORD written = 0;
    if (!WriteFile(file, "state-ok", 8, &written, NULL) || written != 8) return 9;
    CloseHandle(file); puts("state_write=ok"); return 0;
}
'''


def sandbox_command(protected, state, system_temp, prefix):
    protected = [Path(p).resolve() for p in protected]
    args = ["/usr/bin/sandbox-exec", "-D", "STATE=" + str(Path(state).resolve()),
            "-D", "TEMP=" + str(Path(system_temp).resolve())]
    # 当前 wine-cx 源码 unix/server.c:init_server_dir 使用固定 /tmp 路径，不采用 TMPDIR。
    # 只允许本次新 prefix 的 dev/inode 服务目录，不能开放其他 prefix 的套接字。
    identity = Path(prefix).stat()
    server_parent = Path("/private/tmp") / (".wine-" + str(os.getuid()))
    server = server_parent / f"server-{identity.st_dev:x}-{identity.st_ino:x}"
    args.extend(["-D", "WINE_SERVER=" + str(server), "-D", "WINE_SERVER_PARENT=" + str(server_parent)])
    exclusions = []
    for index, path in enumerate(protected):
        key = "PROTECTED_" + str(index)
        args.extend(["-D", key + "=" + str(path)])
        exclusions.append('(require-not (subpath (param "' + key + '")))')
    ancestors = sorted({parent for path in protected for parent in path.parents})
    unlink = []
    for index, path in enumerate(ancestors):
        key = "ANCESTOR_" + str(index)
        args.extend(["-D", key + "=" + str(path)])
        unlink.append('(literal (param "' + key + '"))')
    policy = ('(version 1)\n(allow default)\n(deny file-write*)\n(deny file-link)\n'
              '(allow file-write* (require-all (require-any (subpath (param "STATE")) '
              '(subpath (param "TEMP")) (subpath (param "WINE_SERVER")) (literal "/dev/null")) ' + ' '.join(exclusions) + '))\n'
              '(allow file-write-create (literal (param "WINE_SERVER_PARENT")))\n'
              '(deny file-write-unlink ' + ' '.join(unlink) + ')\n')
    return args + ["-p", policy]


def win(path):
    return "Z:" + str(path).replace("/", "\\")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine", type=Path, default=SN_RUNTIMES / "wine-cx/bin/wine")
    parser.add_argument("--preexisting-hardlink", action="store_true",
                        help="保留已知反例：预先存在的可写硬链接可改写假原件，预期整体失败。")
    args = parser.parse_args()
    runtime, home = SN_RUNTIMES.resolve(strict=True), SN_HOME.resolve()
    if runtime == home or home in runtime.parents:
        parser.error("运行时目录不能位于源码仓库。")
    wine = args.wine.resolve(strict=True)
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not gcc:
        parser.error("缺少 MinGW。")
    base = Path(tempfile.mkdtemp(prefix="sn-wine-writeguard-", dir=runtime))
    os.chmod(base, 0o700)
    print(base, flush=True)
    state = base / "state"
    source = state / "fixtures/source-parent/source"
    saves = state / "fixtures/old-saves"
    source.mkdir(parents=True)
    saves.mkdir(parents=True)
    game_file, save_file = source / "original.dat", saves / "save.dat"
    game_file.write_bytes(b"original-game")
    save_file.write_bytes(b"original-save")
    soft, hard = state / "soft-alias", state / "hard-alias"
    soft.symlink_to(game_file)
    if args.preexisting_hardlink:
        os.link(game_file, hard)
    (base / "probe.c").write_text(CLIENT)
    env = dict(os.environ, WINEPREFIX=str(state / "prefix"), WINEDEBUG="-all", WINEARCH="win64",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d", TMPDIR=str(state / "tmp"))
    (state / "tmp").mkdir()
    Path(env["WINEPREFIX"]).mkdir()
    sandbox = sandbox_command([source, saves], state, tempfile.gettempdir(), env["WINEPREFIX"])
    (base / "sandbox-command.json").write_text(json.dumps(sandbox, indent=2) + "\n")
    (base / "policy.sb").write_text(sandbox[-1])
    report = {"protocol": 1, "ok": False, "scope": "self_owned_wine_writeguard",
              "source_before": abi.sha(game_file), "saves_before": abi.sha(save_file),
              "preexisting_hardlink": args.preexisting_hardlink, "initial_source_nlink": game_file.stat().st_nlink}
    try:
        commands = [
            ("compile", [gcc, "-O2", "-o", str(base / "probe.exe"), str(base / "probe.c")]),
            ("initialize", sandbox + [str(wine), "wineboot", "--init"]),
            ("probe", sandbox + [str(wine), str(base / "probe.exe"), win(state / "written.dat"),
                                    win(game_file), win(save_file), win(soft), win(hard),
                                    win(source.parent), win(source.parent.with_name("renamed-parent")),
                                    "1" if args.preexisting_hardlink else "0"]),
        ]
        for name, command in commands:
            report[name] = abi.bounded(command, env, base, base / (name + ".log"), 90)
            if report[name]["rc"] != 0 or report[name]["timeout"]:
                break
    finally:
        server = wine.parent / "wineserver"
        report["cleanup_kill"] = abi.bounded([str(server), "-k"], env, base, base / "cleanup-kill.log", 5)
        report["cleanup_wait"] = abi.bounded([str(server), "-w"], env, base, base / "cleanup-wait.log", 8)
        report["source_unchanged"] = game_file.exists() and abi.sha(game_file) == report["source_before"]
        report["saves_unchanged"] = save_file.exists() and abi.sha(save_file) == report["saves_before"]
        report["ancestor_unchanged"] = source.exists() and not source.parent.with_name("renamed-parent").exists()
        written = state / "written.dat"
        report["state_written"] = written.exists() and written.read_bytes() == b"state-ok"
        log = (base / "probe.log").read_text(errors="replace") if (base / "probe.log").exists() else ""
        expected_files = range(2, 6) if args.preexisting_hardlink else range(2, 5)
        report["denials"] = (all(f"denied_{i}=5" in log for i in expected_files) and "rename_denied=5" in log and
                             (args.preexisting_hardlink or "link_denied=5" in log))
        report["ok"] = (all(report.get(k) for k in ("source_unchanged", "saves_unchanged", "ancestor_unchanged", "state_written", "denials")) and
                        all(report.get(k, {}).get("rc") == 0 and not report[k]["timeout"]
                            for k in ("compile", "initialize", "probe", "cleanup_kill", "cleanup_wait")))
        (base / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
