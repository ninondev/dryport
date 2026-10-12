# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Unity shader GPU-program blob read/write, from Unity 5.6 through 2021.2+ layouts."""
import struct

V56, V2017, V2018, V2019, V2021 = 201609010, 201708220, 201802150, 201806140, 202012090

def _rint(b, p): return struct.unpack_from('<i', b, p)[0], p + 4
def _rstr(b, p):
    n, p = _rint(b, p); s = b[p:p + n].decode('utf8'); p += n; p = (p + 3) & ~3; return s, p
def _wint(v): return struct.pack('<i', v)
def _wstr(s):
    e = s.encode('utf8'); return _wint(len(e)) + e + b'\0' * ((-len(e)) & 3)

# ---- 参数块：按 blob 版本读取常量缓冲区与纹理绑定 ----
def parse_params(t, p=0, version=V2019):
    n, p = _rint(t, p); cbs = []
    for _ in range(n):
        name, p = _rstr(t, p); size, p = _rint(t, p); nv, p = _rint(t, p); vals = []
        for _ in range(nv):
            vn, p = _rstr(t, p); typ, p = _rint(t, p); rows, p = _rint(t, p); cols, p = _rint(t, p)
            ism, p = _rint(t, p); asz, p = _rint(t, p); off, p = _rint(t, p)
            vals.append(dict(name=vn, type=typ, rows=rows, cols=cols, isMatrix=ism, arraySize=asz, offset=off))
        # 5.6 / 2017.1-2 没有结构体数量字段；读它会把下个缓冲区的名字长度当数量。
        if version >= V2017:
            ns, p = _rint(t, p)
            if ns: raise NotImplementedError('struct params in blob')
        cbs.append(dict(name=name, size=size, vals=vals))
    n, p = _rint(t, p); binds = []
    for _ in range(n):
        name, p = _rstr(t, p); kind, p = _rint(t, p)
        if kind == 0:
            if version < V2017:   # 5.6: 维度低 8 位，采样器索引在高位（可为 -1）。
                idx, p = _rint(t, p); dm, p = _rint(t, p)
                binds.append(dict(kind='tex', name=name, index=idx, sampler=dm >> 8, dim=dm & 0xff, ms=0))
            elif version < V2018:   # 2017.3+: index, dim (unpacked), sampler index
                idx, p = _rint(t, p); dm, p = _rint(t, p); smp, p = _rint(t, p)
                binds.append(dict(kind='tex', name=name, index=idx, sampler=smp, dim=dm, ms=0))
            else:                 # 2018.2+: index, sampler index, (dim << 1) | multisampled
                idx, p = _rint(t, p); smp, p = _rint(t, p); dm, p = _rint(t, p)
                binds.append(dict(kind='tex', name=name, index=idx, sampler=smp, dim=dm >> 1, ms=dm & 1))
        elif kind == 1:
            idx, p = _rint(t, p); x, p = _rint(t, p)
            binds.append(dict(kind='cb', name=name, index=idx, x=x))
        else:
            raise NotImplementedError(f'binding kind {kind} for {name}')
    return dict(cbs=cbs, binds=binds), p

def build_params(r, version=V2019):
    o = _wint(len(r['cbs']))
    for cb in r['cbs']:
        o += _wstr(cb['name']) + _wint(cb['size']) + _wint(len(cb['vals']))
        for v in cb['vals']:
            o += _wstr(v['name']) + b''.join(_wint(v[k]) for k in ('type', 'rows', 'cols', 'isMatrix', 'arraySize', 'offset'))
        if version >= V2017: o += _wint(0)
    o += _wint(len(r['binds']))
    for b in r['binds']:
        if b['kind'] == 'tex':
            if version < V2017: o += _wstr(b['name']) + _wint(0) + _wint(b['index']) + _wint((b['sampler'] << 8) | b['dim'])
            elif version < V2018: o += _wstr(b['name']) + _wint(0) + _wint(b['index']) + _wint(b['dim']) + _wint(b['sampler'])
            else: o += _wstr(b['name']) + _wint(0) + _wint(b['index']) + _wint(b['sampler']) + _wint((b['dim'] << 1) | b['ms'])
        elif b['kind'] == 'cb':
            o += _wstr(b['name']) + _wint(1) + _wint(b['index']) + _wint(b.get('x', 0))
        else: raise NotImplementedError(b['kind'])
    return o

def parse_channels(t, p=0):
    smap, p = _rint(t, p); n, p = _rint(t, p); ch = []
    for _ in range(n):
        s, p = _rint(t, p); d, p = _rint(t, p); ch.append((s, d))
    return smap, ch, p

def build_channels(smap, ch):
    return _wint(smap) + _wint(len(ch)) + b''.join(_wint(s) + _wint(d) for s, d in ch)

# ---- v2019 tail = channels + params ----
def parse_tail(t, version=V2019):
    smap, ch, p = parse_channels(t, 0); r, p = parse_params(t, p, version)
    if p != len(t): raise ValueError(f'tail not fully consumed {p}/{len(t)}')
    return dict(sourceMap=smap, channels=ch, cbs=r['cbs'], binds=r['binds'])

