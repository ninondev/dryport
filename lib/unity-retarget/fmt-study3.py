# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
import UnityPy, lz4.block, struct, sys, re
path=sys.argv[1]; want='Hidden/Internal-Colored'
env=UnityPy.load(path)
def tok(t, limit=40):
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
    blob=bytes(tt['compressedBlob'])
    for pi,plat in enumerate(tt['platforms']):
        if plat not in (4,14,15): continue
        data=b''.join(lz4.block.decompress(blob[a:a+c],uncompressed_size=d) for a,c,d in zip(tt['offsets'][pi],tt['compressedLengths'][pi],tt['decompressedLengths'][pi]))
        cnt=struct.unpack_from('<i',data,0)[0]
        for i in range(cnt):
            off,ln,z=struct.unpack_from('<3i',data,4+i*12); e=data[off:off+ln]
            ver,typ=struct.unpack_from('<2i',e,0)
            if typ<15 and typ not in (6,7,8) and plat!=15 or (plat==15 and i<7 and typ not in (6,7,8)):
                if i in (0,7): print(f'plat{plat} PARAM e{i}: {tok(e,60)}')
                continue
            # code entry
            p=8; stats=struct.unpack_from('<4i',e,p); p+=16; nk=struct.unpack_from('<i',e,p)[0]; p+=4; kws=[]
            for _ in range(nk):
                n=struct.unpack_from('<i',e,p)[0]; kws.append(e[p+4:p+4+n].decode()); p+=4+((n+3)&~3)
            cl=struct.unpack_from('<i',e,p)[0]; p+=4; code=e[p:p+cl]; p+=cl; p=(p+3)&~3; tail=e[p:]
            if i in (3,8,0,1):
                print(f'plat{plat} CODE e{i}: type {typ} stats {stats} kw {kws} codelen {cl} tail[{len(tail)}B]: {tok(tail,20)}')
                if plat==14: print('   metal code head hex:', code[:64].hex(), '| text starts:', code[60:90])
                if plat==4: print('   d3d11 code head hex:', code[:40].hex())
                if plat==15: print('   gl code head:', code[:60])
    break
