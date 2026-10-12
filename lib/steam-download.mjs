#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* 用已登录的 Steam 客户端执行 download_depot；只在客户端确认完成后交付副本。
 * 用法：node steam-download.mjs <appid> [depot] [manifest-gid] [目标目录]
 * 自动模式从当前账号元资料规划全部基础内容；--verify-delivery <appid> <目录> 只读复核副本。
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { STEAM, SN_HOME, SN_GAMES, SN_LIB } from './paths.mjs';
import { identityExpression, evalErrorText } from './steam-library.mjs';
import {numericId, planDepots} from './depot-plan.mjs';
import {parseManifest, verifyDepot, mergeDepots, publishExclusive, verifyDelivery} from './depot-delivery.mjs';
import {budget, budgetMessage, classifySpew, freeBytes, sameVolume, RESERVE_MIN} from './disk-budget.mjs';
import {networkReachable} from './net-probe.mjs';

const RPC_TIMEOUT = 15000;
const get = p => new Promise((resolve, reject) => {
  const request = http.get({ host: '127.0.0.1', port: 8080, path: p }, response => {
    let body = '';
    response.on('data', data => {
      body += data;
      if (body.length > 2 * 1024 * 1024) request.destroy(new Error('Steam 调试端点响应过大'));
    });
    response.on('error', reject);
    response.on('aborted', () => reject(new Error('Steam 调试连接中断')));
    response.on('end', () => {
      try {
        if (response.statusCode !== 200) throw new Error(`Steam 调试端点返回 HTTP ${response.statusCode}`);
        resolve(JSON.parse(body));
      } catch (error) { reject(error); }
    });
  });
  request.setTimeout(RPC_TIMEOUT, () => request.destroy(new Error('读取 Steam 调试端点超时')));
  request.on('error', reject);
});

