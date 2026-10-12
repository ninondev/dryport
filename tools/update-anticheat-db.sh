#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
. "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/paths.sh"
# 更新反作弊数据库(众包, 上游持续更新, 建议每次批量处理前跑一次)
set -euo pipefail
D="$SN_RUNTIMES"
for b in master main; do
  if curl -fsSL -o "$D/anticheat-db.json.new" \
     "https://raw.githubusercontent.com/AreWeAntiCheatYet/AreWeAntiCheatYet/$b/games.json" 2>/dev/null; then
    python3 -c "import json,sys; d=json.load(open('$D/anticheat-db.json.new')); print(f'  {len(d)} 条')" \
      && mv "$D/anticheat-db.json.new" "$D/anticheat-db.json" && exit 0
  fi
done
echo "更新失败, 保留旧库" >&2; rm -f "$D/anticheat-db.json.new"; exit 1
