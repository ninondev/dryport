#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# assemble-mac-app.sh <win game root> <Data dir name> <mac player variation dir> <pkg mono/Managed dir> <retargeted overlay dir> <out .app> <display name> <bundle id> [steam appid] [icon.icns]
set -eu
WIN="$1"; DATA="$2"; PLAYER="$3"; MANAGED="$4"; OVERLAY="$5"; APP="$6"; NAME="$7"; BID="$8"; APPID="${9:-}"; ICON="${10:-}"
C="$APP/Contents"

# 官方播放器模板有两代结构, 这里自动认, 后面各拼各的:
#   新(2019.3 起的独立 Mac Mono Support pkg): 引擎是 Contents/Frameworks/UnityPlayer.dylib,
#      Contents/MacOS/UnityPlayer 只是个薄壳; mac 版 UnityEngine*.dll 在 Variations/mono/Managed;
#      MainMenu.nib 在 Payload/Source/... 下。
#   老(2019.2 及更早, 装在 Windows 编辑器的 Mac 支持安装器里): Contents/MacOS/UnityPlayer 就是引擎本体
#      (x86_64, 走 Rosetta); mono 运行时在 Contents/Frameworks/{Mono,MonoBleedingEdge}/MonoEmbedRuntime/osx,
#      而播放器写死了只找 Contents/Frameworks/MonoEmbedRuntime/osx, 要按游戏用哪套拷到位;
#      MainMenu.nib 与 unity default resources 都已在 Contents/Resources/;
#      Info.plist 里是 UnityPlayerExec / UNITY_BUNDLE_IDENTIFIER 这类占位符(我们整份重写, 不做替换)。
# 两代都认 Contents/Resources/Data 当数据目录(老播放器的字符串表里 Contents/Resources/Data 与 Contents/Data 都在)。
if [ -f "$PLAYER/Contents/Frameworks/UnityPlayer.dylib" ]; then LAYOUT=new; else LAYOUT=old; fi

