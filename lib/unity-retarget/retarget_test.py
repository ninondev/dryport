# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
import sys, os, subprocess, json, difflib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unityblob, rdefsynth
S = sys.argv[1]  # dir with platN.bin
HERE = os.path.dirname(os.path.abspath(__file__))
def run(dxbc, lang, flags, out):
    open(out + '.dxbc', 'wb').write(dxbc)
    r = subprocess.run([HERE + '/hlslcc-cli', out + '.dxbc', lang, flags, out], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr
d3d = unityblob.parse_blob(open(S + '/plat4.bin', 'rb').read())
mtl = unityblob.parse_blob(open(S + '/plat14.bin', 'rb').read())
gl = unityblob.parse_blob(open(S + '/plat15.bin', 'rb').read())
vs, fs = d3d[0], d3d[4]
for name, sp in (('vs', vs), ('fs', fs)):
    tail = unityblob.parse_tail(sp['tail']); refl = rdefsynth.refl_from_tail(tail)
    dxbc = sp['code'][38:]; major, minor, pt = rdefsynth.dxbc_version(dxbc)
    rdef = rdefsynth.build_rdef(refl, major, minor, pt); new = rdefsynth.inject_rdef(dxbc, rdef)
    for lang, flags in (('150', sys.argv[2]), ('metal', sys.argv[3])):
        out = f'/tmp/rt_{name}_{lang}.txt'
        rc, so, se = run(new, lang, flags, out)
        print(f'== {name} {lang} rc={rc} {se.strip()[:300]}')
        if rc == 0:
            print('   refl:', so.strip()[:600])
# compare
def show(a, b, label):
    dl = list(difflib.unified_diff(a.splitlines(), b.splitlines(), 'unity', 'ours', lineterm='', n=0))
    print(f'--- diff {label}: {len(dl)} lines'); print('\n'.join(dl[:60]))
umsl_vs = mtl[0]['code'][56:].decode(); umsl_fs = mtl[4]['code'][56:].decode()
show(umsl_vs, open('/tmp/rt_vs_metal.txt').read(), 'metal vs'); show(umsl_fs, open('/tmp/rt_fs_metal.txt').read(), 'metal fs')
ugl = gl[0]['code'].decode()
ours = '#ifdef VERTEX\n' + open('/tmp/rt_vs_150.txt').read() + '#endif\n#ifdef FRAGMENT\n' + open('/tmp/rt_fs_150.txt').read() + '#endif\n'
show(ugl, ours, 'glcore')
