#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 从明确可信的本地目录升级独立 app：核 SHA256、归档与 app 结构，旧版保留可回退。
#
#   bin/self-update.sh --from <可信的本地目录> --app <Dryport.app 路径> [--check]
#   bin/self-update.sh --app <Dryport.app 路径> --rollback
#
# --check 比较数字版本：同版且同提交退出 0，新版退出 10，无法比较或版本不更新则拒绝；远程来源退出 20。
# 升级：归档下到 $SN_RUNTIMES/build/updates/，SHA256 不符就拒绝；解包到 app 旁边的暂存目录，codesign --verify 与提交号核对
# 通过后才把旧 app 整体移到 .dryport-previous.<时间>/，新 app 落位；任何一步失败都把旧 app 放回原位。
# SHA256 与 codesign 结构检查不验证发布者身份；远程更新暂停，等待独立清单认证。
# 只动 app 本体与本工具的更新缓存；游戏、存档与 Steam 不碰。
set -uo pipefail
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in /*) SELF="$LINK" ;; *) SELF="$(dirname "$SELF")/$LINK" ;; esac
done
source "$(cd "$(dirname "$SELF")/../lib" && pwd)/paths.sh"
die(){ echo "错: $*" >&2; exit 1; }

SRC="${SN_UPDATE_SOURCE:-}" APP="${SN_APP:-}" MODE=update
while [ $# -gt 0 ]; do
  case "$1" in
    --from) SRC="$2"; shift 2;;
    --app) APP="$2"; shift 2;;
    --check) MODE=check; shift;;
    --rollback) MODE=rollback; shift;;
    *) die "不认识的参数: $1";;
  esac
done
[ -n "$APP" ] || die "要 --app <Dryport.app 路径>（或环境变量 SN_APP）"
case "$APP" in *.app) ;; *) die "--app 必须是 .app 路径: $APP";; esac
PARENT="$(dirname "$APP")"; NAME="$(basename "$APP")"

installed_revision(){
  local marker="$APP/Contents/Resources/toolchain/SOURCE-REVISION.txt"
  if [ -f "$marker" ]; then tr -d '[:space:]' < "$marker"; else echo "dev"; fi
}

if [ "$MODE" = rollback ]; then
  prev=$(ls -dt "$PARENT"/.dryport-previous.*/"$NAME" 2>/dev/null | head -1)
  [ -n "$prev" ] || die "没有可回退的旧版（$PARENT/.dryport-previous.*/）"
  stamp=$(date +%Y%m%d-%H%M%S)
  if [ -e "$APP" ]; then
    mkdir -p "$PARENT/.dryport-rolledback.$stamp"
    mv "$APP" "$PARENT/.dryport-rolledback.$stamp/$NAME" || die "现版本挪不开，未改动"
  fi
  if ! mv "$prev" "$APP"; then
    [ -e "$PARENT/.dryport-rolledback.$stamp/$NAME" ] && mv "$PARENT/.dryport-rolledback.$stamp/$NAME" "$APP"
    die "旧版放不回去，现版本已恢复"
  fi
  rmdir "$(dirname "$prev")" 2>/dev/null || true
  echo "已回退到 $(installed_revision)；换下来的版本在 $PARENT/.dryport-rolledback.$stamp/"
  exit 0
fi

