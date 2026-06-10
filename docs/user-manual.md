# Lumi 用户手册

欢迎使用 Lumi！这是一个基于 LangGraph 的 AI Agent 框架，提供终端 TUI 界面和 HTTP API 两种交互方式。本手册将帮助您快速上手并充分利用 Lumi 的功能。

## 目录

1. [快速开始](#快速开始)
2. [安装与配置](#安装与配置)
3. [基本使用](#基本使用)
4. [TUI 界面详解](#tui-界面详解)
5. [斜杠命令](#斜杠命令)
6. [内置工具](#内置工具)
7. [配置管理](#配置管理)
8. [权限系统](#权限系统)
9. [自定义扩展](#自定义扩展)
10. [高级功能](#高级功能)
11. [故障排除](#故障排除)
12. [最佳实践](#最佳实践)

---

## 快速开始

### 1. 安装 Lumi

```bash
# 克隆仓库
git clone https://github.com/deku0818/Lumi.git
cd Lumi

# 安装依赖
uv sync
uv pip install -e .
```

**前置要求：**
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 2. 配置环境

创建 `.lumi/config.yaml` 文件：

```yaml
style: default  # 可选：default, code
env:
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1
```

### 3. 启动 Lumi

```bash
# TUI 交互模式（默认）
lumi

# 指定风格启动
lumi -s code

# Headless 模式（输出到 stdout）
lumi -p "你的问题"

# 特权模式（跳过所有工具审批）
lumi --privileged-danger

# 浏览器模式
lumi web-server --port 8000

# HTTP API 模式
uvicorn lumi.api.app:app --port 8090
```

---

## 安装与配置

### 系统要求

- **操作系统**：Windows、macOS、Linux
- **Python**：3.12 或更高版本
- **内存**：建议 4GB 以上
- **磁盘空间**：至少 1GB 可用空间

### 详细安装步骤

#### 1. 安装 Python

确保已安装 Python 3.12+：

```bash
python --version
# 应该显示 Python 3.12.x 或更高版本
```

#### 2. 安装 uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

#### 3. 克隆并安装 Lumi

```bash
git clone https://github.com/deku0818/Lumi.git
cd Lumi
uv sync
uv pip install -e .
```

#### 4. 验证安装

```bash
lumi --version
```

### 配置文件详解

#### 主配置文件：`.lumi/config.yaml`

```yaml
# 风格配置
style: code  # 可选：default, code

# 环境变量
env:
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1
  ANTHROPIC_API_KEY: sk-xxx
  ANTHROPIC_API_URL: https://api.example.com

# Agent 配置
agents:
  tools: []  # 工具白名单，空列表 = 全部启用
  disabled_tools: []  # 工具黑名单
  max_tokens: 8192
  recursion_limit: 5000
  vision_mode: model  # 图片识别模式：model | tool
  checkpoint: memory  # 检查点存储：memory | sqlite | postgres
  postgres_uri: ""  # PostgreSQL 连接 URI

# Token 配置
token:
  once_tool_ratio: 0.1
  trim_messages_ratio: 0.96
  context_length: 200000
  summary_threshold: 0.7

# LLM 参数
llm_params:
  openai:
    temperature: 0.7
  anthropic:
    temperature: 0.7

# 技能执行配置
skill_execution:
  enabled: true
  command_timeout: 10.0
  max_output_bytes: 10000

# MCP 工具配置
ptc:
  enabled: true
  tools: []
  disabled_tools: []

# 文件系统配置
filesystem:
  grep_max_file_size_mb: 10
```

#### 权限配置文件：`.lumi/permissions.json`

```jsonc
{
  // 额外授权的工作区目录
  "workspaces": [],
  
  // 权限规则
  "permissions": {
    "allow": [
      "read",
      "bash(npm *)",
      "edit(src/**/*.py)"
    ],
    "deny": [
      "bash(rm -rf *)"
    ],
    "ask": [
      "bash(git push *)",
      "bash(npm publish *)"
    ]
  }
}
```

---

## 基本使用

### TUI 交互模式

启动 TUI 界面：

```bash
lumi
```

**基本操作：**
1. 在输入框中输入问题或指令
2. 按 `Enter` 发送
3. 查看 AI 的响应和工具执行结果
4. 使用快捷键控制界面

### Headless 模式

用于脚本集成或批量处理：

```bash
# 单次查询
lumi -p "解释 Python 的装饰器"

# 从文件读取问题
lumi -f questions.txt

# 输出到文件
lumi -p "生成代码" > output.txt
```

### HTTP API 模式

启动 HTTP API 服务器：

```bash
uvicorn lumi.api.app:app --host 0.0.0.0 --port 8090
```

**API 端点：**
- `POST /api/chat` - 发送聊天消息
- `GET /api/sessions` - 获取会话列表
- `DELETE /api/sessions/{id}` - 删除会话

### 桌面应用模式

Lumi 提供 Electron 桌面应用（开发中），经 WebSocket 复用与 TUI 相同的后端运行时。开发模式下进入 `desktop/` 启动：

```bash
cd desktop
npm install
npm run dev
```

桌面端自动拉起 `lumi serve` sidecar，主要能力：

- **多会话并发** — 侧栏管理多个会话，每会话一条独立连接互不阻塞；`⋮` 菜单支持置顶 / 重命名 / 删除。
- **模型供应商管理** — 设置页配置连接与模型（见下节），顶栏快速切换当前模型。
- **桌面通知** — 回复完成或需要审批 / 提问 / 计划确认时，若窗口未聚焦或非当前会话则弹系统通知，点击带回前台。
- **中英双语** — 设置中切换界面语言（中文 / English）。

---

## 模型供应商管理

Lumi 支持自定义模型供应商（OpenAI / Anthropic 兼容连接），配置持久化在 `~/.lumi/providers.json`（明文，权限 `600`，含 API Key），**TUI 与桌面端共享同一份配置**。

**概念：**
- 一个 **供应商 profile** = 一套连接（名称 / `base_url` / `api_key`）+ 该连接下的一组模型。
- 协议（OpenAI / Anthropic 客户端）由模型名自动判定，无需手动指定。
- 当前选中项指向「某 profile 下的某个模型」，切换后**下一轮对话**生效。

**配置方式：**
- **桌面端**：设置 → 模型供应商，可新增 / 编辑 / 删除 profile，并对连接做可达性测试（发一个最小请求，15 秒短超时快速失败）。
- **TUI**：输入 `/model` 打开切换弹窗——把「供应商 × 模型」拍平成列表，搜索 + ↑↓ 导航 + Enter 切换（**仅切换**；新增 / 编辑 / 删除在桌面端完成）。

> `base_url` / `api_key` 为空时沿用环境变量 / SDK 默认连接，因此仅用环境变量配置单一模型时无需创建 profile。

---

## TUI 界面详解

### 界面布局

```
┌─────────────────────────────────────────┐
│ 标题栏                                  │
├─────────────────────────────────────────┤
│                                         │
│           对话内容区域                  │
│                                         │
├─────────────────────────────────────────┤
│ 工具执行状态                            │
├─────────────────────────────────────────┤
│ 输入框                                  │
└─────────────────────────────────────────┘
```

### 快捷键

| 按键 | 功能 |
|------|------|
| `Shift+Tab` | 切换 Plan Mode（只读规划模式） |
| `Escape` | 取消当前生成 |
| `Escape` x2 | 打开 Rewind 界面（回退到历史 checkpoint） |
| `Ctrl+C` | 退出应用 |
| `Ctrl+T` | 切换明暗主题 |
| `Ctrl+L` | 清空对话 |
| `↑` / `↓` | 浏览输入历史 |

### 模式指示器

输入框左侧显示当前模式：
- **auto** - 自动模式（默认）
- **plan** - 计划模式（只读规划）
- **privileged** - 特权模式（跳过审批）

### 工具执行状态

工具执行时，状态栏显示：
- 工具名称
- 执行进度
- 完成状态
- 错误信息（如有）

---

## 斜杠命令

在输入框中输入 `/` 触发命令补全：

### 基础命令

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

### 技能命令

技能命令从 `.lumi/skills/` 自动加载，使用 `/skill-name` 触发。

**示例：**
```bash
/translate "Hello World"
/code-review src/main.py
```

---

## 内置工具

### 文件操作工具

| 工具 | 功能 | 示例 |
|------|------|------|
| `read` | 读取文件内容 | 读取 `src/main.py` 的前 100 行 |
| `write` | 写入文件 | 创建新文件 `config.yaml` |
| `edit` | 基于字符串替换的精确编辑 | 替换文件中的特定代码段 |
| `glob` | 文件模式匹配搜索 | 查找所有 `.py` 文件 |
| `grep` | 文本内容搜索 | 搜索包含 "error" 的代码行 |

### 系统工具

| 工具 | 功能 | 示例 |
|------|------|------|
| `bash` | 执行 Shell 命令 | 运行 `ls -la` |
| `ask` | 向用户提问 | 确认是否继续操作 |
| `todos` | 任务列表管理 | 创建待办事项 |
| `cron` | 定时任务管理 | 创建每日备份任务 |

### 高级工具

| 工具 | 功能 | 示例 |
|------|------|------|
| `skill` | 调用自定义技能 | 执行翻译技能 |
| `agent` | 委托任务给子 Agent | 让代码审查 Agent 检查代码 |
| `background_task` | 管理后台任务 | 在后台运行长时间任务 |

### 工具使用示例

**文件操作：**
```
读取 src/main.py 文件的前 50 行
```

**代码编辑：**
```
将 main.py 中的 `print("hello")` 替换为 `logger.info("hello")`
```

**系统命令：**
```
运行 pytest 测试并查看结果
```

---

## 配置管理

### 环境变量配置

优先级：CLI 参数 > 环境变量 > 配置文件

**常用环境变量：**
```bash
export LLM_MODEL_NAME=gpt-4o
export OPENAI_API_KEY=sk-xxx
export OPENAI_API_BASE=https://api.openai.com/v1
export ANTHROPIC_API_KEY=sk-xxx
```

### 模型配置

支持多种模型提供商：

```yaml
env:
  # OpenAI
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1
  
  # Anthropic
  LLM_MODEL_NAME: claude-sonnet-4-20250514
  ANTHROPIC_API_KEY: sk-xxx
  
  # OpenAI 兼容 API
  LLM_MODEL_NAME: qwen3-max
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.example.com/v1
```

### 检查点配置

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `memory` | 内存存储（默认） | 开发调试、临时使用 |
| `sqlite` | SQLite 文件持久化 | 单机部署、需要会话恢复 |
| `postgres` | PostgreSQL 持久化 | 多实例部署、生产环境 |

**SQLite 配置示例：**
```yaml
agents:
  checkpoint: sqlite
```

**PostgreSQL 配置示例：**
```yaml
agents:
  checkpoint: postgres
  postgres_uri: postgresql://user:password@localhost:5432/lumi
```

---

## 权限系统

### 权限规则

权限规则按优先级评估：Deny → Allow → Unmatched

**规则格式：**
```
tool_name(pattern)
```

**示例：**
```jsonc
{
  "permissions": {
    "allow": [
      "read",
      "bash(npm *)",
      "edit(src/**/*.py)"
    ],
    "deny": [
      "bash(rm -rf *)"
    ],
    "ask": [
      "bash(git push *)"
    ]
  }
}
```

### 工作区边界

限制工具只能访问授权目录：

```jsonc
{
  "workspaces": [
    "/home/user/projects",
    "./src",
    "./tests"
  ]
}
```

### 审批对话框

当工具需要审批时，会显示：
- 工具名称和参数
- 风险提示
- 选项：允许、拒绝、始终允许

### 动态规则积累

在审批对话框中选择「始终允许」会自动添加到权限配置。

---

## 自定义扩展

### 技能系统

技能是可复用的命令序列，位于 `.lumi/skills/` 目录。

**创建技能：**
```markdown
---
name: translate
description: 翻译文本
---

# 翻译技能

将用户提供的文本翻译为目标语言。
保持原文的语气和风格。
```

**使用技能：**
```bash
/translate "Hello World" to Chinese
```

### 自定义 Agent

Agent 是专门的子代理，位于 `.lumi/agents/` 目录。

**创建 Agent：**
```markdown
---
name: code-reviewer
description: 代码审查助手
tools:
  - filesystem
---

# 代码审查

你是一个严格的代码审查员。请对代码进行审查，关注：
1. 代码风格和可读性
2. 潜在的 bug
3. 性能问题
4. 安全隐患
```

**使用 Agent：**
```
请让代码审查 Agent 检查 src/main.py
```

### MCP 集成

Model Context Protocol 支持外部工具集成。

**配置 MCP 服务器：**
```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "mcp-server-filesystem",
      "args": ["--root", "/path/to/dir"]
    }
  ]
}
```

---

## 高级功能

### 计划模式

计划模式允许 AI 进行只读规划，不执行实际操作。

**切换方式：**
- 按 `Shift+Tab` 切换
- 输入框显示 "plan" 指示器

**使用场景：**
- 复杂任务分解
- 代码重构规划
- 架构设计讨论

### 定时任务

使用自然语言创建定时任务。

**创建任务：**
```
每天早上 9 点运行数据库备份
```

**管理任务：**
```bash
/cron          # 查看所有任务
/cron pause    # 暂停任务
/cron resume   # 恢复任务
/cron delete   # 删除任务
```

### 对话摘要

当对话过长时，自动触发摘要压缩。

**配置阈值：**
```yaml
token:
  summary_threshold: 0.7  # 70% 上下文时触发
```

### 子 Agent 委托

将复杂任务委托给专门的子 Agent。

**示例：**
```
请让翻译 Agent 将这段代码注释翻译成英文
```

### 后台任务

长时间任务可在后台运行。

**示例：**
```
在后台运行完整的测试套件
```

---

## 故障排除

### 常见问题

#### 1. 安装失败

**问题：** `uv sync` 报错
**解决：**
```bash
# 清理缓存
uv cache clean

# 重新安装
uv sync --all
```

#### 2. 模型连接失败

**问题：** API 连接超时
**解决：**
1. 检查 API 密钥是否正确
2. 检查网络连接
3. 验证 API 端点 URL

#### 3. 权限被拒绝

**问题：** 工具执行被拒绝
**解决：**
1. 检查 `.lumi/permissions.json` 配置
2. 添加相应的 allow 规则
3. 使用 `--privileged-danger` 模式（不推荐生产环境）

#### 4. 内存不足

**问题：** 程序崩溃或响应缓慢
**解决：**
1. 减少 `max_tokens` 配置
2. 使用检查点持久化
3. 定期清理对话历史

#### 5. 文件权限问题

**问题：** 无法读取或写入文件
**解决：**
1. 检查文件权限
2. 配置工作区边界
3. 使用绝对路径

### 日志调试

启用详细日志：

```bash
# 设置日志级别
export LUMI_LOG_LEVEL=DEBUG

# 启动 Lumi
lumi
```

### 性能优化

1. **减少上下文长度：**
   ```yaml
   token:
     context_length: 100000
   ```

2. **禁用不必要的工具：**
   ```yaml
   agents:
     disabled_tools:
       - bash
       - write
   ```

3. **使用检查点持久化：**
   ```yaml
   agents:
     checkpoint: sqlite
   ```

---

## 最佳实践

### 1. 项目结构

```
your-project/
├── .lumi/
│   ├── config.yaml          # 主配置
│   ├── permissions.json     # 权限配置
│   ├── skills/              # 自定义技能
│   └── agents/              # 自定义 Agent
├── src/
└── tests/
```

### 2. 安全实践

- **最小权限原则：** 只授予必要的权限
- **工作区隔离：** 限制工具访问范围
- **敏感信息：** 使用环境变量而非硬编码
- **定期审计：** 检查权限配置

### 3. 开发工作流

1. **规划阶段：** 使用计划模式讨论架构
2. **实现阶段：** 让 AI 生成代码
3. **审查阶段：** 使用代码审查 Agent
4. **测试阶段：** 运行测试套件
5. **部署阶段：** 使用定时任务自动化

### 4. 团队协作

- **共享配置：** 将 `.lumi/config.yaml` 提交到 Git
- **本地配置：** 使用 `.lumi/permissions.local.json`
- **文档同步：** 更新技能和 Agent 文档

### 5. 性能监控

- **监控 Token 使用：** 避免超出上下文限制
- **检查点管理：** 定期清理旧会话
- **资源限制：** 设置合理的超时时间

---

## 附录

### A. 完整配置参考

```yaml
# 风格配置
style: code

# 环境变量
env:
  LLM_MODEL_NAME: gpt-4o
  OPENAI_API_KEY: sk-xxx
  OPENAI_API_BASE: https://api.openai.com/v1

# Agent 配置
agents:
  tools: []
  disabled_tools: []
  max_tokens: 8192
  recursion_limit: 5000
  vision_mode: model
  checkpoint: sqlite
  postgres_uri: ""

# Token 配置
token:
  once_tool_ratio: 0.1
  trim_messages_ratio: 0.96
  context_length: 200000
  summary_threshold: 0.7

# LLM 参数
llm_params:
  openai:
    temperature: 0.7
  anthropic:
    temperature: 0.7

# 技能执行
skill_execution:
  enabled: true
  command_timeout: 10.0
  max_output_bytes: 10000

# MCP 工具
ptc:
  enabled: true
  tools: []
  disabled_tools: []

# 文件系统
filesystem:
  grep_max_file_size_mb: 10
```

### B. 快捷键速查表

| 按键 | 功能 |
|------|------|
| `Shift+Tab` | 切换 Plan Mode |
| `Escape` | 取消当前生成 |
| `Escape` x2 | 打开 Rewind 界面 |
| `Ctrl+C` | 退出应用 |
| `Ctrl+T` | 切换主题 |
| `Ctrl+L` | 清空对话 |
| `↑` / `↓` | 浏览输入历史 |

### C. 斜杠命令速查表

| 命令 | 说明 |
|------|------|
| `/clear` | 清空对话 |
| `/resume` | 恢复历史会话 |
| `/rewind` | 回退到历史 checkpoint |
| `/model` | 切换模型供应商（共享 `~/.lumi/providers.json`） |
| `/skills` | 查看可用技能 |
| `/agents` | 查看可用 Agent |
| `/bg` | 查看后台任务 |
| `/mcp` | 查看 MCP 状态 |
| `/cron` | 管理定时任务 |
| `/cron-notify` | 查看任务通知 |

### D. 内置工具速查表

| 工具 | 功能 |
|------|------|
| `read` | 读取文件 |
| `write` | 写入文件 |
| `edit` | 编辑文件 |
| `glob` | 文件搜索 |
| `grep` | 内容搜索 |
| `bash` | 执行命令 |
| `ask` | 用户提问 |
| `todos` | 任务管理 |
| `cron` | 定时任务 |
| `skill` | 技能调用 |
| `agent` | 子 Agent |

---

## 获取帮助

- **文档：** [docs/](docs/)
- **问题反馈：** GitHub Issues
- **社区讨论：** GitHub Discussions

---

*最后更新：2026年6月*