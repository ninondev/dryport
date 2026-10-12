#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 只取 CodeWeavers 公开源码，在用户运行时目录编译；默认只检查，不启动全量构建。
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../lib/paths.sh"

VERSION=26.3.0
SOURCE_URL="https://media.codeweavers.com/pub/crossover/source/crossover-sources-${VERSION}.tar.gz"
# 2026-09-12 对本机原始源码包实取；不是上游提供的签名。
SOURCE_SHA256=ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872
MODE=check
PREFIX="$SN_RUNTIMES/wine-cx"
WORK="$SN_RUNTIMES/build/wine-$VERSION"
ARCHIVE=""
JOBS="${SN_BUILD_JOBS:-}"

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
usage() {
  cat <<'HELP'
用法：tools/build-wine.sh [操作] [选项]
  --check       只检查工具、架构、磁盘和已有产物（默认，不下载、不编译）
  --fetch       下载并核对固定 SHA-256
  --prepare     核对源码、只解开 Wine/FreeType、应用本地兼容修复
  --deps-only   准备源码并编译、验证 FreeType（不编译 Wine）
  --configure-only 验证 FreeType 并配置 Wine（不编译 Wine）
  --build       从源码构建 Wine，最后验证 macdrv_functions 导出
  --source FILE 使用本地原始源码包，仍须符合固定 SHA-256
  --work DIR    构建缓存目录；中断后用同一路径续跑
  --prefix DIR  新的安装目录；已有目录绝不覆盖
  --jobs N      编译并发，默认 8 GiB 及以下 1，其余最多 2

前置：macOS Apple Silicon、Xcode Command Line Tools、Rosetta 2。
开源构建工具可用 Homebrew 安装：brew install bison flex mingw-w64 cmake pkgconf
本脚本不安装 Rosetta、不改系统设置。Wine 构建保留 20 GiB 可用空间门槛。
只构建 Wine + 字体库，不包含 DXMT；游戏启动与 DirectX 11 仍须单独验证。
HELP
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check|--fetch|--prepare|--deps-only|--configure-only|--build) MODE="${1#--}"; shift ;;
    --source|--work|--prefix|--jobs)
      [ "$#" -ge 2 ] || die "$1 缺少参数"
      case "$1" in --source) ARCHIVE="$2";; --work) WORK="$2";; --prefix) PREFIX="$2";; --jobs) JOBS="$2";; esac
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "未知参数：$1；运行 --help 查看用法" ;;
  esac
