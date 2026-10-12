// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// dryport 注入给 NW.js 游戏的兜底：游戏自带的 Steam 原生模块（greenworks / Steam4C2 一类 .node）是老 NW.js、x86_64 的 ABI，
// 在 arm64 新版 NW.js 里 require 会抛错，Construct 2 / RPG Maker 的包装脚本随即抛未捕获异常，游戏停在黑屏
// （2026-09-14 Star Sky 3：Steam4C2.js 第 32 行 Cannot set properties of undefined）。
// 这里只在 .node 加载失败、且文件名像 Steam 模块时，给一个「Steam 不在」的桩：init 返回 false，取值返回空，回调走错误分支，
// 事件永不触发。等价于 Wine 路线不接桥时 Valve 的 DLL 找不到 Windows 版 Steam 的状态：游戏照常跑，没有 Steam 功能。
// 不伪造身份、不伪造成就；真正的成就要走桥（待做：greenworks 兼容的原生模块接 steambridge helper）。
(function () {
  try {
    var Module = require('module');
    var original = Module._extensions['.node'];
    if (!original || original.__steam_native_patched) return;
    var STEAMY = /greenworks|steam4c2|steamworks|steam_api|nwsteam|steamnw/i;
    function stub(filename) {
      var events = { on: function () {}, once: function () {}, off: function () {}, emit: function () {} };
      var falsy = new Set(['init', 'initAPI', 'isSteamRunning', 'restartAppIfNecessary', 'isSubscribedApp', 'isDLCInstalled',
                           'isCloudEnabled', 'isCloudEnabledForUser', 'isGameOverlayEnabled', 'isSteamInBigPictureMode']);
      var handler = {
        get: function (target, prop) {
          if (prop in target) return target[prop];
          if (typeof prop !== 'string') return undefined;
          if (prop === '_steam_events') return events;
          if (prop === 'then') return undefined;
          return function () {
            var args = Array.prototype.slice.call(arguments);
            var fns = args.filter(function (a) { return typeof a === 'function'; });
            if (falsy.has(prop)) return false;
            if (/^get(Number|Count)|^getStat|^getAppId|^getAppBuildId|^getDLCCount/.test(prop)) return 0;
            if (/^get.*Names?$|^getFriends|^getAchievementNames$/.test(prop)) return [];
            if (/^get/.test(prop)) return null;
            // 带回调的调用（activateAchievement(name, ok, err) 一类）走错误分支，让游戏自己的错误处理接住
            if (fns.length) { try { fns[fns.length - 1](new Error('dryport: Steam native module unavailable (stub)')); } catch (e) {} }
            return undefined;
          };
        }
      };
      var target = { __steam_native_stub: true, __filename: filename, Utils: {}, Ui: {}, ui: {} };
      return new Proxy(target, handler);
    }
    Module._extensions['.node'] = function (module, filename) {
      try {
        return original.call(this, module, filename);
      } catch (error) {
        if (!STEAMY.test(filename)) throw error;
        try { console.warn('[dryport] Steam 原生模块加载失败，已用桩替代（成就等 Steam 功能不可用）：' + filename + ' — ' + (error && error.message)); } catch (e) {}
        module.exports = stub(filename);
      }
    };
    Module._extensions['.node'].__steam_native_patched = true;
    // greenworks / Steam4C2 的包装脚本按 process.arch 选 .node：只认 x64 / ia32，arm64 下一个分支都不进，模块留空、后面直接抛异常
    // （Star Sky 3 的 Steam4C2.js 就是这样）。把 arch 报成 x64，让它走到 require('./X-osx64')，加载失败后由上面的桩接住。
    // 只在 darwin/arm64 下改；改的是这个游戏进程里的一个只读字符串，不影响别的东西。
    if (process.platform === 'darwin' && process.arch === 'arm64') {
      try { Object.defineProperty(process, 'arch', { value: 'x64', configurable: true, enumerable: true, writable: false }); } catch (e) {}
    }
  } catch (e) {
    try { console.warn('[dryport] 桩安装失败：' + (e && e.message)); } catch (_) {}
  }
})();
