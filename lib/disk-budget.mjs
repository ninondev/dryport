// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* 磁盘空间核算与写满定性:纯计算加只读 stat,不碰 Steam、不发网络。
 * 暂存池(STEAM)先放 Steam 解压出的完整内容,交付池(目标目录所在卷)随后再放一份;
 * 同一卷上两个阶段先后共用同一块空间,不同卷则各管各的,分开判定。
 */
import fs from 'node:fs';
import path from 'node:path';

export const RESERVE_MIN = 512 * 1024 ** 2;
export const DEST_OVERHEAD = 64 * 1024 ** 2;

// 路径本身可能还不存在;最近已存在祖先决定它落在哪个文件系统上,根目录必然存在,循环必然终止。
const existingAncestor = p => {
  let current = p;
  while (!fs.existsSync(current)) current = path.dirname(current);
  return current;
};

export function freeBytes(p) {
  const at = existingAncestor(p);
  const stats = fs.statfsSync(at);
  return stats.bavail * stats.bsize;
}

export function sameVolume(a, b) {
  return fs.statSync(existingAncestor(a)).dev === fs.statSync(existingAncestor(b)).dev;
}

export function budget({selected, stagedBytes = {}, freeStaging = 0, freeDest = 0, sameVolume: samePool = true}) {
  // 大小没标齐就无法事前核算;不拦下载,只如实说明,免得把能下的游戏挡在门外。
  const known = selected.every(item => typeof item.size === 'number' && Number.isFinite(item.size) && item.size >= 0);
  if (!known) return {known:false, total:null, needStaging:null, needDest:null,
    freeStaging, freeDest, sameVolume:!!samePool, ok:true, shortBytes:null};
  const total = selected.reduce((sum, item) => sum + item.size, 0);
  const staged = selected.reduce((sum, item) => {
    const have = stagedBytes?.[String(item.depot)];
    return sum + Math.max(0, item.size - (Number.isFinite(have) ? have : 0));
  }, 0);
  // 中断重跑时已暂存的字节不必再下;另留 5% 余量给文件系统开销,低于 RESERVE_MIN 就按 RESERVE_MIN 算。
  const needStaging = staged + Math.max(RESERVE_MIN, Math.ceil(total * 0.05));
  const needDest = samePool ? DEST_OVERHEAD : total + DEST_OVERHEAD;
  const shortBytes = samePool
    ? Math.max(0, needStaging + needDest - freeStaging)
    : Math.max(0, needStaging - freeStaging, needDest - freeDest);
  return {known:true, total, needStaging, needDest, freeStaging, freeDest, sameVolume:!!samePool,
    ok:shortBytes === 0, shortBytes};
}

// 人类可读的一份大小;1 GB = 1024**3 字节,与访达、df 的口径一致。
export const gb = bytes => `${(bytes / 1024 ** 3).toFixed(1)} GB`;

const freeLabel = result => result.sameVolume
  ? gb(result.freeStaging)
  : `暂存盘 ${gb(result.freeStaging)} / 交付盘 ${gb(result.freeDest)}`;

// 结论先行,给真实换算的数字,方便用户对照 df 或访达腾地方。
export function budgetMessage(result) {
  if (!result.known) return '有内容包没有标大小，无法事前核算空间，照常下载。';
  if (!result.ok) {
    return `磁盘空间不够，未开始下载：这次要 ${gb(result.needStaging + result.needDest)} 的空间（暂存 ${gb(result.needStaging)} + 交付 ${gb(result.needDest)}），可用只有 ${freeLabel(result)}，还差 ${gb(result.shortBytes)}。腾出空间后重跑同一条命令。`;
  }
  return `空间核算：这次要 ${gb(result.needStaging + result.needDest)}，可用 ${freeLabel(result)}，够用。`;
}

const DISK_FULL = /disk write failure|disk full|not enough (disk )?space|no space left|insufficient (disk )?space/i;
const DOWNLOAD_FAILED = /failed|error|no license|no subscription/i;
// Steam 控制台输出是整段累积文本;写满优先于普通失败,两者处置完全不同(写满要保留暂存等用户腾地方)。
export function classifySpew(text) {
  if (typeof text !== 'string') return null;
  if (DISK_FULL.test(text)) return 'disk_full';
  if (DOWNLOAD_FAILED.test(text)) return 'failed';
  return null;
}
