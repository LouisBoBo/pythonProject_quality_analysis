#!/usr/bin/env bash
# PCB 智能品质分析系统启动脚本 (macOS / Linux)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${QA_API_PORT:-8765}"

if lsof -ti ":$PORT" >/dev/null 2>&1; then
  echo "[警告] 端口 $PORT 已被占用，正在清理..."
  lsof -ti ":$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi

echo "========================================"
echo "  PCB品质分析系统 v2.0"
echo "========================================"
echo "启动中: http://localhost:$PORT"
exec python3 "$ROOT/run.py" api
