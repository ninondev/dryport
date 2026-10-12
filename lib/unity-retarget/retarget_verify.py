#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Rosetta-stone regression: take a Windows 'unity default resources' (has D3D11 + Metal + GLCore), strip Metal/GLCore,
regenerate them with retarget.process_shader, and compare our Metal source against Unity's own for every subprogram."""
import sys, os, json, difflib, collections, concurrent.futures, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import UnityPy, retarget, unityblob
from retarget import PLAT_D3D11, PLAT_METAL, PLAT_GLCORE, T_METAL_VS, T_METAL_FS, T_GLCORE32, V2021, decompress_platform
STRIP = {PLAT_METAL, PLAT_GLCORE}; STRIP_TYPES = {T_METAL_VS, T_METAL_FS, T_GLCORE32, 7, 8}

def strip(tt):
    keep = [i for i, p in enumerate(tt['platforms']) if p not in STRIP]
    blobs = {p: decompress_platform(tt, i) for i, p in enumerate(tt['platforms'])}
    import lz4.block
    nb = bytearray(); plats, offs, cls, dls, sc = [], [], [], [], []; listy = isinstance(tt['offsets'][0], list)
    for i in keep:
        p = tt['platforms'][i]; raw = blobs[p]; comp = lz4.block.compress(raw, store_size=False)
        plats.append(p); offs.append([len(nb)] if listy else len(nb)); cls.append([len(comp)] if listy else len(comp)); dls.append([len(raw)] if listy else len(raw)); nb.extend(comp)
        if tt.get('stageCounts'): sc.append(tt['stageCounts'][i])
    tt['platforms'], tt['offsets'], tt['compressedLengths'], tt['decompressedLengths'], tt['compressedBlob'] = plats, offs, cls, dls, bytes(nb)
    if tt.get('stageCounts'): tt['stageCounts'] = sc
    for ss in tt['m_ParsedForm']['m_SubShaders']:
        for ps in ss['m_Passes']:
            if 'm_Platforms' in ps: ps['m_Platforms'] = [p for p in ps['m_Platforms'] if p not in STRIP]
            for st in ('progVertex', 'progFragment', 'progGeometry', 'progHull', 'progDomain'):
                P = ps[st]
                if P.get('m_SubPrograms'): P['m_SubPrograms'] = [e for e in P['m_SubPrograms'] if e['m_GpuProgramType'] not in STRIP_TYPES]
                if P.get('m_PlayerSubPrograms'):
                    for tier, lst in enumerate(P['m_PlayerSubPrograms']):
                        keepi = [i for i, e in enumerate(lst) if e['m_GpuProgramType'] not in STRIP_TYPES]
                        P['m_PlayerSubPrograms'][tier] = [lst[i] for i in keepi]
                        P['m_ParameterBlobIndices'][tier] = [P['m_ParameterBlobIndices'][tier][i] for i in keepi]
    return blobs

def metal_texts(tt, data):
    """map (type, keywords) -> (msl text, params dict or None) from a Metal platform blob"""
    out = {}; es = unityblob.parse_blob(data)
    if es[0]['version'] >= V2021:
        # find param index per code entry via typetree
        for ss in tt['m_ParsedForm']['m_SubShaders']:
            for ps in ss['m_Passes']:
                for st in ('progVertex', 'progFragment'):
                    P = ps[st]
                    for tier, lst in enumerate(P['m_PlayerSubPrograms']):
                        for i, e in enumerate(lst):
                            if e['m_GpuProgramType'] in (T_METAL_VS, T_METAL_FS):
                                c = es[e['m_BlobIndex']]; pr = es[P['m_ParameterBlobIndices'][tier][i]]['params']
                                out[(c['type'], tuple(c['keywords']))] = (c['code'][c['code'].find(b'xlatMtlMain\0') + 12:].decode('utf8', 'replace'), pr, c['sourceMap'], c['channels'])
    else:
        for c in es:
            if c['type'] in (T_METAL_VS, T_METAL_FS):
                t = unityblob.parse_tail(c['tail'], c['version'])
                out[(c['type'], tuple(c['keywords']))] = (c['code'][c['code'].find(b'xlatMtlMain\0') + 12:].decode('utf8', 'replace'), dict(cbs=t['cbs'], binds=t['binds']), t['sourceMap'], t['channels'])
    return out

def main():
    path = sys.argv[1]; env = UnityPy.load(path); pool = concurrent.futures.ThreadPoolExecutor(8); retarget.set_unity_version(env.file.version); print('unity', env.file.version, 'legacy channels', retarget.LEGACY_CHANNELS)
    hdr = retarget.metal_header_from(path); print('metal header', hdr.hex()[:24], len(hdr))
    tot = same = 0; shaders = 0; diffs = collections.Counter(); samples = []; chan_ok = chan_tot = 0; prm_same = prm_tot = 0
    for obj in env.objects:
        if obj.type.name != 'Shader': continue
        tt = obj.read_typetree(); name = tt['m_ParsedForm']['m_Name']
        if PLAT_METAL not in tt['platforms'] or PLAT_D3D11 not in tt['platforms']: continue
        ref = metal_texts(tt, decompress_platform(tt, tt['platforms'].index(PLAT_METAL)))
        tt2 = copy.deepcopy(tt); strip(tt2)
        ok = retarget.process_shader(tt2, True, True, pool, hdr)
        if not ok: print('NOT PROCESSED', name, dict(retarget.log)); continue
        shaders += 1
        ours = metal_texts(tt2, decompress_platform(tt2, tt2['platforms'].index(PLAT_METAL)))
        for k, (rtext, rprm, rsm, rch) in ref.items():
            tot += 1
            if k not in ours: diffs['missing'] += 1; continue
            otext, oprm, osm, och = ours[k]
            a = [l.rstrip() for l in rtext.splitlines() if l.strip()]; b = [l.rstrip() for l in otext.splitlines() if l.strip()]
            if a == b: same += 1
            else:
                diffs['text'] += 1
                if len(samples) < 3: samples.append((name, k, list(difflib.unified_diff(a, b, 'unity', 'ours', lineterm='', n=0))[:14]))
            if k[0] == T_METAL_VS:
                chan_tot += 1
                if (rsm, sorted(rch)) == (osm, sorted(och)): chan_ok += 1
                elif len(samples) < 6: samples.append((name, k, ['channels unity', str((rsm, rch)), 'ours', str((osm, och))]))
            # param comparison: same CB names, sizes, and var offsets?
            def norm(p): return sorted((c['name'], c['size'], tuple(sorted((v['name'], v['offset'], v['type']) for v in c['vals']))) for c in p['cbs']), sorted((b['kind'], b['name'], b['index'], b.get('sampler', -1)) for b in p['binds'])
            prm_tot += 1
            if norm(rprm) == norm(oprm): prm_same += 1
            elif len(samples) < 9: samples.append((name, k, ['params unity', json.dumps(norm(rprm))[:300], 'ours', json.dumps(norm(oprm))[:300]]))
    print(f'shaders {shaders}; metal subprograms {tot}: text identical {same}, diffs {dict(diffs)}; VS channels identical {chan_ok}/{chan_tot}; params identical {prm_same}/{prm_tot}; log {dict(retarget.log)}; errors {len(retarget.errors)}')
    for s in samples: print('---', s[0], s[1]); print('\n'.join(s[2]))
    for e in retarget.errors[:5]: print('ERR', e)
if __name__ == '__main__': main()