// Steam 客户端没就绪(没开、没登录、库页没加载、调试口不可用)是一类可自愈的失败:退出码 5 让外层停在
// 下载这一步,提示用户就绪后原样重跑,不当成坏下载,也不改走别的下载途径。原文完整保留在括号里。
const notReadyError = message => {
  const error = new Error(`Steam 没准备好（${message}）：打开 Steam、登录并等库页面加载完，再重跑同一条命令；调试口没开过的话先运行 dryport steam-debug --enable。`);
  error.exitCode = 5; return error;
};
class Steam {
  constructor() { this.id = 0; this.pending = new Map(); this.key = `__snDownload_${randomUUID().replaceAll('-', '')}`; }
  fail(error) {
    this.failure = error;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
  }
  async connect() {
    const targets = await get('/json');
    const target = Array.isArray(targets) && targets.find(t => t.title === 'SharedJSContext');
    if (!target) throw new Error('连不上 Steam 的 SharedJSContext。确认已登录客户端及调试端点可用后重试。');
    const url = new URL(target.webSocketDebuggerUrl);
    if (url.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.port !== '8080') {
      throw new Error('Steam 调试连接不是预期的本机端点，已停止');
    }
    this.ws = new WebSocket(url.href);
    this.ws.addEventListener('close', () => this.fail(notReadyError('Steam 调试连接已断开，下载尚未交付')));
    this.ws.addEventListener('error', () => this.fail(notReadyError('Steam 调试连接出错，下载尚未交付')));
    this.ws.addEventListener('message', event => {
      let message;
      try { message = JSON.parse(event.data); } catch { this.fail(new Error('Steam 调试消息格式错误')); return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer); this.pending.delete(message.id);
      if (message.error || message.result?.exceptionDetails) {
        const text = 'Steam 未执行请求：'
          + evalErrorText(message.result?.exceptionDetails, JSON.stringify(message.error || message.result?.exceptionDetails));
        // 库页面没加载时求值会报「尚未登录」,同样按没准备好处理,等客户端就绪后原样重跑。
        pending.reject(text.includes('尚未登录') ? notReadyError(text) : new Error(text));
      } else pending.resolve(message.result?.result?.value);
    });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { reject(new Error('连接 Steam 调试端点超时')); this.ws.close(); }, RPC_TIMEOUT);
      this.ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      const failed = () => { clearTimeout(timer); reject(this.failure || new Error('连接 Steam 失败')); };
      this.ws.addEventListener('error', failed, { once: true });
      this.ws.addEventListener('close', failed, { once: true });
    });
    const key = JSON.stringify(this.key);
    await this.eval(`(() => {
      const slot = window[${key}] = {lines: []};
      slot.registration = SteamClient.Console.RegisterForSpewOutput(s => {
        try { const o = typeof s === 'string' ? JSON.parse(s) : s; slot.lines.push(o.spew || ''); }
        catch (e) { slot.lines.push(String(s)); }
        if (slot.lines.length > 2000) slot.lines.splice(0, slot.lines.length - 2000);
      }); return 1;
    })()`);
  }
  eval(expression) {
    if (this.failure) return Promise.reject(this.failure);
    if (!this.ws || this.ws.readyState !== 1) return Promise.reject(new Error('Steam 调试连接不可用'));
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.fail(notReadyError('等待 Steam 调试响应超时')); this.ws.close(); }, RPC_TIMEOUT);
      this.pending.set(id, {resolve, reject, timer});
      try { this.ws.send(JSON.stringify({id, method:'Runtime.evaluate', params:{expression, awaitPromise:true, returnByValue:true}})); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }
  async cmd(command) {
    if (await this.eval(`SteamClient.Console.ExecCommand(${JSON.stringify(command)}); 1`) !== 1) {
      throw new Error('Steam 没有确认收到控制台命令');
    }
  }
  async out() {
    const value = await this.eval(`(window[${JSON.stringify(this.key)}]?.lines || []).join("")`);
    if (typeof value !== 'string') throw new Error('Steam 下载日志不可读，已停止');
    return value;
  }
  clear() { return this.eval(`window[${JSON.stringify(this.key)}].lines.length = 0; 1`); }
  async close() {
    if (!this.ws) return;
    if (!this.failure && this.ws.readyState === 1) {
      try { await this.eval(`(() => { const s = window[${JSON.stringify(this.key)}]; s?.registration?.unregister?.(); delete window[${JSON.stringify(this.key)}]; return 1; })()`); } catch {}
    }
    this.ws.close();
  }
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const mb = bytes => (bytes / 1048576).toFixed(0);
function dirSize(directory) {
  try { return parseInt(execFileSync('du', ['-sk', directory], {encoding:'utf8', stdio:['ignore', 'pipe', 'ignore']}).split('\t')[0], 10) * 1024; }
  catch { return 0; }
}
// Steam 会把 download_depot 的解压内容放在应用内或外层两种 staging;空间核算与轮询进度认同一组候选。
const steamContentPaths = selected => [
  path.join(STEAM, 'Steam.AppBundle/Steam/Contents/MacOS', `steamapps/content/app_${selected.download_appid}/depot_${selected.depot}`),
  path.join(STEAM, `steamapps/content/app_${selected.download_appid}/depot_${selected.depot}`),
];
// 写满与普通失败分家:退出码 4 让外层停在下载这一步,不再退回 steamcmd 重下一遍。
const diskFullError = (depot, staging, bytes) => {
  const error = new Error(`磁盘写满，下载停在 depot ${depot}：已下的暂存保留在 ${staging}（${mb(bytes)} MB）。腾出空间后重跑同一条命令。`);
  error.exitCode = 4; return error;
};
// 断网与普通失败分家:退出码 6,暂存保留,联网后原样重跑续传;路径与 MB 口径和写满一致。
const networkDownError = (depot, staging, bytes) => {
  const error = new Error(`网络断了，下载停在 depot ${depot}：已下的暂存保留在 ${staging}（${mb(bytes)} MB）。联网后重跑同一条命令。`);
  error.exitCode = 6; return error;
};
function entries(directory) {
  try {
    const stat = fs.lstatSync(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`目录不是普通文件夹，已保留：${directory}`);
    return fs.readdirSync(directory);
  } catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}
