# unity-retarget

把只带 D3D11 着色器的 Windows 版 Unity 游戏，原地改成能被 macOS arm64 原生 Unity 播放器加载的 Metal / OpenGL Core 版本。零翻译层：不是 Wine，不是转译，游戏的 Mono 程序集、资源、脚本原样运行在 Unity 官方的 macOS arm64 播放器上。

## 原理

1. Unity 构建产物里每个 Shader 资源包含按平台分块的 GPU 程序 blob。Windows 版只有 `D3D11`(4) 一个平台，DXBC 字节码里的 RDEF 反射块被 Unity 剥掉了，但 Unity 自己在 blob 尾部（每个子程序的 tail）以及序列化的 `m_CommonParameters` 里保留了常量缓冲、变量、贴图绑定的名字和偏移。
2. `rdefsynth.py` 用这些信息重新合成 RDEF 块并注入 DXBC；`hlslcc-cli`（Unity 自家开源的 HLSLcc，MIT）把它翻译成 GLSL 150 和 MSL。对同一着色器，我们的输出和 Unity 官方 mac 播放器自带的 Metal 版本逐字节一致（`Hidden/Internal-Colored` 片元程序 diff 为 0 行）。
3. `unityblob.py` 按已逆向验证的 blob 格式（版本 201806140，Unity 2019.1 到 2021.1）把新子程序连同 tail（通道映射、常量缓冲布局、贴图槽位）写回，追加 Metal(14)/GLCore(15) 平台块，并把 typetree 里的 `SerializedSubProgram` 条目补上。
4. `retarget.py` 遍历所有资源文件，改完用 UnityPy 回写；同时把 `globalgamemanagers` 里 `BuildSettings.m_GraphicsAPIs` 补上 Metal/GLCore，否则 mac 播放器会报 "not built from editor"。
5. `assemble-mac-app.sh` 用 Unity 官方 Mac Mono Support 安装包里的 arm64 播放器模板拼出 .app：Data 目录 APFS 克隆（零额外空间），换上 mac 版 UnityEngine 程序集与 `unity default resources`，加 MainMenu.nib、Info.plist（`NSPrincipalClass=PlayerApplication`）、mono 配置、Steam 原生库。

## 已验证（2026-09-11，A Space for the Unbound，Unity 2020.3.31f1，Apple M5，macOS 26）

- 91 个着色器、215 个 pass、5854 个 D3D11 子程序 → 5955 个 Metal 子程序 + 2981 个 GLCore 程序，31 秒，仅 3 个 pass 跳过（Eye Adaptation 用 UAV，未实现）。
- GLCore 程序已生成但未运行验证：2020.3 的 arm64 播放器忽略 `-force-glcore`，始终用 Metal。
- 游戏以 Metal 原生启动，语言选择、光敏警告画面渲染正确（TextMeshPro 距离场着色器即为翻译产物）。日志 0 条着色器错误。
- `SteamAPI_Init` 通过原生 `libsteam_api.dylib` 加载 Steam 客户端成功；只读探针实测25 个成就可读（未做解锁测试）。从 Steam 快捷方式启动时靠启动壳把 `SteamAppId/SteamGameId` 覆盖成真 appid。
- 音频：FMOD 是闭源库，本机没有 mac 版；`fmodstub.c` 从 `FMODUnity.dll` 的 P/Invoke 签名自动生成 567 个空实现（返回 FMOD_OK，句柄给假值，GetVersion 返回 2.02.07），游戏无异常继续跑但无声。把真的 `libfmodstudio.dylib`（fmod.com 免费账号下载，版本 ≥ 游戏用的 2.02.07）放进 `Contents/PlugIns/` 即有声。

## 版本覆盖（2026-09-11 01:00 实测）

`retarget_verify.py <Windows 版 unity default resources>` 是回归台：把文件里 Unity 官方的 Metal/GLCore 平台剥掉，用本工具从 D3D11 重新生成，再逐子程序和官方 Metal 对照。

