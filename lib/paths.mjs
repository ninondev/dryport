// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// Node 侧与 paths.sh / paths.py 使用相同的根目录和覆盖规则。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SN_HOME = process.env.SN_HOME || path.dirname(path.dirname(fs.realpathSync(fileURLToPath(import.meta.url))));
export const SN_LIB = path.join(SN_HOME, 'lib');
export const SN_GAMES = process.env.SN_GAMES || path.join(os.homedir(), 'Games');
export const SN_RUNTIMES = process.env.SN_RUNTIMES || path.join(os.homedir(), 'Games/runtimes');
export const STEAM = process.env.STEAM || path.join(os.homedir(), 'Library/Application Support/Steam');
