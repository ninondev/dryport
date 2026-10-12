#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# krkr-wrap.sh <游戏目录> <显示名> <输出.app>
#   把一个 KiriKiri/KAG（吉里吉里）Windows 游戏目录换壳成 arm64 原生 mac app，
#   运行时用 krkrsdl2（吉里吉里 Z 的 SDL2 移植，MIT）。不含任何翻译层。
#
# 运行时来源：${KRKR_APP}（默认 ~/Games/runtimes/krkrsdl2/build/krkrsdl2.app）
#   构建方法见 ~/Games/runtimes/进度-krkr.md。
#
# 已知边界：上游 README 明说不支持跑未经修改的商业游戏。用 .dll 插件、视频、
#   Steam 成就的游戏大概率起不来，详见进度文件「已知不支持」一节。
set -eu

GAME_DIR="${1:?用法: krkr-wrap.sh <游戏目录> <显示名> <输出.app>}"
DISPLAY_NAME="${2:?缺显示名}"
OUT_APP="${3:?缺输出 .app 路径}"

. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
require_repack_permission "${1:-}" "${4:-}" || exit $?
KRKR_APP="${KRKR_APP:-$SN_RUNTIMES/krkrsdl2/build/krkrsdl2.app}"
RUNTIME_BIN="$KRKR_APP/Contents/MacOS/krkrsdl2"
ICON_SRC="$KRKR_APP/Contents/Resources/krkrsdl2.icns"

[ -d "$GAME_DIR" ] || { echo "游戏目录不存在: $GAME_DIR" >&2; exit 1; }
[ -x "$RUNTIME_BIN" ] || { echo "没有 krkrsdl2 运行时: ${RUNTIME_BIN}（先编，见 进度-krkr.md）" >&2; exit 1; }
case "$OUT_APP" in *.app) ;; *) echo "输出必须以 .app 结尾" >&2; exit 1 ;; esac

# 只保 ASCII 字母数字和短横，给可执行名和 bundle id 用
SAFE=$(printf '%s' "$DISPLAY_NAME" | LC_ALL=C tr -cs 'A-Za-z0-9' '-' | sed 's/^-*//; s/-*$//')
[ -n "$SAFE" ] || SAFE="krkrgame"
BUNDLE_ID="krkrsdl2.wrap.$(printf '%s' "$SAFE" | LC_ALL=C tr 'A-Z' 'a-z')"

# APFS 克隆优先，跨卷或非 APFS 时退普通拷贝
clone() { cp -Rc "$1" "$2" 2>/dev/null || cp -R "$1" "$2"; }

echo "== krkr-wrap =="
echo "   游戏目录 : $GAME_DIR"
echo "   显示名   : $DISPLAY_NAME  (可执行名 $SAFE)"
echo "   运行时   : $KRKR_APP"

# ---- 清点游戏目录 ----
XP3S=()
while IFS= read -r f; do XP3S+=("$f"); done < <(find "$GAME_DIR" -maxdepth 1 -type f -iname '*.xp3' | sort)
DLLS=()
while IFS= read -r f; do DLLS+=("$(basename "$f")"); done < <(find "$GAME_DIR" -maxdepth 2 \( -iname '*.dll' -o -iname '*.tpm' \) | sort)
EXES=()
while IFS= read -r f; do EXES+=("$f"); done < <(find "$GAME_DIR" -maxdepth 1 -type f -iname '*.exe' | sort)

DATA_DIR=""
for d in data content-data; do
  [ -d "$GAME_DIR/$d" ] && DATA_DIR="$GAME_DIR/$d" && break
done

