# savebridge

一个存档就一份，跟着你走：原生重打包的 app、Wine 兜底、以及别的设备，读写同一份存档，走游戏自己的 Steam 云配额。

## 为什么需要它

Steam 的云存档规则是按平台写死的。以 A Space for the Unbound 为例，`appinfo` 里的规则只有一条：

```
root=WinAppDataLocalLow  path=Mojiken/A Space for the Unbound  pattern=*  platforms=Windows
```

只对 Windows 生效。于是同一台机器上会出现三份互不相干的存档：原生重打包写 `~/Library/Application Support/<公司>/<产品>`，Wine 兜底写 prefix 里的 `AppData/LocalLow/...`，另一台 Windows 机器写它自己的 LocalLow 并由 Steam 云同步。玩一半换个方式启动，进度就回到上次。

savebridge 使用游戏自己的 `ISteamRemoteStorage` 槽位交换文件。内容冲突时保留本地和云端两份，不自动认定哪份进度更新。

## 用法

```bash
savebridge <appid> status [目录]      # 云配额、云端文件、本地文件与校验和
savebridge <appid> pull   <目录>      # 拉取已登记的云文件；不同的本地进度保留
savebridge <appid> push   <目录>      # 本地改过就传上去
savebridge <appid> sync   <目录>      # 双向，冲突两份都留
savebridge <appid> wipe               # 删除这个游戏云槽位中的全部文件，包含其它程序写入的文件
```

装进一个已经打包好的游戏：

```bash
./install-into-app.sh /path/to/game.app <appid> [wine-prefix] [save-directory]
```

它会把 savebridge 与设置工具放进 bundle、改写 launcher。云同步默认关闭；选择按游戏保存在
`SN_GAMES/.savebridge-state/<appid>/choice`，以后启动会读取同一份选择：

```bash
dryport cloud status <appid>
dryport cloud enable <appid>       # 阅读提示并确认
dryport cloud disable <appid>
```

改设置不会连接 Steam 或交换存档。启用只对装有当前同步启动壳的游戏生效；先核对存档目录、匹配模式和 Cloud 槽位。
无选择记录时保持关闭；重新安装启动壳会保留已有的明确选择。旧启动壳需重新安装，已生成的 app 不会自动改写。
单独设置 `SN_SAVEBRIDGE=1` 不会启用当前启动壳；`SN_SAVEBRIDGE=0` 仍可临时停用同步。

启用后开局 pull、退出 push。同步失败会在终端和 macOS 提示框中显示，并返回同步的非零退出码。
pull 冲突会停止游戏启动与后续 push；退出后的 push 冲突也会显示并返回 3。
关闭该游戏的云同步后可使用本地存档启动。指定 Unity 的 Wine prefix 时，对应的 LocalLow 目录会改为指向原生存档目录的软链；原目录保留在旁边的 `.savebridge-backup.*` 目录中，可恢复。

## 冲突怎么处理

每次同步会在云端留一份 `.savebridge.json`（文件名、SHA-1、大小、修改时间），本地留一份 `.savebridge-last.json`。下次同步时三方比对：

- 首次 pull 内容不同，或本地已偏离上次同步记录：本地原样保留，云端写入新的 `<原名>.cloud-<随机后缀>`，退出码 3。
- 未解决的 pull 冲突不登记为已同步；再次 pull 仍保留本地进度和新的云端副本。
- 本地仍与上次同步记录相同：允许拉取云端文件；相同内容跳过。
- 显式 push 或 sync 会上传本地版本；sync 的不同云端版本先保留为本地冲突副本。

冲突副本不会上传，只占本地。

## 历史实测记录（2026-09-11，A Space for the Unbound，appid 1201270）

以下为历史记录。存档同步机制通过模拟 Cloud API 验证；真实存档和跨设备同步仍需验证。

| 项 | 结果 |
|---|---|
| 云开关 | 账号开、本作开；配额 6656000 字节 |
| push 1047 字节存档 | 云端出现 `MainData.sav` + `.savebridge.json` |
| 拉到空目录 | 与源文件逐字节相同（sha1 `c466092c…`） |
| 双边都改后 sync | 本地 1066 字节保留，云端 1062 字节存成 `MainData.sav.cloud-1789135390`，与另一侧逐字节相同 |
| 装进 app 后完整一轮 | 启动时 pull 判定无变化；退出时把改动过的 1070 字节推上云 |
| Wine prefix | `AppData/LocalLow/Mojiken/A Space for the Unbound` 已是指向原生存档目录的软链，两边 `cmp` 相同 |

## 边界

- 用的是 API 命名空间（`root=0`）。它与 AutoCloud 共用同一份配额，但**不会**被一台未装本工具的 Windows 机器自动认成 LocalLow 里的存档。真正的跨机透明同步要求对方也用本工具，或者该游戏本身就有 macOS 的 AutoCloud 规则。这一条是实测配额数字吻合（6656000）+ 官方 API 语义得出的，没有在第二台设备上验证过。
- 单文件上限与总量受游戏自己的云配额限制（本例 6.5 MB / 13 个文件）；超限时会打印上传失败并继续。
- 只同步普通文件，跳过点文件、`Player.log` 和冲突副本。
- 安装器可按已实现的引擎目录规则选择存档位置，也可显式给目录；实际游戏的目录与文件范围仍需逐款确认。目录混有游戏本体时必须给文件匹配模式。

MIT。需要 Steam 正在运行并已登录，且该 appid 在你的库里（家庭共享也行，实测通过）。
