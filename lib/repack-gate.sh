# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# 所有打包入口共用同一退出码约定。检查器异常也必须停止，不能落到引擎分支继续写文件。
require_repack_permission() {
  local game_dir="$1" appid="${2:-${STEAM_APPID:-}}" acf rc receipt verified
  if [ -z "$appid" ] && [ -f "$game_dir/.dryport-appid" ]; then
    appid=$(tr -d '\r\n' < "$game_dir/.dryport-appid")
  fi
  if [ -z "$appid" ]; then
    for acf in "$game_dir"/steamapps/appmanifest_*.acf; do
      [ -f "$acf" ] || continue
      if [ -n "$appid" ]; then
        echo "风险检查未完成: 目录里有多个游戏标识，请用 STEAM_APPID 指定游戏。" >&2
        return 3
      fi
      appid=$(basename "$acf" .acf); appid=${appid#appmanifest_}
    done
  fi
  case "$appid" in
    ''|*[!0-9]*|0) echo "风险检查未完成: 无法确认游戏 appid。请用 dryport install <appid>，或设置 STEAM_APPID 后重试。" >&2; return 3;;
  esac
  rc=0
  receipt=$(python3 "$SN_LIB/risk.py" --gate-json "$appid") || rc=$?
  # Python 在执行 main 前就可能 import/syntax 失败并退出 1。只有完整判定回执才有覆盖资格。
  verified=$(python3 - "$appid" "$rc" "$receipt" <<'PY'
import json, sys
try:
    r = json.loads(sys.argv[3])
    expected = {"可原生改包": 0, "只能原样跑(Wine)": 1, "一律不碰": 2, "风险待核实": 3}
    if (type(r["protocol"]) is not int or r["protocol"] != 1
            or type(r["appid"]) is not int or r["appid"] != int(sys.argv[1])
            or type(r["code"]) is not int or r["code"] != int(sys.argv[2])
            or r["code"] != expected[r["verdict"]]
            or not isinstance(r["why"], list) or not r["why"]
            or not all(isinstance(w, str) and w.strip() for w in r["why"])):
        raise ValueError("风险判定回执不完整或不一致")
    print(f'{r["name"]}: {r["verdict"]}', file=sys.stderr)
    for why in r["why"]: print("  - " + why, file=sys.stderr)
    print(r["code"])
except (ValueError, KeyError, TypeError):
    print("已停止: 风险检查器没有返回完整判定，请修复上面的错误后重试。", file=sys.stderr)
    sys.exit(3)
PY
  ) || return 3
  rc="$verified"
  case "$rc" in
    0) ;;
    1)
      if [ "${SN_FORCE_REPACK:-0}" != 1 ]; then
        echo "已停止: 该游戏带联机，自动改包不放行。" >&2
        return 1
      fi
      echo "SN_FORCE_REPACK=1: 已显式选择改包带联机的游戏；仅用于你确认的单机场景。" >&2
      ;;
    2) echo "已停止: 检出反作弊，不下载或打包。" >&2; return 2;;
    *) echo "已停止: 风险检查未完成（退出码 ${rc}），补齐上面的资料或依赖后重试。" >&2; return 3;;
  esac
  STEAM_APPID="$appid"
  export STEAM_APPID
}