if [ ${#XP3S[@]} -eq 0 ] && [ -z "$DATA_DIR" ]; then
  echo "没找到任何 *.xp3，也没有 data/ 或 content-data/ 目录，不像 KiriKiri 游戏" >&2
  exit 1
fi

# ---- 搭壳 ----
TMP_APP="${OUT_APP}.building.$$"
rm -rf "$TMP_APP"
# 收尾: 中途失败也不留半成品目录, 不留后台进程(本脚本不起任何进程)
trap 'rm -rf "$TMP_APP"' EXIT INT TERM
mkdir -p "$TMP_APP/Contents/MacOS" "$TMP_APP/Contents/Resources"

cp "$RUNTIME_BIN" "$TMP_APP/Contents/MacOS/${SAFE}-bin"
chmod +x "$TMP_APP/Contents/MacOS/${SAFE}-bin"

# 启动壳：krkrsdl2 不读 .cf 配置文件（TVPGetConfigFileOptions 被 #if 0 掉），
# 选项只能从命令行进来，所以必须有这层壳；否则所有换壳游戏共用一个存档目录，
# 而且默认字体 "Noto Sans CJK JP" 本机没有，第一次画字就抛 Font cannot be used。
# 窗口化: krkrsdl2 在 macOS 上本来就是窗口模式: SDL_WINDOW_FULLSCREEN_DESKTOP
# 只在 KRKRSDL2_WINDOW_SIZE_IS_LAYER_SIZE 下加, 而那个宏只对 iOS/Android/
# Emscripten/Vita/Switch 定义(src/core/sdl2/SDLApplication.cpp:52-54, :775-780)。
# 引擎也没有 -fullscreen / -windowed 命令行开关(全仓只有 -fszoom / -smoothzoom),
# 所以这里无需也无法传窗口参数。已知边界: 游戏脚本自己写 Window.fullScreen=true
# 时仍会进全屏, 这时用 Cmd+Tab 或 Cmd+Q 退出。
DEFFONT="${KRKR_DEFFONT:-Hiragino Kaku Gothic ProN}"
cat > "$TMP_APP/Contents/MacOS/$SAFE" <<LAUNCHER
#!/bin/sh
DIR=\$(cd "\$(dirname "\$0")" && pwd)
SAVE="\$HOME/Library/Application Support/krkrsdl2-$SAFE"
mkdir -p "\$SAVE"
exec "\$DIR/${SAFE}-bin" "-datapath=\$SAVE/" "-deffont=$DEFFONT" "\$@"
LAUNCHER
chmod +x "$TMP_APP/Contents/MacOS/$SAFE"

# ---- 数据进 Resources ----
# krkrsdl2 从 SDL_GetBasePath（= Contents/Resources/）依次找
# content-data → data.xp3 → data.exe → data，先当目录再当文件。
COPIED=0
for f in ${XP3S[@]+"${XP3S[@]}"}; do
  clone "$f" "$TMP_APP/Contents/Resources/$(basename "$f")"
  COPIED=$((COPIED+1))
done
if [ -n "$DATA_DIR" ]; then
  clone "$DATA_DIR" "$TMP_APP/Contents/Resources/$(basename "$DATA_DIR")"
  COPIED=$((COPIED+1))
fi

# KAG3 的 Initialize.tjs 用 System.exePath + "xxx.xp3" 找补丁包，而 exePath
# 是 proc_pidpath 的目录（Contents/MacOS/）。放软链让补丁包也能被找到。
for f in ${XP3S[@]+"${XP3S[@]}"}; do
  b=$(basename "$f")
  ln -sf "../Resources/$b" "$TMP_APP/Contents/MacOS/$b"
done

# ---- 图标：能从 exe 抽就抽，抽不到用 krkrsdl2 自带的 ----
ICON_DONE=0
EXEICON="$SN_LIB/unity-retarget/exe-icon.sh"
if [ ${#EXES[@]} -gt 0 ] && [ -x "$EXEICON" ]; then
  if "$EXEICON" "${EXES[0]}" "$TMP_APP/Contents/Resources/AppIcon.icns" >/dev/null 2>&1; then
    ICON_DONE=1
  fi
fi
if [ $ICON_DONE -eq 0 ] && [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$TMP_APP/Contents/Resources/AppIcon.icns"
fi

# ---- Info.plist ----
cat > "$TMP_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>en</string>
	<key>CFBundleDisplayName</key><string>$DISPLAY_NAME</string>
	<key>CFBundleExecutable</key><string>$SAFE</string>
	<key>CFBundleIconFile</key><string>AppIcon</string>
	<key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleName</key><string>$DISPLAY_NAME</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundleVersion</key><string>1.0</string>
	<key>LSApplicationCategoryType</key><string>public.app-category.games</string>
	<key>LSMinimumSystemVersion</key><string>11.0</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSSupportsAutomaticGraphicsSwitching</key><true/>
	<key>NSHumanReadableCopyright</key><string>Kirikiri core (C) W.Dee and contributors. Runtime: krkrsdl2 (MIT).</string>
</dict>
</plist>
PLIST

# ---- 签名 ----
codesign --force --deep --sign - "$TMP_APP" >/dev/null 2>&1 || {
  echo "codesign 失败" >&2; rm -rf "$TMP_APP"; exit 1; }

rm -rf "$OUT_APP"
mv "$TMP_APP" "$OUT_APP"
trap - EXIT INT TERM

# ---- 报告 ----
echo "   已装入   : $COPIED 项数据（xp3 ${#XP3S[@]} 个${DATA_DIR:+，另有目录 $(basename "$DATA_DIR")}）"
if [ ${#DLLS[@]} -gt 0 ]; then
  echo "   已丢弃的 Windows 插件（krkrsdl2 只能 SDL_LoadObject 原生库，.dll 一律加载失败）："
  printf '              %s\n' "${DLLS[@]}"
  echo "              游戏脚本里凡是 Plugins.link(\"上面任意一个\") 且没 try/catch 的，都会在启动时抛异常"
fi
[ ${#EXES[@]} -gt 0 ] && echo "   已丢弃的 exe : ${#EXES[@]} 个"
echo "   窗口模式 : 窗口化(krkrsdl2 的 macOS 构建不会自己进全屏, 已读源码确认)"
echo "   默认字体 : ${DEFFONT}（改用别的：KRKR_DEFFONT='Hiragino Mincho ProN' 再跑一次）"
echo "   存档目录 : ~/Library/Application Support/krkrsdl2-$SAFE/（出错时里面会有 krkr.console.log，UTF-16）"
echo "   产物     : $OUT_APP"
du -sh "$OUT_APP" | awk '{print "   体积     : "$1}'
echo "   验证     : ~/Games/runtimes/krkr-run.py \"$OUT_APP/Contents/MacOS/$SAFE\"   # 伪终端里跑才看得到日志（SDL 在 mac 上走 NSLog）"
