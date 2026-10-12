# 安装、更新、清理

## 试用源码预览

在下载的源码目录中运行。需要 Apple Silicon Mac、Python 3、Node.js；编译伴侣 app 还需要 Apple 命令行开发工具。

```bash
bash bin/install.sh --check     # 只读：报告什么就绪、什么缺
bash bin/install.sh --plan      # 只列安装步骤，不下载、不改动
bash bin/install.sh             # 准备基础风险库
```

正常 `dryport install <appid>` 会在引擎识别后自动补齐缺失的风险资料并选择匹配的运行时。不需要手动选这些运行时开关。要单独准备某个运行时：

```bash
bash bin/install.sh --runtime-only --with-unity # Unity 翻译工具
bash bin/install.sh --runtime-only --with-nwjs  # MV/MZ 的 NW.js
bash bin/install.sh --runtime-only --with-love  # 固定版本 LÖVE 11.5
bash bin/install.sh --runtime-only --with-renpy # 固定版本 Ren'Py 8.1.3
```

`--runtime-only` 只准备所选运行时，不检查 Steam 登录、调试端点或风险库；运行时就绪不表示游戏下载条件已齐。

这是源码预览。受影响的运行时包在精确源码、许可证原文和依赖 notices 核验前不发布，见 [LEGAL](LEGAL.md#preview-release-scope-source-only)。一台干净的 Mac 无人协助完成整套安装**尚未**验证。

Wine 可用[本地构建脚本](../tools/build-wine.sh)准备：

```bash
bash tools/build-wine.sh --check       # 检查前置条件，不启动 Wine、不下载
bash tools/build-wine.sh --help        # 查看构建工具与本地源码归档选项
```

构建需要脚本列出的工具、Rosetta 和匹配的源码归档。脚本构建 Wine 与 FreeType，不含 DXMT；[DXMT 准备](../tools/install-dxmt.sh)另行进行。

mkxp-z 和吉里吉里没有自动源码构建安装器。需要手工构建上游项目，并准备 [lib/runtimes.json](../lib/runtimes.json) 登记的产物与检查项。mkxp-z 还使用[依赖补丁](../tools/patch-mkxp-deps.sh)和[路径补丁](../tools/mkxp-nfc-path-fix.patch)。这些是准备参考，不能作为完整首次安装已验证的证据。

已有可信的本地运行时包时，可用 `tools/runtime-bundle.py install --from <本地目录>` 核对登记的清单 SHA256、归档哈希和输出边界。未登记清单须用 `--untrusted-local` 明确信任该本地目录，这不证明发布者身份或分发已获许可。此处不承诺提供可下载的项目运行时包。

此预览关闭伴侣 app 的远程更新，请从可信本地目录更新。Cloud 同步默认关闭；`dryport cloud enable <appid>` 持久启用单个游戏，`cloud disable <appid>` 再关闭。pull 冲突会在启动游戏和 push 前停止，push 冲突会在游戏退出后报告。

## 安装一款游戏

你能访问的游戏（含家庭共享）把 `<appid>` 换成其 Steam 商店网址里的数字：

```bash
./bin/dryport check <appid>
./bin/dryport install <appid>
./bin/dryport survey        # 列出可访问的游戏（含家庭共享）；不下载任何游戏
```

安装会检查风险、通过已登录客户端下载、识别引擎、生成独立的 `.app`，再加入快捷方式。客户端连接与可选的桥会碰什么见[安全说明](safety.zh.md)。端口仍不可用时，CLI 给出手动下载步骤。部分引擎仍需手动处理。已有下载要有完整的交付回执才会自动复用；旧的单 depot 标记不够。

## 伴侣 app

```bash
bash app/build.sh
```

构建脚本会打印 app 的位置。用库存发现按钮创建路线目录。独立构建把工具链装进 bundle：

```bash
bash app/build.sh --standalone   # 自含 app：工具链随 bundle 走
```

## 文件与清理

| 变量 | 默认 | 内容 |
|---|---|---|
| `SN_HOME` | 本检出 | 项目源码与文档 |
| `SN_RUNTIMES` | `~/Games/runtimes` | 上游运行时与构建缓存 |
| `SN_GAMES` | `~/Games` | 游戏副本、生成的 app 与私人库数据 |

```bash
./bin/dryport doctor
./bin/dryport clean
./bin/dryport archive <appid>
./bin/dryport restore <appid>
./bin/dryport uninstall <appid> [--saves]
./bin/dryport rewrap <appid>
```

`clean` 默认只列空间账单。`archive`、`restore`、`uninstall` 都要保持 Steam 开启并已登录：快捷方式的每一次增删都经客户端官方接口。`archive` 是归档，可逆：客户端移除快捷方式，回读确认后，有匹配标记的 app 原样移入 `SN_GAMES/.uninstalled`；下载内容和存档不动，不释放空间。无法确认移除时，app 和快捷方式备份保留。`restore` 把最近一次归档放回并重新入库，不下载不重建。`uninstall` 是卸载，不可逆：先归档，再只删本工具自己创建的东西（带本工具标记的下载目录、这个游戏的归档 app）。确认前逐项列出路径与大小；存档单独一行，默认保留，加 `--saves` 才删；释放量按磁盘可用空间前后差报，因为 `du` 会把 APFS 克隆重复计。`--keep-download` 继续接受，等于 `archive`。`rewrap` 用当前版本的启动壳重新生成已装好的 app：同名同路径，Steam 快捷方式不动，不下载；旧 app 移入 `.uninstalled` 可回退。升级本工具后已生成的 app 不会自己变，这是升级它们的路。
