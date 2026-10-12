#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# godot-wrap.sh <游戏目录> <显示名> <输出.app> [steam appid]
#
# 把只有 Windows 版的 Godot 游戏包成 Apple Silicon 原生 .app：
# 从 exe 里读引擎版本 -> 取官方 macOS 导出模板 -> 把 pck 按官方「自定义打包」规则装进去。
# 不含任何翻译层。官方模板是 universal（含 arm64）。
set -eu

. "$(dirname "${BASH_SOURCE[0]:-$0}")/paths.sh"
. "$SN_LIB/repack-gate.sh"
require_repack_permission "${1:-}" "${4:-}" || exit $?
CACHE="$RUNTIMES/godot"
PCKTOOL="$SN_LIB/godot-pck.py"
FETCH="$SN_LIB/godot-fetch-template.py"
GH="https://github.com/godotengine/godot/releases/download"

die() { echo "错误: $*" >&2; exit 1; }
note() { echo "  $*"; }
warn() { echo "  [注意] $*"; }

[ $# -ge 3 ] || die "用法: godot-wrap.sh <游戏目录> <显示名> <输出.app> [steam appid]"
GAMEDIR=$(cd "$1" && pwd) || die "游戏目录不存在: $1"
NAME=$2
OUT=$3
APPID=${4:-}
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
[ -x "$PCKTOOL" ] || die "缺 $PCKTOOL"
[ -x "$FETCH" ] || die "缺 $FETCH"

echo "== 1. 找 Godot 可执行文件 =="
EXE=""
EXELIST=$(find "$GAMEDIR" -maxdepth 1 -type f -iname '*.exe' -exec stat -f '%z %N' {} + 2>/dev/null | sort -rn | cut -d' ' -f2- || true)
while IFS= read -r f; do
  [ -n "$f" ] || continue
  # 4.x 的 exe 里有 "Godot Engine v...",3.x 只有裸版本串,所以再认 project.godot 这条命令行帮助文本
  if strings -a "$f" 2>/dev/null | grep -qEm1 'Godot Engine|project\.godot'; then EXE=$f; break; fi
done <<< "$EXELIST"
[ -n "$EXE" ] || die "目录里没有 Godot 特征串的 exe，不像 Godot 游戏"
EXEBASE=$(basename "$EXE"); EXEBASE=${EXEBASE%.*}
note "exe: $EXE"

echo "== 2. 读引擎版本 =="
ST_RE='(stable|beta[0-9]*|rc[0-9]*|dev[0-9]*|alpha[0-9]*)'
# 4.x: "Godot Engine v4.2.2.stable.official";3.x: 只有裸的 "3.6.3.stable.official"
VERLINE=$(strings -a "$EXE" | grep -m1 -oE "Godot Engine v[0-9]+\.[0-9]+(\.[0-9]+)?\.$ST_RE(\.[a-zA-Z_]+)?" || true)
if [ -z "$VERLINE" ]; then
  VERLINE=$(strings -a "$EXE" | grep -m1 -oE "[0-9]+\.[0-9]+(\.[0-9]+)?\.$ST_RE\.[a-zA-Z_]+" || true)
fi
TOK=${VERLINE#Godot Engine v}
VER=""; STATUS=""; FLAVOR=""
if [ -n "$TOK" ]; then
  VER=$(awk -F. -v re="^$ST_RE\$" '{k=0; for(i=1;i<=NF;i++) if ($i ~ re) {k=i; break};
        v=""; for(j=1;j<k;j++) v = v (j>1?".":"") $j; print v}' <<< "$TOK")
  STATUS=$(awk -F. -v re="^$ST_RE\$" '{for(i=1;i<=NF;i++) if ($i ~ re) {print $i; exit}}' <<< "$TOK")
  FLAVOR=$(awk -F. -v re="^$ST_RE\$" '{k=0; for(i=1;i<=NF;i++) if ($i ~ re) {k=i; break};
        if (k>0 && k<NF) print $(k+1)}' <<< "$TOK")
fi
MAJOR=${VER%%.*}
note "版本串: ${VERLINE:-(exe 里没找到,下一步回退读 pck 头)}"
note "版本=${VER:-?} 渠道=${STATUS:-?} 构建=${FLAVOR:-(未标)}"

echo "== 3. 判断 pck 形态 =="
PCKSRC=""; PCKFORM=""
if [ -f "$GAMEDIR/$EXEBASE.pck" ]; then
  PCKSRC="$GAMEDIR/$EXEBASE.pck"; PCKFORM="独立 pck"
else
  CAND=$(find "$GAMEDIR" -maxdepth 1 -type f -iname '*.pck' | head -2)
  CNT=$(printf '%s\n' "$CAND" | grep -c . || true)
  if [ "$CNT" = "1" ]; then PCKSRC=$CAND; PCKFORM="独立 pck（文件名与 exe 不同）"
  elif [ "$CNT" -gt 1 ]; then die "目录里有多个 .pck，无法判断主包：$CAND"
  fi
fi
TMPPCK=""; TMPBASE=""
# 收尾: 中途失败也不留切出来的临时 pck。本脚本不起任何后台进程。
# 注意 mktemp 自己会先建一个无后缀的空文件, 我们真正写的是它 +".pck",
# 两个都要删; 原来只删了后者, /var/folders 里攒了一串 0 字节的 godotpck.XXXX。
cleanup(){ rm -f ${TMPBASE:+"$TMPBASE"} ${TMPPCK:+"$TMPPCK"}; }
trap cleanup EXIT INT TERM
if [ -z "$PCKSRC" ]; then
  TMPBASE=$(mktemp -t godotpck); TMPPCK="$TMPBASE.pck"
  if "$PCKTOOL" split "$EXE" "$TMPPCK" 2>/dev/null; then
    PCKSRC="$TMPPCK"; PCKFORM="pck 嵌在 exe 尾部（已切出）"
  else
    die "既没有独立 pck，exe 尾部也没有 GDPC 嵌入包"
  fi
fi
note "形态: $PCKFORM"
"$PCKTOOL" info "$PCKSRC" | sed 's/^/  /'
PCKVER=$("$PCKTOOL" info "$PCKSRC" | sed -n 's/.*engine_version=//p')
if [ -z "$VER" ]; then
  [ -n "$PCKVER" ] || die "exe 和 pck 头都取不到引擎版本"
  VER=$PCKVER; STATUS=stable; FLAVOR=""
  MAJOR=${VER%%.*}
  warn "exe 里没有版本串，改用 pck 头的 $VER 并假定 stable 官方构建（未核实）"
elif [ -n "$PCKVER" ] && [ "$PCKVER" != "$VER" ]; then
  warn "pck 头里的版本 $PCKVER 与 exe 版本串 $VER 不一致，以 exe 为准"
fi

note "最终认定：引擎 $VER 渠道 $STATUS 构建 ${FLAVOR:-(未标)}"
[ "$STATUS" = "stable" ] || warn "非 stable 渠道，官方模板 tag 用 ${VER}-${STATUS}，可能不存在"
if [ -n "$FLAVOR" ] && [ "$FLAVOR" != "official" ]; then
  warn "这是自定义引擎构建（${FLAVOR}）。官方模板缺它编进去的模块，游戏可能因缺符号或缺单例而崩"
fi

echo "== 4. GDExtension / GodotSteam 体检 =="
DLLS=$(find "$GAMEDIR" -maxdepth 2 -type f -iname '*.dll' | sed "s|$GAMEDIR/||" || true)
if [ -n "$DLLS" ]; then
  note "游戏目录带 dll（Windows 专属，不会拷进 .app）："
  printf '%s\n' "$DLLS" | sed 's/^/    /'
fi
"$PCKTOOL" gdext "$PCKSRC" 2>&1 | sed 's/^/  /' || true
while IFS= read -r g; do
  [ -n "$g" ] || continue
  note "目录里另有扩展描述文件 ${g#$GAMEDIR/}："
  if grep -qiE '^[[:space:]]*(macos|osx)' "$g"; then note "    有 macos 条目"; else warn "    该扩展缺 mac 版"; fi
done < <(find "$GAMEDIR" -maxdepth 2 -type f \( -name '*.gdextension' -o -name '*.gdnlib' \) 2>/dev/null || true)
STEAMHIT=""
if strings -a "$EXE" | grep -qim1 'godotsteam'; then STEAMHIT="exe 的 strings 里有 godotsteam"; fi
if "$PCKTOOL" list "$PCKSRC" 2>/dev/null | grep -qi 'godotsteam'; then
  STEAMHIT="${STEAMHIT:+$STEAMHIT; }pck 里有 godotsteam 文件"
fi
if [ -n "$STEAMHIT" ]; then
  warn "检出 GodotSteam（${STEAMHIT}）。"
  warn "  官方模板不含 GodotSteam，游戏一调 Steam 单例就崩。"
  warn "  需要：与 $VER 同版本的 GodotSteam macOS 构建（或其 GDExtension 的 mac 库）+ steam_appid.txt。"
fi
DATADIR=$(find "$GAMEDIR" -maxdepth 1 -type d -name 'data_*' | head -1 || true)
if [ -n "$DATADIR" ]; then
  warn "有 $(basename "$DATADIR")/：这是 C#/.NET(Mono) 版的数据目录，不是 pck 的散开形态。"
  warn "  官方 plain 模板跑不了 C# 游戏，要 mono 模板 + 对应 .NET 运行时；本脚本会把该目录克隆进去但不保证能跑。"
fi

echo "== 5. 准备 macOS 导出模板 =="
TDIR="$CACHE/$VER/tmpl"
TMPLAPP=$(find "$TDIR" -maxdepth 1 -type d -name '*_template.app' 2>/dev/null | head -1 || true)
if [ -z "$TMPLAPP" ]; then
  mkdir -p "$CACHE/$VER"
  TPZ="$GH/${VER}-${STATUS}/Godot_v${VER}-${STATUS}_export_templates.tpz"
  ZIP="$CACHE/$VER/macos-template.zip"
  if [ ! -f "$ZIP" ]; then
    note "本地无 $VER 模板，从 tpz 里只取 mac 那一份（HTTP Range，不下整包）"
    note "$TPZ"
    "$FETCH" "$TPZ" '(^|/)(macos|osx)\.zip$' "$ZIP" | sed 's/^/  /' \
      || die "取模板失败（$VER 可能没有该 tag，或网络不通）"
  fi
  mkdir -p "$TDIR"
  unzip -q -o "$ZIP" -d "$TDIR"
  TMPLAPP=$(find "$TDIR" -maxdepth 1 -type d -name '*_template.app' | head -1 || true)
  [ -n "$TMPLAPP" ] || die "模板 zip 里没有 *_template.app"
fi
note "模板: $TMPLAPP"

TBIN=""
for c in "$TMPLAPP/Contents/MacOS"/*release* "$TMPLAPP/Contents/MacOS"/*; do
  [ -f "$c" ] || continue
  if file "$c" | grep -q 'Mach-O'; then TBIN=$c; break; fi
done
[ -n "$TBIN" ] || die "模板里找不到 Mach-O 主程序"
ARCHS=$(lipo -archs "$TBIN")
note "模板主程序: $(basename "$TBIN")  架构: $ARCHS"
# Godot 3.3 之前的官方 macOS 模板只有 x86_64。与 Unity 2020.2 之前的播放器同一处理：Rosetta 在就走 Rosetta（仍是官方引擎读原 pck，
# 无 Windows 翻译层），标明「x86_64 走 Rosetta」；Rosetta 不在才拒绝（2026-09-14 Door in the Woods 3.1 / Legend creatures 3.2.1 冒烟）。
case "$ARCHS" in
  *arm64*) ;;
  *x86_64*)
    if [ -f /Library/Apple/usr/share/rosetta/rosetta ] || arch -x86_64 /usr/bin/true 2>/dev/null; then
      warn "  该版本模板只有 x86_64（Godot 3.3 起才有 arm64），走 Rosetta；仍是官方引擎读原 pck，没有 Windows 翻译层"
    else
      die "该版本模板不含 arm64（只有 ${ARCHS}），本机也没有 Rosetta，这条路对 $VER 不成立"
    fi;;
  *) die "该版本模板架构认不出（${ARCHS}）";;
esac

echo "== 6. 组装 .app =="
BID="org.godotengine.wrapped.$(printf '%s' "$NAME" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]//g')"
rm -rf "$OUT"
mkdir -p "$(dirname "$OUT")"
cp -Rc "$TMPLAPP" "$OUT"
BINDIR="$OUT/Contents/MacOS"
[ "$(basename "$TBIN")" = "$NAME" ] || mv "$BINDIR/$(basename "$TBIN")" "$BINDIR/$NAME"
for junk in "$BINDIR"/*; do
  [ "$junk" = "$BINDIR/$NAME" ] || rm -rf "$junk"
done
chmod +x "$BINDIR/$NAME"

# 启动壳: 首次启动强制窗口化。独占全屏一旦起来就盖住桌面、菜单栏和光标,
# 出问题时连退出都难(2026-09-11 Wine 路线实测把 owner 困住过一次)。
# 标记文件记在 ~/Library/Application Support/dryport/firstrun/ 下,
# 第二次起就不再加参数, 游戏自己存的显示设置说了算。
# Godot 3.x 与 4.x 都认 --windowed 和 --resolution WxH(官方命令行参数)。
# 注意: 壳 exec 的是真二进制, Godot 靠 _NSGetExecutablePath 的 basename 找
# 同名 .pck, 所以 pck 名仍然要和二进制同名, 不是和壳同名。
cat > "$BINDIR/launcher" <<LAUNCH
#!/bin/bash
# generated by godot-wrap.sh
D="\$(cd -- "\$(dirname -- "\$0")" && pwd)"
MARK="\$HOME/Library/Application Support/dryport/firstrun/$BID"
_extra=""
if [ ! -f "\$MARK" ]; then
  _extra="--windowed --resolution \${SN_WINDOW_SIZE:-1280x720}"
  mkdir -p "\$(dirname "\$MARK")" 2>/dev/null && : > "\$MARK" 2>/dev/null || true
fi
exec "\$D/$NAME" \$_extra "\$@"
LAUNCH
chmod +x "$BINDIR/launcher"
RES="$OUT/Contents/Resources"
mkdir -p "$RES"
cp -c "$PCKSRC" "$RES/$NAME.pck" 2>/dev/null || cp "$PCKSRC" "$RES/$NAME.pck"
note "pck -> Contents/Resources/$NAME.pck（文件名必须与可执行同名，Godot 靠这条找包）"

# 其余随包文件（不含 Windows 专属件与已处理的 pck）
while IFS= read -r f; do
  [ -n "$f" ] || continue
  b=$(basename "$f")
  case "$b" in *.exe|*.EXE|*.dll|*.DLL|*.pdb|*.PDB|*.pck|*.PCK|*.lib|*.exp) continue ;; esac
  cp -Rc "$f" "$RES/" 2>/dev/null || cp -R "$f" "$RES/"
  note "随包带入 $b"
done < <(find "$GAMEDIR" -maxdepth 1 -mindepth 1 ! -name '.*')

if [ -n "$APPID" ]; then
  printf '%s\n' "$APPID" > "$RES/steam_appid.txt"
  note "写 steam_appid.txt = ${APPID}（Godot 启动后会 chdir 到 Contents/Resources，这里读得到）"
fi

cat > "$OUT/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>English</string>
	<key>CFBundleExecutable</key><string>launcher</string>
	<key>CFBundleName</key><string>$NAME</string>
	<key>CFBundleDisplayName</key><string>$NAME</string>
	<key>CFBundleIconFile</key><string>icon.icns</string>
	<key>CFBundleIdentifier</key><string>$BID</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundleVersion</key><string>1.0</string>
	<key>CFBundleSupportedPlatforms</key><array><string>MacOSX</string></array>
	<key>NSPrincipalClass</key><string>NSApplication</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>LSApplicationCategoryType</key><string>public.app-category.games</string>
	<key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
	<key>LSMinimumSystemVersionByArchitecture</key><dict><key>arm64</key><string>11.0</string><key>x86_64</key><string>10.13</string></dict>
</dict>
</plist>
EOF
plutil -lint "$OUT/Contents/Info.plist" > /dev/null || die "Info.plist 不合法"

echo "== 7. 签名 =="
codesign --force --deep --sign - "$OUT" 2>&1 | sed 's/^/  /'
codesign -v "$OUT" && note "codesign -v 通过"

cleanup; TMPPCK=""; TMPBASE=""
echo
echo "完成: $OUT"
echo "  窗口模式: 首次启动 --windowed --resolution 1280x720, 之后按游戏自己的设置"
echo "  引擎 $VER($STATUS)  模板架构 $ARCHS  体积 $(du -sh "$OUT" | cut -f1)"
[ -n "$STEAMHIT" ] && echo "  [未解决] GodotSteam，见上面第 4 步"
exit 0
