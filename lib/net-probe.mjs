// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 下载停滞时的网络探针:只发起一次 TCP 连接来分辨断网与客户端停滞,不发任何数据、不碰 Steam。
import net from 'node:net';

// 可达与否都是明确结论;连接、出错、超时任一收场都销毁 socket,不让半开连接挂着。
export async function networkReachable(host = 'store.steampowered.com', port = 443, timeoutMs = 5000) {
  return await new Promise(resolve => {
    let socket, timer, settled = false;
    const finish = reachable => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket?.destroy();
      resolve(reachable);
    };
    timer = setTimeout(() => finish(false), timeoutMs);
    try {
      socket = net.connect({host, port});
      socket.once('connect', () => finish(true));
      socket.once('error', () => finish(false));
    } catch { finish(false); }
  });
}
