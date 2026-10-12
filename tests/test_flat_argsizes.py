# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""steam-flat-argsizes 的栈字节推演只认 MSVC 扁平包装的几种写法；写错 N 会让 32 位 thiscall 桩把游戏栈弄坏，所以按真机看到的样子逐条钉住。"""
import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flat_argsizes", ROOT / "tools/steam-flat-argsizes.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)


class EmulateTests(unittest.TestCase):
    def test_ebp_frame_wrapper_one_pointer_argument(self):
        # SteamAPI_ISteamUserStats_SetAchievement（entry-07 的 DLL，2026-09-13 反汇编原样）
        ins = ["push   ebp", "mov    ebp,esp", "mov    ecx,DWORD PTR [ebp+0x8]", "push   DWORD PTR [ebp+0xc]",
               "mov    eax,DWORD PTR [ecx]", "mov    eax,DWORD PTR [eax+0x1c]", "call   eax", "pop    ebp", "ret", "int3"]
        self.assertEqual(fa.emulate(ins), {"stack_bytes": 4, "slot": 7, "hidden_ret": False, "status": "ok"})

    def test_ebp_frame_wrapper_uint64_argument_is_two_pushes(self):
        ins = ["push   ebp", "mov    ebp,esp", "mov    ecx,DWORD PTR [ebp+0x8]", "push   DWORD PTR [ebp+0x10]", "push   DWORD PTR [ebp+0xc]",
               "mov    eax,DWORD PTR [ecx]", "mov    eax,DWORD PTR [eax+0x40]", "call   eax", "pop    ebp", "ret"]
        self.assertEqual(fa.emulate(ins), {"stack_bytes": 8, "slot": 16, "hidden_ret": False, "status": "ok"})

    def test_hidden_result_pointer_is_flagged_and_not_counted_as_an_argument(self):
        # SteamAPI_ISteamUser_GetSteamID：C++ 侧返回 CSteamID 经隐藏指针，扁平侧没有参数
        ins = ["push   ebp", "mov    ebp,esp", "mov    ecx,DWORD PTR [ebp+0x8]", "lea    edx,[ebp-0x8]", "sub    esp,0x8",
               "mov    eax,DWORD PTR [ecx]", "push   edx", "call   DWORD PTR [eax+0x8]", "mov    edx,eax", "mov    eax,DWORD PTR [edx]",
               "mov    edx,DWORD PTR [edx+0x4]", "mov    esp,ebp", "pop    ebp", "ret"]
        self.assertEqual(fa.emulate(ins), {"stack_bytes": 0, "slot": 2, "hidden_ret": True, "status": "ok"})

    def test_three_arguments_with_direct_indirect_call(self):
        # SteamAPI_ISteamClient_GetISteamUserStats(client, user, pipe, version)
        ins = ["push   ebp", "mov    ebp,esp", "mov    ecx,DWORD PTR [ebp+0x8]", "push   DWORD PTR [ebp+0x14]", "push   DWORD PTR [ebp+0x10]",
               "mov    eax,DWORD PTR [ecx]", "push   DWORD PTR [ebp+0xc]", "call   DWORD PTR [eax+0x34]", "pop    ebp", "ret"]
        self.assertEqual(fa.emulate(ins), {"stack_bytes": 12, "slot": 13, "hidden_ret": False, "status": "ok"})

    def test_esp_relative_wrapper_and_tail_jump(self):
        # 没有 ebp 帧的写法：push 之后 esp 偏移要跟着挪
        ins = ["mov    ecx,DWORD PTR [esp+0x4]", "push   DWORD PTR [esp+0xc]", "push   DWORD PTR [esp+0xc]",
               "mov    eax,DWORD PTR [ecx]", "jmp    DWORD PTR [eax+0x2c]"]
        self.assertEqual(fa.emulate(ins), {"stack_bytes": 8, "slot": 11, "hidden_ret": False, "status": "ok"})

    def test_no_call_and_complex_bodies_are_not_guessed(self):
        self.assertEqual(fa.emulate(["push   ebp", "mov    ebp,esp", "pop    ebp", "ret"])["status"], "no-call")
        self.assertEqual(fa.emulate(["push   ebp", "mov    ebp,esp", "rep movs DWORD PTR es:[edi],DWORD PTR ds:[esi]", "call   eax"])["status"], "complex")

    def test_prototype_sizes_use_i386_widths(self):
        src = ("API int SteamAPI_ISteamUtils_IsAPICallCompleted(void *self, unsigned long long h, unsigned char *failed)\n"
               "API void *SteamAPI_ISteamClient_GetISteamUtils(void *c, int p, const char *v)\n"
               "API int SteamAPI_ISteamUserStats_RequestCurrentStats(void *self)\n"
               "API int SteamAPI_ISteamUserStats_SetStatFloat(void *self, const char *name, float value)\n")
        self.assertEqual(fa.prototype_sizes(src), {"SteamAPI_ISteamUtils_IsAPICallCompleted": 12, "SteamAPI_ISteamClient_GetISteamUtils": 8,
                                                   "SteamAPI_ISteamUserStats_RequestCurrentStats": 0, "SteamAPI_ISteamUserStats_SetStatFloat": 8})


