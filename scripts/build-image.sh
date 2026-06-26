#!/usr/bin/env bash
# 多架构（amd64 + arm64）构建 Lumi 后端镜像并推送到镜像仓库。
#
# 用法：
#   ./scripts/build-image.sh            # 用 pyproject 版本号打 :<version> + :latest 推送
#   ./scripts/build-image.sh 0.2.6      # 覆盖版本号
#   IMAGE=… BUILDER=… ./scripts/build-image.sh   # 覆盖镜像路径 / buildx builder
#
# 前置：docker login <registry>（多架构镜像只能直接 --push，无法 --load 进本地）。
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-aidong-backend.tencentcloudcr.com/llm/lumi}"
BUILDER="${BUILDER:-lumi-builder}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
VERSION="${1:-$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)}"

echo "构建 ${IMAGE}:${VERSION} (+latest) [${PLATFORMS}] via ${BUILDER}"
docker buildx build \
  --builder "${BUILDER}" \
  --platform "${PLATFORMS}" \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push .
