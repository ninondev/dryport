#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 已登录客户端的只读订阅凭据。appinfo 缓存没有账号归属，不能代替这一步。
import http from 'node:http';
import { pathToFileURL } from 'node:url';

export const identityExpression = `(() => {
  const store = globalThis.appStore;
  const cm = store?.m_cm;
  const id = cm?.steamid;
  if (!store?.m_bIsInitialized || cm?.logged_on !== true || !id?.BIsIndividualAccount() || !id.BIsValid())
    throw new Error('Steam 尚未登录或游戏库尚未加载；请打开库页面后重试');
  return { steamid64: id.ConvertTo64BitString(), account_id: String(id.GetAccountID()) };
})()`;

export const libraryExpression = `(() => {
  const account = ${identityExpression};
  const apps = globalThis.appStore.allApps;
  if (!Array.isArray(apps)) throw new Error('Steam 游戏库接口已变化，请更新 dryport');
  const library = [];
  let owned_count = 0, shared_count = 0, shortcuts = 0, unsubscribed = 0;
  for (const app of apps) {
    if (typeof app.BIsBorrowed !== 'function' || typeof app.BIsShortcut !== 'function')
      throw new Error('Steam 拥有状态接口已变化，请更新 dryport');
    if (app.BIsShortcut()) { shortcuts++; continue; }
    if (app.subscribed_to !== true) { unsubscribed++; continue; }
    // protobuf 默认值会省略；客户端自己也用 !owner_account_id 表示非借用来源。
    const owner = app.owner_account_id === undefined && app.BIsBorrowed() === false ? 0 : app.owner_account_id;
    if (!Number.isInteger(owner) || owner < 0 || owner > 4294967295 || String(owner) === account.account_id)
      throw new Error('Steam 许可来源无法确认，未生成清单');
    const borrowed = owner !== 0;
    if (!Number.isInteger(app.appid) || app.appid <= 0 || app.appid > 4294967295)
      throw new Error('Steam 返回了无效 appid，未生成清单');
    if (typeof app.display_name !== 'string' || !Number.isInteger(app.app_type))
      throw new Error('Steam 游戏资料格式已变化，请更新 dryport');
    borrowed ? shared_count++ : owned_count++;
    library.push({appid: String(app.appid), name: app.display_name, app_type: app.app_type,
      subscribed: true, borrowed, owner_account_id: String(owner),
      ownership: borrowed ? 'borrowed' : 'owned', license_source: borrowed ? 'family_shared' : 'self',
      availability: 'not_checked'});
  }
  const after = ${identityExpression};
  if (account.steamid64 !== after.steamid64) throw new Error('Steam 账号发生切换，未生成清单；请重试');
  return {protocol: 2, account, apps: library, owned_count, shared_count,
    excluded: {shortcuts, unsubscribed}, observed_apps: apps.length};
})()`;

export function licenseExpression(appid) {
  if (!/^\d+$/.test(String(appid)) || BigInt(appid) <= 0n || BigInt(appid) > 4294967295n)
    throw new Error('appid 必须是有效的正整数');
  appid = BigInt(appid).toString();
  return `(async () => {
    const account = ${identityExpression};
    const appid = ${String(appid)};
    const overview = globalThis.appStore.GetAppOverviewByAppID(appid);
    const rawOwner = overview?.owner_account_id === undefined && overview?.BIsBorrowed?.() === false ? 0 : overview?.owner_account_id;
    if (!overview || !Number.isInteger(rawOwner) || rawOwner < 0
        || rawOwner > 4294967295 || String(rawOwner) === account.account_id)
      throw new Error('Steam 没有可确认的游戏许可来源，请刷新游戏库');
    const owner = String(rawOwner), borrowed = owner !== '0';
    let registration, timer;
    try {
      const details = new Promise(resolve => {
        registration = SteamClient.Apps.RegisterForAppDetails(appid, value => {
          if (value?.unAppID === appid) resolve(value);
        });
      });
      const [subscribed, lenders, detail] = await Promise.race([
        Promise.all([SteamClient.Apps.GetIsSubscribedApp(appid), SteamClient.FamilySharing.GetAvailableLenders(appid), details]),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Steam 即时许可检查超时，请重试')), 6000); })
      ]);
      const after = ${identityExpression};
      const afterOwner = overview.owner_account_id === undefined && overview.BIsBorrowed?.() === false ? 0 : overview.owner_account_id;
      if (after.steamid64 !== account.steamid64 || String(afterOwner) !== owner)
        throw new Error('Steam 账号或许可来源已变化，请刷新游戏库');
      const idType = appStore.m_cm.steamid.constructor;
      const lock = new idType(detail.strLockedBySteamID);
      const ownerID = new idType(detail.strOwnerSteamID);
      const locked_by_other = lock.BIsValid() && lock.ConvertTo64BitString() !== account.steamid64;
      const source_confirmed = ownerID.BIsValid() && (borrowed
        ? String(ownerID.GetAccountID()) === owner && Array.isArray(lenders)
          && lenders.some(lender => lender.steamid === detail.strOwnerSteamID)
        : detail.strOwnerSteamID === account.steamid64);
      let status = 'unknown';
      if (subscribed === false || detail.bIsSubscribedTo === false) status = 'not_subscribed';
      else if (borrowed && detail.bIsExcludedFromSharing === true) status = 'sharing_excluded';
      else if (locked_by_other) status = 'in_use';
      else if (subscribed === true && detail.bIsSubscribedTo === true && source_confirmed
               && (!borrowed || detail.bIsExcludedFromSharing === false)) status = 'available';
      return {protocol: 2, account, appid: String(appid), ownership: borrowed ? 'borrowed' : 'owned',
        license_source: borrowed ? 'family_shared' : 'self', owner_account_id: owner,
        subscribed: subscribed === true && detail.bIsSubscribedTo === true,
        sharing_excluded: detail.bIsExcludedFromSharing, locked_by_other,
        member_copies: detail.unMemberCopies, members_playing: detail.unMembersPlaying,
        lender_count: Array.isArray(lenders) ? lenders.length : null, source_confirmed,
        status, allowed: status === 'available', checked_at: new Date().toISOString()};
    } finally { clearTimeout(timer); registration?.unregister(); }
  })()`;
}

