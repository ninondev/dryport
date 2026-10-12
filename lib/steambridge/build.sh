#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 完整构建通过后才替换运行时；编译或核对失败不动已有产物，源码目录不落二进制。
set -euo pipefail
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in /*) SELF="$LINK" ;; *) SELF="$(dirname "$SELF")/$LINK" ;; esac
done
source "$(cd "$(dirname "$SELF")/.." && pwd)/paths.sh"
HERE="$SN_LIB/steambridge"
OUT="$SN_RUNTIMES/steambridge"
if [ "$#" -ne 0 ]; then
  echo "用法：$0（产物位置由 SN_RUNTIMES 指定）" >&2
  exit 2
fi
for tool in python3 x86_64-w64-mingw32-gcc cc otool; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少构建工具：${tool}。请先安装 Python 3、mingw-w64 和 Xcode Command Line Tools。" >&2; exit 1; }
done
python3 -B - "$SN_HOME" "$OUT" <<'PY'
import sys
from pathlib import Path
home, out = (Path(value).resolve() for value in sys.argv[1:])
if out == home or home in out.parents:
    sys.exit(f"运行时目录不能位于源码仓库内：{out}。请把 SN_RUNTIMES 指向仓库外。")
PY
mkdir -p "$OUT"
if ! mkdir "$OUT/.build-lock" 2>/dev/null; then
  echo "构建锁已存在：$OUT/.build-lock。请等当前构建结束；若上次被强制终止，确认没有构建进程后移除这个空目录。" >&2
  exit 1
fi
STAGE=""
cleanup() {
  [ -z "$STAGE" ] || rm -rf -- "$STAGE"
  rmdir "$OUT/.build-lock"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
STAGE="$(mktemp -d "$OUT/.build.XXXXXXXX")"

echo "生成导出表、空返回桩与惰性句柄取值函数。"
python3 -B "$HERE/gen.py" --output-dir "$STAGE"

echo "构建 Windows x86_64 DLL。"
x86_64-w64-mingw32-gcc -shared -O2 -Wall -Wno-unused-function -I "$HERE" -I "$STAGE" \
  -o "$STAGE/steam_api64.dll" "$HERE/bridge.c" "$HERE/inert_stubs.c" "$HERE/vtables.c" "$STAGE/stubs_gen.c" "$STAGE/inert_gen.c" "$STAGE/vtables_gen.c" \
  "$STAGE/steam_api64.def" -lws2_32 -static-libgcc -Wl,--enable-stdcall-fixup

echo "盖 Wine builtin 标记：外置桥靠 WINEDLLOVERRIDES=steam_api64=b 加载，Wine 只认带标记的 PE。"
python3 -B "$HERE/mark_builtin.py" "$STAGE/steam_api64.dll" --winebuild "$SN_RUNTIMES/wine-cx/bin/winebuild"
python3 -B "$HERE/mark_builtin.py" --check "$STAGE/steam_api64.dll" || { echo "构建失败：DLL 缺 Wine builtin 标记，已有运行时未替换。" >&2; exit 1; }

if command -v i686-w64-mingw32-gcc >/dev/null 2>&1; then
  echo "生成并构建 32 位桥 steam_api.dll（扁平 API + __thiscall C++ 虚表，栈字节数来自 argsizes-i386.json）。"
  python3 -B "$HERE/gen.py" --arch i386 --output-dir "$STAGE/i386"
  i686-w64-mingw32-gcc -shared -O2 -Wall -Wno-unused-function -I "$HERE" -I "$STAGE/i386" \
    -o "$STAGE/steam_api.dll" "$HERE/bridge.c" "$HERE/inert_stubs.c" "$HERE/vtables.c" "$STAGE/i386/stubs_gen.c" "$STAGE/i386/inert_gen.c" "$STAGE/i386/vtables_gen.c" \
    "$STAGE/i386/steam_api.def" -lws2_32 -static-libgcc -Wl,--enable-stdcall-fixup
  python3 -B "$HERE/mark_builtin.py" "$STAGE/steam_api.dll" --winebuild "$SN_RUNTIMES/wine-cx/bin/winebuild"
  python3 -B "$HERE/mark_builtin.py" --check "$STAGE/steam_api.dll" || { echo "构建失败：32 位 DLL 缺 Wine builtin 标记，已有运行时未替换。" >&2; exit 1; }
  i686-w64-mingw32-gcc -O2 -o "$STAGE/sbtest32.exe" "$HERE/sbtest32.c" "$STAGE/steam_api.dll"
  HAVE32=1
else
  echo "没有 i686-w64-mingw32-gcc，跳过 32 位桥（32 位 Unity 游戏将拿不到 Steam 身份）。"
  HAVE32=0
fi

echo "构建 macOS arm64 helper，运行时从 Steam 客户端加载 Valve 库。"
cc -O2 -arch arm64 -Wall -o "$STAGE/steambridge-helper" "$HERE/helper.c"

echo "构建 Windows 测试客户端。"
x86_64-w64-mingw32-gcc -O2 -o "$STAGE/sbtest.exe" "$HERE/sbtest.c" "$STAGE/steam_api64.dll"

python3 -B - "$HERE" "$STAGE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path
source, stage = map(Path, sys.argv[1:])
sys.path.insert(0, str(source))
from pe_exports import read_exports
expected = json.loads((source / "exports.json").read_text())
actual = read_exports(stage / "steam_api64.dll")
if actual != sorted(expected):
    sys.exit("构建失败：DLL 导出表与 exports.json 不一致，已有运行时未替换。")
if (stage / "steam_api.dll").exists():
    actual32 = read_exports(stage / "steam_api.dll")
    if actual32 != sorted(expected):
        sys.exit("构建失败：32 位 DLL 导出表与 exports.json 不一致，已有运行时未替换。")
    from mark_builtin import check as has_mark32
    if not has_mark32(stage / "steam_api.dll"):
        sys.exit("构建失败：32 位 DLL 缺 Wine builtin 标记，已有运行时未替换。")
    print(f"32 位 DLL：已核对 {len(actual32)} 个导出，带 Wine builtin 标记。")
dependencies = subprocess.check_output(["otool", "-L", str(stage / "steambridge-helper")], text=True)
if "steam_api" in dependencies.lower() or "steamclient" in dependencies.lower():
    sys.exit("构建失败：helper 意外链接了 Valve 库，已有运行时未替换。")
sys.path.insert(0, str(source))
from mark_builtin import check as has_builtin_mark
if not has_builtin_mark(stage / "steam_api64.dll"):
    sys.exit("构建失败：DLL 缺 Wine builtin 标记（2026-09-13 真机回归），已有运行时未替换。")
for name in ("steam_api64.dll", "steambridge-helper", "sbtest.exe", "steam_api.dll", "sbtest32.exe"):
    if (stage / name).exists():
        print(f"已构建：{name}，{(stage / name).stat().st_size} 字节")
print(f"已核对 {len(actual)} 个导出；DLL 带 Wine builtin 标记；helper 无 Valve 链接依赖。")
PY

PRODUCTS="steam_api64.dll steambridge-helper sbtest.exe"
[ "$HAVE32" = 1 ] && PRODUCTS="$PRODUCTS steam_api.dll sbtest32.exe"
for name in $PRODUCTS; do
  if [ -d "$OUT/$name" ]; then
    echo "不能替换运行时：$OUT/$name 是目录。已有产物未替换。" >&2
    exit 1
  fi
done
for name in $PRODUCTS; do
  mv -f "$STAGE/$name" "$OUT/$name"
done
echo "构建完成：${OUT}。尚未启动 helper 或验证游戏。"
