# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Exploratory dumper for a Unity 'unity default resources' file of any version: typetree shape + raw blob layout."""
import UnityPy, lz4.block, struct, sys, re, json
path=sys.argv[1]; want=sys.argv[2] if len(sys.argv)>2 else 'Hidden/Internal-Colored'
env=UnityPy.load(path); print('file version', env.file.version)
def tok(t, limit=80):
    out=[];p=0
    while p+4<=len(t) and len(out)<limit:
        v=struct.unpack_from('<i',t,p)[0]
        if 0<v<64 and p+4+v<=len(t) and re.fullmatch(rb'[\x20-\x7e]+', t[p+4:p+4+v]):
            out.append('"'+t[p+4:p+4+v].decode()+'"'); p+=4+((v+3)&~3); continue
        out.append(str(v)); p+=4
    return ' '.join(out)
for o in env.objects:
    if o.type.name!='Shader': continue
    tt=o.read_typetree(); pf=tt['m_ParsedForm']
    if pf['m_Name']!=want: continue
    print('shader keys', list(tt.keys()))
    print('parsedform keys', list(pf.keys()))
    ps=pf['m_SubShaders'][0]['m_Passes'][0]; print('pass keys', list(ps.keys()))
    for k in ('m_NameIndices','m_Platforms','m_ProgramMask','m_GlobalKeywordMask','m_LocalKeywordMask','m_SerializedKeywordStateMask'):
        if k in ps: print(' ',k, json.dumps(ps[k])[:300])
    for st in ('progVertex','progFragment'):
        P=ps[st]; print(st,'keys',list(P.keys()))
        sps=P['m_SubPrograms'] if 'm_SubPrograms' in P else P.get('m_PlayerSubPrograms')
        if isinstance(sps,list) and sps and isinstance(sps[0],list): print('  m_PlayerSubPrograms: per-platform lists', [len(x) for x in sps]); flat=[s for x in sps for s in x]
        else: flat=sps or []
        print('  n',len(flat))
        for s in flat[:3]: print('   ', json.dumps({k:v for k,v in s.items() if k!='m_Parameters'})[:400])
        if 'm_CommonParameters' in P: print('  common', json.dumps(P['m_CommonParameters'])[:300])
    print('platforms', tt['platforms'], 'offsets', tt['offsets'], 'clen', tt['compressedLengths'], 'dlen', tt['decompressedLengths'])
    blob=bytes(tt['compressedBlob'])
    for pi,plat in enumerate(tt['platforms']):
        data=b''.join(lz4.block.decompress(blob[a:a+c],uncompressed_size=d) for a,c,d in zip(tt['offsets'][pi],tt['compressedLengths'][pi],tt['decompressedLengths'][pi]))
        cnt=struct.unpack_from('<i',data,0)[0]; print(f'platform {plat}: dlen {len(data)} count {cnt} entry table head:', struct.unpack_from('<12i',data,4))
        off=struct.unpack_from('<i',data,4)[0]
        print('  sp0 head tokens:', tok(data[off:off+160], 40))
        # find code start heuristically: DXBC or '#include' or 0xF00DCAFE
        for sig in (b'DXBC', b'#include', bytes.fromhex('feca0df0'), b'#version', b'#ifdef'):
            i=data.find(sig, off)
            if 0<=i<off+400: print('  ', sig, 'at +', i-off); break
        # tail after code for sp0: use next entry offset
        nxt=struct.unpack_from('<i',data,4+12)[0] if cnt>1 else len(data)
        print('  sp0 last 200B tokens:', tok(data[max(off,nxt-200):nxt], 60))
    break