done
command -v python3 >/dev/null || die "缺 python3；先安装 Python 3 后重跑"
# Wine 的 Makefile 不支持路径含空白；提前报错，不能等编译半途失败。
for p in "$PREFIX" "$WORK"; do
  case "$p" in /*) ;; *) die "构建和安装目录必须是绝对路径：$p" ;; esac
  [[ "$p" != *[[:space:]]* && "$p" != *\"* && "$p" != *\\* ]] || die "Wine 构建目录暂不支持空白、双引号或反斜线：$p"
done
PREFIX="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$PREFIX")"
WORK="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$WORK")"
[ "$PREFIX" != / ] && [ "$WORK" != / ] || die "不能使用根目录"
case "$WORK/" in "$PREFIX/"*) die "构建目录不能放进最终安装目录" ;; esac
case "$PREFIX/" in "$WORK/"*) die "安装目录不能放进构建缓存" ;; esac
[ ! -L "$WORK" ] && [ ! -L "$PREFIX" ] || die "构建或安装目录是软链，请指定新目录"

verify_wine() {
  local root="$1" so="$1/lib/wine/x86_64-unix/winemac.so"
  [ -x "$root/bin/wine" ] && [ -x "$root/bin/wineserver" ] && [ -f "$so" ] || return 1
  file -b "$root/bin/wine" | grep -q x86_64 || return 1
  file -b "$root/bin/wineserver" | grep -q x86_64 || return 1
  file -b "$so" | grep -q x86_64 || return 1
  local count
  count=$(nm -g "$so" | awk '$NF == "_macdrv_functions" && $(NF-1) != "U" {n++} END {print n+0}')
  [ "$count" = 1 ]
}
free_gib() {
  python3 - "$1" <<'PY'
import pathlib, shutil, sys
p = pathlib.Path(sys.argv[1])
while not p.exists(): p = p.parent
print(shutil.disk_usage(p).free // (1024 ** 3))
PY
}
if [ "$MODE" = check ] || [ "$MODE" = build ] || [ "$MODE" = deps-only ] || [ "$MODE" = configure-only ]; then
  [ "$(uname -s)" = Darwin ] || die "Wine 构建只支持 macOS"
  [ "$(uname -m)" = arm64 ] || die "请在 Apple Silicon 的原生 arm64 终端运行"
  # 不执行待翻译的新二进制。已有系统工具的架构探针不会启动 Wine 或创建 prefix。
  /usr/bin/arch -x86_64 /usr/bin/true || die "Rosetta 2 不可用；原生路线仍可使用"
  if command -v brew >/dev/null; then
    BREW_PREFIX=$(brew --prefix)
    export PATH="$BREW_PREFIX/opt/bison/bin:$BREW_PREFIX/opt/flex/bin:$PATH"
  fi
  MISSING=0
  for tool in clang clang++ make cmake bison flex pkg-config x86_64-w64-mingw32-gcc i686-w64-mingw32-gcc nm otool install_name_tool codesign; do
    if ! command -v "$tool" >/dev/null; then say "缺构建工具：$tool"; MISSING=1; fi
  done
  xcrun --show-sdk-path >/dev/null 2>&1 || { say '缺 macOS SDK：安装 Xcode Command Line Tools'; MISSING=1; }
  if command -v bison >/dev/null && ! bison --version | head -1 | grep -Eq ' ([3-9]|[1-9][0-9]+)\.'; then
    say 'bison 版本低于 3；安装 Homebrew bison 后重跑'; MISSING=1
  fi
  FREE=$(free_gib "$WORK")
  say "构建卷可用：$FREE GiB；全量 Wine 构建门槛：20 GiB"
  if [ "$FREE" -lt 20 ] && [ "$MODE" != deps-only ]; then say '全量构建空间不足；不会删除已有内容'; MISSING=1; fi
  MEMORY=$(sysctl -n hw.memsize)
  if [ -z "$JOBS" ]; then if [ "$MEMORY" -le 8589934592 ]; then JOBS=1; else JOBS=2; fi; fi
  [[ "$JOBS" =~ ^[1-9][0-9]*$ ]] && [ "$JOBS" -le 64 ] || die '--jobs 必须为 1 到 64 的整数'
  say "编译并发：${JOBS}；物理内存：$((MEMORY / 1073741824)) GiB"
  say "源码：$SOURCE_URL"
  say "SHA-256：$SOURCE_SHA256"
  say "构建目录：$WORK"
  say "安装目录：$PREFIX"
  if [ -e "$PREFIX" ]; then
    if verify_wine "$PREFIX"; then
      say '已有 Wine：x86_64 winemac.so 正好导出 1 个 macdrv_functions；未运行游戏'
      if [ "$MODE" = build ]; then say '保留已有安装；要重建请用 --prefix 指定新目录'; exit 0; fi
    else
      say '已有安装目录不满足 Wine 导出验收；不会覆盖，请指定新的 --prefix'
      [ "$MODE" != build ] || exit 1
    fi
  fi
  [ "$MISSING" = 0 ] || die '构建前置条件未齐；参考 --help 补齐后重跑'
  [ "$MODE" != check ] || exit 0
fi

[ "$(free_gib "$WORK")" -ge 2 ] || die '源码与字体构建需要至少 2 GiB 可用空间'
if [ -e "$WORK" ] && [ ! -f "$WORK/.dryport-wine-build" ]; then
  die "构建目录已存在但不属于本脚本，保留原内容：${WORK}；改用 --work 新目录"
fi
mkdir -p "$WORK"
LOCK="$WORK/.lock"
mkdir "$LOCK" 2>/dev/null || die "构建目录被占用：${LOCK}；先确认另一构建已结束"
cleanup() { rmdir "$LOCK" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
EXPECTED="recipe=1 source=$SOURCE_SHA256 prefix=$PREFIX"
if [ -f "$WORK/.dryport-wine-build" ]; then
  [ "$(cat "$WORK/.dryport-wine-build")" = "$EXPECTED" ] || die '缓存属于另一构建配置；请指定新的 --work'
else
  printf '%s\n' "$EXPECTED" > "$WORK/.dryport-wine-build"
fi
if [ -z "$ARCHIVE" ]; then ARCHIVE="$WORK/crossover-sources-$VERSION.tar.gz"; fi
if [ ! -f "$ARCHIVE" ]; then
  [ "$ARCHIVE" = "$WORK/crossover-sources-$VERSION.tar.gz" ] || die "本地源码包不存在：$ARCHIVE"
  say "下载官方源码：$SOURCE_URL"
  curl --proto '=https' --tlsv1.2 --fail --location --retry 3 --connect-timeout 20 \
    --output "$ARCHIVE.part" "$SOURCE_URL"
  ACTUAL=$(shasum -a 256 "$ARCHIVE.part" | awk '{print $1}')
  [ "$ACTUAL" = "$SOURCE_SHA256" ] || die "下载包 SHA-256 不符，保留 $ARCHIVE.part；预期 ${SOURCE_SHA256}，实际 $ACTUAL"
  mv "$ARCHIVE.part" "$ARCHIVE"
fi
ACTUAL=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
[ "$ACTUAL" = "$SOURCE_SHA256" ] || die "源码 SHA-256 不符：${ARCHIVE}；预期 ${SOURCE_SHA256}，实际 ${ACTUAL}；未解压"
say "源码 SHA-256 核对通过：$ACTUAL"
[ "$MODE" != fetch ] || exit 0

if [ ! -f "$WORK/.prepared" ]; then
  # 只解开两个要构建的目录；新目录失败可重跑，不动用户原始源码。
  EXTRACT=$(mktemp -d "$WORK/extract.XXXXXX")
  python3 - "$ARCHIVE" "$EXTRACT" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(sys.argv[1]) as tf:
    selected = [m for m in tf.getmembers() if m.name.startswith(("sources/wine/", "sources/freetype/"))]
    for m in selected:
        if not (m.isfile() or m.isdir()):
            raise SystemExit("源码含非普通文件，停止解压：" + m.name)
        if not (root / m.name).resolve().is_relative_to(root):
            raise SystemExit("源码路径越界：" + m.name)
    tf.extractall(root, members=selected)
wine = root / "sources/wine"
if (wine / "VERSION").read_text().strip() != "Wine version 11.0":
    raise SystemExit("源码内 Wine 版本不符合固定配方")
p = wine / "dlls/win32u/vulkan.c"
s = p.read_text()
old = '#include "config.h"'
if s.count(old) != 1 or "#define SONAME_LIBVULKAN" in s:
    raise SystemExit("Vulkan 兼容修复前置条件不符")
s = s.replace(old, old + '\n#ifndef SONAME_LIBVULKAN\n#define SONAME_LIBVULKAN "libvulkan.1.dylib"\n#endif', 1)
p.write_text(s)
if "macdrv_functions =" not in (wine / "dlls/winemac.drv/d3dmetal.c").read_text():
    raise SystemExit("源码缺少 DXMT 要求的 macdrv_functions 定义")
# 2026-09-14 冒烟：win32u 对外宣称 WGL_ARB_create_context_no_error，SDL2 于是给 wglCreateContextAttribsARB 传 WGL_CONTEXT_OPENGL_NO_ERROR_ARB
# （通常值 0），winemac.drv 却把它当未知属性退 ERROR_INVALID_PARAMETER，所有 SDL2 + OpenGL 的 Windows 游戏都建不出上下文
# （Fixture Title、一款 OpenGL 游戏、Fixture Title 三款实测）。macOS 上没有 no-error 上下文，值为 0 直接放过，非 0 只告警。
p = wine / "dlls/winemac.drv/opengl.c"
s = p.read_text()
old = """            default:
                WARN("Unknown attribute %s.\\n", debugstr_attrib(attr, value));
                RtlSetLastWin32Error(ERROR_INVALID_PARAMETER);
                return FALSE;"""
if s.count(old) != 1 or "WGL_CONTEXT_OPENGL_NO_ERROR_ARB" in s:
    raise SystemExit("winemac.drv 的 WGL no-error 属性修复前置条件不符")
s = s.replace(old, """            case WGL_CONTEXT_OPENGL_NO_ERROR_ARB:
                /* dryport: win32u advertises WGL_ARB_create_context_no_error, so SDL2 passes this (usually 0);
                 * no-error contexts do not exist on macOS, accept the attribute instead of failing the whole context. */
                if (value) WARN("WGL_CONTEXT_OPENGL_NO_ERROR_ARB requested, ignoring\\n");
                break;

