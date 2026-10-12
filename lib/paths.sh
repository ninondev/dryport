# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# dryport 的路径层。所有 shell 脚本都 source 这一个文件，别再各自写死 $HOME/Games。
#
# 三个根，职责不重叠：
#   SN_HOME      本仓库。我们自己写的代码与文档，可以 git clone 到任何地方。
#   SN_RUNTIMES  第三方运行时与构建缓存（mkxp-z / wine-cx / Unity 播放器 / Godot / NW.js …）。
#                不进仓库；准备方法与本地构建限制见 docs/usage.md。
#   SN_GAMES     游戏本体、生成的 .app、以及从你自己的 Steam 库算出来的路线表。
#                含个人数据（你拥有哪些游戏），永远不进仓库。
#
# 三个都可以用同名环境变量覆盖。

if [ -z "${SN_HOME:-}" ]; then
  _sn_self="${BASH_SOURCE[0]:-$0}"
  while [ -L "$_sn_self" ]; do _sn_self="$(readlink "$_sn_self")"; done
  SN_HOME="$(cd "$(dirname "$_sn_self")/.." && pwd)"
  unset _sn_self
fi
SN_LIB="$SN_HOME/lib"
SN_RUNTIMES="${SN_RUNTIMES:-$HOME/Games/runtimes}"
SN_GAMES="${SN_GAMES:-$HOME/Games}"
STEAM="${STEAM:-$HOME/Library/Application Support/Steam}"

# 兼容别名 $RUNTIMES 只指第三方运行时目录。
RUNTIMES="$SN_RUNTIMES"
# 系统没有 node 时用本工具自备的那份（bin/install.sh 取到 SN_RUNTIMES/node/<版本>）；有系统 node 就尊重用户的。
if ! command -v node >/dev/null 2>&1; then
  for _sn_node_bin in "$SN_RUNTIMES"/node/*/bin; do
    if [ -x "$_sn_node_bin/node" ]; then PATH="$_sn_node_bin:$PATH"; export PATH; break; fi
  done
  unset _sn_node_bin
fi
export SN_HOME SN_LIB SN_RUNTIMES SN_GAMES

# Keep UTF-8 paths intact; system Bash's locale-dependent %q can escape only part of a character.
sn_quote_argument() {
  local value="$1"
  value=${value//\'/\'\\\'\'}
  printf "'%s'" "$value"
}
