#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* manifest-oracle — 不下载游戏本体，只把 depot 的文件清单要过来，用来判断引擎。
 *
 * 原理：Steam 客户端自带的 download_depot 会先取 manifest 再下内容；把 cvar
 * sDepotDownloadFileFilter 设成一个匹配不到任何文件的模式，内容部分就是 0 个文件 0 MB，
 * 而 manifest 照样落进 depotcache。再用 manifest_dump 解成文本，文件名列表就有了。
 * 用的是你已经登录的 Steam 客户端，不碰密码，不装 steamcmd，不动已装游戏。
 *
 * 用法: node manifest-oracle.mjs <清单json> [起点] [条数]
 *   清单 json 是 [{appid, depot, gid, name}, ...]
 * 结果逐条追加到 ~/Games/manifest-oracle-results.jsonl，可中断可续跑。
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const STEAM = path.join(os.homedir(), 'Library/Application Support/Steam');
const DEPOTCACHE = path.join(STEAM, 'depotcache');
const OUT = path.join(os.homedir(), 'Games/manifest-oracle-results.jsonl');
const FILTER = '__manifest_only__';

const get = (p) => new Promise((res, rej) =>
  http.get({ host: '127.0.0.1', port: 8080, path: p }, r => { let b = ''; r.on('data', c => b += c); r.on('end', () => res(JSON.parse(b))); }).on('error', rej));

class Steam {
  async connect() {
    const t = (await get('/json')).find(x => x.title === 'SharedJSContext');
    if (!t) throw new Error('SharedJSContext 不在，Steam 没开或调试口没打开');
    this.ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise(r => this.ws.addEventListener('open', r));
    this.id = 0; this.pending = new Map();
    this.ws.addEventListener('message', e => {
      const m = JSON.parse(e.data);
      if (m.id && this.pending.has(m.id)) { this.pending.get(m.id)(m); this.pending.delete(m.id); }
    });
    await this.eval(`window.__mo = window.__mo || [];
      if (!window.__moReg) window.__moReg = SteamClient.Console.RegisterForSpewOutput(s => {
        try { const o = typeof s === 'string' ? JSON.parse(s) : s; window.__mo.push(o.spew || ''); } catch (e) { window.__mo.push(String(s)); }
        if (window.__mo.length > 3000) window.__mo.splice(0, 1500);
      }); 1`);
  }
  eval(expr) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
    return new Promise(r => this.pending.set(id, m => r(m.result?.result?.value)));
  }
  cmd(c) { return this.eval(`SteamClient.Console.ExecCommand(${JSON.stringify(c)}); 1`); }
  spew() { return this.eval('window.__mo.join("")'); }
  clear() { return this.eval('window.__mo.length = 0; 1'); }
  close() { this.ws.close(); }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function fetchManifest(st, { appid, depot, gid }) {
  const file = path.join(DEPOTCACHE, `${depot}_${gid}.manifest`);
  if (!fs.existsSync(file)) {
    await st.clear();
    await st.cmd(`download_depot ${appid} ${depot} ${gid}`);
    let done = false;
    for (let i = 0; i < 40 && !done; i++) {          // 最多等 40 秒
      await sleep(1000);
      const s = await st.spew();
      if (/Depot download complete|fail|error|not available|no license|no subscription/i.test(s)) done = true;
      if (fs.existsSync(file)) done = true;
    }
    if (!fs.existsSync(file)) {
      const why = (await st.spew()).slice(-180);
      // 内容服务器偶尔掉线；等一会再试一次，还不行才算失败
      if (/no connection|timeout/i.test(why)) {
        await sleep(8000); await st.clear(); await st.cmd(`download_depot ${appid} ${depot} ${gid}`);
        for (let i = 0; i < 25 && !fs.existsSync(file); i++) {
          await sleep(1000);
          const s2 = await st.spew();
          if (/Depot download complete|fail|error|no license|no subscription/i.test(s2)) break;
        }
      }
      if (!fs.existsSync(file)) return { ok: false, why };
    }
  }
  const txt = file + '.txt';
  if (!fs.existsSync(txt)) {
    await st.clear();
    await st.cmd(`manifest_dump "${file}"`);
    for (let i = 0; i < 15 && !fs.existsSync(txt); i++) await sleep(400);
  }
  if (!fs.existsSync(txt)) return { ok: false, why: 'manifest_dump 没产出文本' };
  const NAME = /^\s*[\d,]+\s+\d+\s+[0-9a-f]{40}\s+\d+\s+(.+?)\s*$/;
  const files = [];
  for (const l of fs.readFileSync(txt, 'latin1').split('\n')) { const m = NAME.exec(l); if (m) files.push(m[1]); }
  return { ok: true, files };
}