""" + old, 1)
p.write_text(s)
PY
  [ ! -e "$WORK/sources" ] || die "发现未完成的源码目录：$WORK/sources；指定新的 --work 保留现场"
  mv "$EXTRACT/sources" "$WORK/sources"
  rmdir "$EXTRACT"
  touch "$WORK/.prepared"
fi
say "源码准备完成：$WORK/sources（已补 Vulkan 无后端时的宏定义、winemac.drv 的 WGL no-error 属性）"
[ "$MODE" != prepare ] || exit 0

mkdir -p "$WORK/logs" "$WORK/deps"
run_logged() {
  local name="$1"; shift
  if [ -f "$WORK/logs/$name.log" ]; then
    mv "$WORK/logs/$name.log" "$WORK/logs/$name.previous.$(date -u +%Y%m%dT%H%M%S).$$.log"
  fi
  say "执行 ${name}，日志：$WORK/logs/$name.log"
  "$@" > "$WORK/logs/$name.log" 2>&1 || {
    tail -30 "$WORK/logs/$name.log" >&2
    die "$name 失败；修正后用同一命令续跑，完整日志：$WORK/logs/$name.log"
  }
}
# 同包 FreeType 构建为 x86_64；停用外部可选库，防止无意链入 Homebrew arm64 依赖。
run_logged freetype-configure cmake -S "$WORK/sources/freetype" -B "$WORK/freetype-build" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
  -DCMAKE_OSX_ARCHITECTURES=x86_64 -DCMAKE_INSTALL_PREFIX="$WORK/deps" \
  -DCMAKE_INSTALL_NAME_DIR="$WORK/deps/lib" -DCMAKE_MACOSX_RPATH=OFF \
  -DFT_DISABLE_ZLIB=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_PNG=ON \
  -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_BROTLI=ON
run_logged freetype-build cmake --build "$WORK/freetype-build" --parallel "$JOBS"
run_logged freetype-install cmake --install "$WORK/freetype-build"
FT="$WORK/deps/lib/libfreetype.6.dylib"
[ -f "$FT" ] && file -b "$FT" | grep -q x86_64 || die 'FreeType 没有生成 x86_64 dylib'
otool -L "$FT" > "$WORK/logs/freetype-linkage.log"
python3 - "$WORK/logs/freetype-linkage.log" "$WORK/deps/lib/" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()[2:]
for line in lines:
    lib = line.strip().split(" (", 1)[0]
    if not lib.startswith(("/usr/lib/", "/System/Library/")):
        raise SystemExit("FreeType 意外依赖第三方动态库：" + lib)
PY
cat > "$WORK/ft-probe.c" <<'C'
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    void *h = dlopen(argv[1], RTLD_NOW);
    if (!h) { fprintf(stderr, "%s\n", dlerror()); return 3; }
    int (*init)(void **) = (int (*)(void **))dlsym(h, "FT_Init_FreeType");
    int (*done)(void *) = (int (*)(void *))dlsym(h, "FT_Done_FreeType");
    void *ft = NULL;
    if (!init || !done || init(&ft)) return 4;
    done(ft); dlclose(h); puts("FreeType x86_64 dlopen + FT_Init_FreeType: OK");
    return 0;
}
C
run_logged freetype-probe-build clang -arch x86_64 "$WORK/ft-probe.c" -o "$WORK/ft-probe"
run_logged freetype-probe "$WORK/ft-probe" "$FT"
cat "$WORK/logs/freetype-probe.log"
[ "$MODE" != deps-only ] || exit 0

[ ! -e "$PREFIX" ] || die "安装目录已存在，保留原内容：$PREFIX"
mkdir -p "$WORK/wine-build"
cd "$WORK/wine-build"
if [ ! -f "$WORK/.configured" ]; then
  run_logged wine-configure env PKG_CONFIG_PATH= PKG_CONFIG_LIBDIR="$WORK/deps/lib/pkgconfig" \
    /usr/bin/arch -x86_64 /bin/bash "$WORK/sources/wine/configure" \
    CC='clang -arch x86_64' CXX='clang++ -arch x86_64' \
    FREETYPE_CFLAGS="-I$WORK/deps/include/freetype2" FREETYPE_LIBS="-L$WORK/deps/lib -lfreetype" \
    --prefix="$PREFIX" --disable-tests --enable-archs=x86_64,i386 --with-mingw --with-freetype \
    --without-x --without-gstreamer --without-sdl --without-usb --without-vulkan --without-gnutls \
    --without-cups --without-krb5 --without-pcap --without-pcsclite --without-opencl
  python3 - include/config.h "$PREFIX/lib/libfreetype.6.dylib" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s, n = re.subn(r'^#define SONAME_LIBFREETYPE .*$', '#define SONAME_LIBFREETYPE "' + sys.argv[2] + '"', s, flags=re.M)
if n != 1: raise SystemExit("configure 未启用 FreeType，不能宣称字体可用")
p.write_text(s)
PY
  grep -Eq '^host_cpu = x86_64$' Makefile || die 'configure 没有生成 x86_64 宿主配置'
  touch "$WORK/.configured"
fi
say 'Wine configure 完成：x86_64 宿主，FreeType 路径已固定到安装前缀。'
[ "$MODE" != configure-only ] || exit 0
run_logged wine-build make "-j$JOBS"
STAGE=$(mktemp -d "$WORK/stage.XXXXXX")
run_logged wine-install make install "DESTDIR=$STAGE"
TARGET="$STAGE$PREFIX"
mkdir -p "$TARGET/lib"
cp -L "$FT" "$TARGET/lib/libfreetype.6.dylib"
install_name_tool -id "$PREFIX/lib/libfreetype.6.dylib" "$TARGET/lib/libfreetype.6.dylib"
codesign --force --sign - "$TARGET/lib/libfreetype.6.dylib"
verify_wine "$TARGET" || die "安装产物未通过 macdrv_functions 导出验收，保留：$TARGET"
python3 - "$TARGET/.dryport-build.json" "$SOURCE_URL" "$SOURCE_SHA256" "$JOBS" <<'PY'
import datetime, json, sys
with open(sys.argv[1], "w") as f:
    json.dump(dict(source=sys.argv[2], sha256=sys.argv[3], recipe=1, jobs=int(sys.argv[4]),
                   built_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   verified="x86_64 winemac.so exports exactly one macdrv_functions", dxmt=False), f, indent=2)
PY
mkdir -p "$(dirname "$PREFIX")"
# 原子创建目标名，不允许 mv 在并发新增的目录里面套一层安装目录。
python3 - "$TARGET" "$PREFIX" <<'PY'
import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True)
renamex = libc.renamex_np
renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
if renamex(os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2]), 4):  # RENAME_EXCL
    err = ctypes.get_errno(); raise OSError(err, os.strerror(err), sys.argv[2])
PY
verify_wine "$PREFIX" || die '安装后的导出验收失败；保留产物供检查'
say "Wine 构建并安装完成：$PREFIX"
say '验收：x86_64 winemac.so 导出 1 个 macdrv_functions；FreeType 加载探针通过。'
say '尚未包含 DXMT；此结果不代表游戏启动或 DirectX 11 已验证。'