def build_tail(r, version=V2019):
    return build_channels(r['sourceMap'], r['channels']) + build_params(r, version)

# ---- entries ----
def blob_version(data):
    off = struct.unpack_from('<i', data, 4)[0]
    return struct.unpack_from('<i', data, off)[0]

def _parse_code_header(e, version):
    p = 0
    ver, p = _rint(e, p); typ, p = _rint(e, p)
    stats = list(struct.unpack_from('<4i', e, p)); p += 16
    nk, p = _rint(e, p); kws = []
    for _ in range(nk): s, p = _rstr(e, p); kws.append(s)
    lk = []
    if V2019 <= ver < V2021:
        nl, p = _rint(e, p)
        for _ in range(nl): s, p = _rstr(e, p); lk.append(s)
    cl, p = _rint(e, p); code = e[p:p + cl]; p += cl; p = (p + 3) & ~3
    return dict(version=ver, type=typ, stats=stats, keywords=kws, localKeywords=lk, code=code), p

def entry_size(data):
    """Entry table stride: 8 bytes (offset,length) before Unity 2019.3, 12 bytes after."""
    cnt = struct.unpack_from('<i', data, 0)[0]
    if cnt == 0: return 12
    first = struct.unpack_from('<i', data, 4)[0]
    return 12 if first == 4 + 12 * cnt else 8

class EntryList(list):
    entry_size = 12

def parse_blob(data):
    """v2019: list of subprogram dicts (with 'tail' bytes). v2021: list of entries: {'kind': 'code', ..., 'sourceMap', 'channels'} or {'kind': 'param', 'params': {...}}."""
    cnt, _ = _rint(data, 0); out = EntryList(); es = entry_size(data); out.entry_size = es
    for i in range(cnt):
        off, ln = struct.unpack_from('<2i', data, 4 + i * es)
        nxt = struct.unpack_from('<i', data, 4 + (i + 1) * es)[0] if i + 1 < cnt else len(data)
        e = data[off:off + ln] if ln > 0 else data[off:nxt]   # entries may be stored out of table order (2018.x)
        ver = struct.unpack_from('<i', e, 0)[0]
        if ver < V2021:
            sp, p = _parse_code_header(e, ver); sp['tail'] = e[p:]; sp['kind'] = 'code'; out.append(sp)
        else:
            typ = struct.unpack_from('<i', e, 4)[0]
            # param entries: the int after version is the constant-buffer count (small) and no program type; disambiguate by trying to parse as params
            if _looks_like_param_entry(e):
                r, p = parse_params(e, 4, ver); out.append(dict(kind='param', version=ver, params=r, raw=e))
            else:
                sp, p = _parse_code_header(e, ver); smap, ch, p2 = parse_channels(e, p)
                if p2 != len(e): raise ValueError(f'code entry {i}: trailing {len(e)-p2} bytes')
                sp.update(kind='code', sourceMap=smap, channels=ch); out.append(sp)
    return out

def _looks_like_param_entry(e):
    try:
        r, p = parse_params(e, 4)
        return p == len(e) and (len(e) < 8 + 16 or r['cbs'][0]['name'] == '')
    except Exception:
        return False

def build_blob(entries, entry_size_=None):
    es = entry_size_ or getattr(entries, 'entry_size', 12)
    bodies = []
    for sp in entries:
        if sp.get('kind') == 'param':
            b = _wint(sp['version']) + build_params(sp['params'], sp['version'])
        else:
            b = _wint(sp['version']) + _wint(sp['type']) + struct.pack('<4i', *sp['stats'])
            b += _wint(len(sp['keywords'])) + b''.join(_wstr(k) for k in sp['keywords'])
            if V2019 <= sp['version'] < V2021:
                b += _wint(len(sp['localKeywords'])) + b''.join(_wstr(k) for k in sp['localKeywords'])
            b += _wint(len(sp['code'])) + sp['code'] + b'\0' * ((-len(sp['code'])) & 3)
            if sp['version'] < V2021: b += sp['tail']
            else: b += build_channels(sp['sourceMap'], sp['channels'])
        bodies.append(b)
    hdr = _wint(len(entries)); off = 4 + es * len(entries); table = b''
    for b in bodies:
        table += _wint(off) + _wint(len(b)) + (_wint(0) if es == 12 else b''); off += len(b)
    return hdr + table + b''.join(bodies)

if __name__ == '__main__':
    import sys
    d = open(sys.argv[1], 'rb').read(); sps = parse_blob(d)
    rb = build_blob(sps); print('roundtrip identical:', rb == d, len(d), len(rb), 'version', blob_version(d))
    for i, sp in enumerate(sps[:6]):
        if 'tail' in sp: sp['tail_parsed'] = parse_tail(sp['tail'], sp['version'])
        if sp['kind'] == 'param': print(i, 'PARAM', [(c['name'], c['size'], len(c['vals'])) for c in sp['params']['cbs']], sp['params']['binds'])
        else: print(i, 'CODE', sp['type'], sp['keywords'], sp.get('sourceMap'), sp.get('channels'), 'code', len(sp['code']))
