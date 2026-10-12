# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
import UnityPy, sys, os, glob, collections, struct, lz4.block
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import unityblob
root=sys.argv[1]
files=[f for f in glob.glob(os.path.join(root,'*'))+glob.glob(os.path.join(root,'Resources','unity_builtin_extra')) if os.path.isfile(f) and not f.endswith(('.resS','.resource','.json','.config','.info','.bank','.txt','.pdb'))]
types=collections.Counter(); nsh=0; npass=0; nsp=0; plats=collections.Counter(); kinds=collections.Counter(); names=[]; perfile=collections.Counter(); sm=collections.Counter(); tailerr=0; maxk=0
for f in files:
    try: env=UnityPy.load(f)
    except Exception as e: print('ERR',f,e); continue
    for obj in env.objects:
        if obj.type.name!='Shader': continue
        tt=obj.read_typetree(); pf=tt['m_ParsedForm']; nsh+=1; names.append(pf['m_Name']); perfile[os.path.basename(f)]+=1
        for p in tt['platforms']: plats[p]+=1
        blob=bytes(tt['compressedBlob'])
        for pi,plat in enumerate(tt['platforms']):
            data=b''.join(lz4.block.decompress(blob[o:o+c],uncompressed_size=d) for o,c,d in zip(tt['offsets'][pi],tt['compressedLengths'][pi],tt['decompressedLengths'][pi]))
            for sp in unityblob.parse_blob(data):
                nsp+=1; types[sp['type']]+=1; maxk=max(maxk,len(sp['keywords'])+len(sp['localKeywords']))
                try:
                    t=unityblob.parse_tail(sp['tail'])
                    for b in t['binds']: kinds[b['kind']]+=1
                except Exception as e: tailerr+=1; print('TAILERR',pf['m_Name'],sp['type'],e)
                if sp['code'][38:42]==b'DXBC':
                    v=struct.unpack_from('<I',sp['code'],38+struct.unpack_from('<I',sp['code'],38+32+4*0)[0]+8)[0] if False else None
        for ss in pf['m_SubShaders']:
            for ps in ss['m_Passes']:
                npass+=1
                if ps['progGeometry']['m_SubPrograms'] or ps['progHull']['m_SubPrograms']: print('GEOM/HULL in',pf['m_Name'])
print('files',len(files),'shaders',nsh,'passes',npass,'subprograms',nsp,'platforms',dict(plats),'types',dict(types),'binds',dict(kinds),'tailerr',tailerr,'maxkw',maxk)
print('perfile',dict(perfile))
print('names',sorted(set(names)))