const hasPayload = directory => entries(directory).some(name => !['.dryport-appid', '.dryport-download.json'].includes(name));
const normalize = value => value.replaceAll('\\', '/').replace(/\/+/g, '/');
function realTarget(value) {
  const tail = []; let current = path.resolve(value);
  while (!fs.existsSync(current)) { tail.unshift(path.basename(current)); current = path.dirname(current); }
  return path.join(fs.realpathSync(current), ...tail);
}
function parentIdentities(target) {
  const identities = new Map();
  for (let current = path.dirname(target); ; current = path.dirname(current)) {
    try {
      const st = fs.lstatSync(current);
      if (!st.isDirectory() || st.isSymbolicLink()) throw new Error(`目标祖先不是普通目录：${current}`);
      identities.set(current, [st.dev, st.ino]);
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (current === path.dirname(current)) break;
  }
  return identities;
}

async function main() {
  const appid = numericId(process.argv[2], 0xffffffffn, 'appid');
  let [, , , depot, gid, dest] = process.argv;
  const explicit = !!depot || !!gid;
  if (explicit && (!depot || !gid)) throw new Error('手动下载必须同时指定 depot 与 manifest');
  let name = `app_${appid}`, row = null, plan;
  let tablePath = path.join(SN_GAMES, 'owned-windows.json'), catalogAccount = null, catalogProtocol = 1;
  const catalogRoot = path.join(SN_GAMES, '.dryport-catalog');
  if (fs.existsSync(catalogRoot) || (() => { try { return fs.lstatSync(catalogRoot).isSymbolicLink(); } catch { return false; } })()) {
    const directory = execFileSync('python3', [path.join(SN_LIB, 'library_discovery.py'), '--path'], {encoding:'utf8'}).trim();
    if (!directory) throw new Error('当前账号游戏清单不可用，请重新运行 dryport survey');
    const catalog = JSON.parse(fs.readFileSync(path.join(directory, 'catalog.json'), 'utf8'));
    catalogProtocol = catalog.protocol;
    tablePath = path.join(directory, catalog.protocol >= 2 ? 'windows-library.json' : 'owned-windows.json');
    catalogAccount = catalog.account;
  }
  if (fs.existsSync(tablePath)) {
    const table = JSON.parse(fs.readFileSync(tablePath, 'utf8'));
    if (!Array.isArray(table)) throw new Error(`游戏清单不是数组：${tablePath}`);
    row = table.find(r => String(r.appid) === appid);
    if (row) {
      name = typeof row.name === 'string' ? row.name : name;
    }
  }
  if (explicit) {
    depot = numericId(depot, 0xffffffffn, 'depot'); gid = numericId(gid, 0xffffffffffffffffn, 'manifest');
    plan = planDepots({appid, depots:[{depot, gid, mount_order:0}]});
    plan.scope = 'explicit_depot'; // 手动单包保留兼容性，但不声称覆盖应用所有内容。
  } else if (!row) {
    const error = new Error(`查不到 ${appid} 的内容包资料。先重新读取游戏库，或手动传 depot 与 manifest。`);
    error.exitCode = 2; throw error;
  } else if (catalogProtocol < 2) {
    plan = planDepots({...row, depots:row.depots || [{depot:row.depot, gid:row.gid}]});
    if (!Array.isArray(row.depots)) plan.scope = 'legacy_depot'; // 老表仅有首包别名，不能证明基础内容齐全。
  }
  let directoryName = name.replace(/[/:\\]/g, '_').trim();
  if (!directoryName || directoryName === '.' || directoryName === '..') directoryName = `app_${appid}`;
  dest = path.resolve(dest || path.join(SN_GAMES, directoryName));
  const inputDest = dest, destinationRoot = realTarget(dest);
  const forbiddenRoots = [realTarget(STEAM), realTarget(SN_HOME)];
  const protectedTarget = target => target === realTarget(SN_GAMES) || forbiddenRoots.some(root => target === root || target.startsWith(root + path.sep));
  if (protectedTarget(destinationRoot)) throw new Error('交付目录不能是游戏根目录、源码仓库或 Steam 客户端目录；请指定新的游戏目录。');
  if (hasPayload(dest)) {
    let marker = '';
    try { marker = fs.readFileSync(path.join(dest, '.dryport-appid'), 'utf8').trim(); } catch {}
    if (marker !== appid) throw new Error(`目标目录已有内容且不属于这次下载，已保留：${dest}。请指定新的空目录。`);
    const verified = await verifyDelivery(dest, appid);
    const prior = JSON.parse(fs.readFileSync(path.join(dest, '.dryport-download.json'), 'utf8')).plan;
    const identity = value => JSON.stringify({scope:value.scope, selected:value.selected.map(p =>
      [p.depot, p.gid, p.download_appid, p.mount_order]), language:value.language, architecture:value.architecture});
    if (!plan || identity(prior) !== identity(plan)) throw new Error('已有副本与本次下载计划尚未确认一致，已保留；请使用新的目标目录，或运行 --verify-delivery 只读复核旧副本。');
    console.log(`已有副本的 ${verified.depots} 个内容包和文件字节复核通过：${dest}`);
    return;
  }
  if (fs.existsSync(dest)) throw new Error(`目标目录已存在，已保留：${dest}；请指定尚不存在的新目录。`);
  dest = destinationRoot;
  const ancestors = parentIdentities(dest);
  const assertDestination = () => {
    if (realTarget(inputDest) !== dest || protectedTarget(realTarget(dest))) throw new Error('目标路径在下载期间改变或进入受保护目录，已停止');
    for (const [directory, identity] of ancestors) {
      const st = fs.lstatSync(directory);
      if (!st.isDirectory() || st.isSymbolicLink() || st.dev !== identity[0] || st.ino !== identity[1]) throw new Error(`目标祖先在下载期间被替换，已停止：${directory}`);
    }
  };
  const st = new Steam();
  try {
    if (catalogProtocol >= 2) {
      const probe = spawnSync('python3', [path.join(SN_LIB, 'library_discovery.py'), '--license', appid],
                              {encoding:'utf8', timeout:25000});
      let license;
      try { license = JSON.parse(probe.stdout || ''); } catch {}
      const available = probe.status === 0 && license?.status === 'available' && license?.allowed === true;
      const deferred = probe.status === 3 && license?.status === 'unknown' && license?.allowed === false;
      const reasons = {not_subscribed:'Steam 当前没有提供这个游戏的订阅许可',
        sharing_excluded:'Steam 当前不允许这个游戏共享', in_use:'Steam 显示这个游戏的可用副本正在使用中'};
      // 子进程报 error 事件说明库读取本身没成功(多半客户端没就绪),按没准备好停;凭据格式问题照旧普通失败。
      if (license?.event === 'error' && license?.msg) throw notReadyError(String(license.msg));
      if (probe.error || license?.protocol !== 2 || (!available && !deferred) || String(license?.appid) !== appid)
        throw new Error(license?.msg || `${reasons[license?.status] || 'Steam 当前许可资料读取失败'}，未开始下载。`);
      if (license.account?.steamid64 !== catalogAccount?.steamid64 || license.account?.account_id !== catalogAccount?.account_id)
        throw new Error('许可核实时 Steam 账号已变化，未开始下载。请重新读取游戏库。');
      if (deferred) console.log('Steam 本地许可资料未齐，接下来由 Steam 下载服务确认授权。');
    }
    if (!plan) {
      const probe = spawnSync('python3', [path.join(SN_LIB, 'library_discovery.py'), '--depot-metadata', appid],
        {encoding:'utf8', timeout:60000, maxBuffer:16 * 1024 * 1024});
      let metadata;
      try { metadata = JSON.parse(probe.stdout || ''); } catch {}
      if (probe.error || probe.status !== 0 || metadata?.protocol !== 1 || String(metadata?.appid) !== appid
          || metadata.account?.steamid64 !== catalogAccount?.steamid64 || metadata.account?.account_id !== catalogAccount?.account_id) {
        throw new Error('安装内容包资料或当前账号核对失败；未开始下载。');
      }
      plan = planDepots(metadata);
    }
    // 计划定稿、还没连 Steam 时先算空间账;不够就停在本地,一条命令都不发给客户端。
    const stagedBytes = {};
    for (const item of plan.selected) {
      const kept = steamContentPaths(item).find(p => fs.existsSync(p));
      if (kept) stagedBytes[item.depot] = dirSize(kept);
    }
    const space = budget({selected:plan.selected, stagedBytes,
      freeStaging:freeBytes(STEAM), freeDest:freeBytes(path.dirname(dest)),
      sameVolume:sameVolume(STEAM, path.dirname(dest))});
    console.log(budgetMessage(space));
    if (!space.ok) { const error = new Error(budgetMessage(space)); error.exitCode = 4; throw error; }
    // 连不上多半是客户端没开或没登录:整段按没准备好定性;已带退出码的(如连接中断时 fail 的定性)不再包一层。
    try { await st.connect(); }
    catch (error) { throw error.exitCode ? error : notReadyError(error.message); }
    const verifyAccount = async () => {
      const account = await st.eval(identityExpression);
      if (!catalogAccount) {
        if (!account || typeof account.steamid64 !== 'string' || typeof account.account_id !== 'string'
            || !/^\d+$/.test(account.steamid64) || !/^\d+$/.test(account.account_id)
            || BigInt(account.account_id) <= 0n || BigInt(account.account_id) > 0xffffffffn
            || BigInt(account.steamid64) !== 76561197960265728n + BigInt(account.account_id)) {
          throw notReadyError('Steam 当前账号身份不可核实，未开始下载');
        }
        catalogAccount = account;
      }
      if (!account || account.steamid64 !== catalogAccount.steamid64 || account.account_id !== catalogAccount.account_id)
        throw new Error('Steam 账号与游戏清单不一致，已停止；请刷新当前账号游戏库。');
    };
    console.log(`内容计划：${plan.selected.length} 个包，${plan.architecture} 位${plan.language ? '，语言 ' + plan.language : ''}，范围 ${plan.scope}`);
    if (plan.language_reason.endsWith('fallback') || plan.language_reason === 'only_available_language') console.log(`语言选择依据：${plan.language_reason}`);
    if (plan.external_dependencies.length) console.log(`另有 ${plan.external_dependencies.length} 个 Windows 公共依赖，尚未准备；本次只交付基础游戏内容。`);
    console.log(JSON.stringify({event:'download_plan', plan}));
    const packages = [];
    for (const selected of plan.selected) {
      const {depot, gid, download_appid:downloadAppid} = selected;
      const expected = Number(selected.dl) || 0;
      const contentCandidates = steamContentPaths(selected);
      const liveContent = () => contentCandidates.find(p => fs.existsSync(p));
      await verifyAccount();
      console.log(`下载 ${name}（appid ${downloadAppid} depot ${depot}）`);
      // 完整内容由下方逐文件清单与哈希核验，不改客户端的全局下载过滤设置。
      await st.clear();
      await st.cmd(`download_depot ${downloadAppid} ${depot} ${gid}`);
      let completion = '', last = -1, still = 0, netFailures = 0;
      for (let i = 0; i < 3600 && !completion; i++) {
        await sleep(5000);
        const output = await st.out();
        const stopAt = liveContent() || contentCandidates[0];
        const sz = dirSize(stopAt);
        if (sz !== last) { last = sz; still = 0; netFailures = 0; } else still++;
        process.stdout.write(`\r  暂存 ${mb(sz)} MB${expected > 0 ? ' / 压缩后约 ' + mb(expected) + ' MB' : ''}   `);
        completion = output.split(/\r?\n/).find(line => /Depot download complete/i.test(line)
          && new RegExp(`(?:^|/)app_${downloadAppid}/depot_${depot}(?=[/"'\\s)]|$)`).test(normalize(line))) || '';
        if (!completion) {
          const verdict = classifySpew(output);
          if (verdict === 'disk_full') throw diskFullError(depot, stopAt, sz);
          if (verdict === 'failed') throw new Error('Steam 下载失败：' + output.slice(-200).trim());
          // Steam 不一定报得准;可用空间跌破保底也要按写满停,暂存留给重跑续命。
          if (freeBytes(STEAM) < RESERVE_MIN / 4) throw diskFullError(depot, stopAt, sz);
        }
        // 约一分钟没有新字节才探一次网;连续三次不可达按断网停,有进展或可达就清零,十分钟停滞规则照旧兜底。
        if (still > 0 && still % 12 === 0) {
          if (await networkReachable()) netFailures = 0;
          else if (++netFailures >= 3) throw networkDownError(depot, stopAt, sz);
        }
        if (still > 120 && !completion) throw new Error('十分钟没有新数据，Steam 尚未确认完成；暂存保留，未交付游戏副本。');
      }
      console.log();
      if (!completion) throw new Error('等待下载完成超时；Steam 尚未确认完成，暂存保留。');
      await verifyAccount();
      const available = contentCandidates.filter(p => fs.existsSync(p) && entries(p));
      const content = available.find(p => normalize(completion).includes(normalize(p))) || (available.length === 1 ? available[0] : undefined);
      if (!content) throw new Error(`Steam 报告完成但没有唯一对应的暂存目录：depot ${depot}`);
      const rawManifest = path.join(STEAM, 'depotcache', `${depot}_${gid}.manifest`);
      const textManifest = rawManifest + '.txt';
      let manifest;
      const readManifest = () => {
        if (fs.lstatSync(textManifest).isSymbolicLink()) throw new Error('内容清单是符号链接，已停止');
        return parseManifest(fs.readFileSync(textManifest), selected);
      };
      try { manifest = readManifest(); } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        if (!fs.existsSync(rawManifest) || /["\r\n]/.test(rawManifest)) throw new Error(`缺少 Steam 原始内容清单：depot ${depot}`);
        await st.clear(); await st.cmd(`manifest_dump "${rawManifest}"`);
        for (let i = 0; i < 60 && !manifest; i++) {
          await sleep(500);
          try { manifest = readManifest(); } catch (retry) { if (retry.code !== 'ENOENT' && i === 59) throw retry; }
        }
        if (!manifest) throw new Error(`Steam 没有输出可验证的内容清单：depot ${depot}`);
      }
      const verification = await verifyDepot(content, manifest);
      packages.push({plan:selected, manifest, root:content, verification});
    }
    if (fs.existsSync(dest) || entries(dest).length) throw new Error(`目标在下载期间出现，已停止并保留原件：${dest}`);
    assertDestination();
    const missing = [];
    for (let directory = path.dirname(dest); !fs.existsSync(directory); directory = path.dirname(directory)) missing.unshift(directory);
    for (const directory of missing) {
      assertDestination();
      fs.mkdirSync(directory);
      const st = fs.lstatSync(directory); ancestors.set(directory, [st.dev, st.ino]);
    }
    assertDestination();
    const staging = fs.mkdtempSync(path.join(path.dirname(dest), '.dryport-download-'));
    const stagedGame = path.join(staging, 'game');
    try {
      const merged = await mergeDepots(packages, stagedGame, {...plan, assertDestination});
      const receipt = {protocol:1, appid, complete:true, plan,
        packages:packages.map(pack => ({...pack.verification, manifest_files:pack.manifest.files})), ...merged};
      assertDestination();
      fs.writeFileSync(path.join(stagedGame, '.dryport-download.json'), JSON.stringify(receipt), {flag:'wx', mode:0o600});
      fs.writeFileSync(path.join(stagedGame, '.dryport-appid'), appid, {flag:'wx'});
      await verifyDelivery(stagedGame, appid);
      await verifyAccount();
      assertDestination();
      publishExclusive(stagedGame, dest);
    } catch (error) {
      // ENOSPC 是磁盘写满,不是交付缺陷:退出码 4 让外层按空间问题停步,不再退回 steamcmd。
      const full = error.code === 'ENOSPC';
      const out = new Error(`${full ? '交付副本时磁盘写满；' : ''}游戏副本未交付：${error.message}。原始暂存保留；复制现场：${staging}`);
      // 其余情况透传原定性(如交付前核对账号撞上没就绪的 5),别在包装时丢掉。
      if (full) out.exitCode = 4; else out.exitCode = error.exitCode;
      throw out;
    }
    try { fs.rmdirSync(staging); } catch {}
    console.log(`已交付副本：${dest}（${mb(dirSize(dest))} MB，全部所选包已校验，Steam 原始暂存保留）`);
    console.log(`接下来： dryport wrap "${dest}"`);
  } finally { await st.close(); }
}
try {
  if (process.argv[2] === '--verify-delivery') {
    const appid = numericId(process.argv[3], 0xffffffffn, 'appid');
    if (!process.argv[4]) throw new Error('缺少待复核的游戏目录');
    console.log(JSON.stringify(await verifyDelivery(path.resolve(process.argv[4]), appid)));
  } else await main();
}
catch (error) { console.error(`下载未完成：${error.message}`); process.exitCode = error.exitCode || 1; }
