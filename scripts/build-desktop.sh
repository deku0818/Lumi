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
# --managed-python：强制用 uv 下载的 python-build-standalone，禁止捡 runner/本机预装的
# Python。PyInstaller 会把所用解释器的 libpython 打进产物，捡系统 Python 就等于把宿主的
# libc 基线焊进包里 —— ubuntu-24.04 的 /usr/bin/python3.12 链 glibc 2.38，产物在 Ubuntu
# 22.04(glibc 2.35) 上直接 "GLIBC_2.38 not found" 起不来。managed 版基线是 glibc 2.17。
# 顺带让构建不再随 runner 镜像预装什么而漂移（三平台原本各捡各的：deb / python.org
# framework / hostedtoolcache）。
# 注意这只管住 libpython 那一份：依赖到的系统共享库（libssl / liblzma …）仍是从构建机复制
# 的，Linux 产物的 glibc 下限终究等于构建机的 glibc —— 故 CI 把 Linux 钉在最旧目标发行版上
# 构建（见 .github/workflows/desktop-build.yml 的矩阵注释与 Assert glibc baseline）。
# --python 3.12：钉住大版本，否则 requires-python>=3.12 会让 uv 取最新 managed 版本。
uv run --managed-python --python 3.12 --with pyinstaller pyinstaller \
  --name lumi-backend --onedir --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller --specpath build/pyinstaller \
  --collect-data lumi --copy-metadata lumi \
  scripts/pyinstaller_entry.py

echo "── 桌面安装包 (electron-builder) ──"
cd desktop
# npm ci：清光 node_modules 按 lockfile 精确重装（electron 二进制走 ~/Library/Caches/electron 缓存，不依赖网络）
npm ci
# electron@43.1.0 的 npm 包上游漏发了 postinstall（`node install.js`），npm ci 不会
# 自动下载 electron 二进制、node_modules/electron/dist 恒空，electron-builder 随即报
# "electronDist does not exist"。显式补跑 install.js 下载二进制（幂等，已缓存则秒过）。
node node_modules/electron/install.js
npm pkg set version="${VERSION}"
npm run dist

echo "完成，产物在 desktop/release/"
