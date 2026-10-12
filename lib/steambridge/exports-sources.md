# 导出表的来源

`exports.json` 是两份真实导出表的并集，只有名字：

1. 2026-09-12：一份 Steamworks SDK 1.57 的 Windows `steam_api64.dll` 的全部 1059 个导出（`pe_exports.py` 读取用户自己游戏里的 DLL）。
2. 2026-09-13：Steam 客户端自带的 `libsteam_api.dylib`（SDK 1.61）比上面多出的 85 个 Steam API 名字（`nm -gU`），
   其中 `SteamInternal_SteamAPI_Init` / `SteamAPI_InitFlat` 是 1.59 以后所有游戏的初始化入口，没有它们 2023 年后构建的游戏
   连 DLL 都加载不了；`SteamAPI_SteamUser_v023` 等是新版访问器；其余是新增扁平方法，默认成为桩。
   1.61 删掉的 81 个旧名字（AppList、GameSearch、MusicRemote 等）保留，老游戏还在导入它们。

新版本的名字只能这样并进来，不能手写猜名。

3. 2026-09-13：三份 SDK 1.2x–1.3x 的 32 位 `steam_api.dll`（owner 自己游戏里的，`tools/steam-flat-argsizes.py` 的 `pe32_exports` 读取）
   比上面多出的 253 个名字。老游戏静态导入其中的裸访问器（`SteamUser`、`SteamUserStats`、`SteamApps`、`SteamFriends`、`SteamUtils`、
   `SteamRemoteStorage`、`SteamMatchmaking`、`SteamScreenshots`、`SteamHTTP`、`SteamUGC` 等 20 个）与 `Steam_RunCallbacks`、`Steam_GetHSteamUserCurrent`，
   缺一个整个 DLL 就加载不了。served 家族的裸访问器与 `SteamClient()` 一样发家族里最老的表，惰性家族的发该家族最老的按版本全桩表；
   老重载名 `GetStat0/GetStat/SetStat0/SetStat` 转到现在的 `GetStatFloat/GetStatInt32/SetStatFloat/SetStatInt32`；
   其余（`C<回调>_t_SetCallResult` 一类 122 个、VR、GameServer、ContentServer、`SteamController_*`）成桩。
