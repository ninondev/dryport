# C++ 虚表槽位表

这里的 `*.json` 是 `steambridge` 按接口版本选择 C++ 虚表的元数据。它们不包含游戏、Steam 客户端、SDK 或第三方二进制字节。

## 来源

`tools/steam-vtable-map.py` 从用户自己拥有的游戏所带的 `steam_api64.dll` 读取 PE 导出表，找到
`SteamAPI_ISteamXxx_Method` 这类薄包装，再反汇编包装里的跳转，得到方法名对应的 MSVC 虚表槽号。
它只提取名字与槽号；仓库 JSON 额外保留隐藏结果指针和旧导出名这类 ABI 元数据，不复制函数体。

```bash
python3 tools/steam-vtable-map.py /absolute/path/to/steam_api64.dll \
  --out /tmp/steam-vtable-map.json
```

输出里的 `tables` 按接口家族分组。输入 DLL 属于用户自己的游戏，路径、文件内容和导出字节不进入仓库；JSON 的
`source` 只记录来源和提取说明。

## 字段

每个版本文件包含以下顶层字段：

| 字段 | 含义 |
|---|---|
| `interface` | C++ 接口名，例如 `ISteamUser`。 |
| `version` | Steam 接口的精确版本字符串，例如 `SteamUser021`；由运行时按这个字符串选表，不能改写成近似版本。 |
| `family` | 生成器使用的家族名，例如 `user`、`friends`、`userstats`；必须是桥实际服务的家族。 |
| `slot_count` | 该版本虚表的总槽数，从 0 到 `slot_count - 1`；即使某些槽未解析，也要计入。 |
| `source` | DLL、SDK 年代、版本字符串来源和提取日期的说明；这里只记元数据，不放第三方字节。 |
| `unresolved_slots` | 反汇编没有可靠解析出的槽号。对应槽位会保留为桩，不能用猜测的方法名填平。 |
| `slots` | 已解析槽位的数组，按 `slot` 升序排列；缺失的槽由 `unresolved_slots` 或数组空洞表示。 |

`slots` 中每一行的字段如下：

| 字段 | 含义 |
|---|---|
| `slot` | 从 0 开始的 MSVC 虚表槽号。 |
| `method` | 生成器要使用的接口方法名后缀，会与对应家族的 `SteamAPI_ISteam..._` 前缀拼成扁平导出名。 |
| `hidden_result_pointer` | 可选布尔值。为 `true` 时，MS x64 ABI 通过隐藏结果指针返回对象或大结构体，必须走对应 typed thunk，不能直接把扁平函数塞进虚表。 |
| `renamed_from` | 可选的原始 DLL 导出方法名；当 `method` 是按槽位恢复出的规范名时，用它记录 DLL 中实际出现的旧名字。它不改变槽号。 |

## 增加新版本

1. 从用户自己的 `steam_api64.dll` 提取结果，并确认输出中的 `interface_versions` 和 `tables` 对应目标接口。版本字符串缺失时不要猜版本名。
2. 在本目录新建以精确版本字符串命名的 JSON 文件，例如 `SteamUser023.json`。沿用现有文件结构，填写接口名、家族、完整 `slot_count`、来源说明、未解析槽和已解析槽。
3. 保留提取到的 0-based 槽号和空洞。新版本的方法缺少可靠薄包装时，放进 `unresolved_slots`，不要按头文件顺序补写。
4. 如果同一槽位在旧 DLL 中使用了通用重载名，把运行时需要的规范名写在 `method`，把 DLL 中的名字写在 `renamed_from`，并在 `source` 说明依据。
5. 运行构建门和相关测试，确认版本唯一、槽号有序、隐藏结果指针都有 thunk：

   ```bash
   python3 -m unittest tests.test_steambridge_policy
   python3 -m unittest tests.test_steambridge_abi
   ```

   没有真实 Windows DLL、Wine 或 Steam 环境时，只能报告机制测试结果；真实游戏调用仍是未验证。

## 为什么不能照头文件顺序手写

头文件声明顺序不是 MSVC 最终虚表布局的可靠替代。MSVC 会把相邻同名重载按与直觉相反的顺序放进虚表；现有
`STEAMUSERSTATS_INTERFACE_VERSION011` 就把 `GetStatFloat` 放在槽 1、`GetStatInt32` 放在槽 2，而老 DLL 的扁平导出名分别是
`GetStat0` 和 `GetStat`。若按头文件顺序手写，调用会落到另一个签名的方法，轻则返回错误，重则破坏调用方的 ABI。

因此，头文件只能用来交叉核对方法含义；槽号必须从同一版本 DLL 的薄包装读出，旧导出名则用 `renamed_from` 记录。不同 SDK
版本还可能删除、增加或留下未解析槽，不能用另一版本的头文件顺序填补。

## 同一版本串也会变长

Valve 会在不改版本串的情况下往接口末尾追加方法：`STEAMAPPS_INTERFACE_VERSION008` 在 SDK 1.58+ 多了 `SetDlcContext` 等，
`SteamFriends017` 多了装备物品四个方法，`SteamUtils009` 多了 `InitFilterText` 等。所以 `slot_count` 只是「本机看过的 DLL 里
最高有薄包装的槽 + 1」，不是接口的真实长度。两条规则：

1. 多份同版本串的 DLL 只要追加槽名字一致就可以并入老表并延长 `slot_count`；名字不一致或只有一份给出时留 `unresolved_slots`。
2. 生成器给每张表后面垫 `gen.VT_PAD` 个桩槽；游戏调到已知之外的槽只会走桩并在 `steambridge.log` 记
   「beyond the N known slots」，不会读到数组外面。看到这条日志就该去取一份新 DLL 补表。

2026-09-13 的来源说明记录了多份自有 DLL；`source` 只写份数，不写游戏名或 appid。原始交叉验证记录未公开。

## 32 位 DLL 读出的表（2026-09-13）

`SteamUser018.json`、`SteamUtils007.json`、`STEAMAPPS_INTERFACE_VERSION007.json`、`STEAMREMOTESTORAGE_INTERFACE_VERSION012.json` 来自
`tools/steam-flat-argsizes.py --tables-out`：读 Valve 32 位 `steam_api.dll` 里扁平包装的机器码，`call [vtable+disp]` 的 disp/4 就是槽位，
版本串取 DLL 内嵌的那一个。旧记录报告同一份 DLL 对已有版本读出的槽位与 64 位表逐槽一致；该报告尚待原始记录复核。
`hidden_result_pointer` 由包装里 `lea reg,[ebp-..]` 再 push 的写法判定。只有名字、槽号与标记，不含第三方字节。
