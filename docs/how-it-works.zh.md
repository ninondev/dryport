# 工作原理

dryport 从 Steam 元数据与文件清单识别引擎。RPG Maker XP/VX/VX Ace 用 mkxp-z；MV/MZ 及符合条件的 NW.js 游戏用 NW.js。Unity Mono 换匹配的 macOS 播放器与 Metal，2017.3 至 2020.1 的播放器经 Rosetta，更早的播放器也需要它。Ren'Py 只接 Gun-Dog 固定版本，LÖVE 要求 11.5，Koi Farm 用系统 WebKit；Godot 与吉里吉里用对应的运行时。不支持的 Ren'Py 版本会被拒绝。符合条件的兜底路线用 Wine + DXMT，或不带 D3D11 的 Wine Staging。项目维护了修改 mkxp-z 与依赖构建文件的本地补丁；来源与待解决的许可义务见 [LEGAL.md](LEGAL.md)。

识别出引擎不代表游戏已经能玩；[兼容性台账](compatibility.zh.md)区分真游戏验证、样例验证和未实现。引擎识别不等于可玩性成立。

## 路线档位

| 档位 | 路线 |
|---|---|
| 承诺 | 截至 2026-10-01 一条也没有（到期规则把最后四条承诺路线转成了实验） |
| 实验 | Unity（Mono；2017.3 以前经 Rosetta）、Wine + DXMT、mkxp-z（RPG Maker XP/VX/VX Ace）、NW.js、WebKit、Ren'Py 固定版本、Godot、LÖVE、吉里吉里 |

实验路线照样能装，app 和安装输出会标出来，不承诺能玩到底。承诺路线到 2026-09-30 凑不齐两款真游戏全链条验证的，也转成实验。这条规则已于 2026-10-01 按当时台账证据执行：没有任何路线凑齐两款全链游戏，因此所有路线都是实验。档位只在 `lib/route-tiers.json` 一处定，各路线的现状见[兼容性台账](compatibility.zh.md)。

## 下载与库存

下载走已登录的 Steam 客户端（不接触密码，见[安全说明](safety.zh.md)）。家庭共享的可玩游戏与自有游戏用同一套目录和操作。macOS 上的发布版在官方 Steam 客户端里打开。元数据缺失的游戏在路线核实期间保持可见。下载会向 Steam 请求当前授权、选择基础游戏的 depot 并在发布完整副本前核验文件。一次真实的双 depot 下载通过了文件核验；Windows 分发组件仍需准备，可玩性需另行测试。目录更新保留旧版本并核对所选账号。默认扫描复用有效的分类缓存，需要时读本地文件清单；不下载游戏内容，也不抓缺失的清单。

## 路线特例

以下真游戏结果来自历史摘要，原始记录未公开。

Koi Farm 在 Apple Silicon（macOS 14 及以后）有按版本验证的 WebKit 路线。安装器核验每个浏览器资源，并生成保留完整原游戏的原生 app。生成的 app 只用系统框架，不依赖 Python 或本检出。安装、移动鱼、正常退出、在新进程里恢复移动都已测试；完整教程、可听的声音、长时间游玩与低端硬件性能未验证。其他 Electron 游戏不继承这份兼容性。

Stories from Sol: The Gun-Dog 有固定版本的 Ren'Py 路线。安装时把完整游戏对照已测版本核验，生成带自己原生入口的 app，共享原上游 arm64 运行时。构建需要 Apple 命令行开发工具。一次早期剧情存档已在新进程恢复；完整剧情覆盖与其他 Ren'Py 游戏未验证。存档在 `SN_GAMES/.renpy-state/2118420` 下，卸载 app 也在。上游 ZIP 留在持久运行时缓存里，供日后核验与重装。
