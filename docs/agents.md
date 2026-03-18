# Agents 工具

Agents 工具允许你创建自定义子代理，将复杂的多步骤任务委托给专门的代理自主执行。每个 Agent 拥有独立的系统提示词、工具集和可选的模型配置。

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

启动 Lumi 后，主 Agent 会自动发现并注册该工具，在对话中可直接调用。

---

## 文件格式

Agent 配置文件采用 Markdown + YAML frontmatter 格式：

```markdown
---
name: <agent名称>          # 必填，唯一标识
description: <简短描述>     # 必填，展示给主 Agent 的工具说明
model: <模型名称>           # 可选，不填则使用默认模型
tools:                      # 可选，不填则使用所有可用工具（排除 agent 自身）
  - filesystem
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
| `tools` | 否 | 工具白名单列表，空列表表示使用所有可用工具 |

frontmatter 之后的 Markdown 内容即为该 Agent 的系统提示词。

---

## 示例

### 代码审查 Agent

```markdown
---
name: code-reviewer
description: 代码审查助手，检查代码质量和潜在问题
tools:
  - filesystem
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
  - filesystem
---

# 文档生成器

你是一个技术文档撰写专家。根据用户指定的源代码文件，生成清晰的中文技术文档。
文档应包含：功能概述、API 说明、使用示例。
```

---

## 工作原理

1. Lumi 启动时扫描 `.lumi/agents/*.md`，解析所有 Agent 配置
2. 如果存在有效配置，注册 `agent` 工具到工具注册表
3. 对话中主 Agent 根据 `description` 判断是否需要委托任务
4. 调用时创建一个独立的子 Agent（无 checkpointer），执行完毕后返回结果

子 Agent 会自动排除 `agent` 工具本身，避免递归调用。

---

## TUI 中查看

在 TUI 中输入 `/agents` 可查看当前所有已注册的 Agent 列表及其描述信息。

---

## 注意事项

- Agent 文件必须以 `---` 开头的 YAML frontmatter 格式，否则会被跳过
- `name` 字段需唯一，重复名称可能导致不可预期的行为
- `tools` 字段支持逗号分隔的字符串格式（如 `tools: filesystem, bash`）或 YAML 列表格式
- 子 Agent 不使用 checkpointer，执行完毕后状态不会持久化
- 子 Agent 的执行受主 Agent 的 `recursion_limit` 约束
