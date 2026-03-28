# Lumi

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.0.6-orange.svg)](CHANGELOG.md)
[![LangGraph](https://img.shields.io/badge/LangGraph-powered-purple.svg)](https://langchain-ai.github.io/langgraph/)

在终端里与 AI 结对编程 — 基于 LangGraph 的 Agent 框架，支持多模型、工具调用、MCP 协议和丰富的 TUI 交互。

<p align="center">
  <img src="assets/demo.svg" width="800" alt="Lumi TUI Demo">
</p>

## 为什么选择 Lumi

- **LangGraph 驱动** — 基于状态图的 Agent 编排，支持条件路由、并行分支、检查点回退，不是简单的 prompt-response 循环
- **真正的 TUI** — 基于 Textual 的富交互界面，流式渲染、主题切换、斜杠命令补全、Rewind 一键回退
- **可扩展架构** — 技能系统、子 Agent、MCP 协议、定时任务，通过配置文件即可扩展，无需改代码
- **多种接入方式** — TUI / Headless / 浏览器 / HTTP API，同一套 Agent 逻辑，四种使用场景

## 特性

**模型与协议** — 多模型支持（OpenAI / Anthropic / Bedrock / OpenAI 兼容 API） · MCP 协议集成 · 图片识别（model / tool 两种模式）

**Agent 能力** — [子 Agent 委托](docs/agents.md) · [计划模式](docs/plan.md)（只读规划 + 审批，Shift+Tab 切换） · [对话摘要](docs/summary-flow.md)（异步压缩，不阻塞主对话） · [会话持久化](docs/checkpoint.md)（Memory / SQLite / PostgreSQL）

**工具与扩展** — 11 个内置工具（文件读写、Bash、Glob/Grep、任务管理等） · [技能系统](docs/slash_commands.md)（`.lumi/skills/`） · [定时任务](docs/cron.md)（cron，自然语言创建）

**交互与安全** — 终端 TUI（Textual） · [风格系统](docs/styles.md)（可切换的提示词预设） · Checkpoint 回退 · [权限控制](docs/permissions.md)（allow/deny + 工作区边界） · HTTP API（FastAPI + SSE）

## 快速开始

### 安装

```bash
git clone https://github.com/deku0818/Lumi.git
cd Lumi
uv sync
uv pip install -e .
```

前置要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)

### 配置

创建 `.lumi/config.yaml`：

```yaml
style: code                                    # 提示词风格（可选，默认 default）
env:
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1   # 可选
```

也可直接设置环境变量（优先级高于配置文件）。完整配置说明见 [docs/config.md](docs/config.md)。

### 启动

```bash
lumi                                    # TUI 交互模式
lumi -s code                            # 指定提示词风格启动
lumi -p "你的问题"                       # Headless 模式，输出到 stdout
lumi --privileged-danger                # 特权模式，跳过所有工具审批
lumi web-server --port 8000             # 浏览器模式（textual-serve）
uvicorn lumi.api.app:app --port 8090    # HTTP API（FastAPI + SSE）
```

## TUI 快捷键

| 按键 | 功能 |
|------|------|
| `Shift+Tab` | 切换 Plan Mode（只读规划模式） |
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

## 配置

所有配置位于项目根目录的 `.lumi/` 下：

- `config.yaml` — 模型、Agent、Token、风格等主配置（[完整说明](docs/config.md)）
- `mcp_server.json` — MCP 工具服务器
- `permissions.json` — 工具权限规则（[详情](docs/permissions.md)）
- `prompts/` — Agent 系统提示词，可覆盖 style 默认值（[风格系统](docs/styles.md)）
- `skills/` / `agents/` — 自定义技能和子 Agent

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
| 风格系统 | [docs/styles.md](docs/styles.md) |
| 缓存机制 | [docs/cache.md](docs/cache.md) |

## 开发

```bash
uv sync --all              # 安装开发依赖
uv run pytest              # 运行测试
uv run ruff format .       # 代码格式化
uv run ruff check --fix .  # Lint 检查
```

## 技术栈

- [LangGraph](https://langchain-ai.github.io/langgraph/) + [LangChain](https://langchain.com/) — Agent 编排
- [Textual](https://textual.textualize.io/) — TUI 框架
- [FastAPI](https://fastapi.tiangolo.com/) — HTTP API
- [APScheduler](https://apscheduler.readthedocs.io/) — 定时任务调度
- [MCP Adapters](https://github.com/modelcontextprotocol/) — Model Context Protocol 集成

## License

MIT
