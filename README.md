# Lumi

基于 LangGraph 的终端 AI Agent 框架，提供丰富的 TUI 交互界面和 HTTP API，支持多模型、工具调用、定时任务、技能扩展和 MCP 协议集成。

## 特性

- **多模型支持** — 兼容 OpenAI、Anthropic（Claude）、AWS Bedrock 及所有 OpenAI 兼容 API
- **终端 TUI** — 基于 Textual 的富交互界面，支持流式输出、主题切换、斜杠命令
- **HTTP API** — FastAPI + SSE 流式接口，可集成到任意客户端
- **内置工具** — 文件读写编辑、Bash 命令执行、Glob/Grep 搜索、任务管理
- **MCP 协议** — 通过 `.lumi/mcp_server.json` 配置外部 MCP 工具服务器
- **定时任务** — 内置 cron 系统，支持相对时间、固定间隔、cron 表达式，自动重试
- **技能系统** — 通过 `.lumi/skills/` 目录扩展自定义技能，斜杠命令触发
- **子 Agent** — 通过 `.lumi/agents/` 配置子代理，委托复杂任务
- **权限控制** — 基于 allow/deny 规则的工具权限管理，工作区边界保护
- **会话持久化** — 支持 Memory / SQLite / PostgreSQL 三种检查点模式
- **对话摘要** — 自动压缩长对话历史，异步并行不阻塞主对话
- **图片识别** — 支持 model 和 tool 两种视觉模式

## 安装

### 前置要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐的包管理器）

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/pinkpills/lumi.git
cd lumi

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

### 3. 启动 HTTP API

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
| `Ctrl+C` | 退出应用 |
| `Ctrl+T` | 切换明暗主题 |

## 斜杠命令

在输入框中输入 `/` 触发命令补全：

| 命令 | 说明 |
|------|------|
| `/clear` | 清空对话，开始新会话 |
| `/resume` | 恢复历史会话（需 sqlite/postgres 持久化） |
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
  context_length: 200000        # 模型上下文窗口
  summary_threshold: 0.7          # 触发摘要的阈值比例

llm_params:                       # 按模型类型配置参数
  openai:
    temperature: 0.7
  anthropic:
    temperature: 0.7
```

完整配置说明见 [docs/config.md](docs/config.md)。

### MCP 工具服务器

在 `.lumi/mcp_server.json` 中配置外部 MCP 工具：

```json
{
  "my-server": {
    "command": "npx",
    "args": ["@my-org/mcp-server@latest"],
    "transport": "stdio"
  }
}
```

### 权限控制

通过 `permissions.json` 配置工具的 allow/deny 规则：

```json
{
  "permissions": {
    "allow": [
      "read",
      "bash(npm *)",
      "edit(src/**/*.py)"
    ],
    "deny": [
      "bash(rm -rf *)"
    ]
  }
}
```

详见 [docs/permissions.md](docs/permissions.md)。

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

## 定时任务

在对话中自然语言描述即可创建定时任务：

```
用户：每天早上 9 点帮我总结待办事项
Agent：✅ 任务已创建：每日待办总结（调度: cron 0 9 * * *）
```

支持四种调度格式：
- 相对时间：`+10m`、`+2h`
- 固定间隔：`30s`、`5m`、`2h`
- 一次性：`2025-03-10T14:00:00`
- cron 表达式：`*/5 * * * *`

详见 [docs/cron.md](docs/cron.md)。

## 技能扩展

在 `.lumi/skills/` 下创建技能目录：

```
.lumi/skills/my-skill/
├── SKILL.md          # 技能定义（name、description、prompt）
└── scripts/          # 可选的可执行脚本
```

`SKILL.md` 示例：

```markdown
---
name: my-skill
description: 我的自定义技能
---

这里是发送给 Agent 的 prompt 内容...
```

在 TUI 中通过 `/my-skill` 触发。

## 项目结构

```
lumi/
├── agents/
│   ├── base/           # 基础 Graph 和响应服务
│   ├── core/           # 核心节点、状态定义、消息处理
│   ├── cron/           # 定时任务子系统
│   └── tools/          # 工具注册表和提供者
│       ├── providers/  # 各工具实现
│       └── permissions/# 权限引擎
├── api/                # FastAPI HTTP 接口
├── tui/                # Textual TUI 界面
│   └── widgets/        # UI 组件
└── utils/              # 通用工具（模型管理、配置、日志）
```

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
