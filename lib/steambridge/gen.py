#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Generate the export list, stub bodies and inert-handle getters for the Wine-side steam_api64.dll.

The bridge exports the union of two real export tables (exports.json, see exports-sources.md: a SDK 1.57
Windows steam_api64.dll plus the names Steam's own SDK 1.61 library added), so that any game's import table resolves. Each export has one of four kinds of body, and the kind is a code fact
this script checks at build time:

  IMPLEMENTED        hand-written in bridge.c; forwards over a socket to the native macOS helper, so the
                     answer is always the user's own running Steam client.
  INERT_GETTERS      generated into inert_gen.c; return a non-null inert handle for an interface family the
                     bridge does not serve, without ever talking to the helper.
  INERT_HANDWRITTEN  hand-written in inert_stubs.c; typed no-op bodies for exports whose Win64 return
                     convention a bare `return 0` cannot satisfy (hidden result pointers, float returns,
                     caller-provided string buffers). That file contains no socket code at all.
  stubs              everything else: return 0 and record that they were called, so the implemented set can
                     grow from what real games actually use.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import SN_RUNTIMES

EXPORTS = json.loads((HERE / "exports.json").read_text())

# Families the helper forwards; must match the served[] table in helper.c and sb_family_token() in inert_stubs.c.
SERVED_FAMILIES = ("client", "userstats", "user", "apps", "friends", "utils", "remotestorage")

# Implemented by hand in bridge.c; must not get a generated stub.
IMPLEMENTED = [
    "SteamAPI_Init", "SteamAPI_InitSafe", "SteamAPI_InitFlat", "SteamInternal_SteamAPI_Init",
    "SteamAPI_Shutdown", "SteamAPI_RunCallbacks",
    "SteamAPI_RestartAppIfNecessary", "SteamAPI_IsSteamRunning",
    "SteamAPI_SteamUserStats_v012", "SteamAPI_SteamUser_v021", "SteamAPI_SteamApps_v008",
    "SteamAPI_SteamFriends_v017", "SteamAPI_SteamUtils_v010", "SteamAPI_SteamRemoteStorage_v016",
    "SteamAPI_SteamUserStats_v013", "SteamAPI_SteamUser_v023", "SteamAPI_SteamApps_v009",
    "SteamAPI_SteamFriends_v018", "SteamAPI_SteamUtils_v011",
    "SteamAPI_ISteamUserStats_RequestCurrentStats", "SteamAPI_ISteamUserStats_RequestUserStats", "SteamAPI_ISteamUserStats_StoreStats",
    "SteamAPI_ISteamUserStats_GetAchievement", "SteamAPI_ISteamUserStats_SetAchievement",
    "SteamAPI_ISteamUserStats_ClearAchievement", "SteamAPI_ISteamUserStats_GetNumAchievements",
    "SteamAPI_ISteamUserStats_GetAchievementName", "SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute",
    "SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime", "SteamAPI_ISteamUserStats_IndicateAchievementProgress",
    "SteamAPI_ISteamUserStats_GetStatInt32", "SteamAPI_ISteamUserStats_GetStatFloat",
    "SteamAPI_ISteamUserStats_SetStatInt32", "SteamAPI_ISteamUserStats_SetStatFloat",
    "SteamAPI_ISteamUser_GetSteamID", "SteamAPI_ISteamUser_BLoggedOn",
    "SteamAPI_ISteamApps_BIsSubscribed", "SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing",
    "SteamAPI_ISteamApps_GetAppOwner", "SteamAPI_ISteamApps_GetCurrentGameLanguage",
    "SteamAPI_ISteamFriends_GetPersonaName", "SteamAPI_ISteamFriends_SetRichPresence",
    "SteamAPI_ISteamUtils_GetAppID", "SteamAPI_ISteamUtils_IsOverlayEnabled",
    "SteamAPI_ISteamUtils_InitFilterText", "SteamAPI_ISteamUtils_FilterText", "SteamAPI_ISteamUtils_SetWarningMessageHook",
    "SteamAPI_RegisterCallback", "SteamAPI_UnregisterCallback",
    "SteamAPI_RegisterCallResult", "SteamAPI_UnregisterCallResult",
    "SteamAPI_WriteMiniDump", "SteamAPI_SetMiniDumpComment",
    "SteamAPI_GetHSteamUser", "SteamAPI_GetHSteamPipe", "GetHSteamUser", "GetHSteamPipe",
    "SteamClient", "SteamUser", "SteamUserStats", "SteamApps", "SteamFriends", "SteamUtils", "SteamRemoteStorage",
    "Steam_RunCallbacks", "Steam_GetHSteamUserCurrent",
    "SteamAPI_ISteamUserStats_GetStat0", "SteamAPI_ISteamUserStats_GetStat", "SteamAPI_ISteamUserStats_SetStat0", "SteamAPI_ISteamUserStats_SetStat",
    "SteamInternal_CreateInterface", "SteamInternal_ContextInit",
    "SteamInternal_FindOrCreateUserInterface",
    "SteamAPI_ISteamClient_GetISteamUserStats", "SteamAPI_ISteamClient_GetISteamUser",
    "SteamAPI_ISteamClient_GetISteamApps", "SteamAPI_ISteamClient_GetISteamFriends",
    "SteamAPI_ISteamClient_GetISteamUtils", "SteamAPI_ISteamClient_GetISteamRemoteStorage",
    "SteamAPI_ISteamClient_GetISteamGenericInterface",
    "SteamAPI_ManualDispatch_Init", "SteamAPI_ManualDispatch_RunFrame",
    "SteamAPI_ManualDispatch_GetNextCallback", "SteamAPI_ManualDispatch_FreeLastCallback",
    "SteamAPI_ManualDispatch_GetAPICallResult",
    "SteamAPI_ISteamUtils_IsAPICallCompleted", "SteamAPI_ISteamUtils_GetAPICallFailureReason", "SteamAPI_ISteamUtils_GetAPICallResult",
]

# Never forwarded, on purpose. Everything the bridge serves is a straight forward to the user's own
# running Steam client, so identity and ownership are always Steam's answer. These surfaces are where a
# stand-in steam_api64.dll turns into a multiplayer-cheating or piracy tool: auth tickets and sessions,
# matchmaking, networking, game servers, the item economy. The bridge exists for single-player games.
# Putting one of these in IMPLEMENTED fails the build. Their getters hand out inert handles instead of
# NULL : the game sees an object whose every method fails, and the
# helper never hears about it.
NEVER_PREFIXES = (
    "SteamAPI_ISteamMatchmaking", "SteamAPI_ISteamNetworking", "SteamAPI_ISteamGameServer",
    "SteamAPI_ISteamInventory", "SteamAPI_ISteamParties", "SteamAPI_ISteamRemotePlay",
    "SteamAPI_ISteamClient_GetISteamMatchmaking", "SteamAPI_ISteamClient_GetISteamNetworking",
    "SteamAPI_ISteamClient_GetISteamGameServer", "SteamAPI_ISteamClient_GetISteamInventory",
    "SteamAPI_ISteamClient_GetISteamParties", "SteamAPI_ISteamClient_GetISteamRemotePlay",
    "SteamAPI_SteamMatchmaking", "SteamAPI_SteamNetworking", "SteamAPI_SteamGameServer",
    "SteamAPI_SteamInventory", "SteamAPI_SteamParties", "SteamAPI_SteamRemotePlay",
    "SteamGameServer", "SteamInternal_GameServer", "SteamInternal_FindOrCreateGameServerInterface",
    "SteamAPI_ISteamUser_GetAuthSessionTicket", "SteamAPI_ISteamUser_GetAuthTicketForWebApi",
    "SteamAPI_ISteamUser_BeginAuthSession", "SteamAPI_ISteamUser_EndAuthSession",
    "SteamAPI_ISteamUser_CancelAuthTicket", "SteamAPI_ISteamUser_RequestEncryptedAppTicket",
    "SteamAPI_ISteamUser_GetEncryptedAppTicket", "SteamAPI_ISteamUser_InitiateGameConnection",
    "SteamAPI_ISteamUser_TerminateGameConnection", "SteamAPI_ISteamUser_AdvertiseGame",
    "SteamAPI_ISteamUser_UserHasLicenseForApp", "SteamEncryptedAppTicket",
)

# Typed no-op bodies in inert_stubs.c. Reviewed one by one against the Win64 convention the caller expects;
# a bare `return 0` would leave a hidden result buffer, XMM0 or a string buffer untouched.
INERT_HANDWRITTEN = [
    "SteamInternal_FindOrCreateGameServerInterface",
    "SteamAPI_ISteamInput_GetAnalogActionData", "SteamAPI_ISteamInput_GetMotionData",
    "SteamAPI_ISteamController_GetAnalogActionData", "SteamAPI_ISteamController_GetMotionData",
    "SteamAPI_ISteamGameServer_GetPublicIP",
    "SteamAPI_ISteamMusic_GetVolume", "SteamAPI_ISteamNetworkingUtils_GetLocalPingLocation",
    "SteamAPI_ISteamNetworkingUtils_ConvertPingLocationToString",
    "SteamAPI_ISteamNetworkingUtils_SteamNetworkingIPAddr_ToString",
    "SteamAPI_ISteamNetworkingUtils_SteamNetworkingIdentity_ToString",
    "SteamAPI_SteamNetworkingIPAddr_ToString", "SteamAPI_SteamNetworkingIdentity_ToString",
]

VTABLE_DIR = HERE / "vtables"
# C++ 虚表：每张表来自用户自己的 steam_api64.dll 的 MSVC 槽位（tools/steam-vtable-map.py），只含名字与槽号。
# 能转发的槽直接指向已有扁平函数（Win64 上成员函数与 self 打头的扁平函数寄存器布局相同）；
# 经隐藏结果指针返回的方法要手写 thunk（vtables.c）；其余槽是桩，返回 0 并记一次版本、槽位与方法名。
FLAT_PREFIX = {"user": "SteamAPI_ISteamUser_", "userstats": "SteamAPI_ISteamUserStats_", "apps": "SteamAPI_ISteamApps_",
               "friends": "SteamAPI_ISteamFriends_", "utils": "SteamAPI_ISteamUtils_",
               "remotestorage": "SteamAPI_ISteamRemoteStorage_", "client": "SteamAPI_ISteamClient_"}
HIDDEN_THUNKS = {"SteamAPI_ISteamUser_GetSteamID": "sb_thunk_ISteamUser_GetSteamID",
                 "SteamAPI_ISteamApps_GetAppOwner": "sb_thunk_ISteamApps_GetAppOwner"}
GETTER_RE = re.compile(r"^SteamAPI_ISteamClient_GetISteam(\w+)$")
ACCESSOR_RE = re.compile(r"^SteamAPI_Steam(\w+?)_v\d+$")
# SDK <= 1.3x exported the accessors as plain functions (SteamUser, SteamUGC ...); games of that era import them by name.
BARE_RE = re.compile(r"^Steam(User|Friends|Utils|Matchmaking|MatchmakingServers|UserStats|Apps|Networking|RemoteStorage|Screenshots|HTTP|"
                     r"UnifiedMessages|Controller|UGC|AppList|Music|MusicRemote|HTMLSurface|Inventory|Video|ParentalSettings)$")
INERT_SLOTS = 512   # inert vtable size; the widest Steam interface has well under 200 methods


def family_of(name):
    """Family token of a getter/accessor export, in the reduction helper.c uses: 'NetworkingUtils_SteamAPI' -> 'networkingutils'."""
    m = GETTER_RE.match(name) or ACCESSOR_RE.match(name) or BARE_RE.match(name)
    if not m:
        return None
    return "".join(c for c in m.group(1).replace("_SteamAPI", "") if c.isalpha()).lower()


def inert_getters(exports=EXPORTS):
    out = []
    for n in exports:
        fam = family_of(n)
        if fam is None or fam == "genericinterface" or fam in SERVED_FAMILIES:
            continue
        out.append(n)
    return out


INERT_GETTERS = inert_getters()


def policy_errors(exports, implemented, getters, handwritten, never_prefixes=NEVER_PREFIXES):
    """Every way the four lists could quietly contradict the policy. Empty list means the build may proceed."""
    errs = []
    impl, ig, ih, exp = set(implemented), set(getters), set(handwritten), set(exports)
    bad = sorted(n for n in impl if n.startswith(never_prefixes))
    if bad:
        errs.append(f"never-implement names in IMPLEMENTED: {bad}")
    for label, names in (("INERT_GETTERS", ig), ("INERT_HANDWRITTEN", ih)):
        overlap = sorted(names & impl)
        if overlap:
            errs.append(f"{label} overlaps IMPLEMENTED (a name is either forwarded or inert, never both): {overlap}")
        missing = sorted(names - exp)
        if missing:
            errs.append(f"{label} names not exported by the reference dll: {missing}")
    overlap = sorted(ig & ih)
    if overlap:
        errs.append(f"INERT_GETTERS overlaps INERT_HANDWRITTEN: {overlap}")
    notgetter = sorted(n for n in ig if family_of(n) is None)
    if notgetter:
        errs.append(f"INERT_GETTERS may only hold interface getters/accessors: {notgetter}")
    served = sorted(n for n in ig if family_of(n) in SERVED_FAMILIES or family_of(n) == "genericinterface")
    if served:
        errs.append(f"INERT_GETTERS must not hand out inert handles for served families: {served}")
    return errs


def load_vtables(directory=VTABLE_DIR):
    tables = []
    for path in sorted(directory.glob("*.json")):
        t = json.loads(path.read_text(encoding="utf8"))
        t["_file"] = path.name
        tables.append(t)
    return tables


def vtable_kind(family, row, implemented, getters):
    """(kind, symbol)：forward / thunk / stub。惰性家族（没有 FLAT_PREFIX）的表全是桩：只为 32 位的 ret N 与按名字记日志。"""
    if family not in FLAT_PREFIX:
        return ("stub", None)
    flat = FLAT_PREFIX[family] + row["method"]
    if flat in implemented or flat in getters:
        if row.get("hidden_result_pointer"):
            return ("thunk", HIDDEN_THUNKS.get(flat))
        return ("forward", flat)
    return ("stub", None)


def vtable_policy_errors(tables, implemented, getters, exports=EXPORTS, never_prefixes=NEVER_PREFIXES):
    errs = []
    seen = set()
    inert_families = {family_of(n) for n in getters}
    for t in tables:
        fam, ver = t.get("family"), t.get("version")
        if fam not in FLAT_PREFIX and fam not in inert_families:
            errs.append(f"{t['_file']}: family {fam!r} is neither served by the helper nor an inert family"); continue
        if not ver or ver in seen:
            errs.append(f"{t['_file']}: missing or duplicate version {ver!r}"); continue
        seen.add(ver)
        slots = [r["slot"] for r in t["slots"]]
        if slots != sorted(set(slots)) or (slots and slots[-1] >= t["slot_count"]):
            errs.append(f"{ver}: slots must be unique, ascending and below slot_count")
        for r in t["slots"]:
            kind, sym = vtable_kind(fam, r, set(implemented), set(getters))
            if fam not in FLAT_PREFIX:
                continue                       # inert family: every slot is a stub by construction
            flat = FLAT_PREFIX[fam] + r["method"]
            if kind == "forward" and flat not in exports:
                errs.append(f"{ver}: forward target {flat} is not an export")
            if kind == "thunk" and not sym:
                errs.append(f"{ver}: {flat} returns through a hidden result pointer and has no typed thunk; a bare forward would corrupt the caller")
            if kind in ("forward", "thunk") and flat in set(implemented) and flat.startswith(never_prefixes):
                errs.append(f"{ver}: {flat} is on the never-implement list and cannot be a vtable forward")   # inert getters are allowed: they never reach the helper
    return errs


VT_PAD = 32   # 每张 C++ 虚表在已知槽之后多垫的桩槽数
# 没有自己表的版本串，只借逐对核过槽位的表。「Valve 只在末尾追加」不成立：SteamClient020 在第 20 槽插了一格，RemoteStorage014 重排了
# 50/52 个槽（2026-09-13 表对表比出来的）。RemoteStorage013 → 014：SDK 1.36 与 1.4x 两份 DLL 的扁平包装读出的 55 个槽与 014 逐槽一致。
# SteamUser018 / SteamUtils007 / STEAMAPPS 007 已有从 2015 年 DLL 读出的精确表，不再借。
VTABLE_ALIASES = {"STEAMREMOTESTORAGE_INTERFACE_VERSION013": "STEAMREMOTESTORAGE_INTERFACE_VERSION014"}
ARGSIZES_I386 = HERE / "argsizes-i386.json"   # x86 __thiscall 每个方法由被调方清的栈字节（tools/steam-flat-argsizes.py 从 Valve 32 位 DLL 读出）


# 同名方法在更新的接口版本里改过签名的，按 (版本串, 方法) 手写覆盖；SDK 1.57 给 GetAuthSessionTicket 加了 SteamNetworkingIdentity 指针。
ARGSIZE_OVERRIDES = {("SteamUser023", "GetAuthSessionTicket"): (16, False), ("SteamUser023", "GetAuthTicketForWebApi"): (4, False)}


def load_argsizes(path=ARGSIZES_I386):
    """({扁平名: (被调方要清的字节数 N, 是否经隐藏结果指针返回)}, {来源 DLL 里出现的接口版本串})；隐藏结果指针本身算进 N。
    版本串在来源集合里的表，N 是精确的；其它版本的同名方法按名字取，是最佳猜测（Valve 改签名时会换版本串）。"""
    if not path.exists():
        return {}, set()
    doc = json.loads(path.read_text(encoding="utf8"))
    sizes = {name: (m["stack_bytes"] + (4 if m["hidden_ret"] else 0), bool(m["hidden_ret"])) for name, m in doc["methods"].items()}
    exact = {v for src in doc.get("sources", []) for v in src.get("interface_versions", [])}
    return sizes, exact


def render_vtables_i386(tables, implemented, getters, argsizes, exact_versions=frozenset()):
    """32 位 C++ 虚表：x86 成员函数是 __thiscall（this 在 ecx，参数在栈上，由被调方清 N 字节），扁平函数是 __cdecl，
    所以每个转发槽要一段小 asm 把栈上参数复制一遍、把 this 压成第一个参数、调扁平函数、再 ret N。桩槽也必须 ret N，
    N 来自 argsizes-i386.json；没有 N 的槽只能原样 ret 并记「栈可能已坏」，比之前全表都这样好得多。"""
    impl, ig = set(implemented), set(getters)
    h = ["/* generated by gen.py, do not edit */", "#ifndef STEAMBRIDGE_VTABLES_GEN_H", "#define STEAMBRIDGE_VTABLES_GEN_H",
         "typedef struct { const char *version; const char *family; void *const *table; int count; const char *const *names; } sb_vtable;",
         f"#define SB_VTABLE_COUNT {len(tables)}", "extern const sb_vtable sb_vtables[SB_VTABLE_COUNT];",
         "typedef struct { const char *version; const char *alias; } sb_vtable_alias;",
         f"#define SB_VTABLE_ALIAS_COUNT {len(VTABLE_ALIASES)}", "extern const sb_vtable_alias sb_vtable_aliases[SB_VTABLE_ALIAS_COUNT + 1];", "#endif", ""]
    c = ['/* generated by gen.py, do not edit: 32-bit (__thiscall) C++ vtables, stack sizes from argsizes-i386.json */',
         '#include "bridge.h"', '#include "vtables_gen.h"', "",
         "void sb_vt_stub_hit(int table, int slot);", "void sb_vt_stub_hit_unknown(int table, int slot);", ""]
    asm = [".text"]
    declared = set()
    known = unknown = 0
    for i, t in enumerate(tables):
        byslot = {r["slot"]: r for r in t["slots"]}
        names, entries = [], []
        c.append(f"/* {t['version']}: stack sizes {'exact (a reference DLL carries this version)' if t['version'] in exact_versions else 'by method name (best guess for this version)'} */")
        for slot in range(t["slot_count"] + VT_PAD):
            r = byslot.get(slot)
            kind, sym = vtable_kind(t["family"], r, impl, ig) if r else ("stub", None)
            flat = f"SteamAPI_{t['interface']}_{r['method']}" if r else None   # 扁平名按接口名拼，惰性家族也能查到 N
            names.append(json.dumps(r["method"] if r else "?"))
            size = (ARGSIZE_OVERRIDES.get((t["version"], r["method"])) or argsizes.get(flat)) if r else None
            if kind == "forward":
                if size is None:
                    raise SystemExit(f"{t['version']} slot {slot} {flat}: forwarded on i386 but argsizes-i386.json has no stack size")
                n, hidden = size
                if hidden:
                    raise SystemExit(f"{t['version']} slot {slot} {flat}: hidden result pointer must use a typed thunk")
                fn = f"sb_i386_fwd_{flat}"
                if fn not in declared:
                    declared.add(fn)
                    c.append(f"void {fn}(void);")
                    asm += [".p2align 4", f".globl _{fn}", f"_{fn}:", "  pushl %ebp", "  movl %esp, %ebp"]
                    asm += [f"  pushl {8 + off}(%ebp)" for off in range(n - 4, -4, -4)]
                    asm += ["  pushl %ecx", f"  call _{flat}", f"  addl ${n + 4}, %esp", "  popl %ebp", f"  ret ${n}"]
                entries.append(f"(void *){fn}"); known += 1
            elif kind == "thunk":
                if size is None or size[0] != 4 or not size[1]:
                    raise SystemExit(f"{t['version']} slot {slot} {flat}: typed thunk expects a hidden 8-byte result and no other arguments")
                fn = f"sb_i386_hid_{flat}"
                if fn not in declared:
                    declared.add(fn)
                    c.append(f"void {fn}(void);")
                    asm += [".p2align 4", f".globl _{fn}", f"_{fn}:", "  pushl %ebp", "  movl %esp, %ebp", "  pushl %ecx", f"  call _{flat}",
                            "  addl $4, %esp", "  movl 8(%ebp), %ecx", "  movl %eax, (%ecx)", "  movl %edx, 4(%ecx)", "  movl %ecx, %eax",
                            "  popl %ebp", "  ret $4"]
                entries.append(f"(void *){fn}"); known += 1
            else:
                fn = f"sb_i386_stub_{i}_{slot}"
                c.append(f"void {fn}(void);")
                asm += [".p2align 4", f".globl _{fn}", f"_{fn}:", f"  pushl ${slot}", f"  pushl ${i}"]
                if size is not None:
                    n, hidden = size
                    asm += ["  call _sb_vt_stub_hit", "  addl $8, %esp", "  xorl %eax, %eax", "  xorl %edx, %edx"]
                    if hidden:
                        asm += ["  movl 4(%esp), %eax", "  movl $0, (%eax)", "  movl $0, 4(%eax)"]
                    asm += [f"  ret ${n}"]
                    known += 1
                else:
                    asm += ["  call _sb_vt_stub_hit_unknown", "  addl $8, %esp", "  xorl %eax, %eax", "  xorl %edx, %edx", "  ret"]
                    unknown += 1
                entries.append(f"(void *){fn}")
        c.append(f"static const char *const vt_names_{i}[{t['slot_count'] + VT_PAD}] = {{ " + ", ".join(names) + " };")
        c.append(f"static void *const vt_{i}[{t['slot_count'] + VT_PAD}] = {{ " + ", ".join(entries) + " };")
    c.append("")
    c.append('__asm__(' + "\n        ".join(json.dumps(line + "\n") for line in asm) + ");")
    c.append("")
    c.append(f"const sb_vtable sb_vtables[SB_VTABLE_COUNT] = {{")
    for i, t in enumerate(tables):
        c.append(f'    {{ {json.dumps(t["version"])}, {json.dumps(t["family"])}, vt_{i}, {t["slot_count"]}, vt_names_{i} }},')
    c += ["};", ""]
    known_versions = {t["version"] for t in tables}
    for v, a in VTABLE_ALIASES.items():
        if a not in known_versions:
            raise SystemExit(f"vtable alias {v} -> {a}: no table for {a}")
    c.append("const sb_vtable_alias sb_vtable_aliases[SB_VTABLE_ALIAS_COUNT + 1] = {")
    c += [f'    {{ {json.dumps(v)}, {json.dumps(a)} }},' for v, a in VTABLE_ALIASES.items()]
    c += ["    { 0, 0 }", "};", ""]
    c.insert(3, f"/* slots with a known stack size: {known}; without (padding beyond the known slots and methods absent from the reference DLLs): {unknown} */")
    return "\n".join(h), "\n".join(c), known, unknown


def render_vtables(tables, implemented, getters, arch="x86_64"):
    impl, ig = set(implemented), set(getters)
    if arch == "i386":
        sizes, exact = load_argsizes()
        h, c, _known, _unknown = render_vtables_i386(tables, implemented, getters, sizes, exact)
        return h, c
    h = ["/* generated by gen.py, do not edit */", "#ifndef STEAMBRIDGE_VTABLES_GEN_H", "#define STEAMBRIDGE_VTABLES_GEN_H",
         "typedef struct { const char *version; const char *family; void *const *table; int count; const char *const *names; } sb_vtable;",
         f"#define SB_VTABLE_COUNT {len(tables)}", "extern const sb_vtable sb_vtables[SB_VTABLE_COUNT];",
         "typedef struct { const char *version; const char *alias; } sb_vtable_alias;",
         f"#define SB_VTABLE_ALIAS_COUNT {len(VTABLE_ALIASES)}", "extern const sb_vtable_alias sb_vtable_aliases[SB_VTABLE_ALIAS_COUNT + 1];", "#endif", ""]
    c = ['/* generated by gen.py, do not edit */', '#include "bridge.h"', '#include "vtables_gen.h"', "",
         "void sb_vt_stub_hit(int table, int slot);", ""]
    forwards, asm, thunks = set(), [".text"], set()
    for i, t in enumerate(tables):
        for r in t["slots"]:
            kind, sym = vtable_kind(t["family"], r, impl, ig)
            if kind == "forward": forwards.add(sym)
            elif kind == "thunk": thunks.add(sym)
    c += [f"long long {f}(void);" for f in sorted(forwards)]
    c += [f"void *{f}(void *, unsigned long long *);" for f in sorted(thunks)]
    c.append("")
    for i, t in enumerate(tables):
        byslot = {r["slot"]: r for r in t["slots"]}
        names, entries = [], []
        # 同一个版本串后来会追加方法（Apps008 在 1.58+ 多了 SetDlcContext 等），slot_count 只是本机 DLL 看到的上限；
        # 表后面多垫 SB_VT_PAD 个桩，游戏调到已知之外的槽只会走桩并记日志，不会读到数组外面。
        for slot in range(t["slot_count"] + VT_PAD):
            r = byslot.get(slot)
            kind, sym = vtable_kind(t["family"], r, impl, ig) if r else ("stub", None)
            names.append(json.dumps(r["method"] if r else "?"))
            if kind in ("forward", "thunk"):
                entries.append(f"(void *){sym}")
            else:
                fn = f"sb_vt_stub_{i}_{slot}"
                asm += [".p2align 4", f".globl {fn}", f"{fn}:", "  subq $40, %rsp", f"  movl ${i}, %ecx", f"  movl ${slot}, %edx",
                        "  call sb_vt_stub_hit", "  addq $40, %rsp", "  xorl %eax, %eax", "  pxor %xmm0, %xmm0", "  ret"]
                c.append(f"long long {fn}(void);")
                entries.append(f"(void *){fn}")
        c.append(f"static const char *const vt_names_{i}[{t['slot_count'] + VT_PAD}] = {{ " + ", ".join(names) + " };")
        c.append(f"static void *const vt_{i}[{t['slot_count'] + VT_PAD}] = {{ " + ", ".join(entries) + " };")
    c.append("")
    c.append('__asm__(' + "\n        ".join(json.dumps(line + "\n") for line in asm) + ");")
    c.append("")
    c.append(f"const sb_vtable sb_vtables[SB_VTABLE_COUNT] = {{")
    for i, t in enumerate(tables):
        c.append(f'    {{ {json.dumps(t["version"])}, {json.dumps(t["family"])}, vt_{i}, {t["slot_count"]}, vt_names_{i} }},')
    c += ["};", ""]
    known_versions = {t["version"] for t in tables}
    for v, a in VTABLE_ALIASES.items():
        if a not in known_versions:
            raise SystemExit(f"vtable alias {v} -> {a}: no table for {a}")
    c.append("const sb_vtable_alias sb_vtable_aliases[SB_VTABLE_ALIAS_COUNT + 1] = {")
    c += [f'    {{ {json.dumps(v)}, {json.dumps(a)} }},' for v, a in VTABLE_ALIASES.items()]
    c += ["    { 0, 0 }", "};", ""]
    return "\n".join(h), "\n".join(c)


def version_templates(tables=None):
    """家族 → 版本串模板（末尾三位数字换成 {:03d}），从已有的表里学：screenshots → STEAMSCREENSHOTS_INTERFACE_VERSION{:03d}。"""
    out = {}
    for t in (tables if tables is not None else load_vtables()):
        v = t["version"]
        if v[-3:].isdigit() and t["family"] not in out:
            out[t["family"]] = v[:-3] + "{:03d}"
    return out


def oldest_versions(tables=None):
    """家族 → 版本号最小的版本串（裸访问器给老游戏用，发最老的布局）。"""
    out = {}
    for t in (tables if tables is not None else load_vtables()):
        v = t["version"]
        if v[-3:].isdigit() and (t["family"] not in out or int(v[-3:]) < int(out[t["family"]][-3:])):
            out[t["family"]] = v
    return out


def render_inert(getters, slots=INERT_SLOTS, arch="x86_64"):
    families = sorted({family_of(n) for n in getters})
    header = ["/* generated by gen.py, do not edit */", "#ifndef STEAMBRIDGE_INERT_GEN_H", "#define STEAMBRIDGE_INERT_GEN_H",
              f"#define SB_INERT_SLOTS {slots}", f"#define SB_INERT_FAMILY_COUNT {len(families)}",
              "extern const char *const sb_inert_families[SB_INERT_FAMILY_COUNT];", "#endif", ""]
    body = ['/* generated by gen.py, do not edit */', '#include "bridge.h"', '#include "inert_gen.h"', ""]
    body.append("const char *const sb_inert_families[SB_INERT_FAMILY_COUNT] = {")
    body += [f'    "{f}",' for f in families]
    body += ["};", ""]
    body.append("/* Getters and accessors for families the bridge never serves. ISteamClient getters and versioned accessors carry the")
    body.append("   interface version string, so a versioned all-stub table (correct ret N on 32-bit, named slots in the log) can be")
    body.append("   served when one exists; other names read no arguments (a no-argument callee is ABI-compatible on x86_64/cdecl). */")
    body.append("void *sb_inert_versioned(const char *version, const char *family);")
    templates = version_templates()
    oldest = oldest_versions()
    for n in getters:
        fam = family_of(n)
        if BARE_RE.match(n) and fam in oldest:
            body.append(f'API void *{n}(void) {{ return sb_inert_versioned("{oldest[fam]}", "{fam}"); }}')
        elif GETTER_RE.match(n):
            body.append(f'API void *{n}(void *c, int u, int p, const char *v) {{ (void)c; (void)u; (void)p; return sb_inert_versioned(v, "{fam}"); }}')
        elif ACCESSOR_RE.match(n) and fam in templates:
            ver = templates[fam].format(int(n.rsplit("_v", 1)[1]))
            body.append(f'API void *{n}(void) {{ return sb_inert_versioned("{ver}", "{fam}"); }}')
        else:
            body.append(f'API void *{n}(void) {{ return sb_inert_handle("{fam}"); }}')
    body.append("")
    if arch == "i386":
        body.append("/* 32-bit bridge: flat API only. C++ methods are __thiscall with callee-cleaned stacks, so a stub with an")
        body.append("   unknown signature cannot be made ABI-safe. These slots log the call and return 0 with no stack cleanup; a")
        body.append("   32-bit C++ SDK game reaching them is unsupported and steambridge.log says which slot it hit first. */")
        body += [f"static long long __attribute__((thiscall)) sb_inert_slot_{i}(void) {{ sb_inert_hit({i}); return 0; }}" for i in range(slots)]
        body.append("")
    else:
        body.append("/* One inert method per vtable slot: records the slot, then returns 0 in RAX and 0.0 in XMM0, so bool,")
        body.append("   integer, pointer, uint64 and float/double callers all read a failure value. Aggregates wider than 8")
        body.append("   bytes (hidden result pointer) are not covered here; those need the per-version slot tables. */")
        asm = [".text"]
        for i in range(slots):
            asm += [f".p2align 4", f".globl sb_inert_slot_{i}", f"sb_inert_slot_{i}:", "  subq $40, %rsp",
                    f"  movl ${i}, %ecx", "  call sb_inert_hit", "  addq $40, %rsp", "  xorl %eax, %eax",
                    "  pxor %xmm0, %xmm0", "  ret"]
        body.append('__asm__(' + "\n        ".join(json.dumps(line + "\n") for line in asm) + ");")
        body.append("")
        body += [f"long long sb_inert_slot_{i}(void);" for i in range(slots)]
        body.append("")
    body.append(f"void *const sb_inert_vtable[SB_INERT_SLOTS] = {{")
    body += [f"    (void *)sb_inert_slot_{i}," for i in range(slots)]
    body += ["};", ""]
    return "\n".join(header), "\n".join(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成桥的导出表、空返回桩与惰性句柄取值函数，默认写入运行时目录。")
    parser.add_argument("--output-dir", type=Path, default=SN_RUNTIMES / "steambridge" / "generated")
    parser.add_argument("--arch", choices=("x86_64", "i386"), default="x86_64",
                        help="i386：32 位 steam_api.dll，扁平 API 加 __thiscall C++ 虚表（栈字节数来自 argsizes-i386.json）")
    args = parser.parse_args(argv)
    missing = [n for n in IMPLEMENTED if n not in EXPORTS]
    if missing:
        print("warning: not exported by the reference dll:", missing, file=sys.stderr)
    tables = load_vtables()
    errors = policy_errors(EXPORTS, IMPLEMENTED, INERT_GETTERS, INERT_HANDWRITTEN) + \
        vtable_policy_errors(tables, IMPLEMENTED, INERT_GETTERS)
    if errors:
        sys.exit("refusing to build:\n  " + "\n  ".join(errors))
    never = [n for n in EXPORTS if n.startswith(NEVER_PREFIXES)]
    impl, inert = set(IMPLEMENTED), set(INERT_GETTERS) | set(INERT_HANDWRITTEN)
    stubs = [n for n in EXPORTS if n not in impl and n not in inert]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with open(args.output_dir / "stubs_gen.c", "w") as f:
        f.write('/* generated by gen.py, do not edit */\n#include "bridge.h"\n\n')
        for i, n in enumerate(stubs):
            f.write(f'STUB({n}, {i})\n')
        f.write('\nconst char *const stub_names[] = {\n')
        for n in stubs:
            f.write(f'  "{n}",\n')
        f.write('};\nconst int stub_count = %d;\n' % len(stubs))

    header, body = render_inert(INERT_GETTERS, arch=args.arch)
    (args.output_dir / "inert_gen.h").write_text(header)
    (args.output_dir / "inert_gen.c").write_text(body)
    vh, vc = render_vtables(tables, IMPLEMENTED, INERT_GETTERS, arch=args.arch)
    (args.output_dir / "vtables_gen.h").write_text(vh)
    (args.output_dir / "vtables_gen.c").write_text(vc)

    dll = "steam_api.dll" if args.arch == "i386" else "steam_api64.dll"
    with open(args.output_dir / (dll[:-4] + ".def"), "w") as f:
        f.write(f"LIBRARY {dll}\nEXPORTS\n")
        for n in EXPORTS:
            f.write(f"  {n}\n")

    nfwd = sum(1 for t in tables for r in t["slots"] if vtable_kind(t["family"], r, impl, set(INERT_GETTERS))[0] != "stub")
    extra = ""
    if args.arch == "i386":
        sizes, exact = load_argsizes()
        _h, _c, known, unknown = render_vtables_i386(tables, IMPLEMENTED, INERT_GETTERS, sizes, exact)
        extra = f"  i386 slots with stack size {known}, without {unknown}; tables with exact sizes {sum(1 for t in tables if t['version'] in exact)}/{len(tables)}"
    print(f"exports {len(EXPORTS)}  implemented {len(impl & set(EXPORTS))}  inert getters {len(INERT_GETTERS)}  "
          f"inert typed {len(INERT_HANDWRITTEN)}  stubs {len(stubs)}  of which never-implement {len(never)}  "
          f"C++ vtables {len(tables)} ({sum(t['slot_count'] for t in tables)} slots, {nfwd} forwarded){extra}")


if __name__ == "__main__":
    main()
