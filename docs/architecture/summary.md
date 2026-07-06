# 对话摘要流程

## 概述

Lumi 的 Summary 机制在对话历史接近模型上下文窗口时自动压缩中间消息。流程采用**串行 + 当轮就地替换**设计：`Summarizer` 节点位于 `CallModel` 之前的关键路径上，超阈值时**当轮**生成摘要并立即用 `RemoveMessage` 删除历史 + 把摘要前置到末条 Human——即将溢出的这次调用立刻受益，而非等下一轮。

为承受「串行后 summarizer 失败会连带本轮失败」，摘要生成带 **PTL 截头重试**（自身超长时按 API round 丢头部重试）与**进程内熔断器**（同 thread 连续失败超阈值后短暂放行 CallModel）。压缩节点内部的摘要 LLM 调用由 gateway 拦截，**不外泄为助手消息**。

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `summarizer` 节点 | `lumi/agents/core/nodes.py` | 阈值判断 → strip 图像 → PTL 重试生成摘要 → 当轮就地 `RemoveMessage` + 注入摘要/技能/agent/系统信息 |
| `compact` 辅助 | `lumi/agents/core/preprocessing/compact.py` | PTL 错误识别、API round 分组与截头、图像剥离、`summarize_with_ptl_retry`、per-thread 熔断器 |
| `context_window_tokens` | `lumi/utils/sizing.py` | 上下文窗口 token 数：优先取最近一条消息的真实 `usage_metadata`，其后新增消息按字节粗估（无 tiktoken） |
| `inject_summary_into_message` | `lumi/agents/core/preprocessing/summary.py` | 将摘要包成 `<summary>` 标签块前置到用户消息 |
| gateway 拦截 | `lumi/gateway/bridge/core.py` | 把 `langgraph_node == "Summarizer"` 的 `on_chat_model_*` 转成 `compaction.status` 状态事件、丢弃其 stream，摘要不渲染成助手回答 |

## Graph 拓扑

```
START
  └─► PreprocessMessages ──► Summarizer ──► CallModel ──► (工具循环 / 结束)
```

`Summarizer` 串行夹在 `PreprocessMessages` 与 `CallModel` 之间。未超阈值 / 熔断打开 / 可压缩消息 < 2 条时它是近乎空跑的直通节点（只做一次廉价的 token 估算），原样放行到 `CallModel`。

## 触发与阈值

- 阈值：`context_length × summary_threshold`（默认 200000 × 0.7 = 140000 tokens），见 `TokenConfig`（`lumi/utils/config/models.py`）。
- 度量：`context_window_tokens` 取最近一条带 `usage_metadata` 的消息的真实整窗 token（含 system prompt + tools + 历史 + cache），加其后新增消息的字节估算；完全无 usage 时（首轮 / 子代理首调）整体退化为字节估算。

## 详细流程（当轮就地压缩）

1. **熔断检查** — 同 thread summary 连续失败超 `summary_failure_circuit_threshold` 且未到 `summary_circuit_reset_seconds`：直接返回放行 `CallModel`。
2. **阈值判断** — `context_window_tokens < 阈值` → 返回放行。
3. **消息分区** — 跳过头部 SystemMessage（不删除）；尾必须是 HumanMessage（不变量，否则报错）；中间消息为待摘要内容。可压缩消息 < 2 条时直接放行。
4. **strip 图像** — `strip_images_from_messages` 把 image/document block 换成 `[image]`/`[document]` 占位，防摘要调用自身撞 PTL。
5. **生成摘要（PTL 重试）** — `summarize_with_ptl_retry` 复用主对话 system_prompt + tools 前缀（Prompt Caching 命中）调 LLM；自身撞 prompt-too-long 时按 `summary_ptl_retry_drop_ratio` 从头部丢弃整组 round 重试，至多 `summary_ptl_retry_max` 次。失败则记录熔断计数并抛出。
6. **就地替换** — 成功后清零熔断，返回 `[RemoveMessage(历史…), RemoveMessage(末条 Human), 注入后的末条 Human]`。摘要前置到末条 Human，并补回压缩抹掉的技能/agent/系统信息提示（同 id 替换，本轮 `CallModel` 直接看到压缩后的 messages）。

