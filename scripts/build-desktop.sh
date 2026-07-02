#!/usr/bin/env bash
# 打完整桌面安装包：PyInstaller 打后端 → 经 extraResources 塞进 Electron → electron-builder 出安装包。
# 产物含前后端，用户无需装 Python/uv。PyInstaller 不能交叉编译，在哪个平台跑就出哪个平台的包。
#
# 用法：./scripts/build-desktop.sh
# 产物：desktop/release/
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)

echo "── 后端 lumi-backend v${VERSION} (PyInstaller onedir) ──"
uv run --with pyinstaller pyinstaller \
  --name lumi-backend --onedir --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller --specpath build/pyinstaller \
  --collect-data lumi --copy-metadata lumi \
  scripts/pyinstaller_entry.py

echo "── 桌面安装包 (electron-builder) ──"
cd desktop
# npm ci：清光 node_modules 按 lockfile 精确重装（electron 二进制走 ~/Library/Caches/electron 缓存，不依赖网络）
npm ci
npm pkg set version="${VERSION}"
npm run dist

echo "完成，产物在 desktop/release/"
