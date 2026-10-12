# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Synthesize a DXBC RDEF chunk from Unity shader reflection and inject it into a stripped DXBC."""
import struct

SVT = {0: 3, 1: 2, 2: 1, 3: 3, 4: 2, 5: 19}  # Unity ShaderParamType -> D3D_SHADER_VARIABLE_TYPE (float,int,bool,half,short,uint)
DIM = {2: 4, 3: 8, 4: 9, 5: 5, 6: 10}  # Unity TextureDimension -> D3D_SRV_DIMENSION

def dxbc_version(dxbc):
    n = struct.unpack_from('<I', dxbc, 28)[0]
    for o in struct.unpack_from(f'<{n}I', dxbc, 32):
        if dxbc[o:o + 4] in (b'SHDR', b'SHEX'):
            v = struct.unpack_from('<I', dxbc, o + 8)[0]
            return (v >> 4) & 0xf, v & 0xf, (v >> 16) & 0xffff  # major, minor, program type
    raise ValueError('no SHDR')

def var_size(v):
    size = v['rows'] * v['cols'] * 4 if not v['isMatrix'] else 16 * v['rows']
    if v['arraySize']: size = ((size + 15) & ~15) * (v['arraySize'] - 1) + size
    return size

def pad_cbs(cbs):
    """Insert padding vars so the declared layout matches the original D3D11 layout (keeps offsets valid)."""
    out = []
    for cb in cbs:
        if cb['name'] == '':
            out.append(cb); continue
        vals = sorted(cb['vals'], key=lambda v: v['offset']); nv = []; pos = 0; k = 0
        def pad(start, end):
            nonlocal k
            while start < end:
                if start % 16 == 0 and end - start >= 16:
                    n = (end - start) // 16; nv.append(dict(name='unity_retarget_pad%d' % k, type=0, rows=1, cols=4, isMatrix=0, arraySize=n if n > 1 else 0, offset=start)); start += 16 * n
                else:
                    c = min(4, (end - start) // 4, (16 - start % 16) // 4)
                    if c <= 0: break
                    nv.append(dict(name='unity_retarget_pad%d' % k, type=0, rows=1, cols=c, isMatrix=0, arraySize=0, offset=start)); start += 4 * c
                k += 1
        for v in vals:
            if v['offset'] > pos: pad(pos, v['offset'])
            nv.append(v); pos = v['offset'] + var_size(v)
        if cb['size'] > pos: pad(pos, cb['size'])
        out.append(dict(cb, vals=nv))
    return out

def build_rdef(refl, major, minor, progtype, pad=False):
    if pad: refl = dict(refl, cbs=pad_cbs(refl['cbs']))
    """refl: dict(cbs=[{name,size,vals:[{name,type,rows,cols,isMatrix,arraySize,offset}]}], cbbinds=[{name,index}], texs=[{name,index,sampler,dim,ms}], samplers=[{name,index}])"""
    strings = bytearray(); sidx = {}
    def S(s):
        if s not in sidx:
            sidx[s] = len(strings); strings.extend(s.encode('utf8') + b'\0')
        return sidx[s]
    cbs = [cb for cb in refl['cbs'] if cb['name'] != '']
    res = []
    for b in refl['cbbinds']:
        res.append((b['name'], 0, 0, 0, 0, b['index'], 1, 0))
    for s in refl['samplers']:
        res.append((s['name'], 3, 0, 0, 0, s['index'], 1, 0))
    for t in refl['texs']:
        dim = 6 if t['ms'] else DIM[t['dim']]
        res.append((t['name'], 2, 5, dim, 0xFFFFFFFF, t['index'], 1, 0x0c))
    varsz = 40 if major >= 5 else 24
    hdr = 28; resoff = hdr; cboff = resoff + 32 * len(res)
    varoff = cboff + 24 * len(cbs); nvars = sum(len(cb['vals']) for cb in cbs)
    typeoff = varoff + varsz * nvars; ntypes = nvars
    stroff = typeoff + 16 * ntypes
    # strings must be placed after everything: compute names first
    for cb in cbs:
        S(cb['name'])
        for v in cb['vals']: S(v['name'])
    for r in res: S(r[0])
    S('unity-retarget')
    out = bytearray()
    out += struct.pack('<IIIIBBHII', len(cbs), cboff if cbs else 0, len(res), resoff, minor, major, progtype, 0, stroff + S('unity-retarget'))
    for r in res:
        name, typ, ret, dim, ns, bp, bc, fl = r
        out += struct.pack('<8I', stroff + S(name), typ, ret, dim, ns, bp, bc, fl)
    vi = 0
    for cb in cbs:
        out += struct.pack('<6I', stroff + S(cb['name']), len(cb['vals']), varoff + varsz * vi, cb['size'], 0, 0)
        vi += len(cb['vals'])
    vi = 0
    for cb in cbs:
        for v in cb['vals']:
            elems = v['arraySize'] or 0
            size = var_size(v)
            rec = struct.pack('<6I', stroff + S(v['name']), v['offset'], size, 2, typeoff + 16 * vi, 0)
            if major >= 5: rec += struct.pack('<4I', 0xFFFFFFFF, 0, 0xFFFFFFFF, 0)
            out += rec; vi += 1
    for cb in cbs:
        for v in cb['vals']:
            if v['isMatrix']: cls = 3
            elif v['cols'] > 1: cls = 1
            else: cls = 0
            out += struct.pack('<6HI', cls, SVT[v['type']], v['rows'], v['cols'], v['arraySize'] or 0, 0, 0)
    assert len(out) == stroff, (len(out), stroff)
    out += strings
    while len(out) % 4: out += b'\0'
    return bytes(out)

def inject_rdef(dxbc, rdef):
    n = struct.unpack_from('<I', dxbc, 28)[0]
    offs = struct.unpack_from(f'<{n}I', dxbc, 32)
    chunks = []
    for o in offs:
        sz = struct.unpack_from('<I', dxbc, o + 4)[0]; chunks.append(dxbc[o:o + 8 + sz])
    chunks = [c for c in chunks if c[:4] != b'RDEF']
    chunks.insert(0, b'RDEF' + struct.pack('<I', len(rdef)) + rdef)
    m = len(chunks); table = 32 + 4 * m; body = b''; offsets = []
    for c in chunks:
        offsets.append(table + len(body)); body += c
    total = table + len(body)
    return b'DXBC' + b'\0' * 16 + struct.pack('<III', 1, total, m) + struct.pack(f'<{m}I', *offsets) + body

def refl_from_tail(tail):
    """Convert a parsed Unity tail (unityblob.parse_tail) into refl for build_rdef."""
    texs = [b for b in tail['binds'] if b['kind'] == 'tex']
    cbbinds = [dict(name=b['name'], index=b['index']) for b in tail['binds'] if b['kind'] == 'cb']
    samplers = {}
    for t in texs:
        if t['sampler'] >= 0 and t['sampler'] not in samplers: samplers[t['sampler']] = dict(name=t['name'], index=t['sampler'])
    return dict(cbs=tail['cbs'], cbbinds=cbbinds, texs=texs, samplers=list(samplers.values()))