| Unity 版本 | blob 格式 | Metal 子程序 | 顶点通道映射与官方一致 | 说明 |
|---|---|---|---|---|
| 2017.4.40f1 | 201708220 | 34/34 生成 | 17/17 | 旧枚举：ShaderChannel 里 Color=2、TexCoord0=3，Metal 属性槽从 12 起；贴图绑定为 [index, dim, sampler]；D3D11 代码头 6 字节；条目表 8 字节且物理顺序不按表序 |
| 2018.4.36f1 | 201802150 | 34/34 生成 | 17/17 | 新枚举，贴图绑定改为 [index, sampler, dim<<1\|ms]；参数直接挂在 SerializedSubProgram 上，没有 m_CommonParameters |
| 2019.4.41f2 | 201806140 | 34/34 生成 | 17/17 | 同 2020 格式，但参数仍直接挂在条目上 |
| 2020.3.31f1 | 201806140 | 42/42 生成 | 21/21 | 16 个逐字节相同；其余差异只是官方把 UnityPerDraw/UnityPerFrame 合进 VGlobals，我们保留独立常量缓冲并按 D3D11 布局补 padding。**唯一做过真机运行验证的版本** |
| 2022.3.62f3 | 202012090 | 56/56 生成 | 24/24 | 同上；官方新版 HLSLcc 不再输出 fma 宏，无功能差异 |
| 6000.0.83f1 | 202012090 | 56/56 生成 | 24/24 | 官方 Metal 也改成独立常量缓冲（UnityPerDraw 结构体含全部成员），与本工具的 padding 布局等价，参数表 29/56 完全相同 |

六个版本的官方 Windows 支持包都从 Unity CDN 取（`~/Games/runtimes/unity-players/<版本>/`），全部通过。Mac 播放器：2020.2 起有 arm64；2017 到 2020.1 只有 x86_64（需要可用的 Rosetta；与 Wine 的性能比较未在此记录）。

**Unity 5.x 没接通着色器那一层**：5.6.0f3 实测 61 个 pass 全跳过（`empty_blob`），一个 Metal 子程序都没生成，拼出来的 app 能起来、能进 Metal，但画面整片品红。`dryport` 碰到这种情况会自己印警告。

## 老版本的播放器从哪来（2026-09-11 接通）

2019.3 起才有独立的 Mac Mono Support pkg；2019.2 及更早（含 5.x）的 mac 播放器藏在 **Windows 编辑器的「Mac 支持」安装器**里，路径前缀是 `TargetSupportInstaller` 而不是 `MacEditorTargetInstaller`：

```
https://download.unity3d.com/download_unity/<hash>/TargetSupportInstaller/UnitySetup-Mac-Support-for-Editor-<版本>.exe
```

NSIS 自解压包，`7zz x` 解，要的东西在 `$INSTDIR$_<n>_/Variations/macosx64_nondevelopment_mono/`（`UnityPlayer.app/` 与平级的 `Data/Managed/`）。2017.3.1f1 是 28 MB，5.6.0f3 是 86 MB，比新式包小一个数量级。

老模板的 bundle 结构与新的不一样，`assemble-mac-app.sh` 自动分辨（`Contents/Frameworks/UnityPlayer.dylib` 在不在）。四条实测要点写在脚本注释里：mono 运行时路径 2017 与 5.x 各不相同、原生插件的 DllImport 搜索目录是 `Contents/Frameworks/MonoEmbedRuntime/osx/`、游戏数据 5.x 放 `Contents/Data` 而 2017 放 `Contents/Resources/Data`、老播放器的 ScreenSelector 对话框要靠 `defaults write` 钉成窗口化。真机验证：entry-03（2017.3.1f1）出标题画面。

202012090（2021.2 起）与 201806140 的差别已在 `unityblob.py`/`retarget.py` 里处理：参数块独立成 blob 条目并由 `m_ParameterBlobIndices` 引用、typetree 用 `m_PlayerSubPrograms[tier]`、关键字走 `m_KeywordNames`、通道映射放在代码条目尾部、`stageCounts` 按平台追加、Metal 代码头模板从游戏自带的 `unity default resources` 读取（2020 为 56 字节，2022/6000 为 60 字节）。2021.2+ 只做了数据层验证，还没有拿真实游戏跑过播放器。

## 用法

已接进主命令 `dryport`，整条流水线一条命令（2026-09-11）：

