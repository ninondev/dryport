// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// steam-add.mjs <名字> <.app 路径>  — 通过 Steam CDP 添加非 Steam 游戏,打印新 appid
const [name, exe] = process.argv.slice(2);
if (!name || !exe) { console.error('用法: steam-add.mjs <名字> <.app 路径>'); process.exit(2); }
const list = await (await fetch('http://localhost:8080/json/list')).json();
const t = list.find(x => x.title === 'SharedJSContext');
if (!t) { console.error('没找到 SharedJSContext (Steam 的 CDP 未就绪)'); process.exit(1); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
let id = 0; const pend = new Map();
const send = (m, p={}) => new Promise(r => { pend.set(++id, r); ws.send(JSON.stringify({id, method:m, params:p})); });
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; setTimeout(() => rej(new Error('连接超时')), 10000); });
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const expr = `(async()=>{
  const id = await SteamClient.Apps.AddShortcut(${JSON.stringify(name)}, ${JSON.stringify(exe)}, "", "");
  try { await SteamClient.Apps.SetShortcutStartDir(id, ${JSON.stringify(exe.replace(/\/[^/]+$/, '/'))}); } catch(e){}
  return String(id);
})()`;
const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
const v = r?.result?.result?.value;
if (!v) { console.error('AddShortcut 失败:', JSON.stringify(r?.result?.exceptionDetails ?? r).slice(0,300)); process.exit(1); }
console.log(v);
ws.close(); process.exit(0);
