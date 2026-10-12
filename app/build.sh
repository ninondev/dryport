#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# Build Dryport.app from the SwiftPM package. Idempotent: run it as often as you like.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BUILD_CALLER_DIR="$PWD"
source "$SRC/../lib/paths.sh"
# 改名面：app 名、可执行名、bundle id 集中在这两个常量，改名只改这里；
# 命令名（bin/dryport）与 SN_HOME/SN_RUNTIMES/SN_GAMES 等路径不动，改名时同步这两处常量。
APP_BASENAME="Dryport"
BUNDLE_ID="dev.ninon.dryport"
APP_VERSION="${SN_APP_VERSION:-0.1}"
python3 - "$APP_VERSION" "$SN_LIB" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from app_versions import version_tuple
version_tuple(sys.argv[1])
PY
# --standalone：把仓库里 git 跟踪的 bin/ lib/ tools/ 与许可证一起放进 app（Contents/Resources/toolchain），
# SN_HOME 记成 @bundle 相对路径，app 移动到哪里都能找到自己的工具链；不带 --standalone 仍指向本仓库（开发用）。
STANDALONE=0
for arg in "$@"; do
    case "$arg" in
        --standalone) STANDALONE=1 ;;
        *) echo "用法：app/build.sh [--standalone]" >&2; exit 2 ;;
    esac
done
APP="${SN_APP:-$SN_GAMES/${APP_BASENAME}.app}"
case "$APP" in
    *.app) ;;
    *) echo "输出路径必须以 .app 结尾：$APP" >&2; exit 2 ;;
esac
case "$APP" in /*) ;; *) APP="$BUILD_CALLER_DIR/$APP" ;; esac

check_target() {
    [ ! -L "$APP" ] || { echo "不替换软链：$APP" >&2; return 1; }
    if [ -e "$APP" ]; then
        local bundle_id
        bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null || true)
        [ "$bundle_id" = "$BUNDLE_ID" ] || {
            echo "目标不是本项目生成的 app，已保留原文件：$APP" >&2
            return 1
        }
    fi
}
check_target

cd "$SRC"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/$APP_BASENAME"
[ -x "$BIN" ] || { echo "没有产物：$BIN" >&2; exit 1; }

PARENT="$(dirname "$APP")"
mkdir -p "$PARENT"
APP="$(cd "$PARENT" && pwd)/$(basename "$APP")"
STAGE="$(mktemp -d "$(dirname "$APP")/.dryport-build.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
STAGED_APP="$STAGE/${APP_BASENAME}.app"
mkdir -p "$STAGED_APP/Contents/MacOS" "$STAGED_APP/Contents/Resources"
cp "$BIN" "$STAGED_APP/Contents/MacOS/$APP_BASENAME"
HOME_VALUE="$SN_HOME"
if [ "$STANDALONE" = 1 ]; then
    git -C "$SN_HOME" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "--standalone 需要 SN_HOME 是 git 工作树：$SN_HOME" >&2; exit 1; }
    TOOLCHAIN="$STAGED_APP/Contents/Resources/toolchain"
    mkdir -p "$TOOLCHAIN"
    # 只带 git 跟踪的文件：没有 __pycache__、没有本机编出来的二进制、没有个人库表（与 tools/release-check.py 同一口径）。
    # tools/ingame-save-games.json 是业主游戏机的真机自动化配方，可分发性未确认，不随 app 发布；
    # tests/test_release_pack.py 会核对这个文件确实不在任何包里。
    git -C "$SN_HOME" ls-files -z bin lib tools LICENSE README.md README.zh.md docs/LEGAL.md \
        | grep -zEv '^tools/一次性/|^tools/ingame-save-games\.json$' \
        | tar -C "$SN_HOME" --null -T - -cf - | tar -C "$TOOLCHAIN" -xf -
    [ -x "$TOOLCHAIN/bin/dryport" ] && [ -f "$TOOLCHAIN/lib/api.py" ] || { echo "工具链没有完整复制进 app" >&2; exit 1; }
    git -C "$SN_HOME" rev-parse HEAD > "$TOOLCHAIN/SOURCE-REVISION.txt"
    HOME_VALUE="@bundle/Contents/Resources/toolchain"
fi
python3 - "$STAGED_APP/Contents/Resources/toolchain-paths.plist" "$BUILD_CALLER_DIR" \
    "$HOME_VALUE" "$SN_GAMES" "$SN_RUNTIMES" "$STEAM" "${SN_UPDATE_SOURCE:-}" <<'PY'
import os
import pathlib
import plistlib
import sys

destination, caller, *values = sys.argv[1:]
keys = ("SN_HOME", "SN_GAMES", "SN_RUNTIMES", "STEAM")
paths = {key: value if value.startswith("@bundle/") else os.path.normpath(value if os.path.isabs(value) else os.path.join(caller, value))
         for key, value in zip(keys, values[:4])}
if values[4]:
    paths["SN_UPDATE_SOURCE"] = values[4]   # 更新来源（本地目录）；没设就不写，app 里的「检查更新」保持不可用
pathlib.Path(destination).write_bytes(plistlib.dumps(paths))
PY

cat > "$STAGED_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
    <key>CFBundleExecutable</key><string>${APP_BASENAME}</string>
    <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>${APP_BASENAME}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${APP_VERSION}</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
    <key>NSHumanReadableCopyright</key><string>Copyright (c) 2026 Ninon</string>
</dict>
</plist>
PLIST

printf 'APPL????' > "$STAGED_APP/Contents/PkgInfo"

codesign --force --sign - "$STAGED_APP"
codesign --verify --verbose=1 "$STAGED_APP" 2>&1 | tail -1

# 新包完成并通过签名验证后才落位，旧包保留在同一卷；落位失败会恢复原位置。
check_target
BACKUP=""
if [ -e "$APP" ]; then
    BACKUP_DIR="$(mktemp -d "$(dirname "$APP")/.dryport-backup.XXXXXX")"
    BACKUP="$BACKUP_DIR/$(basename "$APP")"
    mv "$APP" "$BACKUP"
fi
if ! mv "$STAGED_APP" "$APP"; then
    [ -z "$BACKUP" ] || mv "$BACKUP" "$APP"
    echo "新 app 未能落位：$APP" >&2
    exit 1
fi
[ -z "$BACKUP" ] || echo "旧版已保留：$BACKUP"

echo "已生成：$APP  ($(du -sh "$APP" | cut -f1))"
[ "$STANDALONE" = 0 ] || echo "独立包：工具链在 app 内（Contents/Resources/toolchain，$(git -C "$SN_HOME" rev-parse --short HEAD)），运行时与游戏仍在 SN_RUNTIMES / SN_GAMES"