```bash
dryport detect ~/Games/<游戏目录>              # 认 Unity Mono 与版本号
dryport wrap   ~/Games/<游戏目录>              # 播放器包(没有就下) → 重定向 → 拼 app → 抽图标 → 插件交代
dryport add    "~/Games/<显示名>.app" <appid>   # 进 Steam 库(加入当前选择的 Steam 账号)
```

`wrap` 会把成品放在 `~/Games/<显示名>.app`，显示名取 `<X>_Data/app.info` 第二行；
中间产物在 `~/Games/.unity-work/<名字>/`（retarget.log、重定向输出、图标、FMOD 空实现）。
Python 环境固定在本目录的 `venv`（UnityPy 1.25.3、lz4、dnfile、icoextract）。

手工分步（调试用）：

```bash
# 1. 下载并展开对应版本的 Unity Mac Mono Support 包（约 480MB）
#    https://download.unity3d.com/download_unity/<hash>/MacEditorTargetInstaller/UnitySetup-Mac-Mono-Support-for-Editor-<ver>.pkg
pkgutil --expand-full <pkg> expanded
# 2. 着色器重定向（输出只含改动过的文件）
python3 retarget.py "<游戏>_Data" retargeted
# 3. 拼 app
./assemble-mac-app.sh <游戏目录> "<游戏>_Data" expanded/TargetSupport.pkg.tmp/Payload/Variations/macos_arm64_nondevelopment_mono/UnityPlayer.app \
    expanded/TargetSupport.pkg.tmp/Payload/Variations/mono/Managed retargeted "<输出>.app" "<显示名>" <bundle id> [steam appid]
```

依赖：Python 3 + `UnityPy lz4 dnfile`；`hlslcc-cli` 用 `c++ -std=c++11 -I hlslcc/include -I hlslcc/src/internal_includes hlslcc-cli.cpp -lhlslcc` 编（HLSLcc 需先 cmake，`CMAKE_POLICY_VERSION_MINIMUM=3.5`）。

## 边界

- 只处理 Mono 后端；IL2CPP 游戏的原生代码是 x86_64，不适用。
- 只做 VS/FS；几何、曲面、计算着色器与 UAV/StructuredBuffer 参数的 pass 跳过（保留 D3D11，运行时挑最接近的变体）。
- 游戏自带的 Windows 原生插件（`Plugins/x86_64/*.dll`）需要各自的 mac 版本：Steamworks.NET 用 `libsteam_api.dylib`（Steam SDK，不可再分发），FMOD 见上，InControlNative/XInput 缺失无害。
- Metal 常量缓冲结构体按 D3D11 布局补了 padding（`unity_retarget_pad*`），保证引擎按名字绑定的内置缓冲偏移一致；`$Globals` 走引擎按 tail 偏移拼装的路径。

## 中国版 Unity 构建（2026-09-13）

`UnityPlayer.dll` 里版本串带 `c1` 一类后缀（例：anonymous-title anonymous-title 的 `2020.3.14f1c1`）是 Unity 中国版。官方 `download.unity3d.com`
没有对应的 mac 播放器（探过 404），`download.unity.cn` 可达性未验证（未验证是否存在）。所以这类构建暂不做原生重定向，
`dryport install` 识别为「Unity 版本未知」后改走 Wine + DXMT 兜底（32 位的接 32 位桥）。将来若能确认 unity.cn 的下载点，
可以在这里加第二个下载主机并接受 `c\d+` 后缀。

## 2019.3–2019.4 模板的 Mono 运行时位置（2026-09-13）

独立 Mac Mono Support 包在 2019.3–2019.4 把 `libmonobdwgc-2.0.dylib` 与 `libMonoPosixHelper.dylib` 放在 `Payload/Frameworks/`（各变体共用），
变体自己的 `UnityPlayer.app/Contents/Frameworks` 只有 UnityPlayer/libcrypto/libssl；2020.3 起才进变体目录。`assemble-mac-app.sh`
现在从 `Payload/Frameworks` 补进 `Contents/Frameworks`。一款 Unity 游戏（2019.4.41f1）此前起步即报
`Unable to load mono library ... Contents/Frameworks/libmonobdwgc-2.0.dylib` 退出，修后用 `-batchmode -nographics` 无头跑满 60 秒，
Mono 起来、Steam 初始化通过；有画面的验证仍待游戏验证。
