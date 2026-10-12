// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* Steam manifest_dump 的文件 SHA1 是交付依据，完成日志只允许进入校验阶段。
 * 原 depot 不删除、不改写；先在新目录合并，最后由调用方一次排他发布。
 */
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const canonical = name => name.normalize('NFC').toLowerCase();
const reserved = new Set(['.dryport-appid', '.dryport-download.json']);
const zeroSha1 = '0'.repeat(40), emptySha1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709';
export function relativeName(value) {
  const name = value.replaceAll('\\', '/');
  if (!name || /[\x00-\x1f\x7f:]/.test(name) || name.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`内容清单含不安全路径：${JSON.stringify(value)}`);
  }
  if (reserved.has(canonical(name.split('/')[0]))) throw new Error(`游戏文件占用了交付回执路径：${name}`);
  return name;
}
export function parseManifest(bytes, expected) {
  const text = new TextDecoder('utf-8', {fatal:true}).decode(bytes);
  const depot = text.match(/^Content Manifest for Depot (\d+)\s*$/m)?.[1];
  const gid = text.match(/^Manifest ID \/ date\s*:\s*(\d+)\s*\//m)?.[1];
  const count = text.match(/^Total number of files\s*:\s*([\d,]+)\s*$/m)?.[1];
  const total = text.match(/^Total bytes on disk\s*:\s*([\d,]+)\s*$/m)?.[1];
  if (depot !== String(expected.depot) || gid !== String(expected.gid) || count == null || total == null) {
    throw new Error(`depot ${expected.depot} 内容清单身份或头部不匹配`);
  }
  const number = value => {
    const result = Number(value.replaceAll(',', ''));
    if (!Number.isSafeInteger(result) || result < 0) throw new Error('内容清单数字越界');
    return result;
  };
  const files = [], seen = new Set();
  const lines = text.split(/\r?\n/);
  const heading = lines.findIndex(line => /^\s*Size\s+Chunks\s+File SHA\s+Flags\s+Name\s*$/.test(line));
  if (heading < 0) throw new Error('内容清单缺少文件表');
  for (const line of lines.slice(heading + 1)) {
    if (!line.trim()) continue;
    const match = line.match(/^\s*([\d,]+)\s+(\d+)\s+([a-fA-F\d]{40})\s+([a-fA-F\d]+)\s+(.+)$/);
    if (!match) throw new Error(`内容清单文件行无法识别：${line.slice(0,100)}`);
    const name = relativeName(match[5]), key = canonical(name), size = number(match[1]), chunks = number(match[2]);
    const flags = Number.parseInt(match[4], 16), directory = (flags & 0x40) !== 0;
    let sha1 = match[3].toLowerCase();
    // 不猜加密文件或符号链接的验证语义；Windows 普通文件/目录保留完整字节即可。
    if (!Number.isSafeInteger(flags) || flags > 0xffffffff || (flags & 0x204)) throw new Error(`内容清单含尚未支持的文件标记：${name}`);
    if (seen.has(key)) throw new Error(`同一内容包有重复或大小写冲突路径：${name}`);
    if (directory && (size !== 0 || chunks !== 0 || sha1 !== zeroSha1)) throw new Error(`目录清单记录异常：${name}`);
    if (!directory && sha1 === zeroSha1) {
      // Steam 清单可用全零表示无 chunk 的空文件；在丢弃块数前限定该情形，后续仍读回并校验空内容。
      if (size !== 0 || chunks !== 0) throw new Error(`内容清单全零摘要仅允许无块空文件：${name}`);
      sha1 = emptySha1;
    }
    seen.add(key); files.push({path:name, size, sha1, directory, flags});
  }
  if (files.length !== number(count) || files.reduce((sum,file) => sum + file.size, 0) !== number(total)) {
    throw new Error(`depot ${depot} 内容清单不完整，文件数量或总字节不匹配`);
  }
  return {depot, gid, files, bytes:number(total), manifest_sha256:createHash('sha256').update(bytes).digest('hex')};
}
function regularAt(root, name, directory = false) {
  let current = root;
  const rootStat = fs.lstatSync(root);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error(`内容目录不是普通文件夹：${root}`);
  const parts = name.split('/');
  for (let i = 0; i < parts.length; i++) {
    current = path.join(current, parts[i]);
    const stat = fs.lstatSync(current), isDir = i < parts.length - 1 || directory;
    if (stat.isSymbolicLink() || (isDir ? !stat.isDirectory() : !stat.isFile())) throw new Error(`内容路径类型不匹配：${name}`);
  }
  return current;
}
export async function sha1File(file) {
  const hash = createHash('sha1');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
export async function verifyDepot(root, manifest) {
  const wanted = new Set(manifest.files.map(file => file.path));
  // Steam 可能保留旧版本多余文件；这些文件不属于当前 gid，不能夹带到本次交付。
  // 只复制清单列出的条目，额外旧文件留在原 depot，不删除。
  for (const file of manifest.files) {
    const source = regularAt(root, file.path, file.directory);
    if (!file.directory && (fs.statSync(source).size !== file.size || await sha1File(source) !== file.sha1)) {
      throw new Error(`depot ${manifest.depot} 文件字节校验失败：${file.path}`);
    }
  }
  return {depot:manifest.depot, gid:manifest.gid, files:wanted.size, bytes:manifest.bytes,
    manifest_sha256:manifest.manifest_sha256, verified:true};
}
export async function mergeDepots(packages, destination, {mount_order_verified = false, assertDestination = () => {}} = {}) {
  assertDestination();
  fs.mkdirSync(destination); // 调用方必须提供自己新暂存区内尚不存在的目录。
  const created = fs.lstatSync(destination);
  const owners = new Map(), nodes = new Map(), overlays = [], duplicates = [];
  const materialPath = file => {
    let actual = '', logical = '';
    const parts = file.path.split('/');
    for (let i = 0; i < parts.length; i++) {
      logical += (logical ? '/' : '') + parts[i];
      const key = canonical(logical), directory = i < parts.length - 1 || file.directory;
      const prior = nodes.get(key);
      if (prior && prior.directory !== directory) throw new Error(`内容包间文件与目录冲突：${logical}`);
      actual = prior?.path || (actual ? actual + '/' : '') + parts[i];
      if (!prior) nodes.set(key, {path:actual, directory});
      if (directory) {
        fs.mkdirSync(path.join(destination, actual), {recursive:true});
        const st = fs.lstatSync(path.join(destination, actual));
        if (!st.isDirectory() || st.isSymbolicLink()) throw new Error(`暂存目录路径已变化：${actual}`);
      }
    }
    return actual;
  };
  for (const {plan, manifest, root} of packages) {
    for (const file of manifest.files) {
      assertDestination();
      const now = fs.lstatSync(destination);
      if (!now.isDirectory() || now.isSymbolicLink() || now.dev !== created.dev || now.ino !== created.ino) throw new Error('本次暂存目录已被替换');
      const key = canonical(file.path), previous = owners.get(key);
      const actualPath = materialPath(file);
      if (previous && previous.directory !== file.directory) throw new Error(`内容包间文件与目录冲突：${file.path}`);
      if (file.directory) {
        if (!previous) owners.set(key, {...file, path:actualPath, depot:plan.depot, gid:plan.gid, mount_order:plan.mount_order});
        continue;
      }
      const source = regularAt(root, file.path), output = path.join(destination, actualPath);
      const owner = {path:actualPath, depot:plan.depot, gid:plan.gid, sha1:file.sha1, size:file.size,
        directory:false, mount_order:plan.mount_order};
      if (previous && previous.sha1 === file.sha1 && previous.size === file.size) {
        duplicates.push({path:owner.path, before:previous, after:owner, winner:previous.depot}); continue;
      }
      if (previous) {
        // https://partner.steamgames.com/doc/store/application/depots#depot_mounting_rules
        // 只有已证实的完整顺序可以在本次暂存区覆盖，回执保留来源及最终胜出版本。
        if (!mount_order_verified || !Number.isSafeInteger(previous.mount_order) || !Number.isSafeInteger(plan.mount_order)
            || previous.mount_order >= plan.mount_order) throw new Error(`文件冲突缺少确定挂载顺序：${file.path}`);
        overlays.push({path:owner.path, before:previous, after:owner, winner:owner.depot});
        fs.unlinkSync(output); // 此路径是本函数刚创建的普通文件，原 Steam depot 从未参与覆盖。
      }
      fs.mkdirSync(path.dirname(output), {recursive:true});
      fs.copyFileSync(source, output, fs.constants.COPYFILE_EXCL | fs.constants.COPYFILE_FICLONE);
      if (fs.statSync(output).size !== file.size || await sha1File(output) !== file.sha1) throw new Error(`复制后字节校验失败：${file.path}`);
      owners.set(key, owner);
    }
  }
  if (![...owners.values()].some(file => !file.directory)) throw new Error('所有内容包合并后没有游戏文件');
  return {overlays, duplicates, files:[...owners.values()]};
}
export function publishExclusive(source, destination) {
  // 普通 rename 会吞掉并发新建的空目录。macOS 的排他 rename 保留任何已有目标。
  execFileSync('python3', ['-c', `import ctypes, os, sys
lib = ctypes.CDLL(None, use_errno=True)
fn = lib.renamex_np
fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
fn.restype = ctypes.c_int
if fn(os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2]), 4):
    raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), sys.argv[2])
`, source, destination], {stdio:'pipe'});
}

export async function verifyDelivery(root, appid) {
  const receiptFile = regularAt(root, '.dryport-download.json');
  if (fs.statSync(receiptFile).size > 128 * 1024 * 1024) throw new Error('交付回执过大');
  const receipt = JSON.parse(fs.readFileSync(receiptFile, 'utf8'));
  if (receipt.protocol !== 1 || receipt.appid !== appid || receipt.plan?.appid !== appid || receipt.plan?.complete !== true
      || receipt.complete !== true || !Array.isArray(receipt.plan.selected) || !receipt.plan.selected.length
      || !Array.isArray(receipt.packages) || receipt.packages.length !== receipt.plan.selected.length
      || !Array.isArray(receipt.files) || !receipt.files.length) throw new Error('缺少完整的内容包交付回执');
  const expected = new Map();
  for (let i = 0; i < receipt.packages.length; i++) {
    const plan = receipt.plan.selected[i], pack = receipt.packages[i];
    if (pack.depot !== plan.depot || pack.gid !== plan.gid || pack.verified !== true || !Array.isArray(pack.manifest_files)
        || pack.files !== pack.manifest_files.length) throw new Error('交付回执中内容包不完整');
    let bytes = 0;
    const seen = new Set();
    for (const file of pack.manifest_files) {
      const name = relativeName(file.path), key = canonical(name), prior = expected.get(key);
      if (seen.has(key) || typeof file.directory !== 'boolean' || !Number.isSafeInteger(file.size) || file.size < 0
          || !/^[a-f0-9]{40}$/.test(file.sha1)) throw new Error('交付回执文件记录错误');
      if (prior && prior.directory !== file.directory) throw new Error('交付回执存在文件类型冲突');
      seen.add(key); bytes += file.size;
      if (prior && prior.sha1 !== file.sha1 && (!receipt.plan.mount_order_verified
          || !Number.isSafeInteger(plan.mount_order) || !Number.isSafeInteger(prior.mount_order) || plan.mount_order <= prior.mount_order)) {
        throw new Error('交付回执中的覆盖顺序未得到确认');
      }
      expected.set(key, {...file, path:prior?.path || name, mount_order:plan.mount_order});
    }
    if (bytes !== pack.bytes) throw new Error('交付回执内容包字节总数不匹配');
  }
  if (receipt.files.length !== expected.size) throw new Error('交付回执最终文件表不完整');
  const recorded = new Set();
  for (const file of receipt.files) {
    const key = canonical(relativeName(file.path)), want = expected.get(key);
    if (recorded.has(key) || !want || file.size !== want.size || file.sha1 !== want.sha1 || file.directory !== want.directory) {
      throw new Error('交付回执最终文件表不匹配');
    }
    recorded.add(key);
    const source = regularAt(root, file.path, file.directory);
    if (!file.directory && (fs.statSync(source).size !== file.size || await sha1File(source) !== file.sha1)) throw new Error(`本地副本内容已变化：${file.path}`);
  }
  const marker = regularAt(root, '.dryport-appid');
  if (fs.readFileSync(marker, 'utf8').trim() !== appid) throw new Error('本地副本 appid 标记不匹配');
  const allowed = new Set(expected.keys());
  for (const name of [...allowed]) {
    let parent = path.posix.dirname(name);
    while (parent !== '.') { allowed.add(parent); parent = path.posix.dirname(parent); }
  }
  const walk = (directory, prefix = '') => {
    for (const entry of fs.readdirSync(directory, {withFileTypes:true})) {
      const name = prefix + entry.name;
      if (!prefix && reserved.has(entry.name)) continue;
      if (entry.isSymbolicLink() || !allowed.has(canonical(name))) throw new Error(`本地副本出现清单外内容：${name}`);
      if (entry.isDirectory()) walk(path.join(directory, entry.name), name + '/');
    }
  };
  walk(root);
  return {protocol:1, appid, complete:true, files:receipt.files.length, depots:receipt.packages.length,
    scope:receipt.plan.scope, external_dependencies:receipt.plan.external_dependencies || []};
}