// 文件名 → 引擎。只用不会撞车的特征。
const RULES = [
  ['Unity-IL2CPP', /(^|[\\/])GameAssembly\.dll$|global-metadata\.dat$/i, 'wine'],
  ['Unity-Mono', /(^|[\\/])Assembly-CSharp\.dll$|MonoBleedingEdge[\\/]/i, 'unity'],
  ['Unity', /(^|[\\/])UnityPlayer\.dll$|unity default resources$/i, 'unity'],
  ['Unreal', /-(Win64|Win32|WinGDK)-Shipping\.exe$|[\\/]Engine[\\/]Binaries[\\/]/i, 'wine'],
  ['RPGMaker-MVMZ', /(^|[\\/])nw\.dll$|rpg_core\.js$|rmmz_core\.js$/i, 'nwjs'],
  ['RPGMaker-RGSS', /\.rgss[0-9]?a$|RGSS[0-9]{3}[EJ]?\.dll$/i, 'mkxp'],
  ["Ren'Py", /(^|[\\/])renpy[\\/]|\.rpa$|\.rpyc$/i, 'renpy'],
  ['Godot', /\.pck$|godot[^\\/]*\.exe$/i, 'godot'],
  ['KiriKiri', /\.xp3$|(^|[\\/])(krkr|kirikiri)/i, 'krkr'],
  ['GameMaker', /(^|[\\/])data\.win$/i, 'maybe'],
  ['WolfRPG', /(^|[\\/])Data\.wolf$|GuruguruSMF4\.dll$/i, 'maybe'],
  ['NScripter', /(^|[\\/])(nscript\.dat|arc\.nsa|onscripter)/i, 'maybe'],
  ['AdobeAIR', /Adobe AIR\.dll$/i, 'wine'],
  ['EasyAntiCheat', /EasyAntiCheat|start_protected_game\.exe$/i, 'refuse'],
  ['BattlEye', /BEService|BEClient/i, 'refuse'],
];
function classify(files) {
  const hits = [];
  for (const [name, rx, route] of RULES) {
    const f = files.find(x => rx.test(x));
    if (f) hits.push({ engine: name, route, evidence: f.slice(0, 90) });
  }
  if (!hits.length) return { engine: '未识别', route: 'unknown', evidence: `${files.length} 个文件，无已知特征` };
  const bad = hits.find(h => h.route === 'refuse'); if (bad) return bad;
  return hits[0];
}

const list = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const from = parseInt(process.argv[3] || '0', 10);
const count = parseInt(process.argv[4] || String(list.length), 10);
const done = new Set(fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8').trim().split('\n').filter(Boolean).map(l => JSON.parse(l).appid) : []);
const st = new Steam(); await st.connect();
let reset = async () => { await st.cmd('sDepotDownloadFileFilter ""'); };
process.on('SIGINT', async () => { await reset(); process.exit(130); });
await st.cmd(`sDepotDownloadFileFilter ${FILTER}`);
let n = 0, ok = 0, fail = 0, streak = 0;
try {
  for (const g of list.slice(from, from + count)) {
    if (done.has(g.appid)) continue;
    n++;
    const r = await fetchManifest(st, g);
    const rec = r.ok ? { appid: g.appid, name: g.name, ...classify(r.files), nfiles: r.files.length }
                     : { appid: g.appid, name: g.name, engine: '取不到', route: 'unknown', evidence: r.why };
    r.ok ? (ok++, streak = 0) : fail++;
    // 掉线/超时不写进结果，留给下次重试；只有真结论和「没许可」才落盘
    if (!/No connection|timeout/i.test(rec.evidence || '')) fs.appendFileSync(OUT, JSON.stringify(rec) + '\n');
    else {
      streak++;
      // 连着掉线说明是内容服务器在限流，退避时间翻倍，最长歇 15 分钟
      // 实测：连着掉线是 Steam 的内容连接被限流，重启 Steam 客户端就恢复（人工重启即可）。
      const wait = Math.min(90000 * Math.pow(2, Math.min(streak - 1, 3)), 600000);
      console.log(`[${new Date().toTimeString().slice(0,8)}] 内容服务器掉线第 ${streak} 次，歇 ${Math.round(wait/1000)} 秒（${g.name}）`);
      await sleep(wait);
    }
    if (n % 25 === 0) console.log(`[${new Date().toTimeString().slice(0, 8)}] ${n} 个：成功 ${ok} 失败 ${fail}  最近 ${rec.name} → ${rec.engine}`);
    await sleep(1200);   // 对 Steam 的内容服务器客气一点，太快会被限流
  }
} finally {
  await reset();
  const v = await st.eval('1');
  console.log(`完成 ${n} 个：成功 ${ok}，失败 ${fail}。过滤器已复位。`);
  st.close();
}