rm -rf "$APP"
if [ "$LAYOUT" = new ]; then
  GDATA="$C/Resources/Data"
  mkdir -p "$C/MacOS" "$C/Frameworks" "$C/Resources" "$C/PlugIns" "$C/MonoBleedingEdge"
  cp "$PLAYER/Contents/MacOS/UnityPlayer" "$C/MacOS/$NAME"
  cp "$PLAYER"/Contents/Frameworks/*.dylib "$C/Frameworks/"
  # 2019.1–2019.2 是过渡形态：引擎已是 Frameworks/UnityPlayer.dylib（按新式装），但 Mono 运行时还按老式放在
  # Frameworks/{Mono,MonoBleedingEdge}/MonoEmbedRuntime/osx，播放器也按老路径找；不带上就 "Unable to load mono library ...
  # MonoBleedingEdge/MonoEmbedRuntime/osx/libmonobdwgc-2.0.dylib"（Necken 2019.1.14f1，2026-09-14 冒烟）
  for mset in Mono MonoBleedingEdge; do
    if [ -d "$PLAYER/Contents/Frameworks/$mset/MonoEmbedRuntime" ] && [ ! -e "$C/Frameworks/$mset/MonoEmbedRuntime" ]; then
      mkdir -p "$C/Frameworks/$mset" && cp -Rc "$PLAYER/Contents/Frameworks/$mset/MonoEmbedRuntime" "$C/Frameworks/$mset/MonoEmbedRuntime"
    fi
  done
  # 2019.2 又不一样：两套 Mono 放在 Payload/{Mono,MonoBleedingEdge}/MonoEmbedRuntime/osx（各变体共用），播放器仍按
  # Contents/Frameworks/<套>/MonoEmbedRuntime/osx 找（場外人生 2019.2.0f1，2026-09-14 冒烟）
  PKG0="$(cd "$PLAYER/../../.." && pwd)"
  # 2019.2.7f2 又是一种：Payload/Frameworks/{Mono,MonoBleedingEdge}/MonoEmbedRuntime/osx（NecroWorm，2026-09-14）。三处都找，先到先得。
  # 只带 MonoEmbedRuntime，不带 etc/（etc/mono/2.0 这种目录名在 Frameworks 下会让 codesign 当成坏框架，整包签名失败：場外人生 2026-09-14）
  for mset in Mono MonoBleedingEdge; do
    for base in "$PKG0/Frameworks/$mset" "$PKG0/$mset"; do
      if [ -d "$base/MonoEmbedRuntime" ] && [ ! -e "$C/Frameworks/$mset/MonoEmbedRuntime" ]; then
        mkdir -p "$C/Frameworks/$mset" && cp -Rc "$base/MonoEmbedRuntime" "$C/Frameworks/$mset/MonoEmbedRuntime"
      fi
    done
  done
  cp -Rc "$WIN/$DATA" "$GDATA"                                 # APFS clone, zero extra space
  rm -rf "$GDATA/Plugins"
  cp "$PLAYER/Contents/Resources/unity default resources" "$GDATA/Resources/unity default resources"
  cp "$PLAYER/Contents/Resources/unity default resources" "$C/Resources/unity default resources"   # mac player reads it from Contents/Resources
  PKG="$(cd "$PLAYER/../../.." && pwd)"   # Payload dir
  # 2019.3–2019.4 的独立 Mac Mono Support 包把 libmonobdwgc-2.0.dylib / libMonoPosixHelper.dylib 放在 Payload/Frameworks（各变体共用），
  # 变体自己的 Contents/Frameworks 只有 UnityPlayer/libcrypto/libssl；2020.3 起才进变体目录。少了它播放器起步就报
  # "Unable to load mono library ... Contents/Frameworks/libmonobdwgc-2.0.dylib" 退出（一款 Unity 游戏 2019.4.41f1，2026-09-13 无头实测）。
  if [ -d "$PKG/Frameworks" ]; then
    for f in "$PKG"/Frameworks/*.dylib; do [ -f "$f" ] && [ ! -e "$C/Frameworks/$(basename "$f")" ] && cp "$f" "$C/Frameworks/"; done
    echo "Mono: 运行时 dylib 从 Payload/Frameworks 补进 Contents/Frameworks（2019.3–2019.4 式模板）"
  fi
  if [ -d "$PKG/MonoBleedingEdge/etc" ]; then cp -R "$PKG/MonoBleedingEdge/etc" "$C/MonoBleedingEdge/etc"; else cp -R "$WIN/MonoBleedingEdge/etc" "$C/MonoBleedingEdge/etc"; fi
  NIB="$PKG/Source/Player/MacPlayer/MacPlayerEntryPoint/Resources/MainMenu.nib"; [ -d "$NIB" ] && cp -R "$NIB" "$C/Resources/MainMenu.nib"
  # 2019.3–2019.4 的独立包把 MainMenu.nib（单文件）放在变体自己的 Contents/Resources/ 里；不拷的话播放器起步就 "Unable to load nib file: MainMenu"
  # 退出（一款 Unity 游戏 2019.4.17f1，2026-09-14 冒烟；此前 一款 Unity 游戏 只无头跑过，没露）
  [ -e "$C/Resources/MainMenu.nib" ] || { [ -e "$PLAYER/Contents/Resources/MainMenu.nib" ] && cp -R "$PLAYER/Contents/Resources/MainMenu.nib" "$C/Resources/MainMenu.nib"; }
  # 2019.1–2019.2 的播放器还要 ScreenSelector.nib / KeyConfig.nib（分辨率对话框与按键设置），缺了报 "Couldn't load Screen selector nib!" 退出
  # （NecroWorm 2019.2.7f2、場外人生 2019.2.0f1，2026-09-14）；变体 Resources 里有什么 nib 就都带上
  for nib in "$PLAYER"/Contents/Resources/*.nib; do
    [ -e "$nib" ] && [ ! -e "$C/Resources/$(basename "$nib")" ] && cp -R "$nib" "$C/Resources/$(basename "$nib")"
  done
else
  mkdir -p "$C"
  # 模板的整个 Contents 先原样搬过来: 引擎、nib、Mono 运行时、unity default resources 一次到位
  cp -Rc "$PLAYER/Contents/." "$C/"
  mkdir -p "$C/PlugIns"
  mv "$C/MacOS/UnityPlayer" "$C/MacOS/$NAME"
  rm -f "$C/Info.plist"            # 模板那份全是占位符, 下面整份重写
  # 数据目录放哪, 老模板里又分两种。播放器两条路径都认(字符串表里 Contents/Resources/Data 与
  # Contents/Data 都在), 所以按模板自己的样子选, 不猜:
  #   5.x : 模板自带 Contents/Data(里面是 Managed/etc 这份 mono 配置) → 游戏数据放 Contents/Data
  #   2017: 模板没有 Contents/Data(mono 配置在 Contents/Mono/etc) → 放 Contents/Resources/Data
  if [ -d "$PLAYER/Contents/Data" ]; then GDATA="$C/Data"; else GDATA="$C/Resources/Data"; fi
  rm -rf "$GDATA"
  cp -Rc "$WIN/$DATA" "$GDATA"                                 # APFS clone, zero extra space
  # 5.x 的 mono 配置在 Data/Managed/etc(Windows 版那份在 Data/Mono/etc, 用不了), 从模板补回来
  [ -d "$PLAYER/Contents/Data/Managed/etc" ] && cp -Rc "$PLAYER/Contents/Data/Managed/etc" "$GDATA/Managed/etc"
  rm -rf "$GDATA/Plugins"
  # mac 版的 unity default resources 覆盖游戏自带的 Windows 版; Contents/Resources 那份随模板已到位
  [ -d "$GDATA/Resources" ] && cp "$PLAYER/Contents/Resources/unity default resources" "$GDATA/Resources/unity default resources"
  # mono 运行时也分两种:
  #   5.x : 模板直接给 Contents/Frameworks/MonoEmbedRuntime/osx
  #   2017: 给了 Frameworks/Mono(经典 3.5, libmono.0.dylib) 与 Frameworks/MonoBleedingEdge
  #         (.NET 4.x, libmonobdwgc-2.0.dylib) 两套, 播放器按游戏的脚本运行时版本自己拼
  #         Frameworks/<Mono|MonoBleedingEdge>/MonoEmbedRuntime/osx/ —— 实测 2017.3.1f1 报的是
  #         Unable to load mono library from .../Contents/Frameworks/Mono/MonoEmbedRuntime/osx/libmono.0.dylib
  #         所以两套都原样留着, 别搬也别删。
  if [ -d "$C/Frameworks/MonoEmbedRuntime/osx" ]; then
    echo "Mono: 老结构(5.x 式), 运行时就在 Contents/Frameworks/MonoEmbedRuntime/osx"
  else
    if [ -d "$WIN/$DATA/MonoBleedingEdge" ] || [ -d "$WIN/MonoBleedingEdge" ]; then MONOSET=MonoBleedingEdge; else MONOSET=Mono; fi
    [ -d "$C/Frameworks/$MONOSET/MonoEmbedRuntime/osx" ] \
      || { echo "模板里既没有 Frameworks/MonoEmbedRuntime/osx 也没有 Frameworks/$MONOSET/MonoEmbedRuntime/osx" >&2; exit 1; }
    echo "Mono: 老结构(2017 式), 游戏用 $MONOSET; 模板的 Frameworks/{Mono,MonoBleedingEdge} 两套都随包带上"
  fi
fi

# 以下两代共用
chmod +x "$C/MacOS/$NAME"                                      # 模板里的引擎是 0644, 不加执行位起不来
for f in "$MANAGED"/UnityEngine*.dll; do b=$(basename "$f"); if [ -f "$GDATA/Managed/$b" ]; then cp "$f" "$GDATA/Managed/$b"; fi; done
if [ -d "$OVERLAY" ]; then (cd "$OVERLAY" && find . -type f | while read -r f; do cp "$f" "$GDATA/$f"; done); fi
if [ -f "${SN_RUNTIMES:-$HOME/Games/runtimes}/libsteam_api.dylib" ]; then cp "${SN_RUNTIMES:-$HOME/Games/runtimes}/libsteam_api.dylib" "$C/PlugIns/libsteam_api.dylib"; cp "${SN_RUNTIMES:-$HOME/Games/runtimes}/libsteam_api.dylib" "$C/PlugIns/libsteam_api64.dylib"; fi
[ -z "$APPID" ] || echo "$APPID" > "$C/MacOS/steam_appid.txt"

# ── 音频原生插件: FMOD 自动接入 / Wwise 只做识别 ────────────────────────────
# Windows 构建带的是 fmodstudio.dll, mac 上一个字节都用不了。FMOD 是闭源库、
# 不能随工具分发, 所以: 本机有真库就装真库(有声), 没有就装按 FMODUnity.dll 的
# P/Invoke 签名自动生成的空实现(不崩, 无声), 并在输出里写清楚怎么补真库。
# 这一步必须在下面 codesign 之前做, 否则 bundle 封签和内容对不上。
SELF_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
FMOD_DIR="${FMOD_DIR:-${SN_RUNTIMES:-$HOME/Games/runtimes}/fmod}"
PYBIN="${SN_VENV:-${SN_RUNTIMES:-$HOME/Games/runtimes}/unity-retarget/venv}/bin/python"; [ -x "$PYBIN" ] || PYBIN=python3
case "$PLAYER" in *x64*) SARCH=x86_64;; *) SARCH=arm64;; esac

HAS_FMOD=0
if [ -d "$WIN/$DATA/Plugins" ] && find "$WIN/$DATA/Plugins" -iname 'fmodstudio*.dll' -print -quit 2>/dev/null | grep -q .; then HAS_FMOD=1; fi
[ -f "$GDATA/Managed/FMODUnity.dll" ] && HAS_FMOD=1

if [ "$HAS_FMOD" = 1 ]; then
  if [ -f "$FMOD_DIR/libfmodstudio.dylib" ]; then
    cp "$FMOD_DIR/libfmodstudio.dylib" "$C/PlugIns/libfmodstudio.dylib"
    if [ -f "$FMOD_DIR/libfmod.dylib" ]; then
      cp "$FMOD_DIR/libfmod.dylib" "$C/PlugIns/libfmod.dylib"
      echo "FMOD: 用真库 $FMOD_DIR/libfmodstudio.dylib + libfmod.dylib, 有声"
    else
      echo "FMOD: 用真库 $FMOD_DIR/libfmodstudio.dylib, 但同目录没有 libfmod.dylib"
      echo "      libfmodstudio 依赖 libfmod(@rpath), 缺了会在加载时失败。把 dmg 里"
      echo "      api/core/lib/libfmod.dylib 一并拷进 $FMOD_DIR/ 再重跑。"
    fi
  else
    STUB_C="${WORK_DIR:-$(dirname "$APP")}/.fmodstub-$$.c"
    STUB_SO="${WORK_DIR:-$(dirname "$APP")}/.fmodstub-$$.dylib"
    OK=0
    if [ -f "$GDATA/Managed/FMODUnity.dll" ] && [ -f "$SELF_DIR/fmodstub-gen.py" ]; then
      if "$PYBIN" "$SELF_DIR/fmodstub-gen.py" "$GDATA/Managed/FMODUnity.dll" "$STUB_C" >/dev/null 2>&1 \
         && cc -O1 -dynamiclib -arch "$SARCH" -o "$STUB_SO" "$STUB_C" >/dev/null 2>&1; then
        cp "$STUB_SO" "$C/PlugIns/libfmodstudio.dylib"; OK=1
      fi
    fi
    rm -f "$STUB_C" "$STUB_SO"
    if [ "$OK" = 1 ]; then
      echo "FMOD: 本机没有真库, 已装空实现($SARCH, 按 FMODUnity.dll 的 P/Invoke 签名生成), 不崩但没声音"
    else
      echo "FMOD: 本机没有真库, 空实现也没生成成功, 游戏一碰音频就可能崩"
    fi
    echo "      要有声: 去 https://www.fmod.com/download 用免费账号下 FMOD Engine macOS 2.02.x,"
    echo "      把 api/studio/lib/libfmodstudio.dylib 与 api/core/lib/libfmod.dylib 拷进"
    echo "      $FMOD_DIR/ , 然后重跑 dryport wrap。说明也在 $FMOD_DIR/README.md。"
  fi
fi

# Wwise: 没有可自动生成的 P/Invoke 签名表(Wwise 走 C++ 原生接口, 不是 DllImport),
# 所以只识别、只提示, 不代做。
if [ -d "$WIN/$DATA/Plugins" ]; then
  WW=$(find "$WIN/$DATA/Plugins" -iname '*.dll' -exec sh -c 'LC_ALL=C strings -a "$1" 2>/dev/null | grep -qm1 AkSoundEngine && basename "$1"' _ {} \; 2>/dev/null | head -3)
  if [ -n "$WW" ]; then
    echo "Wwise: 检出 $(printf '%s' "$WW" | tr '\n' ' ')(dll 里有 AkSoundEngine)。"
    echo "      Wwise 的 mac 库要从游戏方或 Audiokinetic 拿对应版本的 libAkSoundEngine 等,"
    echo "      本工具不代替生成空实现(它不是 P/Invoke 接口)。缺库时游戏大概率在初始化音频处崩。"
  fi
fi

# 老播放器解 DllImport 的搜索目录不是 Contents/PlugIns, 而是 Contents/Frameworks/MonoEmbedRuntime/osx/。
# 实测日志(2017.3.1f1): Fallback handler could not load library
#   .../Contents/Frameworks/MonoEmbedRuntime/osx/libsteam_api64.dylib
# 它会按 <name> / lib<name> / <name>.dylib / lib<name>.dylib 一路试, 所以把插件按这几种名字都放一份。
# 这一步必须在 codesign 之前。2019.1–2019.2 虽是新式布局，播放器仍按这条老路找（NecroWorm 2019.2.7f2 日志同款，2026-09-14），
# 所以带 ScreenSelector.nib 的播放器也放。
# 2019.3.0f6（一款 Unity 游戏）没有 ScreenSelector.nib 也按这条老路找 libsteam_api：干脆一律放，多几份小 dylib 无害
if true; then
  mkdir -p "$C/Frameworks/MonoEmbedRuntime/osx"
  for f in "$C/PlugIns"/*.dylib; do
    [ -f "$f" ] || continue
    cp "$f" "$C/Frameworks/MonoEmbedRuntime/osx/$(basename "$f")"
  done
  # Windows 构建里 Steamworks.NET 的 DllImport 名是 steam_api64(32 位构建是 steam_api)
  if [ -f "$C/PlugIns/libsteam_api.dylib" ]; then
    for n in libsteam_api64.dylib steam_api64.dylib steam_api.dylib; do
      cp "$C/PlugIns/libsteam_api.dylib" "$C/Frameworks/MonoEmbedRuntime/osx/$n"
    done
  fi
fi

ICONKEY=""
if [ -n "$ICON" ] && [ -f "$ICON" ]; then cp "$ICON" "$C/Resources/PlayerIcon.icns"; ICONKEY="<key>CFBundleIconFile</key><string>PlayerIcon</string>"; fi

# 启动壳: mac 播放器不会 chdir 进 bundle, 光有 steam_appid.txt 不够 —
# SteamAPI_Init 找不到 appid 就不初始化。从 Steam 快捷方式启动时 Steam 只会
# 设它自己那个非 Steam 游戏的 id, 所以这里把工作目录和真 appid 都钉死再 exec 播放器。
# 播放器二进制仍叫原名, 进程名、pkill、窗口归属都不变。
# 2019.1–2019.2 已是新式布局，但播放器仍带 ScreenSelector 分辨率对话框：模板里有 ScreenSelector.nib 就按老播放器钉偏好键
# （NecroWorm 2019.2.7f2、場外人生 2019.2.0f1 冒烟时对话框一直等人点，2026-09-14）
if [ "$LAYOUT" = old ] || [ -e "$PLAYER/Contents/Resources/ScreenSelector.nib" ]; then LEGACY_PLAYER=1; else LEGACY_PLAYER=0; fi
cat > "$C/MacOS/launcher" <<LAUNCH
#!/bin/bash
# generated by assemble-mac-app.sh
D="\$(cd -- "\$(dirname -- "\$0")" && pwd)"
cd "\$D" || exit 1
if [ -f "\$D/steam_appid.txt" ]; then
  _id=\$(tr -d ' \t\r\n' < "\$D/steam_appid.txt")
  [ -n "\$_id" ] && export SteamAppId="\$_id" SteamGameId="\$_id"
fi
# First launch (no saved screen prefs yet): start windowed so the desktop, menu bar and cursor stay reachable.
# Afterwards the game's own saved display settings win. Cmd+Q quits at any time.
# 判据看的是 Screenmanager 那两个键在不在, 不是 plist 文件在不在 —— 老播放器的分辨率
# 对话框光是弹出来就会写一条 NSWindow Frame 进同一个 plist, 按文件判会漏。
_seeded=0
defaults read "$BID" "Screenmanager Fullscreen mode"    >/dev/null 2>&1 && _seeded=1
defaults read "$BID" "Screenmanager Is Fullscreen mode" >/dev/null 2>&1 && _seeded=1
_extra=""
if [ "\$_seeded" = 0 ]; then
  _extra="-screen-fullscreen 0 -screen-width 1280 -screen-height 720"
  # 2019.2 及更早的播放器启动时先弹 ScreenSelector 分辨率对话框, 里面的 Windowed 默认没勾,
  # 一按 Play 就全屏盖住桌面(命令行的 -screen-fullscreen 0 管不到它)。这几个键名取自播放器
  # 二进制的字符串表: 先钉成窗口化 1280x720, 再把对话框改成「按住 alt 才弹」。
  if [ "$LEGACY_PLAYER" = 1 ]; then
    defaults write "$BID" "Screenmanager Is Fullscreen mode" -int 0
    defaults write "$BID" "Screenmanager Resolution Width"   -int 1280
    defaults write "$BID" "Screenmanager Resolution Height"  -int 720
    defaults write "$BID" "Screenmanager Press alt to display" -int 1
  fi
fi
exec "\$D/$NAME" \$_extra "\$@"
LAUNCH
chmod +x "$C/MacOS/launcher"

# 版本号从引擎二进制里取: 新结构在 UnityPlayer.dylib, 老结构就是 MacOS/UnityPlayer 本体。
# 5.x 也要认, 所以正则是 (5|20xx).y.z
if [ "$LAYOUT" = new ]; then ENGINE_BIN="$PLAYER/Contents/Frameworks/UnityPlayer.dylib"; else ENGINE_BIN="$PLAYER/Contents/MacOS/UnityPlayer"; fi
BUILD=$(LC_ALL=C strings -n 8 "$ENGINE_BIN" | grep -m1 -oE '(5|20[0-9]{2})\.[0-9]+\.[0-9]+[fpab][0-9]+ \([0-9a-f]{12}\)' || true)
VER=${BUILD%% *}; HASH=$(printf '%s' "$BUILD" | grep -oE '[0-9a-f]{12}' || true)
cat > "$C/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDevelopmentRegion</key><string>English</string>
<key>CFBundleExecutable</key><string>launcher</string>
$ICONKEY
<key>CFBundleGetInfoString</key><string>Unity Player version $VER ($HASH), repackaged by unity-retarget</string>
<key>CFBundleIdentifier</key><string>$BID</string>
<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
<key>CFBundleName</key><string>$NAME</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundleSignature</key><string>????</string>
<key>CFBundleSupportedPlatforms</key><array><string>MacOSX</string></array>
<key>CFBundleVersion</key><string>0</string>
<key>LSApplicationCategoryType</key><string>public.app-category.games</string>
<key>LSMinimumSystemVersion</key><string>10.13.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSMainNibFile</key><string>MainMenu</string>
<key>NSPrincipalClass</key><string>PlayerApplication</string>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/></dict>
<key>UnityBuildNumber</key><string>$HASH</string>
</dict></plist>
PL
printf 'APPL????' > "$C/PkgInfo"
# 播放器不再是 CFBundleExecutable, --deep 只会把它当资源封, arm64 上未签名的 Mach-O 直接起不来, 所以先单独签
codesign --force --sign - "$C/MacOS/$NAME" 2>&1 | grep -v "replacing existing signature" || true
codesign --force --deep --sign - "$APP" 2>&1 | grep -v "replacing existing signature" || true
echo "assembled: $APP ($VER $HASH)"; du -sh "$APP"