class MergeAndTableTests(unittest.TestCase):
    def test_merge_keeps_each_source_and_defaults_to_more_exports(self):
        method = "SteamAPI_ISteamUser_GetSteamID"
        with tempfile.TemporaryDirectory(prefix="sn-flat-merge-") as tmp:
            base = Path(tmp)
            reports = {
                "n4.json": {"flat_exports": 4, "interface_versions": ["SteamUser020"],
                            "methods": {method: {"status": "ok", "stack_bytes": 4, "hidden_ret": False}}},
                "n8.json": {"flat_exports": 8, "interface_versions": ["SteamUser021"],
                            "methods": {method: {"status": "ok", "stack_bytes": 8, "hidden_ret": False}}},
            }
            for name, report in reports.items():
                (base / name).write_text(json.dumps(report), encoding="utf8")
            out = base / "merged.json"

            fa.merge([base / "n4.json", base / "n8.json"], out)

            merged = json.loads(out.read_text(encoding="utf8"))
            entry = merged["methods"][method]
            self.assertEqual(entry["stack_bytes"], 8)
            self.assertEqual(entry["by_source"], {"n4": [4, False], "n8": [8, False]})
            self.assertEqual(merged["conflicts"][method], {"n4": [4, False], "default": [8, False]})
            self.assertEqual(entry["sources"], ["n8", "n4"])

    def test_write_tables_writes_hidden_pointer_and_does_not_overwrite_existing_table(self):
        result = {
            "SteamAPI_ISteamUser_BLoggedOn": {
                "interface": "ISteamUser", "method": "BLoggedOn", "slot": 0,
                "hidden_ret": False, "status": "ok",
            },
            "SteamAPI_ISteamUser_GetSteamID": {
                "interface": "ISteamUser", "method": "GetSteamID", "slot": 2,
                "hidden_ret": True, "status": "ok",
            },
        }
        with tempfile.TemporaryDirectory(prefix="sn-flat-tables-") as tmp:
            out_dir = Path(tmp) / "vtables"
            dll = Path(tmp) / "owned-steam_api.dll"
            data = b"SteamUser021\0"

            fa.write_tables(result, data, dll, out_dir)
            target = out_dir / "SteamUser021.json"
            table = json.loads(target.read_text(encoding="utf8"))
            self.assertEqual(table["interface"], "ISteamUser")
            self.assertEqual(table["version"], "SteamUser021")
            self.assertEqual(table["family"], "user")
            self.assertEqual(table["slot_count"], 3)
            self.assertEqual(table["unresolved_slots"], [1])
            self.assertEqual(table["slots"], [
                {"slot": 0, "method": "BLoggedOn"},
                {"slot": 2, "method": "GetSteamID", "hidden_result_pointer": True},
            ])

            before = target.read_text(encoding="utf8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                fa.write_tables(result, data, dll, out_dir)
            self.assertEqual(target.read_text(encoding="utf8"), before)
            self.assertIn("仓库已有表", output.getvalue())
            self.assertIn("一致", output.getvalue())


if __name__ == "__main__":
    unittest.main()
