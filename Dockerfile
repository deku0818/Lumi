# Lumi 后端镜像：跑 `lumi serve`，供桌面 client（本地或远程）经 WebSocket 连接。
FROM python:3.12-slim

# rg 二进制：lumi 的内容搜索 shell out 调 `rg`（无则自动降级纯 Python）。
# 用 Debian 预编译包，避免 ripgrep PyPI 包的 Rust 源码编译（已从依赖移除）；各架构自动选。
# curl：@larksuite/cli 的 postinstall 用系统 curl 下载真实二进制（无 wget/Node 兜底），
# 缺它时报出来的是包自己那段误导性的「配代理/公司镜像」文案。
# libicuXX：officecli（Office 预览转换，.NET 自包含二进制）的运行时依赖——slim 镜像
# 无 ICU 时进程启动即 Abort；office_rpc 虽有 invariant 降级兜底，装上才有完整 locale 保真。
# 包名带版本号随 Debian 版本漂移，构建期动态解析只装运行时库（-dev 元包会多拖 ~50MB
# 头文件与静态库进镜像）。
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep curl \
    "$(apt-cache search --names-only '^libicu[0-9]+$' | awk '{print $1}' | sort -V | tail -1)" \
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

# agent 的文件/bash 操作发生在工作目录；挂载你要让它操作的目录到这里
VOLUME ["/workspace"]
WORKDIR /workspace

EXPOSE 8765
# 监听 0.0.0.0 对外；token 经 LUMI_TOKEN 环境变量传入（公网部署务必设置），不进命令行。
# 例：docker run -p 8765:8765 -e LUMI_TOKEN=<你的口令> \
#       -v ~/.lumi:/root/.lumi -v $PWD:/workspace ycw0818/lumi-harness
# 公网建议前面挂 Caddy/nginx 终止 TLS（wss://），不要裸暴露明文 ws。
# 非 Docker 部署（uv tool install + systemd unit 样例）见 docs/guides/deploy.md。
ENTRYPOINT ["lumi", "serve", "--host", "0.0.0.0", "--port", "8765"]
