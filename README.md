# Lumi

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.0.6-orange.svg)](CHANGELOG.md)

基于 LangGraph 的终端 AI Agent 框架，提供丰富的 TUI 交互界面和 HTTP API，支持多模型、工具调用、定时任务、技能扩展和 MCP 协议集成。

## 特性

- **多模型支持** — 兼容 OpenAI、Anthropic（Claude）、AWS Bedrock 及所有 OpenAI 兼容 API
- **终端 TUI** — 基于 Textual 的富交互界面，支持流式输出、主题切换、斜杠命令
- **HTTP API** — FastAPI + SSE 流式接口，可集成到任意客户端
- **内置工具** — 文件读写编辑、Bash 命令执行、Glob/Grep 搜索、任务管理
- **MCP 协议** — 通过 `.lumi/mcp_server.json` 配置外部 MCP 工具服务器
- **定时任务** — 内置 cron 系统，支持自然语言创建（[详情](docs/cron.md)）
- **技能系统** — 通过 `.lumi/skills/` 扩展自定义技能，斜杠命令触发（[详情](docs/slash_commands.md)）
- **子 Agent** — 通过 `.lumi/agents/` 配置子代理，委托复杂任务（[详情](docs/agents.md)）
- **权限控制** — 基于 allow/deny 规则的工具权限管理，工作区边界保护（[详情](docs/permissions.md)）
- **会话持久化** — 支持 Memory / SQLite / PostgreSQL 三种检查点模式（[详情](docs/checkpoint.md)）
- **对话摘要** — 自动压缩长对话历史，异步并行不阻塞主对话（[详情](docs/summary-flow.md)）
- **图片识别** — 支持 model 和 tool 两种视觉模式

## 安装

### 前置要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐的包管理器）

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/deku0818/Lumi.git
cd Lumi

# 安装依赖
uv sync

# 安装为可执行命令
uv pip install -e .
```

## 快速开始

### 1. 配置模型

创建 `.lumi/config.yaml`：

```yaml
env:
  LLM_MODEL_NAME: gpt-4o          # 或 claude-sonnet-4-20250514、qwen3-max 等
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1  # 可选，兼容 API 地址
```

也可以直接设置环境变量，环境变量优先级高于配置文件。

### 2. 启动 TUI

```bash
lumi
```

### 3. Headless 模式

```bash
lumi -p "你的问题"
```

直接输出到 stdout，适合脚本集成和管道操作。

### 4. 浏览器模式

```bash
lumi web-server --host 0.0.0.0 --port 8000
```

通过 textual-serve 在浏览器中运行 TUI，支持 `--host`、`--port`、`--title`、`--debug` 参数。

### 5. HTTP API

```bash
uvicorn lumi.api.app:app --host 0.0.0.0 --port 8090
```

API 端点：
- `POST /api/agent/langgraph` — Agent 对话（SSE 流式响应）
- `GET /api/cron/events` — 定时任务结果订阅（SSE）

## TUI 快捷键

| 按键 | 功能 |
|------|------|
| `Escape` | 取消当前生成 |
| `Escape` x2 | 打开 Rewind 界面（回退到历史 checkpoint） |
| `Ctrl+C` | 退出应用 |
| `Ctrl+T` | 切换明暗主题 |

## 斜杠命令

在输入框中输入 `/` 触发命令补全：

| 命令 | 说明 |
|------|------|
| `/clear` | 清空对话，开始新会话 |
| `/resume` | 恢复历史会话（需 sqlite/postgres 持久化） |
| `/rewind` | 回退到历史 checkpoint（恢复文件和会话状态） |
| `/skills` | 查看可用技能列表 |
| `/agents` | 查看可用 Agent 列表 |
| `/mcp` | 查看 MCP 服务器状态和工具 |
| `/cron` | 管理定时任务 |
| `/cron-notify` | 查看定时任务通知记录 |

技能命令从 `.lumi/skills/` 自动加载，使用 `/skill-name` 触发。

## 配置

所有配置位于项目根目录的 `.lumi/` 下：

```
.lumi/
├── config.yaml              # 主配置文件
├── mcp_server.json          # MCP 工具服务器配置
├── permissions.json         # 工具权限规则（可提交 Git）
├── permissions.local.json   # 本地权限规则（建议 .gitignore）
├── prompts/                 # Agent 系统提示词
│   ├── SOUL.md              # 核心人格
│   ├── AGENTS.md            # Agent 行为指令
│   └── GUARDRAILS.md        # 安全护栏
├── skills/                  # 自定义技能
│   └── my-skill/
│       └── SKILL.md
└── agents/                  # 子 Agent 配置
    └── my-agent.md
```

### config.yaml 主要配置项

```yaml
env:                              # 环境变量注入
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx

agents:
  checkpoint: memory              # memory | sqlite | postgres
  max_tokens: 8192                # 模型输出最大 token
  recursion_limit: 100            # Agent 最大执行轮次
  vision_mode: model              # 图片识别：model | tool

token:
  context_length: 200000          # 模型上下文窗口
  summary_threshold: 0.7          # 触发摘要的阈值比例

llm_params:                       # 按模型类型配置参数
  openai:
    temperature: 0.7
  anthropic:
    temperature: 0.7
```

- MCP 工具服务器配置见 [docs/config.md](docs/config.md)
- 权限控制详见 [docs/permissions.md](docs/permissions.md)
- 完整配置说明见 [docs/config.md](docs/config.md)

## 内置工具

| 工具 | 功能 |
|------|------|
| `read` | 读取文件内容（支持行号范围） |
| `write` | 写入文件 |
| `edit` | 基于字符串替换的精确编辑 |
| `glob` | 文件模式匹配搜索 |
| `grep` | 文本内容搜索（基于 ripgrep） |
| `bash` | 执行 Shell 命令（持久化会话） |
| `ask` | 向用户提问并等待回答 |
| `todos` | 任务列表管理 |
| `cron` | 定时任务管理（创建/删除/暂停/执行） |
| `skill` | 调用自定义技能 |
| `agent` | 委托任务给子 Agent |

## 文档

| 主题 | 链接 |
|------|------|
| 完整配置说明 | [docs/config.md](docs/config.md) |
| 权限控制 | [docs/permissions.md](docs/permissions.md) |
| 定时任务 | [docs/cron.md](docs/cron.md) |
| Checkpoint 回退 | [docs/checkpoint.md](docs/checkpoint.md) |
| 子 Agent | [docs/agents.md](docs/agents.md) |
| 斜杠命令 | [docs/slash_commands.md](docs/slash_commands.md) |
| 对话摘要 | [docs/summary-flow.md](docs/summary-flow.md) |
| Grep/Glob 工具 | [docs/grep_glob.md](docs/grep_glob.md) |
| 计划模式 | [docs/plan.md](docs/plan.md) |
| 缓存机制 | [docs/cache.md](docs/cache.md) |

## 开发

```bash
# 安装开发依赖
uv sync --all

# 运行测试
uv run pytest

# 代码格式化
uv run ruff format .

# Lint 检查
uv run ruff check --fix .
```

## 技术栈

- [LangGraph](https://langchain-ai.github.io/langgraph/) + [LangChain](https://langchain.com/) — Agent 编排
- [Textual](https://textual.textualize.io/) — TUI 框架
- [FastAPI](https://fastapi.tiangolo.com/) — HTTP API
- [APScheduler](https://apscheduler.readthedocs.io/) — 定时任务调度
- [MCP Adapters](https://github.com/modelcontextprotocol/) — Model Context Protocol 集成

## License

MIT
