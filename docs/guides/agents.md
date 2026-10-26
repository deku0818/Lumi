# 自定义 Agent 使用指南

Lumi 支持通过 Markdown 文件定义自定义子 Agent，将复杂任务委托给专门的代理自主执行。

使用 Codex 或 Claude Code 前，在项目主页「子 Agent」卡片点击 ＋，选择对应入口即可启用；
再次点击勾选的入口可关闭。「自定义 Agent」沿用现有的角色创建方式。
它们默认关闭，启用时检查运行 Lumi 后端的机器上是否已安装并登录；检查不会启动模型任务，
Lumi 不会自动安装或登录这些程序。开关保存在项目的 `.lumi/agents.json`，各项目独立，
远程项目也保存在对应的远程目录。启用且检查通过后，项目清单和主 Agent 的 `system-reminder`
都会显示该入口，无需角色 Markdown 文件。关闭限制新建，已有实例仍可继续对话。

主 Agent 通过三个工具管理持久 subagent：

```python
# name 必填，从可用清单中选择现有 Lumi 角色
agent_create(name="translator", title="翻译任务", prompt="翻译这段文字")

# 在当前项目的子 Agent 菜单开启后，调用原生 CLI，无需角色 Markdown 文件
agent_create(name="codex", title="测试失败调查", prompt="调查测试失败的原因")
agent_create(name="claude", title="代码审查", prompt="审查当前改动")

# 可选择其他工作目录，相对路径基于主会话的当前目录
agent_create(name="claude", title="项目调查", cwd="../another-project", prompt="调查这个项目")

agent_list()
agent_message(agent_id="创建时返回的 ID", prompt="请继续验证第二个结论")
```

同一实例保留自己的上下文，消息按顺序执行。默认后台运行并自动回传每轮结果，
主 Agent 无需轮询；`run_in_background=false` 可等待本轮并直接取得结果。
`agent_id` 是实例 ID，`turn_id` 是轮次 ID。以下文件格式仅用于自定义 Lumi 角色。
新实例和轮次使用固定 8 字符短 ID（如 `a7k3m9q2`、`t4n8p6r1`），跨重启保持稳定。
Codex / Claude Code 自己的原生会话 ID 由 Lumi 内部保存，无需模型复制。
`codex`、`claude` 是保留名称。实例归属主会话，不按目录隔离；`cwd` 默认继承创建时的
主会话目录，后续续聊始终沿用该实例保存的目录。同一主会话切换目录后，仍可查询和联系
此前创建的 subagent；不同会话在相同目录工作也不能互相访问对方的实例。
创建时 `title` 必填，由主 Agent 填写简短、具体的任务标题，通常为 6–12 个字或 2–5 个单词，用于区分同名实例；`name` 仍从可用角色或原生入口中选择。

桌面右侧的子代理列表按任务标题和角色名称展示实时状态；点击可查看每轮任务、回复、工具执行和错误，支持在
侧栏或主区域查看。详情只读，后续消息仍由主 Agent 通过 `agent_message` 派发。
本机 Codex 实例可从详情头部打开原生会话；停止执行会同时取消该实例尚未开始的排队消息。

---

## 快速开始

在 `.lumi/agents/` 目录下创建一个 `.md` 文件即可定义一个 Agent：

```markdown
---
name: translator
description: 专业翻译助手，支持中英互译
---

# 翻译助手

你是一个专业的翻译助手。用户会给你一段文本，请将其翻译为目标语言。
翻译时保持原文的语气和风格，专业术语需准确。
```

启动 Lumi 后，主 Agent 会自动发现该角色，通过 `agent_create(name="translator", title="翻译任务", prompt=...)` 使用。

---

## 文件格式

Agent 配置文件采用 Markdown + YAML frontmatter 格式：

```markdown
---
name: <agent名称>          # 必填，唯一标识
description: <简短描述>     # 必填，展示给主 Agent 的工具说明
model: <模型名称>           # 可选，不填则使用默认模型
tools:                      # 可选，填工具名（如 read、bash），不填则用全部可用工具；未知名会被静默过滤
  - read
  - bash
---

<系统提示词内容，支持完整 Markdown>
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | Agent 的唯一名称，调用时使用 |
| `description` | 是 | 简短描述，主 Agent 据此判断何时委托任务 |
| `model` | 否 | 指定模型（如 `gpt-4o`、`claude-sonnet-4-20250514`），默认使用全局配置的模型 |
| `tools` | 否 | 工具名白名单列表（如 `read`、`bash`），空列表表示使用所有可用工具 |

frontmatter 之后的 Markdown 内容即为该 Agent 的系统提示词。

---

## 示例

### 代码审查 Agent

```markdown
---
name: code-reviewer
description: 代码审查助手，检查代码质量和潜在问题
tools:
  - read
  - bash
---

# 代码审查

你是一个严格的代码审查员。请对用户提供的代码进行审查，关注：

1. 代码风格和可读性
2. 潜在的 bug 和边界情况
3. 性能问题
4. 安全隐患

给出具体的改进建议和修改示例。
```

### 文档生成 Agent

```markdown
---
name: doc-writer
description: 根据代码自动生成技术文档
model: gpt-4o
tools:
  - read
  - bash
---

# 文档生成器

你是一个技术文档撰写专家。根据用户指定的源代码文件，生成清晰的中文技术文档。
文档应包含：功能概述、API 说明、使用示例。
```

---

## 注意事项

- Agent 文件必须以 `---` 开头的 YAML frontmatter 格式，否则会被跳过
- `name` 字段需唯一，重复名称可能导致不可预期的行为
- `tools` 字段支持逗号分隔的字符串格式（如 `tools: read, bash`）或 YAML 列表格式
- subagent 使用独立持久上下文，完成后可通过 `agent_message` 继续对话
- 外部执行器使用原生 CLI 的认证与上下文，默认完全访问：Codex 关闭执行沙箱并使用 never 审批策略，Claude Code 使用 bypassPermissions 并关闭命令沙箱；新建和继续会话均生效
- 子代理的问题通过 `questions` 回传主 Agent；主 Agent 能回答就直接回答，缺少用户决策时用自己的 `ask` 提问，再通过 `agent_message` 回复原实例继续任务。子会话保持只读，Claude Code 仍遵守显式禁止规则
- 后端重启不会自动重做未完成任务；主 Agent 可查询状态后向原实例发送新消息
- 子 Agent 的执行受主 Agent 的 `recursion_limit` 约束
