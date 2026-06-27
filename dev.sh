#!/usr/bin/env bash
# Lumi 桌面开发一键启动：装好后端 / 前端依赖，再起 vite + Electron。
# Electron 主进程会自己 `uv run lumi serve` 拉起后端 sidecar，无需单独启动后端。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "✗ 缺少 $1：$2"; exit 1; }; }
need uv  "https://docs.astral.sh/uv/getting-started/installation/"
need npm  "请先安装 Node.js（含 npm）"

echo "› 同步后端依赖 (uv sync)…"
uv sync   # 飞书等可选功能需要额外 extra 时改用：uv sync --all-extras

if [ ! -d desktop/node_modules ]; then
  echo "› 安装前端依赖 (npm install)…"
  (cd desktop && npm install)
fi

echo "› 启动 desktop（vite + Electron + 后端 sidecar）…"
exec npm --prefix desktop run dev
