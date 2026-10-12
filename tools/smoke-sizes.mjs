// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 给自动冒烟算每款游戏的基础包体积：对 windows-library.json 逐行调 lib/depot-plan.mjs 的 planDepots（与真实下载同一套选包逻辑），
// 输出 NDJSON {appid, size, dl, depots, error}。抛错的行（多语言无英文、共享包缺来源、mount_order 不全）真下载时也会抛，调用方直接排除。
// 用法：node tools/smoke-sizes.mjs <windows-library.json> [language]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planDepots } from '../lib/depot-plan.mjs';

const [file, language] = process.argv.slice(2);
if (!file) { console.error('用法: smoke-sizes.mjs <windows-library.json> [language]'); process.exit(2); }
const rows = JSON.parse(fs.readFileSync(file, 'utf8'));
const shared = {};
for (const row of rows) if (row.sharedApps) Object.assign(shared, row.sharedApps);
for (const row of rows) {
  const out = { appid: String(row.appid) };
  try {
    const plan = planDepots(row, { language: language || undefined, sharedApps: row.sharedApps || shared });
    out.size = plan.selected.reduce((a, d) => a + (Number(d.size) || 0), 0);
    out.dl = plan.selected.reduce((a, d) => a + (Number(d.dl) || 0), 0);
    out.depots = plan.selected.length;
    out.language = plan.language;
  } catch (error) {
    out.error = String(error.message || error).slice(0, 160);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
}
