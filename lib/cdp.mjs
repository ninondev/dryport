// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// Minimal CDP client for the Steam client's SharedJSContext. usage: node cdp.mjs '<js expression>'
import http from 'node:http';
const get = (p) => new Promise((res, rej) => http.get({host:'127.0.0.1',port:8080,path:p}, r => {let b='';r.on('data',c=>b+=c);r.on('end',()=>res(JSON.parse(b)))}).on('error',rej));
const targets = await get('/json');
const t = targets.find(x => x.title === 'SharedJSContext');
if (!t) { console.error('SharedJSContext not found'); process.exit(1); }
const { WebSocket } = await import('node:worker_threads').then(()=>({WebSocket: globalThis.WebSocket}));
const ws = new WebSocket(t.webSocketDebuggerUrl);
const expr = process.argv[2];
await new Promise(r => ws.addEventListener('open', r));
ws.send(JSON.stringify({id:1, method:'Runtime.evaluate', params:{expression:expr, awaitPromise:true, returnByValue:true, allowUnsafeEvalBlockedByCSP:true}}));
const out = await new Promise(r => ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id===1) r(m); }));
ws.close();
if (out.result?.exceptionDetails) { console.error('EXC', JSON.stringify(out.result.exceptionDetails.exception?.description || out.result.exceptionDetails).slice(0,600)); process.exit(2); }
const v = out.result?.result?.value;
console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 1));
