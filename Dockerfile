# Lumi 后端镜像：跑 `lumi serve`，供桌面 client（本地或远程）经 WebSocket 连接。
FROM python:3.12-slim

# rg 二进制：lumi 的内容搜索 shell out 调 `rg`（无则自动降级纯 Python）。
# 用 Debian 预编译包，避免 ripgrep PyPI 包的 Rust 源码编译（已从依赖移除）；各架构自动选。
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
# 仅复制安装所需，利用层缓存（改代码不必重装依赖）
COPY pyproject.toml README.md ./
COPY lumi ./lumi
RUN uv pip install --system --no-cache .

# 默认 config：style=code（default 风格无提示词会启动即崩）+ checkpoint=sqlite
# （默认 memory 不落盘、会话聊完即消失、list_sessions 看不到）。用户挂 .lumi 时以挂载为准。
RUN mkdir -p /root/.lumi && printf 'style: code\nagents:\n  checkpoint: sqlite\n' > /root/.lumi/config.yaml

# agent 的文件/bash 操作发生在工作目录；挂载你要让它操作的目录到这里
VOLUME ["/workspace"]
WORKDIR /workspace

EXPOSE 8765
# 监听 0.0.0.0 对外；token 在 docker run 时追加（公网部署务必设置）。
# 例：docker run -p 8765:8765 -v ~/.lumi:/root/.lumi -v $PWD:/workspace lumi --token <你的口令>
# 公网建议前面挂 Caddy/nginx 终止 TLS（wss://），不要裸暴露明文 ws。
ENTRYPOINT ["lumi", "serve", "--host", "0.0.0.0", "--port", "8765"]
