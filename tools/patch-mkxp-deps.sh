#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# mkxp-z 依赖在 macOS 26 / Xcode 26 上的兼容修复
# 2026-09-10 实测趟出。上游这些库多年未动,新工具链收紧后逐个失败。
# 用法: 在 mkxp-z 源码根目录跑 ./patch-mkxp-deps.sh,然后再 make everything -f arm64.make
set -e
ROOT="${1:-$(pwd)}"
DEPS="$ROOT/macos/Dependencies"
DL="$DEPS/downloads/aarch64-apple-darwin"
[ -d "$DEPS" ] || { echo "错: $ROOT 不像 mkxp-z 源码根目录"; exit 1; }

echo "[1/5] common.make: cmake 4 兼容 + 全局降级 -Werror"
sed -i '' 's|^CMAKE     := $(CONFIGURE_ENV) cmake \.\. $(CMAKE_ARGS)|CMAKE     := $(CONFIGURE_ENV) cmake .. -DCMAKE_POLICY_VERSION_MINIMUM=3.5 $(CMAKE_ARGS)|' "$DEPS/common.make"
sed -i '' '8s|.*|CFLAGS := -I$(INCLUDEDIR) $(TARGETFLAGS) $(DEFINES) -O3 -Wno-error|' "$DEPS/common.make"

echo "[2/5] common.make: Ruby 钉回 gnu17 (C23 改了 K&R 声明语义)"
python3 - "$DEPS/common.make" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
o='export CFLAGS="-flto=full -DRUBY_FUNCTION_NAME_STRING=__func__'
n='export CFLAGS="-flto=full -std=gnu17 -DRUBY_FUNCTION_NAME_STRING=__func__ -Wno-error=incompatible-function-pointer-types -Wno-error=implicit-function-declaration -Wno-error=int-conversion'
if o in s and '-std=gnu17' not in s: open(p,'w').write(s.replace(o,n))
PY

echo "[3/5] common.make: 修上游 openssl 浅克隆 bug (--depth 1 被错写到 cd 后面)"
python3 - "$DEPS/common.make" <<'PY'
import sys
p=sys.argv[1]; L=open(p).read().split('\n')
for i,l in enumerate(L):
    if 'openssl/openssl' in l and 'CLONE' in l:
        L[i]='\t$(CLONE) $(GITHUB)/openssl/openssl $(DOWNLOADS)/openssl --single-branch --branch OpenSSL_1_1_1i --depth 1'
        if i+1<len(L) and L[i+1].strip().startswith('cd ') and '--depth' in L[i+1]: L[i+1]=''
        break
open(p,'w').write('\n'.join(L))
PY

echo "[4/5] libvorbis: 摘掉新链接器不认的 -force_cpusubtype_ALL"
[ -d "$DL/vorbis" ] && find "$DL/vorbis" \( -name "configure*" -o -name "Makefile*" \) -exec sed -i '' 's/ *-force_cpusubtype_ALL//g' {} + 2>/dev/null || true

echo "[5/5] libpng: TARGET_OS_MAC 现代 SDK 恒为 1,会误入 Classic Mac 分支找 fp.h"
[ -f "$DL/libpng/pngpriv.h" ] && sed -i '' 's/defined(TARGET_OS_MAC)/0/' "$DL/libpng/pngpriv.h" || true

echo "[附] SDL2: 摘掉自加的 -Werror=declaration-after-statement"
[ -d "$DL/sdl2" ] && sed -i '' 's/ -Werror=declaration-after-statement//g' "$DL/sdl2/Makefile" "$DL/sdl2/configure" 2>/dev/null || true

echo "完成。注意: 4/5/附 三项针对已 clone 的源码,首次构建请先跑一次 make 让它 clone 后再执行本脚本。"
