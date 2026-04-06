# 对话摘要流程

## 概述

Lumi 的 Summary 机制用于在对话历史过长时自动压缩中间消息，避免超出模型上下文窗口限制。整个流程采用**异步并行 + 延迟替换**的两阶段设计：摘要生成与主对话 LLM 调用并行执行，生成的摘要在下一轮对话的预处理阶段才真正替换消息。

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `SummaryData` | `lumi/agents/core/scheme.py` | 摘要数据结构（`summarized_ids` + `summary_text`） |
| `summarizer` 节点 | `lumi/agents/core/node.py` | 判断是否触发摘要、调用 LLM 生成摘要 |
| `preprocess_messages` 节点 | `lumi/agents/core/node.py` | 下一轮对话时执行实际的消息替换 |
| `summary_injector` | `lumi/agents/core/summary_injector.py` | 将摘要格式化为 `<summary>` 标签并注入用户消息 |

## Graph 拓扑

```
START
  └─► PreprocessMessages
        ├─► CallModel  ──► (工具循环 / 结束)
        └─► Summarizer ──► END
```

`PreprocessMessages` 之后同时分叉到 `CallModel` 和 `Summarizer`，两者并行执行。

## 详细流程

### 第一阶段：摘要生成（当前轮）

1. **Token 计算与阈值判断** — 阈值：`context_length × summary_threshold`（默认 200000 × 0.7 = 140000 tokens）
2. **消息分区** — 跳过 SystemMessage，保留最后一条 HumanMessage，中间消息作为待摘要内容
3. **调用 LLM** — 复用主对话的 system_prompt + tools 前缀，确保 Prompt Caching 命中
4. **写入 State** — `summarized_ids` + `summary_text` 写入 `state["summary"]`，本轮不修改 messages

### 第二阶段：摘要替换（下一轮）

`preprocess_messages` 检测到 `state["summary"]` 有值：

1. 删除旧消息（`RemoveMessage`）
2. 注入摘要到用户消息（`<summary>` 标签）
3. 重新注入技能列表
4. 清空 summary 防止重复

### 防重复机制

`summarizer` 节点检查 `state["summary"]` 是否已有未消费的摘要数据，有则跳过。

## 摘要注入格式

```
<summary>
{摘要文本}
</summary>

{用户原始消息内容}
```

## 时序图

```
用户发送消息 (轮次 N)
│
├─► PreprocessMessages
│     └─ 检查 summary → 有值则执行替换
│
├─► CallModel (并行) ◄──► Summarizer
│     │                      └─ token >= 阈值 → 生成摘要 → 写入 state
│     └─► 工具调用循环 / 返回结果
│
用户发送消息 (轮次 N+1)
│
└─► PreprocessMessages
      └─ 检测到 summary → 删除旧消息 + 注入摘要
```

## 设计要点

- **并行不阻塞**：摘要生成与主对话并行
- **延迟替换**：下一轮才生效，避免当前轮消息被修改
- **缓存友好**：复用主对话的 system_prompt + tools 前缀
- **不可变消息**：所有操作创建新对象
