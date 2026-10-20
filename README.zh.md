# dryport

dryport 能让一部分只有 Windows 版的游戏在 Apple 芯片的 Mac 上跑起来。用 Mono 打包的 Unity 游戏不走 Wine：它读取游戏自带的 D3D11 shader，改写成 Metal，再用同版本的 macOS Unity 播放器来跑。其他引擎各有各的路线，见[工作原理](#工作原理)。现在还是早期预览版。

2026-09-29，**A Space for the Unbound** 在一台 16 GB 内存的 Apple M5 Mac 上通过了全部五项检查，包括强退后重新读档、数据逐字节一致。一款匿名的 RPG Maker VX Ace 游戏也通过了全部五项，**60.0 fps，帧时间 p99 19.44 ms**。

这些数字来自我自己的测试记录。原始记录没有公开，有些版本字段缺失，这次预览也没有重跑这些游戏。[各路线记录表](docs/compatibility.zh.md)和[汇总记录](docs/compatibility-evidence.json)保留了日期、阶段和缺口。五项检查是：有画面且能操作、游戏内存档、正常读档、强退后读档、原文件和存档不被改动。只到第 1 阶段的游戏只说明有画面、能操作，说明不了存档，也说明不了能不能玩通。帧时间 p99 是帧时间的第 99 百分位，由[测速工具](tools/bench-game.py)采样，采样设置也还没公开。

> **开发预览，与 Valve 无关、未经其认可。** 识别出引擎不代表游戏能玩：[兼容性页面](docs/compatibility.zh.md)选录了历史结果摘要，等待原始证据复核。可选的 Steam 集成通过其调试接口控制你已登录的客户端，可能写入统计与成就；账号安全性未证实。游戏与运行时的许可证各不相同。

| 你的游戏用 | 在这里跑 | 档位 |
|---|---|---|
| RPG Maker XP、VX、VX Ace | [mkxp-z](https://github.com/mkxp-z/mkxp-z)，原生 arm64 | 实验 |
| RPG Maker MV、MZ 及其他 NW.js 游戏 | NW.js，原生 arm64 | 实验 |
| Unity（Mono，2017.3 及以后） | 匹配的 macOS 播放器与 Metal；2017.3 至 2020.1 经 Rosetta | 实验 |
| Ren'Py（Gun-Dog 固定版本）、Godot、LÖVE 11.5、吉里吉里、Koi Farm | 各引擎对应运行时；Koi Farm 用系统 WebKit | 实验 |
| 其他符合条件的 Windows 游戏 | Wine + DXMT，或不带 D3D11 的 Wine Staging | 实验 |

干净 Mac 的完整首次安装尚未验证。mkxp-z 和吉里吉里需要预先准备本地构建。
运行时清单须匹配 `lib/runtimes.json` 登记的 SHA256；明确信任的本地目录可使用 `--untrusted-local`。
准备步骤与二进制发布限制见[使用说明](docs/usage.zh.md)。

```bash
git clone https://github.com/ninondev/dryport && cd dryport
bash bin/install.sh --check          # 只读：什么就绪、什么缺
./bin/dryport survey            # 你的游戏各有哪条路线；不下载任何游戏
./bin/dryport install <appid>   # 查风险、用你的 Steam 客户端下载、生成独立的 .app
```

它用你已登录的 Steam 客户端。绝不接触密码，绝不给 Steam 打补丁。检出已知反作弊的游戏一律拒绝。游戏原件保持原样。

[English](README.md)

## 工作原理

下载前先从 Steam 的元数据与文件清单识别引擎。安装还需通过各路线的版本与资源检查：Ren'Py 只接 Gun-Dog 固定版本，LÖVE 要求 11.5，Koi Farm 的浏览器路线用系统 WebKit。不支持的 Ren'Py 版本会被拒绝。符合条件的兜底路线用 Wine + DXMT，或不带 D3D11 的 Wine Staging。项目维护了修改 mkxp-z 与依赖构建文件的本地补丁。

长文、路线档位与各路线细节：[docs/how-it-works.zh.md](docs/how-it-works.zh.md)。

## 它会在你的机器与账号上碰什么

下载需要 Steam 的本地调试开关；开关开着且 Steam 在跑时，这台 Mac 上的任何程序都能经它控制你的 Steam 客户端——装时开，装完关。Steam 桥（Wine 路线）默认不接、按游戏打开，打开即把成就与统计写入登录的账号；联机、票据与物品面永不转发。带 DRM 包装的主程序在 Wine 路线被拒绝；原生路线不运行它。

完整说明：[docs/safety.zh.md](docs/safety.zh.md)。

## 安装、更新、清理

`bin/install.sh --check` 报告就绪状况；`install <appid>` 查风险、经登录客户端下载、生成独立 `.app`。生成的 app 用 `rewrap` 升级、`archive` 归档、`uninstall` 移除；`clean` 只预览可清理项。

操作细节：[docs/usage.zh.md](docs/usage.zh.md)。

## 参与贡献

报告游戏与引擎版本、播放器架构、实际画面与可复现步骤；绝不附游戏资源、账号标识、库导出或存档。当前源码与 Git 历史分别检查；清理工作区不能洗掉旧提交里的个人数据。

怎么报告、怎么跑测试：[CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证与第三方运行时

仓库不含游戏、Steam 组件、SDK 或第三方运行时二进制。一部分运行时在安装时从上游获取；mkxp-z 和吉里吉里需预先准备本地构建。旧归档可能缺许可证原文。预览版限定源码范围，受影响二进制等待精确源码、许可证与完整依赖声明核验后再发布。

来源、许可证与待解决的授权问题：[docs/LEGAL.md](docs/LEGAL.md)。
