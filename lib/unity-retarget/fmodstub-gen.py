#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Generate a no-op libfmodstudio stub from a game's FMODUnity.dll P/Invoke signatures.
Usage: fmodstub-gen.py <FMODUnity.dll> <out.c>   then:  cc -O1 -dynamiclib -arch arm64 -o libfmodstudio.dylib out.c
Every function returns FMOD_OK (0); bool-returning ones return true; out IntPtr handles get a dummy non-null value;
out int/uint/float/bool/enum get 0 (GetVersion gets the wrapper's own FMOD version so the version check passes).
"""
import sys, dnfile
pe = dnfile.dnPE(sys.argv[1]); md = pe.net.mdtables
typedefs = list(md.TypeDef); typerefs = list(md.TypeRef)
def tdname(i): t = typedefs[i - 1]; return (str(t.TypeNamespace) + '.' + str(t.TypeName)).strip('.')
def trname(i): t = typerefs[i - 1]; return (str(t.TypeNamespace) + '.' + str(t.TypeName)).strip('.')
def is_enum(i):
    try: return str(typedefs[i - 1].Extends.row.TypeName) == 'Enum'
    except Exception: return False
def rc(b, p):
    x = b[p]
    if x < 0x80: return x, p + 1
    if x & 0xc0 == 0x80: return ((x & 0x3f) << 8) | b[p + 1], p + 2
    return ((x & 0x1f) << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3], p + 4
PRIM = {0x02: 'bool', 0x04: 'i1', 0x05: 'u1', 0x06: 'i2', 0x07: 'u2', 0x08: 'i4', 0x09: 'u4', 0x0a: 'i8', 0x0b: 'u8', 0x0c: 'r4', 0x0d: 'r8', 0x0e: 'string', 0x18: 'I', 0x19: 'U', 0x01: 'void', 0x1c: 'object'}
def parse_type(b, p):
    t = b[p]; p += 1
    if t in (0x11, 0x12):
        tok, p = rc(b, p); tag = tok & 3; idx = tok >> 2
        nm = tdname(idx) if tag == 0 else trname(idx) if tag == 1 else '?'
        return ('enum' if (tag == 0 and is_enum(idx)) else 'struct', nm), p
    if t == 0x10: inner, p = parse_type(b, p); return ('byref', inner), p
    if t in (0x0f, 0x1d, 0x45): inner, p = parse_type(b, p); return ('ptr', inner), p
    if t in (0x1f, 0x20): tok, p = rc(b, p); return parse_type(b, p)
    if t == 0x14:
        inner, p = parse_type(b, p); rank, p = rc(b, p); ns, p = rc(b, p)
        for _ in range(ns): _, p = rc(b, p)
        nl, p = rc(b, p)
        for _ in range(nl): _, p = rc(b, p)
        return ('ptr', inner), p
    return ('prim', PRIM.get(t, hex(t))), p
# wrapper's FMOD version constant: FMOD.VERSION.number (const uint) lives in the Constant table; fall back to 2.02.07
version = 0x00020207
try:
    for row in md.Constant:
        par = row.Parent.row
        if getattr(par, 'Name', None) is not None and str(par.Name) == 'number':
            v = int.from_bytes(bytes(row.Value.value if hasattr(row.Value, 'value') else row.Value)[:4], 'little')
            if 0x00010000 <= v <= 0x000fffff: version = v; break
except Exception: pass
seen = set(); out = ['#include <stdint.h>', 'typedef uintptr_t U;']
for row in md.ImplMap:
    name = str(row.ImportName)
    if name in seen: continue
    seen.add(name)
    mref = row.MemberForwarded.row; b = bytes(mref.Signature.value) if hasattr(mref.Signature, 'value') else bytes(mref.Signature)
    p = 1; cnt, p = rc(b, p); ret, p = parse_type(b, p); params = []
    for _ in range(cnt): ty, p = parse_type(b, p); params.append(ty)
    body = []; reg = 0; ok = True
    for ty in params:
        k = ty[0]
        if k == 'prim':
            if ty[1] in ('r4', 'r8'): continue
            reg += 1
        elif k in ('byref', 'ptr'):
            if reg >= 8: ok = False; break
            if k == 'byref':
                inner = ty[1]
                if inner[0] == 'prim' and inner[1] in ('I', 'U', 'i8', 'u8'): body.append(f'if(a{reg}) *(uint64_t*)a{reg}=1;')
                elif inner[0] == 'prim' and inner[1] in ('i4', 'u4', 'r4', 'bool'): body.append(f'if(a{reg}) *(uint32_t*)a{reg}={"0x%08xu" % version if name.endswith("GetVersion") else "0"};')
                elif inner[0] == 'enum': body.append(f'if(a{reg}) *(uint32_t*)a{reg}={"2" if "PLAYBACK" in inner[1].upper() else "0"};')
            reg += 1
        elif k == 'enum': reg += 1
        else: reg += 2  # by-value struct (GUID etc.), assume <=16 bytes
    if not ok: body = []
    rv = '1' if ret == ('prim', 'bool') else '0'
    out.append(f'int {name}(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){{ {" ".join(body)} return {rv}; }}')
open(sys.argv[2], 'w').write('\n'.join(out) + '\n')
print(f'{len(seen)} stubs, FMOD version 0x{version:08x}')
