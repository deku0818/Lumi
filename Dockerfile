# Lumi 后端镜像：跑 `lumi serve`，供桌面 client（本地或远程）经 WebSocket 连接。
FROM python:3.12-slim

# rg 二进制：lumi 的内容搜索 shell out 调 `rg`（无则自动降级纯 Python）。
# 用 Debian 预编译包，避免 ripgrep PyPI 包的 Rust 源码编译（已从依赖移除）；各架构自动选。
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
# 装锁文件里那套版本（--frozen）：不带锁去解析会拿到本地从未跑过的组合，
# 上一次就把 mcp 解到 2.0 而 langchain-mcp-adapters 还在 import 1.x 的符号，镜像启动即崩。
# 分两段：依赖只依赖 pyproject/uv.lock，改代码不会打穿这层（近百个包、上百 MB 轮子）
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-cache --no-install-project
COPY lumi ./lumi
RUN uv sync --frozen --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH"

# 默认 config：style=code（default 风格无提示词会启动即崩）+ checkpoint=sqlite
# （默认 memory 不落盘、会话聊完即消失、list_sessions 看不到）。用户挂 .lumi 时以挂载为准。
RUN mkdir -p /root/.lumi && printf '{"style": "code", "agents": {"checkpoint": "sqlite"}}\n' > /root/.lumi/config.json

# agent 的文件/bash 操作发生在工作目录；挂载你要让它操作的目录到这里
VOLUME ["/workspace"]
WORKDIR /workspace

EXPOSE 8765
# 监听 0.0.0.0 对外；token 在 docker run 时追加（公网部署务必设置）。
# 例：docker run -p 8765:8765 -v ~/.lumi:/root/.lumi -v $PWD:/workspace lumi --token <你的口令>
# 公网建议前面挂 Caddy/nginx 终止 TLS（wss://），不要裸暴露明文 ws。
ENTRYPOINT ["lumi", "serve", "--host", "0.0.0.0", "--port", "8765"]
