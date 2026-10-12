#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""从 Valve 32 位 steam_api.dll 的扁平包装函数里读出每个 C++ 方法在 x86 __thiscall 下由被调方清理的栈字节数 N。

32 位桥的 C++ 虚表桩必须 `ret N`，N 不对就把调用方的栈弄坏。SDK 头文件里有签名但仓库不带 SDK；
Valve 自己的扁平包装 `SteamAPI_ISteamX_Method(self, args...)`（__cdecl）会把参数逐个从 [esp+k] 取出 push 再 `call [vtable+slot*4]`，
所以只读机器码就能得到：访问到的最高参数偏移 → 参数总字节 → N；`lea reg,[esp+..]` 再 push 的是隐藏结果指针（返回 CSteamID/CGameID
一类，C++ 侧多 4 字节）；`call/jmp [reg+disp]` 的 disp/4 是槽位。只读，不改文件；输出 JSON。

  tools/steam-flat-argsizes.py <steam_api.dll> [--json 输出] [--verify lib/steambridge/bridge.c] [--objdump i686-w64-mingw32-objdump]

--verify 用桥源码里已实现的扁平函数原型算出的 N 做对照（i386：unsigned long long / double 8 字节，其余 4），报一致与不一致。
"""
import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

STRIDE, SNIP = 192, 128


def pe32_exports(data):
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("不是 PE 文件")
    coff = e_lfanew + 4
    machine = struct.unpack_from("<H", data, coff)[0]
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    optsz = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic != 0x10B or machine != 0x14C:
        raise ValueError(f"要 PE32 / i386 的 DLL（magic {magic:#x}, machine {machine:#x}）")
    exp_rva, exp_size = struct.unpack_from("<II", data, opt + 96)
    secs = []
    p = opt + optsz
    for _ in range(nsec):
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, p + 8)
        secs.append((vaddr, max(vsize, rsize), rptr))
        p += 40

    def off(rva):
        for va, size, rp in secs:
            if va <= rva < va + size:
                return rp + (rva - va)
        raise ValueError(hex(rva))
    e = off(exp_rva)
    nfunc, nnames, a_funcs, a_names, a_ords = struct.unpack_from("<IIIII", data, e + 20)
    funcs = struct.unpack_from("<%dI" % nfunc, data, off(a_funcs))
    names = struct.unpack_from("<%dI" % nnames, data, off(a_names))
    ords = struct.unpack_from("<%dH" % nnames, data, off(a_ords))
    out = {}
    for n_rva, o in zip(names, ords):
        no = off(n_rva)
        nm = data[no:data.index(b"\0", no)].decode("ascii", "replace")
        rva = funcs[o]
        if exp_rva <= rva < exp_rva + exp_size:
            continue   # forwarder
        out[nm] = off(rva)
    return out


def disassemble(snippets, objdump):
    blob = b"".join(s[:SNIP].ljust(SNIP, b"\xcc") + b"\xcc" * (STRIDE - SNIP) for s in snippets)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        text = subprocess.run([objdump, "-D", "-b", "binary", "-m", "i386", "-M", "intel", path],
                              capture_output=True, text=True, check=True, timeout=300).stdout
    finally:
        Path(path).unlink(missing_ok=True)
    per = [[] for _ in snippets]
    for line in text.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$", line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        if addr % STRIDE < SNIP:
            per[addr // STRIDE].append(m.group(2).strip())
    return per


MEM = re.compile(r"\[(esp|ebp)(?:\+0x([0-9a-f]+))?\]")
SIZES = {"DWORD": 4, "QWORD": 8, "WORD": 2, "BYTE": 1}


def emulate(instrs):
    """按 MSVC 扁平包装的样子走一遍（两种写法都见过：`push ebp; mov ebp,esp` 后按 [ebp+8/0xc/...] 取参，或直接 [esp+..]）。
    返回 dict(stack_bytes, slot, hidden_ret, status)。status: ok / no-call / complex。"""
    pushed = 0            # 到目前为止压栈的字节（含 push ebp、sub esp）
    ebp_base = None       # `mov ebp,esp` 那一刻的 pushed，之后 [ebp+d] 对应入口栈偏移 d - ebp_base
    end = 8               # 参数区末尾（入口 [esp+4] 是 this，至少到 8）
    hidden = False
    slot = None
    status = "no-call"
    lea_regs = set()
    last_disp = {}        # 寄存器 ← [reg+disp] 的 disp，`call eax` 时查
    for ins in instrs:
        op = ins.split()[0] if ins else ""
        if op in ("ret", "int3") or ins.startswith("ret "):
            break
        if op in ("rep", "movs", "movsb") or (op == "movsd" and "xmm" not in ins):
            return {"stack_bytes": None, "slot": None, "hidden_ret": hidden, "status": "complex"}
        m = re.match(r"sub\s+esp,0x([0-9a-f]+)", ins)
        if m:
            pushed += int(m.group(1), 16)
            continue
        m = re.match(r"add\s+esp,0x([0-9a-f]+)", ins)
        if m:
            pushed -= int(m.group(1), 16)
            continue
        if re.match(r"mov\s+ebp,esp", ins):
            ebp_base = pushed
            continue
        if re.match(r"mov\s+esp,ebp", ins):
            pushed = ebp_base if ebp_base is not None else pushed
            continue
        m = re.match(r"lea\s+(e[a-d]x|e[sd]i),\[e[bs]p", ins)
        if m:
            lea_regs.add(m.group(1))
            continue
        mem = MEM.search(ins)
        if mem and not ins.startswith("lea"):
            size = 4
            ms = re.search(r"(DWORD|QWORD|WORD|BYTE) PTR \[e[bs]p", ins)
            if ms:
                size = SIZES[ms.group(1)]
            if op in ("fld", "fild") and "QWORD" in ins:
                size = 8
            if op in ("movsd", "movq") and "xmm" in ins:
                size = 8
            disp = int(mem.group(2), 16) if mem.group(2) else 0
            if mem.group(1) == "esp":
                orig = disp - pushed
            elif ebp_base is not None:
                orig = disp - ebp_base
            else:
                orig = None
            if orig is not None and orig >= 4:
                end = max(end, orig + size)
        m = re.match(r"mov\s+(e[a-d]x|e[sd]i),DWORD PTR \[(e[a-d]x|e[sd]i)(?:\+0x([0-9a-f]+))?\]", ins)
        if m:
            last_disp[m.group(1)] = int(m.group(3), 16) if m.group(3) else 0
        if op == "push":
            arg = ins[5:].strip()
            if arg in lea_regs:
                hidden = True
            pushed += 4
            continue
        if op == "pop":
            pushed -= 4
            continue
        m = re.match(r"(call|jmp)\s+DWORD PTR \[(e[a-d]x|e[sd]i)(?:\+0x([0-9a-f]+))?\]", ins)
        if m:
            slot = int(m.group(3), 16) // 4 if m.group(3) else 0
            status = "ok"
            break
        m = re.match(r"(call|jmp)\s+(e[a-d]x|e[sd]i)$", ins)
        if m and m.group(2) in last_disp:
            slot = last_disp[m.group(2)] // 4
            status = "ok"
            break
    return {"stack_bytes": end - 8 if status == "ok" else None, "slot": slot, "hidden_ret": hidden, "status": status}


I386 = {"unsigned long long": 8, "uint64_t": 8, "long long": 8, "double": 8, "int64_t": 8}


def prototype_sizes(source):
    """桥源码里 `API ret SteamAPI_ISteamX_M(void *self, ...)` 的 i386 参数字节数（不含 self）。"""
    out = {}
    for m in re.finditer(r"^API\s+[^(\n]+?\b(SteamAPI_ISteam[A-Za-z]+_[A-Za-z0-9_]+)\(([^)]*)\)", source, re.M):
        params = [p.strip() for p in m.group(2).split(",") if p.strip()][1:]
        n = 0
        for p in params:
            t = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*$", "", p).strip()
            n += 4 if "*" in t else I386.get(t, 4)
        out[m.group(1)] = n
    return out


def merge(reports, out):
    """把几份 DLL 的结果合成桥用的元数据：每个扁平名一条 N 与隐藏结果指针标记；几份 DLL 不一致的名字记进 conflicts 不写 N。"""
    methods, conflicts, sources = {}, {}, []
    loaded = []
    for path in reports:
        r = json.loads(Path(path).read_text(encoding="utf8"))
        loaded.append((Path(path).stem, r))
    loaded.sort(key=lambda t: -t[1]["flat_exports"])   # 导出最多的（最新的）DLL 排前面，它的值当默认
    for stem, r in loaded:
        sources.append({"dll_sha256_12": stem, "flat_exports": r["flat_exports"], "interface_versions": r["interface_versions"]})
        for name, m in r["methods"].items():
            if m["status"] != "ok":
                continue
            entry = [m["stack_bytes"], bool(m["hidden_ret"])]
            e = methods.setdefault(name, {"stack_bytes": m["stack_bytes"], "hidden_ret": bool(m["hidden_ret"]), "sources": [], "by_source": {}})
            e["sources"].append(stem)
            e["by_source"][stem] = entry            # 每份 DLL 的值都留着：gen.py 按表的版本串挑同版本 DLL 的值
            if entry != [e["stack_bytes"], e["hidden_ret"]]:
                conflicts.setdefault(name, {})[stem] = entry
    for name in conflicts:
        conflicts[name]["default"] = [methods[name]["stack_bytes"], methods[name]["hidden_ret"]]
    doc = {"purpose": "x86 __thiscall stack bytes per Steamworks method, read from the flat wrappers of Valve's 32-bit steam_api.dll "
                      "by tools/steam-flat-argsizes.py; interface metadata only, no third-party bytes. hidden_ret methods return a "
                      "struct through a hidden pointer, so the C++ callee cleans stack_bytes + 4. A method's N is exact for the interface "
                      "versions listed under its sources; for other versions of the same interface it is a best guess (Valve bumps the "
                      "version string when a signature changes, e.g. GetAuthSessionTicket grew a parameter in SteamUser021).",
           "generated": __import__("time").strftime("%Y-%m-%d"), "sources": sources, "methods": dict(sorted(methods.items())), "conflicts": conflicts}
    Path(out).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
    print(f"合并 {len(reports)} 份：{len(methods)} 个方法，{len(conflicts)} 个跨版本不一致（默认取最新 DLL 的值，按表版本可选同版本 DLL 的值）→ {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dll", type=Path, nargs="?")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--verify", type=Path)
    ap.add_argument("--merge", nargs="+", type=Path, help="把几份 --json 结果合成一份元数据（配 --out）")
    ap.add_argument("--tables-out", type=Path, help="把这份 DLL 内嵌版本串对应的 C++ 槽位表写成 vtables/*.json（与 tools/steam-vtable-map.py 同一格式）")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--objdump", default=shutil.which("i686-w64-mingw32-objdump") or shutil.which("x86_64-w64-mingw32-objdump") or "objdump")
    a = ap.parse_args(argv)
    if a.merge:
        if not a.out:
            ap.error("--merge 需要 --out")
        merge(a.merge, a.out)
        return 0
    if not a.dll:
        ap.error("要一个 steam_api.dll，或 --merge")
    data = a.dll.read_bytes()
    exports = pe32_exports(data)
    names = sorted(n for n in exports if re.match(r"SteamAPI_ISteam[A-Za-z]+_", n))
    if not names:
        print(f"{a.dll.name}: 没有 SteamAPI_ISteam* 扁平导出（SDK 1.3x 之前的 DLL），读不出 N 也出不了表")
        return 1
    per = disassemble([data[exports[n]:exports[n] + SNIP] for n in names], a.objdump)
    result = {}
    for n, instrs in zip(names, per):
        r = emulate(instrs)
        r["offset"] = exports[n]
        fam, meth = n[len("SteamAPI_"):].split("_", 1)
        r["interface"], r["method"] = fam, meth
        result[n] = r
    ok = sum(1 for r in result.values() if r["status"] == "ok")
    versions = sorted(set(re.findall(rb"(Steam(?:Client|User|Friends|Utils|Matchmaking|MatchmakingServers|Networking|RemoteStorage|Screenshots|HTTP|Controller|Input|UGC|AppList|Music|MusicRemote|HTMLSurface|Inventory|Video|ParentalSettings|GameServer|GameServerStats|Apps|UserStats|MatchMaking)\d{3}|STEAM[A-Z]+_INTERFACE_VERSION\d{3})", data)))
    report = {"dll": str(a.dll), "flat_exports": len(names), "decoded": ok, "undecoded": len(names) - ok,
              "interface_versions": [v.decode() for v in versions], "methods": result}
    print(f"{a.dll.name}: 扁平导出 {len(names)}，读出 N {ok}，读不出 {len(names) - ok}")
    if a.verify:
        expect = prototype_sizes(a.verify.read_text(encoding="utf8"))
        agree, differ, absent = [], [], []
        for n, want in sorted(expect.items()):
            r = result.get(n)
            if not r or r["status"] != "ok":
                absent.append(n)
                continue
            (agree if r["stack_bytes"] == want else differ).append((n, want, r["stack_bytes"], r["hidden_ret"]))
        report["verify"] = {"agree": len(agree), "differ": [d[:3] for d in differ], "absent_or_undecoded": absent}
        print(f"对照桥源码 {len(expect)} 个原型：一致 {len(agree)}，不一致 {len(differ)}，DLL 里没有或读不出 {len(absent)}")
        for n, want, got, hidden in differ:
            print(f"  不一致 {n}: 原型 {want} 字节，机器码 {got}{'（隐藏结果指针）' if hidden else ''}")
    if a.tables_out:
        write_tables(result, data, a.dll, a.tables_out)
    if a.json:
        a.json.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(f"写入 {a.json}")
    return 0 if ok else 1


FAMILIES = {"ISteamClient": ("client", r"SteamClient\d{3}"), "ISteamUser": ("user", r"SteamUser\d{3}"),
            "ISteamUserStats": ("userstats", r"STEAMUSERSTATS_INTERFACE_VERSION\d{3}"), "ISteamApps": ("apps", r"STEAMAPPS_INTERFACE_VERSION\d{3}"),
            "ISteamFriends": ("friends", r"SteamFriends\d{3}"), "ISteamUtils": ("utils", r"SteamUtils\d{3}"),
            "ISteamRemoteStorage": ("remotestorage", r"STEAMREMOTESTORAGE_INTERFACE_VERSION\d{3}"),
            # 惰性家族：表全是桩，但 32 位上 ret N 要对；家族记号与 gen.py 的 family_of 一致
            "ISteamScreenshots": ("screenshots", r"STEAMSCREENSHOTS_INTERFACE_VERSION\d{3}"), "ISteamHTTP": ("http", r"STEAMHTTP_INTERFACE_VERSION\d{3}"),
            "ISteamMatchmaking": ("matchmaking", r"SteamMatchMaking\d{3}"), "ISteamMatchmakingServers": ("matchmakingservers", r"SteamMatchMakingServers\d{3}"),
            "ISteamNetworking": ("networking", r"SteamNetworking\d{3}"), "ISteamUGC": ("ugc", r"STEAMUGC_INTERFACE_VERSION\d{3}"),
            "ISteamController": ("controller", r"SteamController\d{3}"), "ISteamInput": ("input", r"SteamInput\d{3}"),
            "ISteamInventory": ("inventory", r"STEAMINVENTORY_INTERFACE_V\d{3}"), "ISteamMusic": ("music", r"STEAMMUSIC_INTERFACE_VERSION\d{3}"),
            "ISteamMusicRemote": ("musicremote", r"STEAMMUSICREMOTE_INTERFACE_VERSION\d{3}"), "ISteamHTMLSurface": ("htmlsurface", r"STEAMHTMLSURFACE_INTERFACE_VERSION_\d{3}"),
            "ISteamAppList": ("applist", r"STEAMAPPLIST_INTERFACE_VERSION\d{3}"), "ISteamVideo": ("video", r"STEAMVIDEO_INTERFACE_V\d{3}"),
            "ISteamParentalSettings": ("parentalsettings", r"STEAMPARENTALSETTINGS_INTERFACE_VERSION\d{3}"), "ISteamGameServer": ("gameserver", r"SteamGameServer\d{3}"),
            "ISteamGameServerStats": ("gameserverstats", r"SteamGameServerStats\d{3}"), "ISteamNetworkingUtils": ("networkingutils", r"SteamNetworkingUtils\d{3}"),
            "ISteamNetworkingSockets": ("networkingsockets", r"SteamNetworkingSockets\d{3}"), "ISteamNetworkingMessages": ("networkingmessages", r"SteamNetworkingMessages\d{3}"),
            "ISteamRemotePlay": ("remoteplay", r"STEAMREMOTEPLAY_INTERFACE_VERSION\d{3}"), "ISteamParties": ("parties", r"SteamParties\d{3}")}


def write_tables(result, data, dll, out_dir):
    """每个扁平包装 `call [vtable+disp]` 的 disp/4 就是该方法在这份 DLL 对应接口版本里的槽位：按家族凑成一张表。
    版本串取 DLL 里内嵌的那一个（同家族多于一个就跳过，说不清是哪个）。只写仓库没有的版本；已有的打印是否一致，当交叉验证。"""
    import hashlib, time
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(data).hexdigest()[:12]
    for iface, (family, pat) in FAMILIES.items():
        versions = sorted(set(m.decode() for m in re.findall(pat.encode(), data)))
        if len(versions) != 1:
            print(f"{iface}: DLL 里有 {len(versions)} 个版本串 {versions}，不写表"); continue
        version = versions[0]
        rows = sorted((m["slot"], m["method"], m["hidden_ret"]) for n, m in result.items() if m["interface"] == iface and m["status"] == "ok" and m["slot"] is not None)
        if not rows:
            continue
        byslot, hidden = {}, {}
        for slot, meth, hid in rows:
            byslot.setdefault(slot, []).append(meth)
            hidden[slot] = hid
        slots = [dict({"slot": s, "method": ms[0]}, **({"hidden_result_pointer": True} if hidden[s] else {})) for s, ms in sorted(byslot.items()) if len(ms) == 1]
        ambiguous = {s: ms for s, ms in byslot.items() if len(ms) > 1}
        count = max(byslot) + 1
        unresolved = [s for s in range(count) if s not in byslot or s in ambiguous]
        table = {"interface": iface, "version": version, "family": family, "slot_count": count,
                 "source": f"MSVC vtable slots read from the flat-API wrappers of a 32-bit steam_api.dll (SHA-256 {sha}...) shipped with a Steam game "
                           f"used as a reference; the version string is the one embedded in that DLL. Extracted with tools/steam-flat-argsizes.py --tables-out on "
                           f"{time.strftime('%Y-%m-%d')}. Names and slot numbers only, no third-party bytes."
                           + (f" Ambiguous slots (several wrappers call the same slot): {ambiguous}." if ambiguous else ""),
                 "unresolved_slots": unresolved, "slots": slots}
        target = out_dir / f"{version}.json"
        if target.exists():
            old = json.loads(target.read_text(encoding="utf8"))
            mine = {r["slot"]: r["method"] for r in slots}; theirs = {r["slot"]: r["method"] for r in old["slots"]}
            diff = {s: (theirs.get(s), mine.get(s)) for s in set(mine) | set(theirs) if theirs.get(s) != mine.get(s) and s in mine and s in theirs}
            print(f"{version}: 仓库已有表（{len(theirs)} 槽），32 位读出 {len(mine)} 槽，同槽不同名 {len(diff)}" + (f" {diff}" if diff else "，一致"))
            continue
        target.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
        print(f"{version}: 新表 {len(slots)} 槽（slot_count {count}，未解析 {len(unresolved)}）→ {target}")


if __name__ == "__main__":
    sys.exit(main())