## 摘要注入格式

```
<summary>
{摘要文本}
</summary>

{用户原始消息内容}
```

## 压缩状态隔离（gateway）

`astream_events` 会把节点内任何 chat model 调用逐字浮现为 `on_chat_model_stream`，与 `streaming=False` 无关。若不处理，摘要全文会被 bridge 当成 `message.delta` 流给前端、渲染成助手回答。

bridge 据 `event.metadata.langgraph_node == "Summarizer"` 拦截该节点的 `on_chat_model_*`：

- `on_chat_model_start` → `compaction.status {active: true}`
- `on_chat_model_end` / `on_chat_model_error` → `compaction.status {active: false}`
- `on_chat_model_stream` → 丢弃（摘要不外泄）

前端（`desktop/src/App.tsx`）据此把会话切到 `compacting` 状态，`StatusIndicator` 显示「正在压缩对话…」，并在 `turn.complete` / `error` 兜底清除。事件名 `compaction.status` 由 `protocol/events.json` 单一事实源定义。

## 离线强制压缩（/compact · IM 每日整理）

Summarizer 节点只在「即将溢出的当轮」工作；对**空闲会话**的主动压缩走另一条离线入口
（`compact.py` 文件末尾 + `AgentBridge.compact_thread`），供 `/compact` 命令（两端可用）与
IM 每日整理的 summary 阶段调用：

- **共用压缩核**：`run_summary`（strip 图 → 缓存安全 tool_call_chain → PTL 截头重试 → 提取
  文本）被节点与离线入口共用；离线绕开节点专属的阈值门 / 熔断器 / 「末条必须 Human」不变量。
- **判定**（`select_for_compaction`）：不设大小门，仅两条结构性前提——末条须是无 tool_calls
  的干净 AIMessage（= 已完成一轮的空闲会话），且末条之外至少有一条可删消息。
- **写回**（`build_compacted_update` → `aupdate_state(..., as_node="CallModel")`）：删除整段
  body（含末条 AI），按序追加 `[Human(<summary>), AI(末条副本)]`——`add_messages` 按序追加，
  末条恒为 AI。头部 SystemMessage 不动。`as_node` 显式指定，不依赖 LangGraph 从末次
  checkpoint 推断写入者。
- **两个刻意的"不带"**：末条 AI 副本**不带 usage_metadata**——`context_window_tokens` 无
  usage 锚点时退化为字节估算，压缩后不会因旧 usage 误判仍超阈值；摘要载体 Human **不带
  lumi ts**——IM 每日整理的判活（`latest_human_ts`）不会把"压缩过但无人说话"的会话误判为
  有新内容（连带要求 dream 失败必须挡住压缩，见 `feishu.md`《每日记忆整理》）。
- **不外泄**：全程不经 `astream_events`，不会流到渠道/前端；`/compact` 只回一条结果消息。

压缩后首条真实 human 可能已并入摘要，`session_store._summary_from_snapshot` 不再因取不到
首条 human 丢弃会话（`first_message` 留空，标题由上层 meta 兜住）。

## 设计要点

- **当轮受益**：压缩在关键路径上、即将溢出的这次调用就用上压缩结果，不滞后一轮。
- **字节度量**：阈值/trim 不再依赖本地 tokenizer，优先真实 usage、退化字节估算（见 `lumi/utils/sizing.py`）。
- **失败兜底**：PTL 截头重试 + per-thread 熔断器，避免串行 summarizer 失败反复打挂本轮。
- **缓存友好**：复用主对话 system_prompt + tools 前缀。
- **不外泄**：压缩的内部 LLM 调用不进用户流，改为 `compaction.status` 指示。
- **不可变消息**：所有操作创建新对象。
