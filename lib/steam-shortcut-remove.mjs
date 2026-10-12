#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// Steam 运行时必须由官方接口删除，单改 VDF 会被客户端内存里的旧条目写回。
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {evaluate, identityExpression} from './steam-library.mjs';

export function removalExpression({uid, exe, ids}) {
  if (!/^\d+$/.test(uid) || BigInt(uid) > 4294967295n || typeof exe !== 'string'
      || !path.isAbsolute(exe) || /[\0\r\n]/.test(exe) || !Array.isArray(ids)
      || ids.some(id => !Number.isInteger(id) || id <= 0 || id > 4294967295)
      || new Set(ids).size !== ids.length)
    throw new Error('快捷方式删除凭据无效');
  return `(async () => {
    const expected = ${JSON.stringify({uid, exe, ids})};
    const identity = () => ${identityExpression};
    const requireAccount = () => {
      if (identity().account_id !== expected.uid) throw new Error('Steam 当前登录账号与删除备份不一致');
    };
    const shortcuts = () => {
      const apps = appStore.allApps;
      if (!Array.isArray(apps) || apps.some(a => typeof a?.BIsShortcut !== 'function'))
        throw new Error('Steam 快捷方式读取接口已变化');
      return apps.filter(a => a.BIsShortcut());
    };
    const details = async appid => {
      let registration, timer;
      try {
        return await new Promise((resolve, reject) => {
          timer = setTimeout(() => reject(new Error('快捷方式路径回读超时')), 6000);
          registration = SteamClient.Apps.RegisterForAppDetails(appid, value => {
            if (value?.unAppID === appid) resolve(value);
          });
        });
      } finally { clearTimeout(timer); registration?.unregister(); }
    };
    const matches = value => value === expected.exe || value === '"' + expected.exe + '"';
    const same = (a, b) => {
      const right = [...b].sort((x,y) => x-y);
      return a.length === b.length && [...a].sort((x,y) => x-y).every((id,i) => id === right[i]);
    };
    requireAccount();
    if (typeof SteamClient.Apps.RemoveShortcut !== 'function') throw new Error('Steam 没有可用的官方快捷方式删除接口');
    const before = shortcuts().map(a => a.appid);
    if (before.some(id => !Number.isInteger(id) || id <= 0 || id > 4294967295)
        || new Set(before).size !== before.length) throw new Error('Steam 快捷方式身份不完整');
    const settled = await Promise.allSettled(before.map(details));
    const failed = settled.find(result => result.status === 'rejected');
    if (failed) throw failed.reason;
    const values = settled.map(result => result.value);
    requireAccount();
    if (!same(before, shortcuts().map(a => a.appid))
        || values.some(v => typeof v.strShortcutExe !== 'string')) throw new Error('读取期间快捷方式发生变化');
    const matching = values.filter(v => matches(v.strShortcutExe)).map(v => v.unAppID);
    if (!same(matching, expected.ids)) throw new Error('客户端与磁盘的目标快捷方式不一致，未删除');
    for (const id of expected.ids) {
      requireAccount();
      if (appStore.GetAppOverviewByAppID(id)?.BIsShortcut() !== true)
        throw new Error('目标已不再是非 Steam 快捷方式');
      SteamClient.Apps.RemoveShortcut(id);
    }
    // RemoveShortcut 返回 void，必须等内存中实际消失，不能把调用成功当作删除成功。
    const remaining = before.filter(id => !expected.ids.includes(id));
    const deadline = Date.now() + 6000;
    while (true) {
      requireAccount();
      const current = shortcuts().map(a => a.appid);
      if (same(current, remaining) && expected.ids.every(id => !appStore.GetAppOverviewByAppID(id)))
        return {protocol: 2, uid: expected.uid, removed_ids: expected.ids, client_verified: true};
      if (Date.now() >= deadline) throw new Error('Steam 尚未确认移除目标，app 与备份已保留');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  })()`;
}

export async function remove(exe) {
  const lib = process.env.SN_LIB || path.dirname(fileURLToPath(import.meta.url));
  const checker = path.join(lib, 'steam-shortcut-check.py');
  const check = args => {
    const result = spawnSync('python3', [checker, ...args], {encoding: 'utf8', timeout: 15000, maxBuffer: 4 * 1024 * 1024});
    if (result.error || result.status !== 0) throw new Error(result.stderr?.trim() || result.error?.message || '快捷方式回读失败');
    return result.stdout.trim();
  };
  const backup = check(['prepare-remove', exe]);
  try {
    const receipt = JSON.parse(fs.readFileSync(backup, 'utf8'));
    if (receipt.protocol !== 1 || receipt.action !== 'remove' || receipt.exe !== fs.realpathSync(exe))
      throw new Error('删除备份与指定 app 不符');
    const result = await evaluate(removalExpression(receipt), 18000);
    if (result.client_verified !== true || result.uid !== receipt.uid
        || JSON.stringify(result.removed_ids) !== JSON.stringify(receipt.ids)) throw new Error('Steam 删除回执不符');
    check(['verify-remove', backup]);
    return {protocol: 1, removed: receipt.ids.length, backup, client_verified: true, disk_verified: true};
  } catch (error) {
    throw new Error(error.message + '；原始快捷方式备份：' + backup);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3) throw new Error('用法: steam-shortcut-remove.mjs <自有 app 路径>');
    console.log(JSON.stringify(await remove(process.argv[2])));
  } catch (error) {
    console.error('快捷方式未完成移除：' + error.message);
    process.exitCode = 1;
  }
}
