'use strict';
// Electron 原厂 -r 入口加载此自有层；原游戏目录、package 和 ASAR 不写入。
const fs = require('fs');
const path = require('path');
const Module = require('module');
const electron = require('electron');
const {app, BrowserWindow} = electron;
// 防止探针加载失败时 Electron 默认错误框抢占桌面；错误保留到 stderr 和退出码。
process.on('uncaughtException', error => { console.error(error.stack || error); app.exit(1); });
process.on('unhandledRejection', error => { console.error(error?.stack || error); app.exit(1); });
const state = process.env.SN_ELECTRON_STATE;
if (!state || !path.isAbsolute(state) || !fs.statSync(state).isDirectory()) throw new Error('缺少探针独立状态目录');
for (const [key, leaf] of [['appData','app-data'], ['userData','user-data'], ['cache','cache'], ['logs','logs'], ['crashDumps','crashes'], ['temp','tmp']]) {
  const directory = path.join(state, leaf);
  fs.mkdirSync(directory, {recursive:true});
  app.setPath(key, directory);
}
app.disableHardwareAcceleration(); // 自有机制样例不做 GPU 性能结论；生产策略另行验证。
// CJS 首个试点：在原构造前约束窗口，不靠创建后退出全屏掩盖短暂抢屏。
class WindowedBrowserWindow extends BrowserWindow {
  constructor(options = {}) {
    super({...options, fullscreen:false, fullscreenable:false, simpleFullscreen:false, kiosk:false, show:false});
    if (options.show !== false) this.show();
  }
  setFullScreen() { return super.setFullScreen(false); }
  setSimpleFullScreen() { return super.setSimpleFullScreen(false); }
  setKiosk() { return super.setKiosk(false); }
}
const facade = Object.create(electron);
Object.defineProperty(facade, 'BrowserWindow', {value:WindowedBrowserWindow, enumerable:true});
const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {
  if (request === 'electron' || request === 'electron/main') return facade;
  return originalLoad.apply(this, arguments);
};
