# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""核对生产声明；显式 SN_RUN_WINE_ABI=1 时再跑无游戏、无 Steam 的 Win64 调用。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "lib/steambridge"
GCC = shutil.which("x86_64-w64-mingw32-gcc")

# 逐个核对过的上游声明，第三方头文件本身不入仓库：
# https://github.com/rlabrecque/Steamworks.NET/blob/ba71581f1ed7349e8d0f17ddc6f135dd3bc8a6a3/CodeGen/steam/steam_api_flat.h
DECLARATIONS = '''
typedef void *(*with_user)(void *, int, int, const char *);
typedef void *(*without_user)(void *, int, const char *);
#define ABI(name, type) _Static_assert(__builtin_types_compatible_p(__typeof__(&name), type), #name " ABI")
ABI(SteamAPI_ISteamClient_GetISteamUtils, without_user);
ABI(SteamAPI_ISteamClient_GetISteamUser, with_user);
ABI(SteamAPI_ISteamClient_GetISteamUserStats, with_user);
ABI(SteamAPI_ISteamClient_GetISteamFriends, with_user);
ABI(SteamAPI_ISteamClient_GetISteamApps, with_user);
ABI(SteamAPI_ISteamClient_GetISteamRemoteStorage, with_user);
ABI(SteamAPI_ISteamClient_GetISteamGenericInterface, with_user);
typedef void *(*context_init)(void *);
ABI(SteamInternal_ContextInit, context_init);
typedef void (*register_cb)(void *, int);
ABI(SteamAPI_RegisterCallback, register_cb);
typedef int (*next_cb)(int, void *);
ABI(SteamAPI_ManualDispatch_GetNextCallback, next_cb);
'''

# inert_stubs.c 的 typed 主体：宽于 8 字节的返回值走隐藏结果指针（第一参数），float 走 XMM0。
INERT_DECLARATIONS = '''
#define ABI(name, type) _Static_assert(__builtin_types_compatible_p(__typeof__(&name), type), #name " ABI")
typedef void *(*aggregate_two)(void *, void *, uint64_t, uint64_t);
typedef void *(*aggregate_one)(void *, void *, uint64_t);
typedef void *(*aggregate_none)(void *, void *);
typedef float (*float_none)(void *);
typedef float (*float_one)(void *, void *);
typedef void *(*find_or_create)(int, const char *);
ABI(SteamAPI_ISteamInput_GetAnalogActionData, aggregate_two);
ABI(SteamAPI_ISteamInput_GetMotionData, aggregate_one);
ABI(SteamAPI_ISteamController_GetAnalogActionData, aggregate_two);
ABI(SteamAPI_ISteamController_GetMotionData, aggregate_one);
ABI(SteamAPI_ISteamGameServer_GetPublicIP, aggregate_none);
ABI(SteamAPI_ISteamMusic_GetVolume, float_none);
ABI(SteamAPI_ISteamNetworkingUtils_GetLocalPingLocation, float_one);
ABI(SteamInternal_FindOrCreateGameServerInterface, find_or_create);
'''


