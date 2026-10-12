# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
import UnityPy, lz4.block, struct, sys, re, json
path=sys.argv[1]; want=sys.argv[2] if len(sys.argv)>2 else 'Hidden/Internal-Colored'; plats=[int(x) for x in (sys.argv[3] if len(sys.argv)>3 else '4,14').split(',')]
env=UnityPy.load(path)
def tok(t, limit=30):
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
    print('stageCounts', tt['stageCounts'], 'm_KeywordNames', pf['m_KeywordNames'], 'm_KeywordFlags', pf['m_KeywordFlags'])
    ps=pf['m_SubShaders'][0]['m_Passes'][0]
    for st in ('progVertex','progFragment'):
        P=ps[st]; print('##',st,'m_ParameterBlobIndices',P['m_ParameterBlobIndices'],'kwmask',P['m_SerializedKeywordStateMask'])
        psp=P['m_PlayerSubPrograms']; print('  m_PlayerSubPrograms type',type(psp).__name__,'len',len(psp))
        for i,x in enumerate(psp):
            if isinstance(x,list): print('   plat-list',i,'n',len(x), json.dumps(x[:2])[:500])
            else: print('   entry',i,json.dumps(x)[:400])
    blob=bytes(tt['compressedBlob'])
    for pi,plat in enumerate(tt['platforms']):
        if plat not in plats: continue
        data=b''.join(lz4.block.decompress(blob[a:a+c],uncompressed_size=d) for a,c,d in zip(tt['offsets'][pi],tt['compressedLengths'][pi],tt['decompressedLengths'][pi]))
        cnt=struct.unpack_from('<i',data,0)[0]; print(f'== platform {plat}: {cnt} entries')
        for i in range(cnt):
            off,ln,z=struct.unpack_from('<3i',data,4+i*12); e=data[off:off+ln]
            sig=''
            for s in (b'DXBC', bytes.fromhex('feca0df0'), b'#version', b'#ifdef', b'#include'):
                j=e.find(s)
                if j>=0: sig=f'{s} @+{j}'; break
            print(f'  e{i} off {off} len {ln} | {tok(e[:96],16)} | {sig}')
            if sig and i<3: print('      head hex:', e[:min(48, e.find(b'DXBC') if b'DXBC' in e else 48)].hex())
    break
