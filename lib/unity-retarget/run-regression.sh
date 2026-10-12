#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# Rosetta-stone regression across all cached Unity versions: strip Unity's own Metal/GLCore from each official
# Windows-player 'unity default resources', regenerate from D3D11, compare. Prints one line per version.
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="${SN_VENV:-${SN_RUNTIMES:-$HOME/Games/runtimes}/unity-retarget/venv}/bin/python"; [ -x "$PY" ] || PY=python3
for d in "$HOME"/Games/runtimes/unity-players/*/; do
  f=$(find "$d/expanded" -path "*win64*nondevelopment*" -name "unity default resources" 2>/dev/null | head -1)
  [ -n "$f" ] || continue
  printf '%s: ' "$(basename "$d")"; "$PY" "$HERE/retarget_verify.py" "$f" 2>&1 | grep -E "^shaders|Traceback|^ERR" | head -2
done
f="$HOME/Games/a_space_unbound/A Space for the Unbound_Data/Resources/unity default resources"
[ -f "$f" ] && { printf '2020.3.31f1 (game): '; "$PY" "$HERE/retarget_verify.py" "$f" 2>&1 | grep -E "^shaders|Traceback" | head -1; }
