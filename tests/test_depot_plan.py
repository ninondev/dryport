# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""计划与清单机制试验。Valve 顺序规则见 https://partner.steamgames.com/doc/store/application/depots 。"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import fs from 'node:fs';
import {planDepots} from './lib/depot-plan.mjs';
import {parseManifest, verifyDepot, mergeDepots, publishExclusive, verifyDelivery} from './lib/depot-delivery.mjs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
try {
  let result;
  if (input.op === 'plan') result = planDepots(input.row, input.options);
  if (input.op === 'parse') result = parseManifest(Buffer.from(input.text), input.expected);
  if (input.op === 'merge') {
    const packages = [];
    for (const p of input.packages) {
      const manifest = parseManifest(Buffer.from(p.text),p.plan);
      await verifyDepot(p.root, manifest); packages.push({...p, manifest});
    }
    result = await mergeDepots(packages, input.destination, {mount_order_verified:input.ordered});
  }
  if (input.op === 'publish') { publishExclusive(input.source, input.destination); result = true; }
  console.log(JSON.stringify({ok:true,result}));
} catch(error) { console.log(JSON.stringify({ok:false,error:error.message})); }
'''


def manifest(depot, gid, files):
    records = []
    for name, data in files.items():
        records.append(f"{len(data) if data is not None else 0:,} 0 "
                       f"{hashlib.sha1(data).hexdigest() if data is not None else '0'*40} "
                       f"{'0' if data is not None else '40'} {name}")
    return (f'Content Manifest for Depot {depot}\nManifest ID / date : {gid} / fixture\n'
            f'Total number of files : {len(files)}\nTotal bytes on disk : {sum(len(v) for v in files.values() if v is not None):,}\n'
            'Size Chunks File SHA Flags Name\n' + '\n'.join(records) + '\n')


@unittest.skipUnless(shutil.which('node'), '需要 Node.js')
class DepotPlanTests(unittest.TestCase):
    def run_js(self, **data):
        result = subprocess.run(['node','--input-type=module','-e',RUNNER], input=json.dumps(data),
                                text=True,capture_output=True,cwd=ROOT,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def depot(self, depot=43, order=0, **values):
        return dict(depot=str(depot), mount_order=order, config={},
                    manifests={'public':dict(gid=str(depot+1), download=50, size=100)}, **values)

    def plan(self, depots, **values):
        return self.run_js(op='plan', row=dict(appid='42', depots=depots, **values))

    def test_core_os_arch_language_and_dlc_are_selected_together(self):
        core = self.depot()
        x64 = self.depot(45, 1); x64['config']={'oslist':'windows','osarch':'64'}
        x32 = self.depot(47, 2); x32['config']={'oslist':'windows','osarch':'32'}
        chinese = self.depot(49, 3); chinese['config']={'language':'schinese'}
        english = self.depot(51, 4); english['config']={'language':'english'}
        linux = self.depot(53, 5); linux['config']={'oslist':'linux'}
        dlc = self.depot(55, 6, dlc_appid='99')
        result = self.plan([core,x64,x32,chinese,english,linux,dlc],current_app_language='schinese')['result']
        self.assertEqual([p['depot'] for p in result['selected']],['43','45','49'])
        self.assertEqual({p['reason'] for p in result['excluded']},{'other_os','other_architecture','other_language','dlc_not_requested'})
        self.assertTrue(result['complete'])

    def test_only_32_bit_game_is_not_lost_on_64_bit_host(self):
        d=self.depot();d['config']={'osarch':'32'}
        self.assertEqual(self.plan([d])['result']['architecture'],'32')

    def test_language_fallback_is_explicit_not_all_languages(self):
        ds=[self.depot(43,0),self.depot(45,1)]
        ds[0]['config']={'language':'english'};ds[1]['config']={'language':'japanese'}
        result=self.plan(ds)['result']
        self.assertEqual([p['depot'] for p in result['selected']],['43'])
        self.assertEqual(result['language_reason'],'english_fallback')
        ds[0]['config']={'language':'german'}
        self.assertFalse(self.plan(ds)['ok'])

    def test_configured_language_in_common_core_is_not_replaced_by_foreign_addons(self):
        ds=[self.depot(43,0),self.depot(45,1),self.depot(47,2)]
        ds[1]['config']={'language':'french'};ds[2]['config']={'language':'russian'}
        result=self.plan(ds,steam_language='english')['result']
        self.assertEqual([p['depot'] for p in result['selected']],['43'])
        self.assertEqual(result['language'],'english')
        self.assertEqual(result['language_reason'],'configured')
        self.assertFalse(self.plan(ds[1:],steam_language='english')['ok'])
        result=self.plan(ds[:2],current_app_language='english')['result']
        self.assertEqual([p['depot'] for p in result['selected']],['43'])

    def test_unknown_conditions_and_missing_required_manifest_block(self):
        cases=[]
        for config in ({'realm':'steamchina'},{'lowviolence':'1'},{'steamdeck':'1'}):
            d=self.depot();d['config']=config;cases.append(d)
        cases += [self.depot(optional=True),self.depot(systemdefined='1')]
        d=self.depot();d['manifests']={};cases.append(d)
        d=self.depot();d['manifests']['public']['metadata_issues']=['invalid size'];cases.append(d)
        for d in cases:
            with self.subTest(d=d): self.assertFalse(self.plan([d])['ok'])
        self.assertFalse(self.plan([self.depot()],depot_metadata_issues=['bad key'])['ok'])

    def test_missing_or_duplicate_order_never_becomes_id_order(self):
        for order in (None,0):
            self.assertFalse(self.plan([self.depot(43,0),self.depot(45,order)])['ok'])
        result=self.plan([self.depot(43,8),self.depot(45,2)])['result']
        self.assertEqual([p['depot'] for p in result['selected']],['45','43'])

    def test_shared_core_inherits_conditions_and_missing_manifest_without_changing_host_authorization(self):
        master=self.depot(43,5);master['config']={'oslist':'windows','language':'english'}
        host=dict(depot='43',shared_from_app='77',mount_order=2,sharedinstall=2)
        result=self.plan([host],sharedApps={'77':{'depots':[master]}})['result']
        self.assertEqual(result['selected'][0]['gid'],'44')
        self.assertEqual(result['selected'][0]['download_appid'],'42')
        self.assertEqual(result['selected'][0]['mount_order'],2)
        self.assertFalse(self.plan([host],sharedApps={})['ok'])
        master['shared_from_app']='77'
        self.assertFalse(self.plan([host],sharedApps={'77':{'depots':[master]}})['ok'])

    def test_shared_host_manifest_is_not_overwritten_by_source_public_version(self):
        host=self.depot(43,0,shared_from_app='77');host['manifests']['public']['gid']='999'
        self.assertEqual(self.plan([host],sharedApps={'77':{'depots':[self.depot()]}})['result']['selected'][0]['gid'],'999')

    def test_common_redists_are_unprepared_external_dependencies(self):
        deps=[self.depot(),dict(depot='228981',shared_from_app='228980',mount_order=1)]
        result=self.plan(deps)['result']
        self.assertEqual(len(result['selected']),1)
        self.assertFalse(result['external_dependencies'][0]['installed'])
        self.assertEqual(result['scope'],'base_game')

    def test_invalid_id_and_gid_do_not_become_commands(self):
        for key,value in [('depot','43;quit'),('depot','4294967296')]:
            d=self.depot();d[key]=value;self.assertFalse(self.plan([d])['ok'])
        d=self.depot();d['manifests']['public']['gid']=2**64
        self.assertFalse(self.plan([d])['ok'])

    def test_manifest_chinese_spaces_backslashes_and_directory_hex_flags(self):
        files={'中文 空间':None,'中文 空间\\文本 file.bin':b'bytes','trailing space ':b'x'}
        r=self.run_js(op='parse',text=manifest(43,44,files),expected=dict(depot='43',gid='44'))
        self.assertTrue(r['ok'],r)
        self.assertEqual([f['path'] for f in r['result']['files']],['中文 空间','中文 空间/文本 file.bin','trailing space '])
        self.assertTrue(r['result']['files'][0]['directory'])

    def test_manifest_zero_digest_empty_file_is_read_verified_and_merged(self):
        # 真实 manifest_dump 会把无 chunk 的空文件记作全零摘要；仍须读回空内容。
        empty_sha1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
        text = manifest(43,44,{'empty.bin':b'', 'normal.bin':b'content'})
        text = text.replace(empty_sha1, '0' * 40)
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);root=base/'source';root.mkdir()
            (root/'empty.bin').write_bytes(b'');(root/'normal.bin').write_bytes(b'content')
            args=dict(op='merge',packages=[dict(plan=dict(depot='43',gid='44',mount_order=0),
                      root=str(root),text=text)],destination=str(base/'out'),ordered=True)
            result=self.run_js(**args)
            self.assertTrue(result['ok'],result)
            self.assertEqual((base/'out/empty.bin').read_bytes(),b'')
            self.assertEqual((base/'out/normal.bin').read_bytes(),b'content')
            empty=next(f for f in result['result']['files'] if f['path']=='empty.bin')
            self.assertEqual(empty['sha1'],empty_sha1)
            self.assertEqual((root/'empty.bin').read_bytes(),b'')

    def test_manifest_zero_digest_requires_both_zero_size_and_zero_chunks(self):
        zero = '0' * 40
        for size,chunks in [(1,0),(0,1),(1,1)]:
            with self.subTest(size=size,chunks=chunks):
                data=b'x' * size
                text=manifest(43,44,{'file':data}).replace(hashlib.sha1(data).hexdigest(),zero)
                text=text.replace(f'\n{size} 0 ',f'\n{size} {chunks} ')
                result=self.run_js(op='parse',text=text,expected=dict(depot='43',gid='44'))
                self.assertFalse(result['ok'],result)
        text=manifest(43,44,{'folder':None}).replace('\n0 0 ', '\n0 1 ')
        self.assertFalse(self.run_js(op='parse',text=text,expected=dict(depot='43',gid='44'))['ok'])

    def test_manifest_empty_file_missing_or_malformed_digest_and_chunks_fail(self):
        empty_sha1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
        text=manifest(43,44,{'empty':b''})
        cases=[text.replace(empty_sha1,value) for value in ['', '0'*39, '0'*41, 'g'*40]]
        cases += [text.replace('\n0 0 ',f'\n0 {value} ') for value in ['', '-1', '9007199254740992']]
        for bad in cases:
            with self.subTest(text=bad):
                self.assertFalse(self.run_js(op='parse',text=bad,expected=dict(depot='43',gid='44'))['ok'])
        parsed=self.run_js(op='parse',text=manifest(43,44,{'folder':None,'empty':b''}),
                           expected=dict(depot='43',gid='44'))
        self.assertTrue(parsed['ok'],parsed)
        self.assertEqual([f['sha1'] for f in parsed['result']['files']],['0'*40,empty_sha1])

    def test_manifest_empty_file_still_rejects_changed_missing_and_link_sources(self):
        empty_sha1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
        template=manifest(43,44,{'empty':b''})
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);outside=base/'outside';outside.write_bytes(b'')
            for kind in ['changed','missing','symlink','directory','wrong_digest']:
                with self.subTest(kind=kind):
                    root=base/kind;root.mkdir();file=root/'empty'
                    if kind=='changed': file.write_bytes(b'not empty')
                    elif kind=='symlink': file.symlink_to(outside)
                    elif kind=='directory': file.mkdir()
                    elif kind=='wrong_digest': file.write_bytes(b'')
                    text=template.replace(empty_sha1, '1'*40 if kind=='wrong_digest' else '0'*40)
                    destination=base/(kind+'-output')
                    result=self.run_js(op='merge',packages=[dict(plan=dict(depot='43',gid='44',mount_order=0),
                        root=str(root),text=text)],destination=str(destination),ordered=True)
                    self.assertFalse(result['ok'],result)
                    self.assertFalse(destination.exists())
            self.assertEqual(outside.read_bytes(),b'')

    def test_manifest_identity_truncation_and_path_escape_are_rejected(self):
        text=manifest(43,44,{'game.dat':b'abc'})
        for bad in [text.replace('Depot 43','Depot 99'),text.replace('Total number of files : 1','Total number of files : 2'),
                    text.replace('Total bytes on disk : 3','Total bytes on disk : 4'),text.rsplit('\n',2)[0]+'\n',
                    manifest(43,44,{'../outside':b'a'}),manifest(43,44,{'/outside':b'a'}),
                    manifest(43,44,{'C:\\outside':b'a'}),manifest(43,44,{'.dryport-appid':b'a'})]:
            with self.subTest(text=bad): self.assertFalse(self.run_js(op='parse',text=bad,expected=dict(depot='43',gid='44'))['ok'])

    def test_ordered_overlay_and_equal_duplicates_preserve_sources(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); packages=[]
            for depot,order,files in [(43,0,{'same':b'same','replace':b'old'}),(45,1,{'same':b'same','replace':b'new'})]:
                root=base/str(depot);root.mkdir()
                for name,data in files.items():(root/name).write_bytes(data)
                packages.append(dict(plan=dict(depot=str(depot),gid=str(depot+1),mount_order=order),root=str(root),text=manifest(depot,depot+1,files)))
            result=self.run_js(op='merge',packages=packages,destination=str(base/'out'),ordered=True)
            self.assertTrue(result['ok'],result)
            self.assertEqual((base/'out/replace').read_bytes(),b'new')
            self.assertEqual((base/'43/replace').read_bytes(),b'old')
            self.assertEqual((base/'45/replace').read_bytes(),b'new')
            self.assertEqual(len(result['result']['overlays']),1)
            self.assertEqual(result['result']['overlays'][0]['winner'],'45')
            self.assertEqual(len(result['result']['duplicates']),1)
            self.assertFalse(self.run_js(op='merge',packages=packages,destination=str(base/'unordered'),ordered=False)['ok'])

    def test_casefold_implicit_parents_merge_into_one_real_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);packages=[]
            for depot,order,files in [(43,0,{'Data/a':b'a'}),(45,1,{'data/b':b'b'})]:
                root=base/str(depot);root.mkdir()
                for name,data in files.items():
                    (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_bytes(data)
                packages.append(dict(plan=dict(depot=str(depot),gid=str(depot+1),mount_order=order),root=str(root),text=manifest(depot,depot+1,files)))
            result=self.run_js(op='merge',packages=packages,destination=str(base/'out'),ordered=True)
            self.assertTrue(result['ok'],result)
            self.assertEqual(sorted(p.name for p in (base/'out').iterdir()),['Data'])
            self.assertEqual((base/'out/Data/b').read_bytes(),b'b')

    def test_file_cannot_replace_an_implicit_parent_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);packages=[]
            for depot,order,files in [(43,0,{'Data/a':b'a'}),(45,1,{'data':b'conflict'})]:
                root=base/str(depot);root.mkdir()
                for name,data in files.items():
                    (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_bytes(data)
                packages.append(dict(plan=dict(depot=str(depot),gid=str(depot+1),mount_order=order),root=str(root),text=manifest(depot,depot+1,files)))
            result=self.run_js(op='merge',packages=packages,destination=str(base/'out'),ordered=True)
            self.assertFalse(result['ok'])
            self.assertEqual((base/'43/Data/a').read_bytes(),b'a')
            self.assertEqual((base/'45/data').read_bytes(),b'conflict')

    def test_source_hash_mismatch_missing_file_and_symlink_fail(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);root=base/'source';root.mkdir();file=root/'game';file.write_bytes(b'bad')
            args=dict(op='merge',packages=[dict(plan=dict(depot='43',gid='44',mount_order=0),root=str(root),text=manifest(43,44,{'game':b'good'}))],destination=str(base/'out'),ordered=True)
            self.assertFalse(self.run_js(**args)['ok'])
            file.unlink();self.assertFalse(self.run_js(**args)['ok'])
            outside=base/'outside';outside.write_bytes(b'good');file.symlink_to(outside)
            self.assertFalse(self.run_js(**args)['ok']);self.assertFalse((base/'out').exists())

    @unittest.skipUnless(__import__('sys').platform=='darwin','macOS 排他发布')
    def test_exclusive_publish_preserves_empty_directory_and_dangling_link(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'source';source.mkdir();(source/'game').write_bytes(b'data')
            target=base/'target';target.mkdir()
            self.assertFalse(self.run_js(op='publish',source=str(source),destination=str(target))['ok'])
            self.assertEqual(list(target.iterdir()),[]);self.assertTrue(source.exists())
            target.rmdir();target.symlink_to(base/'missing')
            self.assertFalse(self.run_js(op='publish',source=str(source),destination=str(target))['ok'])
            self.assertTrue(target.is_symlink());self.assertFalse((base/'missing').exists())