[ -n "$SRC" ] || die "要 --from <可信的本地发布目录>（或环境变量 SN_UPDATE_SOURCE）"
case "$SRC" in *://*) echo "remote updates are off in this preview; update from a local folder（远程更新已暂停）" >&2; exit 20;; esac
FIELDS=$(python3 - "$SRC" "$SN_LIB" <<'PY'
import json, re, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from bundle_safety import relative_violation
from app_versions import version_tuple
src = Path(sys.argv[1])
manifest = src / 'release-manifest.json'
if not src.is_dir() or manifest.is_symlink() or not manifest.is_file():
    sys.exit('本地发布清单不存在或是符号链接')
data = manifest.read_bytes()
m = json.loads(data)
if m.get('manifest_version') != 1:
    sys.exit('发布清单版本不支持')
for field in ('revision', 'version'):
    if not isinstance(m.get(field), str) or not re.fullmatch(r'[A-Za-z0-9._-]+', m[field]):
        sys.exit('发布清单字段不合法：' + field)
version_tuple(m['version'])
app = m.get('app', {})
archive = app.get('archive')
why = relative_violation(archive, 'archive')
if why or not re.fullmatch(r'[A-Za-z0-9._-]+', archive) or archive in ('.', '..'):
    sys.exit(why or 'archive 必须是单个文件名')
if not isinstance(app.get('sha256'), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', app['sha256']):
    sys.exit('发布清单 sha256 不合法')
if type(app.get('bytes')) is not int or app['bytes'] < 0:
    sys.exit('发布清单 bytes 不合法')
print(m['revision'], m['version'], archive, app['sha256'], app['bytes'])
PY
) || die "读不到或拒绝本地发布清单；app 未改动"
read -r REV VERSION ARCHIVE SHA BYTES <<< "$FIELDS"
HAVE=$(installed_revision)
COMPARISON=$(python3 - "$APP" "$VERSION" "$SN_LIB" <<'PY'
import sys
sys.path.insert(0, sys.argv[3])
from app_versions import bundle_version, version_tuple
try:
    current = version_tuple(bundle_version(sys.argv[1]))
    candidate = version_tuple(sys.argv[2])
    print((candidate > current) - (candidate < current))
except ValueError as error:
    sys.exit(str(error))
PY
) || die "Could not determine installed version（已装 ${HAVE}）；app 未改动"
if [ "$COMPARISON" -le 0 ]; then
  if [ "$MODE" = check ] && [ "$COMPARISON" -eq 0 ] && [ "$HAVE" = "$REV" ]; then
    echo "已是最新：${VERSION}（${REV}）"; exit 0
  fi
  die "Release version is not newer: ${VERSION}（已装 ${HAVE}）；app 未改动"
fi
if [ "$MODE" = check ]; then
  echo "有新版本：${VERSION}（${REV}，$(python3 -c 'import sys; print(f"{int(sys.argv[1]) / 1e6:.1f}")' "$BYTES") MB）；已装 $HAVE"; exit 10
fi
[ "$HAVE" != dev ] || echo "  注意：当前 app 指向源码仓库（开发构建），升级后会换成独立包。"

SN_RUNTIMES=$(python3 - "$SN_RUNTIMES" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
if '\n' in str(root) or '\r' in str(root):
    sys.exit('Configured runtime root contains a line break')
if root.exists() and not root.is_dir():
    sys.exit('Configured runtime root is not a directory')
print(root)
PY
) || die "运行时根目录不是可用目录；app 未改动"
FILE="$SN_RUNTIMES/build/updates/$ARCHIVE"
python3 - "$SRC" "$ARCHIVE" "$FILE" "$SHA" "$SN_LIB" "$SN_RUNTIMES" <<'PY' || die "复制或校验失败，未改动 app"
import hashlib, os, shutil, sys
from pathlib import Path
src, name, dest, want, lib, runtimes = sys.argv[1:]
sys.path.insert(0, lib)
from bundle_safety import require_output, atomic_output, archive_violation
root = Path(runtimes)
dest = Path(dest).absolute()
for path in (dest, Path(str(dest) + '.part'), Path(str(dest) + '.bad')):
    require_output(root, path)
source = Path(src) / name
if source.is_symlink() or not source.is_file():
    sys.exit('本地归档不存在或是符号链接')
def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
if not (os.path.exists(dest) and digest(dest) == want):
    root.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as source_file, atomic_output(root, dest) as target:
        shutil.copyfileobj(source_file, target, 1 << 20)
got = digest(dest)
if got != want:
    os.replace(dest, str(dest) + ".bad"); sys.exit(f"SHA256 不符：清单 {want[:12]}… 实得 {got[:12]}…，文件已改名 .bad")
why = archive_violation(dest)
if why:
    sys.exit(why)
PY
STAGE="$(mktemp -d "$PARENT/.dryport-update.XXXXXX")"
cleanup(){ [ -z "${STAGE:-}" ] || rm -rf "$STAGE"; }
trap cleanup EXIT
tar -xf "$FILE" -C "$STAGE" || die "解包失败，未改动 app"
NEW="$STAGE/$NAME"
[ -x "$NEW/Contents/MacOS/Dryport" ] || die "包里没有 $NAME 主程序，未改动 app"
[ "$(tr -d '[:space:]' < "$NEW/Contents/Resources/toolchain/SOURCE-REVISION.txt" 2>/dev/null)" = "$REV" ] || die "包内提交号与清单不符，未改动 app"
python3 - "$NEW" "$VERSION" "$SN_LIB" <<'PY' || die "Archive version mismatch; app 未改动"
import sys
sys.path.insert(0, sys.argv[3])
from app_versions import bundle_version
if bundle_version(sys.argv[1]) != sys.argv[2]:
    sys.exit('Archive version mismatch with release manifest')
PY
codesign --verify "$NEW" >/dev/null 2>&1 || die "新 app 签名核对不过，未改动 app"
stamp=$(date +%Y%m%d-%H%M%S)
PREV_DIR="$PARENT/.dryport-previous.$stamp"
if [ -e "$APP" ]; then
  mkdir -p "$PREV_DIR"
  mv "$APP" "$PREV_DIR/$NAME" || die "旧 app 挪不开，未改动"
fi
if ! mv "$NEW" "$APP"; then
  [ -e "$PREV_DIR/$NAME" ] && mv "$PREV_DIR/$NAME" "$APP"
  die "新 app 落位失败，旧版已放回"
fi
# 只留最近一份旧版，更早的旧版目录是本工具自己留的备份
ls -dt "$PARENT"/.dryport-previous.* 2>/dev/null | tail -n +2 | while read -r d; do rm -rf "$d"; done
echo "已升级到 ${VERSION}（${REV}）：$APP"
[ -e "$PREV_DIR/$NAME" ] && echo "旧版保留在 $PREV_DIR/（回退：$0 --app \"$APP\" --rollback）"
exit 0