function getTargets() {
  return new Promise((resolve, reject) => {
    const req = http.get({host: '127.0.0.1', port: 8080, path: '/json', timeout: 5000}, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 4 * 1024 * 1024) req.destroy(new Error('Steam 调试目标响应过大'));
      });
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw new Error(`Steam 调试接口状态 ${response.statusCode}`);
          const targets = JSON.parse(body);
          if (!Array.isArray(targets)) throw new Error('Steam 调试目标格式无效');
          resolve(targets);
        } catch (error) { reject(error); }
      });
      response.on('error', reject);
    });
    req.on('timeout', () => req.destroy(new Error('Steam 调试接口超时；请打开 Steam 后重试')));
    req.on('error', reject);
  });
}

// 客户端刚重连、库页面还没初始化时，同一句求值会瞬时报「尚未登录或游戏库尚未加载」（2026-09-13 剥离环境第五轮就撞上一次，
// 11 分钟后原样通过）。这几种瞬时错误重试几次再放弃；别的错误照旧立刻报。
const TRANSIENT = ['尚未登录或游戏库尚未加载', '找不到唯一的 Steam SharedJSContext', '请求超时', '调试连接提前关闭'];
export async function collect(identityOnly = false, appid = null, attempts = 4, waitMs = 5000) {
  const expression = appid !== null ? licenseExpression(appid)
    : identityOnly ? `({protocol:2,account:${identityExpression}})` : libraryExpression;
  for (let i = 1; ; i++) {
    try {
      return await evaluate(expression);
    } catch (error) {
      const transient = TRANSIENT.some(t => String(error?.message || '').includes(t));
      if (!transient || i >= attempts) throw error;
      process.stderr.write(`Steam 客户端暂未就绪（${error.message}），${waitMs / 1000} 秒后重试（${i}/${attempts - 1}）\n`);
      await new Promise(resolve => setTimeout(resolve, waitMs));
    }
  }
}

// V8 的异常描述把消息和「    at」堆栈行揉在一起，整段塞进用户提示会淹没真正的原因；
// 只取首行，再剥掉一个「Error: 」「TypeError: 」这类错误名前缀，剩下的才是该给用户看的那句话。
export function evalErrorText(details, fallback) {
  const first = String(details?.exception?.description ?? details?.text ?? fallback).split('\n', 1)[0].trim();
  return first.replace(/^(?:[A-Za-z]+)?Error: /, '').trim();
}

// 共用有界本机 CDP 连接；删除快捷方式也须同时核对客户端内存与官方落盘结果。
export async function evaluate(expression, timeout = 10000) {
  if (typeof WebSocket !== 'function') throw new Error('Node.js 缺少 WebSocket，请使用 Node.js 22 或更新版本');
  const matches = (await getTargets()).filter(target => target.title === 'SharedJSContext');
  if (matches.length !== 1) throw new Error('找不到唯一的 Steam SharedJSContext；请打开 Steam 库并启用客户端调试口后重试');
  const url = new URL(matches[0].webSocketDebuggerUrl);
  if (url.protocol !== 'ws:' || !['localhost', '127.0.0.1'].includes(url.hostname) || url.port !== '8080')
    throw new Error('Steam 返回了非本机调试连接，已停止');
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer); ws.close();
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('Steam 客户端请求超时；请等待库加载后重试')), timeout);
    ws.addEventListener('open', () => ws.send(JSON.stringify({id: 1, method: 'Runtime.evaluate', params: {
      expression, awaitPromise: true, returnByValue: true,
    }})));
    ws.addEventListener('error', () => finish(new Error('Steam 调试连接失败；请检查客户端是否仍在运行')));
    ws.addEventListener('close', () => finish(new Error('Steam 调试连接提前关闭，未生成清单')));
    ws.addEventListener('message', event => {
      try {
        const message = JSON.parse(event.data);
        if (message.id !== 1) return;
        if (message.error || message.result?.exceptionDetails)
          throw new Error(evalErrorText(message.result?.exceptionDetails, message.error?.message || 'Steam 库读取失败'));
        const value = message.result?.result?.value;
        if (!value || value.protocol !== 2) throw new Error('Steam 没有返回有效许可凭据');
        finish(null, value);
      } catch (error) { finish(error); }
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const args = process.argv.slice(2);
    const license = args[0] === '--license' && args.length === 2;
    if (args.length && !(args.length === 1 && args[0] === '--identity') && !license)
      throw new Error('用法: node steam-library.mjs [--identity | --license <appid>]');
    const value = await collect(args[0] === '--identity', license ? args[1] : null);
    console.log(JSON.stringify(value));
    if (license && !value.allowed) process.exitCode = 3;
  } catch (error) {
    console.error(`Steam 游戏库读取失败：${error.message}`);
    process.exitCode = 3;
  }
}
