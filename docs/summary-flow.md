# Lumi Summary（对话摘要）流程说明

## 概述

Lumi 的 Summary 机制用于在对话历史过长时自动压缩中间消息，避免超出模型上下文窗口限制。整个流程采用**异步并行 + 延迟替换**的两阶段设计：摘要生成与主对话 LLM 调用并行执行，生成的摘要在下一轮对话的预处理阶段才真正替换消息。

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `SummaryData` | `lumi/agents/core/scheme.py` | 摘要数据结构（`summarized_ids` + `summary_text`） |
| `summarizer` 节点 | `lumi/agents/core/node.py` | 判断是否触发摘要、调用 LLM 生成摘要 |
| `preprocess_messages` 节点 | `lumi/agents/core/node.py` | 下一轮对话时执行实际的消息替换 |
| `summary_injector` | `lumi/agents/core/summary_injector.py` | 将摘要格式化为 `<summary>` 标签并注入用户消息 |
| `TokenConfig` | `lumi/utils/config/models.py` | 配置阈值参数 |

## Graph 拓扑

```
START
  └─► PreprocessMessages
        ├─► CallModel  ──► (工具循环 / 结束)
        └─► Summarizer ──► END
```

`PreprocessMessages` 之后同时分叉到 `CallModel` 和 `Summarizer`，两者并行执行。`Summarizer` 完成后直接到 `END`，不阻塞主对话流程。

## 详细流程

### 第一阶段：摘要生成（当前轮）

1. **Token 计算与阈值判断**
   - 计算当前 `messages` 的总 token 数（使用 `tiktoken_counter`）
   - 阈值公式：`context_length × summary_threshold`
   - 默认值：`200000 × 0.7 = 140000` tokens
   - 若 token 数 < 阈值，直接返回空 `summary`，不触发摘要

2. **消息分区**
   - 头部：跳过 `SystemMessage`（不参与摘要）
   - 尾部：保留最后一条 `HumanMessage`（当前用户输入，不参与摘要）
   - 中间：所有其他消息作为待摘要内容

3. **调用 LLM 生成摘要**
   - 加载 `.lumi/prompts/SUMMARY.md` 作为摘要指令
   - 构建 chain 时复用主对话的 `system_prompt` + `tools`，确保 Prompt Caching 命中
   - 将待摘要消息 + 摘要指令一起发送给 LLM
   - `temperature=1`，`streaming=False`

4. **写入 State**
   - 将 `summarized_ids`（被摘要消息的 ID 列表）和 `summary_text`（摘要文本）写入 `state["summary"]`
   - 本轮不修改 `messages`，仅记录元数据

### 第二阶段：摘要替换（下一轮）

下一轮用户发送消息时，`preprocess_messages` 节点检测到 `state["summary"]` 有值：

1. **删除旧消息** — 根据 `summarized_ids` 生成 `RemoveMessage` 指令
2. **注入摘要** — 调用 `inject_summary_into_message` 将摘要文本以 `<summary>` 标签格式插入到当前用户消息的 content 最前面
3. **重新注入技能** — 摘要替换后重新检测并注入技能列表，避免之前的 system-reminder 被摘要吞掉
4. **清空 summary** — 将 `state["summary"]` 置空，防止重复替换

### 防重复机制

`summarizer` 节点开头会检查 `state["summary"]` 是否已有未消费的摘要数据，如果有则直接返回空，避免重复生成。

## 摘要注入格式

摘要以如下格式注入到用户消息中：

```
<summary>
{摘要文本}
</summary>

{用户原始消息内容}
```

注入实现遵循不可变原则，不修改原消息，而是创建新的 `HumanMessage`。

## 配置项

在 `.lumi/config.yaml` 的 `token` 段配置：

```yaml
token:
  context_length: 200000       # 模型上下文窗口大小
  summary_threshold: 0.7         # 触发摘要的阈值比例
```

## 时序图

```
用户发送消息 (轮次 N)
│
├─► PreprocessMessages
│     ├─ 检查 state["summary"] → 有值？执行替换 → 删除旧消息 + 注入摘要
│     └─ 无值？执行常规预处理（清理工具调用、卸载大结果、技能注入）
│
├─► CallModel (主对话)          ◄── 并行 ──►  Summarizer
│     │                                         │
│     │                                         ├─ token < 阈值？→ 返回空
│     │                                         └─ token >= 阈值？→ 生成摘要 → 写入 state
│     │
│     └─► 工具调用循环 / 返回结果
│
用户发送消息 (轮次 N+1)
│
└─► PreprocessMessages
      └─ 检测到 summary → 执行替换（删除旧消息 + 注入摘要到用户消息）
```

## 设计要点

- **并行不阻塞**：摘要生成与主对话并行，不增加用户等待时间
- **延迟替换**：摘要在下一轮才生效，避免当前轮消息被意外修改
- **缓存友好**：摘要 chain 复用主对话的 system_prompt + tools 前缀，最大化 Prompt Caching 命中率
- **不可变消息**：所有消息操作都创建新对象，不修改原始消息
