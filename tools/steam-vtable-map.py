#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""从用户自己的 steam_api64.dll 读出每个 C++ 接口方法在 MSVC 虚表里的槽位（只读二进制作为数据，不复制任何代码）。

Valve 的扁平导出 SteamAPI_ISteamXxx_Method 是薄包装：`mov rax,[rcx]; jmp [rax+槽位*8]`；返回 CSteamID、SteamIPAddress_t
这类经隐藏结果指针的方法是 `lea rdx,[rsp+..]; call [rax+槽位*8]`。逐个反汇编这几十字节就得到该 DLL 所对应 SDK 版本
（DLL 内的接口版本字符串）的完整槽位表，用来生成桥的 C++ 虚表并交叉核对头文件抽取的顺序。

用法：steam-vtable-map.py <steam_api64.dll> [--out 结果.json]
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

FAMILIES = ("User", "UserStats", "Apps", "Friends", "Utils", "RemoteStorage", "Client")
VERSION_RE = re.compile(rb"(SteamUser\d{3}|SteamFriends\d{3}|SteamUtils\d{3}|SteamClient\d{3}|STEAMUSERSTATS_INTERFACE_VERSION\d{3}|"
                        rb"STEAMAPPS_INTERFACE_VERSION\d{3}|STEAMREMOTESTORAGE_INTERFACE_VERSION\d{3})\x00")
FAMILY_OF_VERSION = {"SteamUser": "User", "SteamFriends": "Friends", "SteamUtils": "Utils", "SteamClient": "Client",
                     "STEAMUSERSTATS_INTERFACE_VERSION": "UserStats", "STEAMAPPS_INTERFACE_VERSION": "Apps",
                     "STEAMREMOTESTORAGE_INTERFACE_VERSION": "RemoteStorage"}


def pe_exports(data):
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise SystemExit("不是 PE 文件")
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    optsz = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    if struct.unpack_from("<H", data, opt)[0] != 0x20B:
        raise SystemExit("只支持 PE32+（x64）")
    exp_rva, exp_size = struct.unpack_from("<II", data, opt + 112)
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
    for n, o in zip(names, ords):
        no = off(n)
        nm = data[no:data.index(b"\0", no)].decode()
        rva = funcs[o]
        if exp_rva <= rva < exp_rva + exp_size:
            continue
        out[nm] = off(rva)
    return out


def disassemble(snippets, objdump):
    """一次 objdump：每段 64 字节后再垫 32 字节 int3，前一段末尾被截断的指令吞不到下一段，按 96 字节步长切回。"""
    STRIDE = 96
    blob = b"".join(s[:64].ljust(64, b"\xcc") + b"\xcc" * (STRIDE - 64) for s in snippets)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        text = subprocess.run([objdump, "-D", "-b", "binary", "-m", "i386:x86-64", "-M", "intel", path],
                              capture_output=True, text=True, check=True, timeout=120).stdout
    finally:
        Path(path).unlink(missing_ok=True)
    per = [[] for _ in snippets]
    for line in text.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$", line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        if addr % STRIDE < 64:
            per[addr // STRIDE].append((addr % STRIDE, m.group(2).strip()))
    return per


def classify(instrs):
    """返回 (槽位, 隐藏结果指针?, self 所在寄存器) 或 None。"""
    self_reg = None
    vt_reg = None
    hidden = False
    for _, ins in instrs:
        m = re.match(r"mov\s+(rax|r10|r11),QWORD PTR \[(rcx|rdx)\]", ins)   # 虚表指针装进 rax 或 r10/r11
        if m and self_reg is None:
            vt_reg, self_reg = m.group(1), m.group(2)
        if re.match(r"lea\s+rdx,\[rsp", ins):
            hidden = True
        m = re.match(r"(?:rex\.W[B]?\s+)?(jmp|call)\s+QWORD PTR \[(rax|r10|r11)(?:\+0x([0-9a-f]+))?\]", ins)   # objdump 把 48 ff 20 印成 rex.W jmp
        if m and self_reg and m.group(2) == vt_reg:
            slot = int(m.group(3), 16) // 8 if m.group(3) else 0
            return slot, hidden or (self_reg == "rdx"), self_reg
        if ins.startswith("ret") or ins.startswith("int3"):
            break
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dll", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    objdump = shutil.which("x86_64-w64-mingw32-objdump") or shutil.which("objdump")
    if not objdump:
        raise SystemExit("缺少 objdump（mingw-w64）")
    data = args.dll.read_bytes()
    versions = {}
    for m in VERSION_RE.finditer(data):
        v = m.group(1).decode()
        fam = FAMILY_OF_VERSION[re.sub(r"\d{3}$", "", v)]
        versions.setdefault(fam, set()).add(v)
    exports = pe_exports(data)
    names = [n for n in exports if re.match(r"SteamAPI_ISteam(User|UserStats|Apps|Friends|Utils|RemoteStorage|Client)_", n)]
    names.sort()
    per = disassemble([data[exports[n]:exports[n] + 64] for n in names], objdump)
    tables, unresolved = {}, []
    for n, instrs in zip(names, per):
        fam, method = re.match(r"SteamAPI_ISteam(\w+?)_(\w+)$", n).groups()
        c = classify(instrs)
        if not c:
            unresolved.append(n)
            continue
        slot, hidden, self_reg = c
        t = tables.setdefault(fam, {})
        entry = {"method": method, "hidden_result_pointer": hidden}
        if slot in t and t[slot]["method"] != method:
            t[slot].setdefault("aliases", []).append(method)   # 同一槽位多个导出名（如同一方法的兼容别名）
        else:
            t[slot] = entry
    result = {"dll": str(args.dll), "exports": len(exports), "interface_versions": {k: sorted(v) for k, v in versions.items()},
              "tables": {fam: {str(slot): t[slot] for slot in sorted(t)} for fam, t in tables.items()},
              "unresolved": unresolved}
    for fam, t in tables.items():
        slots = sorted(t)
        result["tables"][fam]["_summary"] = {"methods": len(slots), "max_slot": slots[-1], "gaps": [s for s in range(slots[-1] + 1) if s not in t]}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text)
    for fam, t in result["tables"].items():
        s = t["_summary"]
        print(f"{fam:14s} versions={result['interface_versions'].get(fam)} methods={s['methods']} max_slot={s['max_slot']} gaps={s['gaps']}")
    print("unresolved", len(unresolved), unresolved[:10])


if __name__ == "__main__":
    main()
