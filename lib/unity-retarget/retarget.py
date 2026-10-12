#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Retarget a built Unity game's D3D11-only shaders to Metal (and GLCore), including Unity 5.6 blobs.
Usage: retarget.py <Data dir> <out Data dir> [--files a,b] [--no-metal] [--no-gl] [--jobs N] [--no-shader-cache]
"""
import sys, os, re, json, glob, struct, subprocess, tempfile, collections, concurrent.futures, time
import lz4.block, UnityPy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unityblob, rdefsynth
from unityblob import V2019, V2021

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from paths import SN_HOME, SN_RUNTIMES
from shader_cache import ShaderCache
CLI = str(SN_RUNTIMES / 'unity-retarget/hlslcc-cli')
CACHE_DIR = (SN_RUNTIMES / 'unity-retarget/shader-cache').resolve()
# 运行时路径若被误设到源码内，仍可编译，但不把游戏派生数据写进仓库。
HLSLCC_CACHE = ShaderCache(CACHE_DIR, enabled=(os.environ.get('SN_SHADER_CACHE', '1') != '0'
                                            and SN_HOME.resolve() not in (CACHE_DIR, *CACHE_DIR.parents)))
GL_FLAGS = '20888'      # INOUT_SEMANTIC_NAMES|GLOBAL_CONSTS_NEVER_IN_UBO|DISABLE_GLOBALS_STRUCT|TRANSLATE_MATRICES
MTL_FLAGS = 'a0080'     # INOUT_SEMANTIC_NAMES|TRANSLATE_MATRICES|METAL_SHADOW_SAMPLER_LINEAR
PLAT_D3D11, PLAT_METAL, PLAT_GLCORE = 4, 14, 15
T_DX11_VS = (15, 16); T_DX11_PS = (17, 18); T_DX11 = T_DX11_VS + T_DX11_PS
T_GLCORE32, T_METAL_VS, T_METAL_FS = 6, 23, 24
METAL_HDR_2020 = bytes.fromhex('feca0df02c00000004000000380000000000000000000000000000000000000000000000050000000f000000') + b'xlatMtlMain\0'
CHANNEL = {'POSITION': 0, 'NORMAL': 1, 'TANGENT': 2, 'COLOR': 3, 'BLENDWEIGHT': 12, 'BLENDINDICES': 13}   # ShaderChannel, Unity 2018.1+
CHANNEL_2017 = {'POSITION': 0, 'NORMAL': 1, 'COLOR': 2, 'TANGENT': 7}                                     # ShaderChannel, Unity 5.x-2017.x (TEXCOORDn = 3+n, n<4)
LEGACY_CHANNELS = False
METAL_ATTR_BASE = 13   # VertexComponent kVertexCompAttrib0: 12 on Unity <= 2017.x, 13 on 2018.1+
TEXDIM = {2: 2, 3: 3, 4: 4, 5: 2, 6: 5, 7: 6}
MSL_SZ = {'float': (4, 4), 'int': (4, 4), 'uint': (4, 4), 'bool': (1, 1), 'half': (2, 2), 'short': (2, 2), 'ushort': (2, 2)}
EMPTY_PARAMS = {"m_VectorParams": [], "m_MatrixParams": [], "m_TextureParams": [], "m_BufferParams": [], "m_ConstantBuffers": [], "m_ConstantBufferBindings": [], "m_UAVParams": [], "m_Samplers": []}
log = collections.Counter(); errors = []

def metal_header_from(path):
    """Take the Metal program header template (up to and including 'xlatMtlMain\\0') from a reference file that has Metal programs."""
    try:
        env = UnityPy.load(path)
        for obj in env.objects:
            if obj.type.name != 'Shader': continue
            tt = obj.read_typetree()
            if PLAT_METAL not in tt['platforms']: continue
            data = decompress_platform(tt, tt['platforms'].index(PLAT_METAL))
            for e in unityblob.parse_blob(data):
                if e['kind'] == 'code' and e['type'] in (T_METAL_VS, T_METAL_FS):
                    i = e['code'].find(b'xlatMtlMain\0')
                    if i > 0: return e['code'][:i + 12]
    except Exception as ex:
        print('metal header template: fallback to 2020 layout:', ex)
    return None

def _lst(x): return x if isinstance(x, list) else [x]

def decompress_platform(tt, pi):
    blob = bytes(tt['compressedBlob'])
    return b''.join(lz4.block.decompress(blob[o:o + c], uncompressed_size=d) for o, c, d in zip(_lst(tt['offsets'][pi]), _lst(tt['compressedLengths'][pi]), _lst(tt['decompressedLengths'][pi])))

def entry_params(e):
    """SerializedSubProgram parameter block: wrapped in m_Parameters (2019.1-2021.1) or inline on the entry (<=2018.4 and 2019.x player builds)."""
    return e['m_Parameters'] if 'm_Parameters' in e else e

def clear_params(e):
    tgt = entry_params(e)
    for k in EMPTY_PARAMS: tgt[k] = []

def semantic_channel(name):
    m = re.fullmatch(r'(?:in_)?([A-Za-z_]+?)(\d*)', name)
    if not m: return None
    sem, idx = m.group(1), int(m.group(2) or 0)
    if LEGACY_CHANNELS:
        if sem == 'TEXCOORD': return 3 + idx if idx < 4 else None
        return CHANNEL_2017.get(sem)
    if sem == 'TEXCOORD': return 4 + idx if idx < 8 else None
    return CHANNEL.get(sem)

def set_unity_version(ver):
    """Select enum tables by Unity version string (e.g. '2017.4.40f1' or '2020.3f1')."""
    global LEGACY_CHANNELS, METAL_ATTR_BASE
    m = re.match(r'(\d+)\.(\d+)', str(ver)); major = int(m.group(1)) if m else 2020
    LEGACY_CHANNELS = major <= 2017; METAL_ATTR_BASE = 12 if major <= 2017 else 13

def msl_layout(text, cbname):
    m = re.search(r'struct ' + re.escape(cbname) + r'_Type\n\{\n(.*?)\n\};', text, re.S)
    if not m: return None
    off = 0; maxal = 1; out = []
    for line in m.group(1).split('\n'):
        mm = re.match(r'\s*(\w+?)(\d?)\s+(\w+)(?:\[(\d+)\])?;', line)
        if not mm: raise ValueError('bad msl member: ' + line)
        base, nc, name, arr = mm.group(1), int(mm.group(2) or 1), mm.group(3), int(mm.group(4) or 0)
        sz, al = MSL_SZ[base]
        if nc == 2: sz, al = sz * 2, al * 2
        elif nc in (3, 4): sz, al = sz * 4, al * 4
        off = (off + al - 1) // al * al
        out.append((name, off, nc, arr)); off += sz * (arr or 1); maxal = max(maxal, al)
    return out, (off + maxal - 1) // maxal * maxal

def _run_hlslcc_uncached(dxbc, lang, flags):
    with tempfile.NamedTemporaryFile(suffix='.dxbc', delete=False) as f:
        f.write(dxbc); p = f.name
    out = p + '.out'
    try:
        r = subprocess.run([CLI, p, lang, flags, out], capture_output=True, text=True, timeout=60)
        if r.returncode != 0: return None, r.stderr.strip()[:200]
        return open(out, 'rb').read().decode('utf8'), json.loads(r.stdout)['lines']
    finally:
        for q in (p, out):
            try: os.unlink(q)
            except OSError: pass

def run_hlslcc(dxbc, lang, flags):
    return HLSLCC_CACHE.run(dxbc, CLI, lang, flags, lambda: _run_hlslcc_uncached(dxbc, lang, flags))

class Refl:
    def __init__(self):
        self.cbs = collections.OrderedDict(); self.cbbinds = collections.OrderedDict(); self.texs = collections.OrderedDict(); self.samplers = collections.OrderedDict()
    def cb(self, name, size):
        c = self.cbs.setdefault(name, {'size': 0, 'vals': collections.OrderedDict()}); c['size'] = max(c['size'], size); return c
    def add_params(self, r):
        for cb in r['cbs']:
            c = self.cb(cb['name'], cb['size'])
            for v in cb['vals']: c['vals'].setdefault(v['name'], dict(v))
        for b in r['binds']:
            if b['kind'] == 'cb': self.cbbinds.setdefault(b['name'], b['index'])
            else: self.texs.setdefault(b['name'], dict(b))
    def add_refl(self, o):
        for n, c in o.cbs.items():
            rc = self.cb(n, c['size'])
            for vn, v in c['vals'].items(): rc['vals'].setdefault(vn, v)
        for n, i in o.cbbinds.items(): self.cbbinds.setdefault(n, i)
        for n, t in o.texs.items(): self.texs.setdefault(n, t)
        for bp, n in o.samplers.items(): self.samplers.setdefault(bp, n)
    def add_typetree(self, P, names):
        if P['m_BufferParams'] or P['m_UAVParams']: raise NotImplementedError('buffer/UAV params')
        def vec(v): return dict(name=names[v['m_NameIndex']], type=v['m_Type'], rows=1, cols=v['m_Dim'], isMatrix=0, arraySize=v['m_ArraySize'], offset=v['m_Index'])
        def mat(v): return dict(name=names[v['m_NameIndex']], type=v['m_Type'], rows=v['m_RowCount'], cols=4, isMatrix=1, arraySize=v['m_ArraySize'], offset=v['m_Index'])
        root = self.cb('', 0)
        for v in P['m_VectorParams']: root['vals'].setdefault(names[v['m_NameIndex']], vec(v))
        for v in P['m_MatrixParams']: root['vals'].setdefault(names[v['m_NameIndex']], mat(v))
        for cb in P['m_ConstantBuffers']:
            if cb.get('m_StructParams'): raise NotImplementedError('struct params')
            c = self.cb(names[cb['m_NameIndex']], cb['m_Size'])
            for v in cb['m_VectorParams']: c['vals'].setdefault(names[v['m_NameIndex']], vec(v))
            for v in cb['m_MatrixParams']: c['vals'].setdefault(names[v['m_NameIndex']], mat(v))
        for b in P['m_ConstantBufferBindings']: self.cbbinds.setdefault(names[b['m_NameIndex']], b['m_Index'])
        for t in P['m_TextureParams']: self.texs.setdefault(names[t['m_NameIndex']], dict(kind='tex', name=names[t['m_NameIndex']], index=t['m_Index'], sampler=t['m_SamplerIndex'], dim=t['m_Dim'], ms=1 if t.get('m_MultiSampled') else 0))
        for s in P.get('m_Samplers', []): self.samplers.setdefault(s['bindPoint'], 'sampler_inline_%d' % s['bindPoint'])
    def params(self):
        cbs = [dict(name=n, size=c['size'], vals=list(c['vals'].values())) for n, c in self.cbs.items()]
        if not cbs or cbs[0]['name'] != '': cbs.insert(0, dict(name='', size=0, vals=[]))
        binds = [dict(kind='cb', name=n, index=i, x=0) for n, i in self.cbbinds.items()] + [dict(t) for t in self.texs.values()]
        return dict(cbs=cbs, binds=binds)
    def for_rdef(self):
        samp = {}
        for t in self.texs.values():
            if t['sampler'] >= 0: samp.setdefault(t['sampler'], t['name'])
        for bp, n in self.samplers.items(): samp.setdefault(bp, n)
        return dict(cbs=[dict(name=n, size=c['size'], vals=list(c['vals'].values())) for n, c in self.cbs.items()],
                    cbbinds=[dict(name=n, index=i) for n, i in self.cbbinds.items()], texs=list(self.texs.values()),
                    samplers=[dict(name=n, index=i) for i, n in samp.items()])

def translate_sp(code, refl, want_gl, want_mtl):
    i = code.find(b'DXBC', 0, 64)   # Unity prepends a small header: 6 bytes (<=2018.4) or 38 bytes (2019+)
    if i < 0: raise ValueError('no DXBC')
    dxbc = code[i:]
    major, minor, pt = rdefsynth.dxbc_version(dxbc); res = {}
    if want_gl:
        res['gl'] = run_hlslcc(rdefsynth.inject_rdef(dxbc, rdefsynth.build_rdef(refl.for_rdef(), major, minor, pt)), '150', GL_FLAGS)
    if want_mtl:
        res['mtl'] = run_hlslcc(rdefsynth.inject_rdef(dxbc, rdefsynth.build_rdef(refl.for_rdef(), major, minor, pt, pad=True)), 'metal', MTL_FLAGS)
    return res

def metal_params(text, lines, refl, is_vs):
    cbs = [dict(name='', size=0, vals=[])]; binds = []; allvals = {}
    for c in refl.cbs.values():
        for n, v in c['vals'].items(): allvals[n] = v
    for L in lines:
        if L['k'] == 'cb':
            lay = msl_layout(text, L['name'])
            if lay is None: continue
            members, size = lay; vals = []
            for name, off, nc, arr in members:
                if name.startswith('unity_retarget_pad'): continue
                base = name[len('hlslcc_mtx4x4'):] if name.startswith('hlslcc_mtx4x4') else name
                v = allvals.get(base)
                if v is None: raise ValueError('unknown cb member ' + name)
                vals.append(dict(name=base, type=v['type'], rows=v['rows'], cols=v['cols'], isMatrix=v['isMatrix'], arraySize=v['arraySize'], offset=off))
            cbs.append(dict(name=L['name'], size=size, vals=vals))
        elif L['k'] == 'cbbind': binds.append(dict(kind='cb', name=L['name'], index=L['bind'], x=0))
        elif L['k'] == 'tex': binds.append(dict(kind='tex', name=L['name'], index=L['bind'], sampler=L['sampler'], dim=TEXDIM.get(L['dim'], 2), ms=1 if L['ms'] else 0))
    channels = []; smap = 0
    if is_vs:
        for L in lines:
            if L['k'] == 'input':
                ch = semantic_channel(L['name'])
                if ch is None: raise ValueError('unknown input semantic ' + L['name'])
                channels.append((ch, METAL_ATTR_BASE + L['bind'])); smap |= 1 << ch
    return dict(cbs=cbs, binds=binds), smap, channels

def gl_params(vs_refl, fs_refl):
    vals = collections.OrderedDict(); texs = collections.OrderedDict()
    for r in (vs_refl, fs_refl):
        for c in r.cbs.values():
            for n, v in c['vals'].items(): vals.setdefault(n, v)
        for n, t in r.texs.items(): texs.setdefault(n, t)
    ordered = [v for v in vals.values() if v['isMatrix']] + [v for v in vals.values() if not v['isMatrix']]
    out = []; off = 0
    for v in ordered:
        out.append(dict(v, offset=off)); off += (16 * v['rows'] if v['isMatrix'] else 16) * max(1, v['arraySize'])
    return dict(cbs=[dict(name='', size=0, vals=[]), dict(name='$Globals', size=off, vals=out)], binds=[dict(t) for t in texs.values()])

def kwset(sp): return (tuple(sp['keywords']), tuple(sp.get('localKeywords') or []))

def best_pair(d, k):
    if k in d: return d[k]
    ks = set(k[0]) | set(k[1]); cand = [(len(set(kk[0]) | set(kk[1])), kk) for kk in d if (set(kk[0]) | set(kk[1])) <= ks]
    return d[max(cand)[1]] if cand else None

def translate_all(stage_lists, pool, want_gl, want_mtl):
    futs = {}
    for st, lst in stage_lists.items():
        for item in lst: futs[(st, id(item['entry']))] = pool.submit(translate_sp, item['code']['code'], item['refl'], want_gl, want_mtl)
    res = {}
    for k, f in futs.items():
        try: res[k] = f.result()
        except Exception as ex: res[k] = {'err': str(ex)}
    return res

def process_shader(tt, want_gl, want_mtl, pool, mtl_hdr):
    pf = tt['m_ParsedForm']; name = pf['m_Name']
    if PLAT_D3D11 not in tt['platforms']: log['no_d3d11'] += 1; return False
    want_mtl = want_mtl and PLAT_METAL not in tt['platforms']; want_gl = want_gl and PLAT_GLCORE not in tt['platforms']
    if not want_mtl and not want_gl: log['already'] += 1; return False
    d3d = unityblob.parse_blob(decompress_platform(tt, tt['platforms'].index(PLAT_D3D11)))
    if not d3d: log['empty_blob'] += 1; return False   # shader with no compiled programs (e.g. failed/stripped at build time)
    version = d3d[0]['version']; v21 = version >= V2021
    hdr = mtl_hdr or METAL_HDR_2020
    mtl_entries, gl_entries = unityblob.EntryList(), unityblob.EntryList(); mtl_entries.entry_size = gl_entries.entry_size = d3d.entry_size; changed = False
    for ss in pf['m_SubShaders']:
        for ps in ss['m_Passes']:
            if not ps['progVertex'].get('m_SubPrograms') and not (v21 and any(ps['progVertex']['m_PlayerSubPrograms'])): continue
            names = dict((i, n) for n, i in ps['m_NameIndices'])
            if ps['progGeometry']['m_SubPrograms'] or ps['progHull']['m_SubPrograms'] or (v21 and (any(ps['progGeometry']['m_PlayerSubPrograms']) or any(ps['progHull']['m_PlayerSubPrograms']))):
                log['pass_skipped_geom'] += 1; continue
            # ---- collect D3D11 subprograms per stage: items = {entry, tier, idx, code, refl}
            stages = {}; ok = True
            for st in ('progVertex', 'progFragment'):
                P = ps[st]; items = []
                try:
                    common = Refl()
                    if P.get('m_CommonParameters'): common.add_typetree(P['m_CommonParameters'], names)
                    if v21:
                        for tier, lst in enumerate(P['m_PlayerSubPrograms']):
                            for i, e in enumerate(lst):
                                if e['m_GpuProgramType'] not in T_DX11: continue
                                code = d3d[e['m_BlobIndex']]; r = Refl(); r.add_params(d3d[P['m_ParameterBlobIndices'][tier][i]]['params']); r.add_refl(common)
                                items.append(dict(entry=e, tier=tier, idx=i, code=code, refl=r, kws=[pf['m_KeywordNames'][k] for k in e['m_KeywordIndices']]))
                    else:
                        for e in P['m_SubPrograms']:
                            if e['m_GpuProgramType'] not in T_DX11: continue
                            code = d3d[e['m_BlobIndex']]; t = unityblob.parse_tail(code['tail'], code['version']); r = Refl(); r.add_params(t); r.add_refl(common); r.add_typetree(entry_params(e), names)
                            items.append(dict(entry=e, tier=None, idx=None, code=code, refl=r, tail=t))
                except NotImplementedError as ex:
                    errors.append((name, st, str(ex))); ok = False; break
                stages[st] = items
            if not ok: log['pass_skipped'] += 1; continue
            # ---- fold common/typetree params back into per-subprogram blob params, clear typetree copies
            for st, items in stages.items():
                for it in items:
                    if v21:
                        pi_ = ps[st]['m_ParameterBlobIndices'][it['tier']][it['idx']]; d3d[pi_]['params'] = it['refl'].params()
                    else:
                        it['code']['tail'] = unityblob.build_tail(dict(it['refl'].params(), sourceMap=it['tail']['sourceMap'], channels=it['tail']['channels']), it['code']['version'])
                        clear_params(it['entry'])
                if ps[st].get('m_CommonParameters'): ps[st]['m_CommonParameters'] = json.loads(json.dumps(EMPTY_PARAMS))
            changed = True
            results = translate_all(stages, pool, want_gl, want_mtl)
            # ---- Metal
            if want_mtl:
                for st, ptype in (('progVertex', T_METAL_VS), ('progFragment', T_METAL_FS)):
                    for it in stages[st]:
                        res = results[(st, id(it['entry']))].get('mtl')
                        if not res or res[0] is None: log['mtl_fail'] += 1; errors.append((name, st, 'mtl: ' + str(res[1] if res else results[(st, id(it['entry']))].get('err')))); continue
                        text, lines = res
                        try: prm, smap, ch = metal_params(text, lines, it['refl'], st == 'progVertex')
                        except Exception as ex: log['mtl_fail'] += 1; errors.append((name, st, 'mtlparams: ' + str(ex))); continue
                        c = it['code']
                        code_entry = dict(kind='code', version=c['version'], type=ptype, stats=c['stats'], keywords=c['keywords'], localKeywords=c.get('localKeywords', []), code=hdr + text.encode('utf8'))
                        if v21:
                            code_entry.update(sourceMap=smap, channels=ch); mtl_entries.append(code_entry); ci = len(mtl_entries) - 1
                            mtl_entries.append(dict(kind='param', version=c['version'], params=prm)); pi_ = len(mtl_entries) - 1
                            ne = json.loads(json.dumps(it['entry'])); ne['m_BlobIndex'] = ci; ne['m_GpuProgramType'] = ptype
                            ps[st]['m_PlayerSubPrograms'][it['tier']].append(ne); ps[st]['m_ParameterBlobIndices'][it['tier']].append(pi_)
                        else:
                            code_entry['tail'] = unityblob.build_tail(dict(prm, sourceMap=smap, channels=ch), c['version']); mtl_entries.append(code_entry)
                            ne = json.loads(json.dumps(it['entry'])); ne['m_BlobIndex'] = len(mtl_entries) - 1; ne['m_GpuProgramType'] = ptype
                            ne['m_Channels'] = {'m_Channels': [{'source': s, 'target': t} for s, t in ch], 'm_SourceMap': smap}; clear_params(ne)
                            ps[st]['m_SubPrograms'].append(ne)
                        log['mtl_ok'] += 1
            # ---- GLCore: one combined program per keyword set (progVertex only)
            if want_gl:
                vs_by = {kwset(it['code']): it for it in stages['progVertex']}; fs_by = {kwset(it['code']): it for it in stages['progFragment']}
                for k in list(dict.fromkeys(list(fs_by.keys()) + list(vs_by.keys()))):
                    v = best_pair(vs_by, k); f = best_pair(fs_by, k)
                    if v is None or f is None: log['gl_nopair'] += 1; continue
                    rv = results[('progVertex', id(v['entry']))].get('gl'); rf = results[('progFragment', id(f['entry']))].get('gl')
                    if not rv or rv[0] is None or not rf or rf[0] is None: log['gl_fail'] += 1; errors.append((name, 'gl', str((rv and rv[1]) or (rf and rf[1])))); continue
                    code = '#ifdef VERTEX\n' + rv[0] + '\n#endif\n#ifdef FRAGMENT\n' + rf[0] + '\n#endif\n'
                    prm = gl_params(v['refl'], f['refl']); smap = v['code']['sourceMap'] if v21 else v['tail']['sourceMap']
                    twin = v if kwset(v['code']) == k else f; c = v['code']
                    code_entry = dict(kind='code', version=c['version'], type=T_GLCORE32, stats=c['stats'], keywords=list(k[0]), localKeywords=list(k[1]), code=code.encode('utf8'))
                    if v21:
                        code_entry.update(sourceMap=smap, channels=[]); gl_entries.append(code_entry); ci = len(gl_entries) - 1
                        gl_entries.append(dict(kind='param', version=c['version'], params=prm)); pi_ = len(gl_entries) - 1
                        ne = json.loads(json.dumps(twin['entry'])); ne['m_BlobIndex'] = ci; ne['m_GpuProgramType'] = T_GLCORE32
                        ps['progVertex']['m_PlayerSubPrograms'][twin['tier']].append(ne); ps['progVertex']['m_ParameterBlobIndices'][twin['tier']].append(pi_)
                    else:
                        code_entry['tail'] = unityblob.build_tail(dict(prm, sourceMap=smap, channels=[]), c['version']); gl_entries.append(code_entry)
                        ne = json.loads(json.dumps(twin['entry'])); ne['m_BlobIndex'] = len(gl_entries) - 1; ne['m_GpuProgramType'] = T_GLCORE32
                        ne['m_Channels'] = {'m_Channels': [], 'm_SourceMap': smap}; clear_params(ne)
                        ps['progVertex']['m_SubPrograms'].append(ne)
                    log['gl_ok'] += 1
            if 'm_Platforms' in ps:
                plats = set(ps['m_Platforms'])
                if want_gl and gl_entries: plats.add(PLAT_GLCORE)
                if want_mtl and mtl_entries: plats.add(PLAT_METAL)
                ps['m_Platforms'] = sorted(plats)
    if not changed: return False
    newblob = bytearray(); plats = []; offs = []; cls = []; dls = []; stage_counts = []
    old_sc = list(tt.get('stageCounts') or []); listy = isinstance(tt['offsets'][0], list)
    def add(plat, entries, sc):
        raw = unityblob.build_blob(entries); comp = lz4.block.compress(raw, store_size=False)
        plats.append(plat); offs.append([len(newblob)] if listy else len(newblob)); cls.append([len(comp)] if listy else len(comp)); dls.append([len(raw)] if listy else len(raw)); stage_counts.append(sc); newblob.extend(comp)
    for i, p in enumerate(tt['platforms']):
        add(p, d3d if p == PLAT_D3D11 else unityblob.parse_blob(decompress_platform(tt, i)), old_sc[i] if old_sc else 2)
    if want_mtl and mtl_entries: add(PLAT_METAL, mtl_entries, 2)
    if want_gl and gl_entries: add(PLAT_GLCORE, gl_entries, 1)
    tt['platforms'] = plats; tt['offsets'] = offs; tt['compressedLengths'] = cls; tt['decompressedLengths'] = dls; tt['compressedBlob'] = bytes(newblob)
    if 'stageCounts' in tt: tt['stageCounts'] = stage_counts
    return True

def patch_buildsettings(src, dst, want_gl, want_mtl):
    env = UnityPy.load(src); done = False
    for obj in env.objects:
        if obj.type.name == 'BuildSettings':
            tt = obj.read_typetree(); apis = list(tt['m_GraphicsAPIs'])
            new = ([16] if want_mtl else []) + ([17] if want_gl else []) + [a for a in apis if a not in (16, 17)]
            if new != apis: tt['m_GraphicsAPIs'] = new; obj.save_typetree(tt); done = True
            print('BuildSettings m_GraphicsAPIs', apis, '->', new)
    if done: os.makedirs(os.path.dirname(dst), exist_ok=True); open(dst, 'wb').write(env.file.save())
    return done

def main():
    args = sys.argv[1:]; src, dst = args[0], args[1]
    if '--no-shader-cache' in args: HLSLCC_CACHE.enabled = False
    only = None; want_gl = '--no-gl' not in args; want_mtl = '--no-metal' not in args; jobs = 8
    if '--files' in args: only = set(args[args.index('--files') + 1].split(','))
    if '--jobs' in args: jobs = int(args[args.index('--jobs') + 1])
    files = [f for f in glob.glob(os.path.join(src, '*')) + [os.path.join(src, 'Resources', 'unity_builtin_extra')] if os.path.isfile(f) and not f.endswith(('.resS', '.resource', '.json', '.config', '.info', '.bank', '.txt', '.pdb', '.dll'))]
    mtl_hdr = metal_header_from(os.path.join(src, 'Resources', 'unity default resources'))
    try: set_unity_version(UnityPy.load(os.path.join(src, 'globalgamemanagers')).file.version)
    except Exception: pass
    print('unity version tables: legacy channels =', LEGACY_CHANNELS)
    print('metal header template:', (mtl_hdr or METAL_HDR_2020)[:16].hex(), 'len', len(mtl_hdr or METAL_HDR_2020))
    pool = concurrent.futures.ThreadPoolExecutor(jobs); t0 = time.time(); nfiles = 0
    for f in files:
        rel = os.path.relpath(f, src)
        if only and os.path.basename(f) not in only: continue
        try: env = UnityPy.load(f)
        except Exception as ex: print('skip', rel, ex); continue
        mod = 0
        for obj in env.objects:
            # 打成 data.unity3d 的游戏，BuildSettings 也在这个包里；图形 API 列表不改成 Metal 优先，mac 播放器就起 NullGfxDevice 然后段错误
            # （一款 Unity 游戏 2020.3.26f1，2026-09-14）。散文件 globalgamemanagers 那条路在下面另有处理。
            if obj.type.name == 'BuildSettings':
                try:
                    bt = obj.read_typetree(); apis = list(bt['m_GraphicsAPIs'])
                    new_apis = ([16] if want_mtl else []) + ([17] if want_gl else []) + [a for a in apis if a not in (16, 17)]
                    if new_apis != apis:
                        bt['m_GraphicsAPIs'] = new_apis; obj.save_typetree(bt); mod += 1
                    print(f'{rel}: BuildSettings m_GraphicsAPIs', apis, '->', new_apis)
                except Exception as ex:
                    errors.append(('BuildSettings', 'buildsettings', str(ex)))
                continue
            if obj.type.name != 'Shader': continue
            tt = obj.read_typetree()
            try:
                if process_shader(tt, want_gl, want_mtl, pool, mtl_hdr): obj.save_typetree(tt); mod += 1
            except Exception as ex:
                # 5.5 之前的 Shader 没有 m_ParsedForm（一款 Unity 游戏 5.3.5p3，2026-09-14）：错误处理自己再 KeyError 会把整个文件的重定向掐断
                import traceback; traceback.print_exc()
                pf = tt.get('m_ParsedForm') if isinstance(tt, dict) else None
                sname = (pf or {}).get('m_Name') or tt.get('m_Name', '?') if isinstance(tt, dict) else '?'
                errors.append((sname, 'shader', str(ex)))
        if mod:
            out = os.path.join(dst, rel); os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, 'wb').write(env.file.save()); nfiles += 1
            print(f'{rel}: {mod} shaders retargeted ({time.time()-t0:.0f}s) {dict(log)}', flush=True)
    gg = os.path.join(src, 'globalgamemanagers')
    if os.path.isfile(gg) and (not only or 'globalgamemanagers' in only):
        if patch_buildsettings(gg, os.path.join(dst, 'globalgamemanagers'), want_gl, want_mtl): nfiles += 1
    print('done', nfiles, 'files written;', dict(log)); print('errors', len(errors))
    print('shader cache:', HLSLCC_CACHE.stats())
    for e in errors[:40]: print('  ', e)

if __name__ == '__main__': main()