@unittest.skipUnless(GCC, "需要 MinGW 编译生产声明")
class SteamBridgeABI(unittest.TestCase):
    def test_production_client_interface_signatures(self):
        # 编译生产实现而非匹配源码字符串，错误参数数量或类型会令编译失败。
        with tempfile.TemporaryDirectory(prefix="sn-steambridge-abi-") as directory:
            base = Path(directory)
            source = base / "types.c"
            source.write_text('#include "bridge.c"\n' + DECLARATIONS)
            result = subprocess.run([GCC, "-std=c11", "-Werror", "-I", str(BRIDGE),
                                     "-c", str(source), "-o", str(base / "types.o")],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_inert_typed_bodies_match_win64_conventions(self):
        with tempfile.TemporaryDirectory(prefix="sn-steambridge-inert-abi-") as directory:
            base = Path(directory)
            subprocess.run([sys.executable, "-B", str(BRIDGE / "gen.py"), "--output-dir", str(base / "generated")],
                           check=True, capture_output=True, timeout=15)
            source = base / "types.c"
            source.write_text('#include "inert_stubs.c"\n' + INERT_DECLARATIONS)
            result = subprocess.run([GCC, "-std=c11", "-Werror", "-I", str(BRIDGE), "-I", str(base / "generated"),
                                     "-c", str(source), "-o", str(base / "types.o")],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.environ.get("SN_RUN_WINE_ABI") == "1", "Wine 机制探针需显式启用")
    def test_actual_win64_three_argument_call(self):
        sys.path.insert(0, str(ROOT / "lib"))
        from paths import SN_RUNTIMES
        wine = SN_RUNTIMES / "wine-cx/bin/wine"
        self.assertTrue(wine.is_file(), "需要现有 wine-cx；测试不会安装或更换运行时")
        with tempfile.TemporaryDirectory(prefix="sn-steambridge-win64-", dir=SN_RUNTIMES) as directory:
            base = Path(directory)
            generated = base / "generated"
            subprocess.run([sys.executable, "-B", str(BRIDGE / "gen.py"), "--output-dir", str(generated)],
                           check=True, capture_output=True, timeout=15)
            dll = base / "steam_api64.dll"
            subprocess.run([GCC, "-shared", "-O2", "-I", str(BRIDGE), "-I", str(generated), "-o", str(dll),
                            str(BRIDGE / "bridge.c"), str(BRIDGE / "inert_stubs.c"), str(BRIDGE / "vtables.c"), str(generated / "stubs_gen.c"),
                            str(generated / "inert_gen.c"), str(generated / "vtables_gen.c"), str(generated / "steam_api64.def"), "-lws2_32", "-static-libgcc"],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([sys.executable, "-B", str(BRIDGE / "mark_builtin.py"), str(dll)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe-wine-steamutils.py"),
                                     "--dll", str(dll), "--wine", str(wine)],
                                    env=dict(os.environ, SN_RUNTIMES=str(base)),
                                    capture_output=True, text=True, timeout=240)
            evidence = list(base.glob("sn-wine-steamutils-*/evidence.json"))
            self.assertEqual(len(evidence), 1, result.stdout + result.stderr)
            report = json.loads(evidence[0].read_text())
            self.assertEqual(result.returncode, 0, json.dumps(report) + result.stderr)
            self.assertTrue(report["ok"])
            self.assertEqual(report["calls_completed"], 5)
            self.assertFalse(report["steam_contacted"])

    @unittest.skipUnless(os.environ.get("SN_RUN_WINE_ABI") == "1", "Wine 机制探针需显式启用")
    def test_actual_win64_inert_handles_never_reach_helper(self):
        sys.path.insert(0, str(ROOT / "lib"))
        from paths import SN_RUNTIMES
        wine = SN_RUNTIMES / "wine-cx/bin/wine"
        self.assertTrue(wine.is_file(), "需要现有 wine-cx；测试不会安装或更换运行时")
        with tempfile.TemporaryDirectory(prefix="sn-steambridge-inert-", dir=SN_RUNTIMES) as directory:
            base = Path(directory)
            generated = base / "generated"
            subprocess.run([sys.executable, "-B", str(BRIDGE / "gen.py"), "--output-dir", str(generated)],
                           check=True, capture_output=True, timeout=15)
            dll = base / "steam_api64.dll"
            subprocess.run([GCC, "-shared", "-O2", "-I", str(BRIDGE), "-I", str(generated), "-o", str(dll),
                            str(BRIDGE / "bridge.c"), str(BRIDGE / "inert_stubs.c"), str(BRIDGE / "vtables.c"), str(generated / "stubs_gen.c"),
                            str(generated / "inert_gen.c"), str(generated / "vtables_gen.c"), str(generated / "steam_api64.def"), "-lws2_32", "-static-libgcc"],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([sys.executable, "-B", str(BRIDGE / "mark_builtin.py"), str(dll)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe-wine-inert.py"),
                                     "--dll", str(dll), "--wine", str(wine)],
                                    env=dict(os.environ, SN_RUNTIMES=str(base)), capture_output=True, text=True, timeout=240)
            evidence = list(base.glob("sn-wine-inert-*/evidence.json"))
            self.assertEqual(len(evidence), 1, result.stdout + result.stderr)
            report = json.loads(evidence[0].read_text())
            self.assertEqual(result.returncode, 0, json.dumps(report) + result.stderr)
            self.assertTrue(report["ok"])
            self.assertEqual(report["checks_failed"], 0)
            self.assertEqual(report["helper_requests_during_inert"], 0)
            self.assertFalse(report["steam_contacted"])


if __name__ == "__main__":
    unittest.main()
