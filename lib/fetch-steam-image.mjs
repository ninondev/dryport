// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 借 Steam 客户端的网络栈下载图片(客户端能连 Steam CDN, 本机直连被墙)
const [appid, outPath] = process.argv.slice(2);
const list = await (await fetch('http://localhost:8080/json/list')).json();
const t = list.find(x => (x.title||'').trim() === 'SharedJSContext');
if (!t) { console.error('无 SharedJSContext'); process.exit(1); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
const expr = `(async () => {
  const cands = [
    "https://cdn.akamai.steamstatic.com/steam/apps/${appid}/library_600x900.jpg",
    "https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/${appid}/library_600x900.jpg",
    "https://cdn.akamai.steamstatic.com/steam/apps/${appid}/header.jpg",
    "https://cdn.akamai.steamstatic.com/steam/apps/${appid}/capsule_231x87.jpg"
  ];
  for (const u of cands) {
    try {
      const r = await fetch(u);
      if (!r.ok) continue;
      const b = await r.arrayBuffer();
      if (b.byteLength < 2000) continue;
      let s = ''; const a = new Uint8Array(b);
      for (let i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
      return JSON.stringify({ok:true, url:u, size:b.byteLength, b64:btoa(s)});
    } catch(e) {}
  }
  return JSON.stringify({ok:false});
})()`;
const out = await new Promise((res, rej) => {
  ws.onopen = () => ws.send(JSON.stringify({id:1, method:'Runtime.evaluate',
    params:{expression:expr, awaitPromise:true, returnByValue:true}}));
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id===1) res(m.result?.result?.value); };
  ws.onerror = rej; setTimeout(()=>rej(new Error('超时')), 40000);
});
ws.close();
const r = JSON.parse(out || '{"ok":false}');
if (!r.ok) { console.error('客户端也取不到'); process.exit(1); }
const fs = await import('fs');
fs.writeFileSync(outPath, Buffer.from(r.b64, 'base64'));
console.log(`  ✅ ${r.size} 字节  来源 ${r.url.split('/').pop()}`);
process.exit(0);
