# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""gen.py 的四类导出与构建门：永不转发的名字进不了 IMPLEMENTED，惰性句柄只能给取句柄函数，四类不重叠且加起来正好是导出表。"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("steambridge_gen", ROOT / "lib/steambridge/gen.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class PolicyTests(unittest.TestCase):
    def errors(self, implemented=None, getters=None, handwritten=None):
        return gen.policy_errors(gen.EXPORTS, implemented if implemented is not None else gen.IMPLEMENTED,
                                 getters if getters is not None else gen.INERT_GETTERS,
                                 handwritten if handwritten is not None else gen.INERT_HANDWRITTEN)

    def test_production_lists_pass(self):
        self.assertEqual(self.errors(), [])

    def test_ticket_and_matchmaking_forwarding_is_refused(self):
        for name in ("SteamAPI_ISteamUser_GetAuthSessionTicket", "SteamAPI_ISteamMatchmaking_CreateLobby",
                     "SteamAPI_SteamMatchmaking_v009", "SteamAPI_SteamGameServer_v014", "SteamInternal_FindOrCreateGameServerInterface"):
            errs = self.errors(implemented=gen.IMPLEMENTED + [name])
            self.assertTrue(any("never-implement" in e and name in e for e in errs), (name, errs))

    def test_inert_getters_must_be_getters_and_not_served(self):
        errs = self.errors(getters=gen.INERT_GETTERS + ["SteamAPI_ISteamMatchmaking_CreateLobby"])
        self.assertTrue(any("only hold interface getters" in e for e in errs), errs)
        errs = self.errors(getters=gen.INERT_GETTERS + ["SteamAPI_SteamUser_v021"])
        self.assertTrue(any("served families" in e for e in errs), errs)

    def test_kinds_do_not_overlap(self):
        errs = self.errors(implemented=gen.IMPLEMENTED + [gen.INERT_HANDWRITTEN[0]])
        self.assertTrue(any("overlaps IMPLEMENTED" in e for e in errs), errs)
        errs = self.errors(getters=gen.INERT_GETTERS + ["SteamAPI_ISteamMusic_GetVolume"])
        self.assertTrue(any("only hold interface getters" in e or "overlaps INERT_HANDWRITTEN" in e for e in errs), errs)

    def test_served_families_match_helper_table(self):
        helper = (ROOT / "lib/steambridge/helper.c").read_text(encoding="utf8")
        stubs = (ROOT / "lib/steambridge/inert_stubs.c").read_text(encoding="utf8")
        for family in gen.SERVED_FAMILIES:
            self.assertIn(f'"{family}"', helper)
            self.assertIn(f'"{family}"', stubs)

    def test_union_export_table_carries_the_new_init_entry_points(self):
        self.assertGreaterEqual(len(gen.EXPORTS), 1140)
        for name in ("SteamInternal_SteamAPI_Init", "SteamAPI_InitFlat", "SteamAPI_SteamUser_v023", "SteamAPI_SteamUtils_v011"):
            self.assertIn(name, gen.EXPORTS); self.assertIn(name, gen.IMPLEMENTED)
        for name in ("SteamAPI_SteamInput_v007", "SteamAPI_SteamUGC_v021", "SteamAPI_SteamTimeline_v004"):
            self.assertIn(name, gen.INERT_GETTERS)
        self.assertIn("SteamAPI_ISteamAppList_GetAppName", gen.EXPORTS)   # 1.61 删掉的旧名字保留，老游戏还导入

    def test_generated_sources_partition_the_export_table(self):
        with tempfile.TemporaryDirectory(prefix="sn-steambridge-gen-") as directory:
            out = Path(directory)
            result = subprocess.run([sys.executable, "-B", str(ROOT / "lib/steambridge/gen.py"), "--output-dir", str(out)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            stubs = out.joinpath("stubs_gen.c").read_text()
            inert = out.joinpath("inert_gen.c").read_text()
            defined_stubs = {line.split("(")[1].split(",")[0] for line in stubs.splitlines() if line.startswith("STUB(")}
            defined_inert = {line.split()[2].lstrip("*").split("(")[0] for line in inert.splitlines() if line.startswith("API void *")}
            self.assertEqual(defined_inert, set(gen.INERT_GETTERS))
            self.assertFalse(defined_stubs & set(gen.INERT_GETTERS))
            self.assertFalse(defined_stubs & set(gen.INERT_HANDWRITTEN))
            self.assertFalse(defined_stubs & set(gen.IMPLEMENTED))
            self.assertEqual(len(defined_stubs) + len(set(gen.IMPLEMENTED) & set(gen.EXPORTS))
                             + len(gen.INERT_GETTERS) + len(gen.INERT_HANDWRITTEN), len(gen.EXPORTS))
            self.assertIn(f"#define SB_INERT_SLOTS {gen.INERT_SLOTS}", out.joinpath("inert_gen.h").read_text())
            self.assertEqual(inert.count("pxor %xmm0, %xmm0"), gen.INERT_SLOTS)
            for name in ("SteamAPI_ISteamClient_GetISteamMatchmaking", "SteamAPI_SteamNetworkingUtils_SteamAPI_v004",
                         "SteamAPI_SteamGameServer_v014", "SteamAPI_SteamInput_v006"):
                self.assertIn(name, gen.INERT_GETTERS)
            self.assertNotIn("SteamAPI_ISteamClient_GetISteamGenericInterface", gen.INERT_GETTERS)


class ThirtyTwoBitRenderTests(unittest.TestCase):
    """32 位桥只有扁平 API：惰性槽是 thiscall 的 C 桩（无 x64 汇编），不带 C++ 表，def 文件叫 steam_api。"""

    def test_i386_inert_slots_are_thiscall_c_and_no_asm(self):
        header, body = gen.render_inert(gen.INERT_GETTERS, arch="i386")
        self.assertIn("__attribute__((thiscall))", body)
        self.assertNotIn("__asm__", body)
        self.assertEqual(body.count("sb_inert_hit("), gen.INERT_SLOTS)
        self.assertIn(f"#define SB_INERT_SLOTS {gen.INERT_SLOTS}", header)
        self.assertIn("void *const sb_inert_vtable[SB_INERT_SLOTS]", body)

    def test_i386_vtables_are_thiscall_with_stack_sizes_from_argsizes(self):
        tables = gen.load_vtables()
        sizes, exact = gen.load_argsizes()
        self.assertGreater(len(sizes), 800); self.assertIn("SteamUser021", exact)
        header, body, known, unknown = gen.render_vtables_i386(tables, gen.IMPLEMENTED, gen.INERT_GETTERS, sizes, exact)
        self.assertIn(f"#define SB_VTABLE_COUNT {len(tables)}", header)
        # 转发槽：复制 N 字节参数、this 压栈、调 cdecl 扁平函数、清 N+4、ret N（SetAchievement 一个指针参数 → 4）
        self.assertIn("_sb_i386_fwd_SteamAPI_ISteamUserStats_SetAchievement:", body)
        fwd = body[body.index("_sb_i386_fwd_SteamAPI_ISteamUserStats_SetAchievement:"):]
        self.assertIn("pushl 8(%ebp)", fwd[:400]); self.assertIn("call _SteamAPI_ISteamUserStats_SetAchievement", fwd[:400])
        self.assertIn("addl $8, %esp", fwd[:400]); self.assertIn("ret $4", fwd[:400])
        # 隐藏结果指针：u64 写进调用方给的地址并把地址放回 eax，ret $4
        hid = body[body.index("_sb_i386_hid_SteamAPI_ISteamUser_GetSteamID:"):]
        self.assertIn("movl %edx, 4(%ecx)", hid[:400]); self.assertIn("ret $4", hid[:400])
        # 有 N 的桩 ret N；没有 N 的桩（表后垫的槽）走 unknown 记录且只 ret
        self.assertIn('call _sb_vt_stub_hit\\n"', body); self.assertIn("call _sb_vt_stub_hit_unknown", body)   # asm 行是 JSON 转义过的字符串
        self.assertGreater(known, 500); self.assertGreater(unknown, 0)
        self.assertIn("SteamUser021: stack sizes exact", body)
        # 覆盖表：SteamUser023 的 GetAuthSessionTicket 多一个参数
        user023 = [t for t in tables if t["version"] == "SteamUser023"]
        slot = next((r["slot"] for t in user023 for r in t["slots"] if r["method"] == "GetAuthSessionTicket"), None)
        if slot is not None:
            stub = body[body.index(f"_sb_i386_stub_{tables.index(user023[0])}_{slot}:"):]
            self.assertIn("ret $16", stub[:300])
        else:
            self.assertIn(("SteamUser023", "GetAuthSessionTicket"), gen.ARGSIZE_OVERRIDES)   # 表里没这个名字时覆盖表仍在，等新表补上
        self.assertEqual(gen.render_vtables(tables, gen.IMPLEMENTED, gen.INERT_GETTERS, arch="i386")[0], header)
        # 老版本串借新表：只放纯追加的版本，别名目标必须有表，两种位数都发出同一张别名表
        for arch in ("x86_64", "i386"):
            h, b = gen.render_vtables(tables, gen.IMPLEMENTED, gen.INERT_GETTERS, arch=arch)[:2]
            self.assertIn(f"#define SB_VTABLE_ALIAS_COUNT {len(gen.VTABLE_ALIASES)}", h)
            self.assertIn('{ "STEAMREMOTESTORAGE_INTERFACE_VERSION013", "STEAMREMOTESTORAGE_INTERFACE_VERSION014" }', b)
        self.assertTrue({"SteamUser018", "SteamUtils007", "STEAMAPPS_INTERFACE_VERSION007"} <= {t["version"] for t in tables}, "entry-07 要的三张老表要是精确表")
        # 惰性家族也有按版本的表：全是桩（不转发），32 位上 ret N；getter 与带版本号的访问器把版本串传给 sb_inert_versioned
        inert = [t for t in tables if t["family"] not in gen.FLAT_PREFIX]
        self.assertGreater(len(inert), 20); self.assertIn("screenshots", {t["family"] for t in inert})
        for t in inert:
            for r in t["slots"]:
                self.assertEqual(gen.vtable_kind(t["family"], r, gen.IMPLEMENTED, gen.INERT_GETTERS)[0], "stub", f"{t['version']} {r['method']}")
        i = tables.index(next(t for t in inert if t["version"] == "STEAMSCREENSHOTS_INTERFACE_VERSION002"))
        slot3 = body[body.index(f"_sb_i386_stub_{i}_3:"):]
        self.assertIn("ret $4", slot3[:300], "HookScreenshots(bool) 清 4 字节（entry-07 打到的惰性槽）")
        ih, ib = gen.render_inert(gen.INERT_GETTERS, arch="i386")
        self.assertIn('API void *SteamAPI_ISteamClient_GetISteamScreenshots(void *c, int u, int p, const char *v) { (void)c; (void)u; (void)p; return sb_inert_versioned(v, "screenshots"); }', ib)
        self.assertIn('API void *SteamAPI_SteamScreenshots_v003(void) { return sb_inert_versioned("STEAMSCREENSHOTS_INTERFACE_VERSION003", "screenshots"); }', ib)
        self.assertEqual(gen.version_templates()["screenshots"], "STEAMSCREENSHOTS_INTERFACE_VERSION{:03d}")
        # SDK <= 1.3x 裸访问器：惰性家族发该家族最老的按版本表；served 家族的在 bridge.c 手写（IMPLEMENTED）
        self.assertIn('API void *SteamScreenshots(void) { return sb_inert_versioned("STEAMSCREENSHOTS_INTERFACE_VERSION002", "screenshots"); }', ib)
        self.assertIn('API void *SteamUGC(void) { return sb_inert_versioned("STEAMUGC_INTERFACE_VERSION003", "ugc"); }', ib)
        for n in ("SteamUser", "SteamUserStats", "SteamApps", "SteamFriends", "SteamUtils", "SteamRemoteStorage", "Steam_RunCallbacks"):
            self.assertIn(n, gen.EXPORTS); self.assertIn(n, gen.IMPLEMENTED); self.assertNotIn(n, gen.INERT_GETTERS)
        self.assertEqual(gen.oldest_versions()["user"], "SteamUser018")
        self.assertTrue(set(gen.VTABLE_ALIASES.values()) <= {t["version"] for t in tables})

    def test_x86_64_rendering_is_unchanged_by_the_arch_parameter(self):
        self.assertEqual(gen.render_inert(gen.INERT_GETTERS), gen.render_inert(gen.INERT_GETTERS, arch="x86_64"))
        self.assertIn("__asm__", gen.render_inert(gen.INERT_GETTERS)[1])


if __name__ == "__main__":
    unittest.main()


class BuiltinMarkTests(unittest.TestCase):
    """Wine 只把 DOS 头后带 32 字节签名的 PE 当 builtin；2026-09-13 真机发现重建的桥掉了这个标记。"""
    def test_mark_and_check_on_a_synthetic_pe(self):
        spec = importlib.util.spec_from_file_location("mark_builtin", ROOT / "lib/steambridge/mark_builtin.py")
        mb = importlib.util.module_from_spec(spec); spec.loader.exec_module(mb)
        with tempfile.TemporaryDirectory(prefix="sn-mark-") as directory:
            fake = Path(directory) / "x.dll"
            header = bytearray(b"MZ" + b"\0" * 0x3A) + (0x80).to_bytes(4, "little") + b"This program cannot be run in DOS mode.\r\r\n$".ljust(0x40, b"\0") + b"PE\0\0" + b"\0" * 64
            fake.write_bytes(bytes(header))
            self.assertFalse(mb.check(fake))
            mb.mark(fake)                       # 没有 winebuild 时自己写签名
            self.assertTrue(mb.check(fake))
            data = fake.read_bytes()
            self.assertEqual(data[0x40:0x50], b"Wine builtin DLL")
            self.assertEqual(data[0x80:0x84], b"PE\0\0")   # PE 头没被碰
            small = Path(directory) / "small.dll"
            small.write_bytes(b"MZ" + b"\0" * 0x3A + (0x50).to_bytes(4, "little") + b"\0" * 0x50)
            with self.assertRaises(SystemExit):
                mb.mark(small)                  # DOS 头与 PE 头之间放不下签名就拒绝


class VtableTests(unittest.TestCase):
    """lib/steambridge/vtables/*.json 是从用户自己的 DLL 读出的 MSVC 槽位表；生成的虚表只能把槽指向已实现的扁平函数或惰性 getter。"""
    def test_tables_load_and_pass_policy(self):
        tables = gen.load_vtables()
        self.assertGreaterEqual(len(tables), 11)
        self.assertEqual(gen.vtable_policy_errors(tables, gen.IMPLEMENTED, gen.INERT_GETTERS), [])
        versions = {t["version"] for t in tables}
        for v in ("SteamUser021", "STEAMUSERSTATS_INTERFACE_VERSION012", "STEAMAPPS_INTERFACE_VERSION008", "SteamFriends017",
                  "SteamUtils010", "STEAMREMOTESTORAGE_INTERFACE_VERSION016", "SteamClient020", "SteamUser019"):
            self.assertIn(v, versions)

    def test_msvc_reversed_overloads_are_recorded_as_read_from_the_dll(self):
        t = {x["version"]: x for x in gen.load_vtables()}["STEAMUSERSTATS_INTERFACE_VERSION012"]
        by = {r["slot"]: r["method"] for r in t["slots"]}
        self.assertEqual((by[1], by[2]), ("GetStatFloat", "GetStatInt32"))   # 头文件里 int32 版先声明，MSVC 虚表却是 float 版在前
        self.assertEqual(by[2 - 2], "RequestCurrentStats")

    def test_hidden_result_forward_without_thunk_is_refused(self):
        fake = [{"_file": "x.json", "interface": "ISteamUser", "version": "SteamUser999", "family": "user", "slot_count": 1,
                 "slots": [{"slot": 0, "method": "BLoggedOn", "hidden_result_pointer": True}]}]
        errs = gen.vtable_policy_errors(fake, gen.IMPLEMENTED, gen.INERT_GETTERS)
        self.assertTrue(any("hidden result pointer" in e for e in errs), errs)
        fake[0]["slots"][0]["method"] = "GetSteamID"
        self.assertEqual(gen.vtable_policy_errors(fake, gen.IMPLEMENTED, gen.INERT_GETTERS), [])

    def test_generated_vtables_cover_every_slot(self):
        tables = gen.load_vtables()
        header, body = gen.render_vtables(tables, gen.IMPLEMENTED, gen.INERT_GETTERS)
        self.assertIn(f"#define SB_VTABLE_COUNT {len(tables)}", header)
        total = sum(t["slot_count"] for t in tables)
        forwarded = sum(1 for t in tables for r in t["slots"] if gen.vtable_kind(t["family"], r, set(gen.IMPLEMENTED), set(gen.INERT_GETTERS))[0] != "stub")
        self.assertEqual(body.count("pxor %xmm0, %xmm0"), total + len(tables) * gen.VT_PAD - forwarded)   # 每表多垫 VT_PAD 个桩
        self.assertIn("(void *)sb_thunk_ISteamUser_GetSteamID", body)
        self.assertIn("(void *)SteamAPI_ISteamUserStats_GetNumAchievements", body)
        self.assertIn("(void *)SteamAPI_ISteamClient_GetISteamMatchmaking", body)   # ISteamClient 槽指向惰性 getter

