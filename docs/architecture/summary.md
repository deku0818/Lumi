# 对话摘要流程

## 概述

Lumi 的 Summary 机制在对话历史接近模型上下文窗口时自动压缩中间消息。流程采用**串行 + 当轮就地替换**设计：`Summarizer` 节点位于 `CallModel` 之前的关键路径上，超阈值时**当轮**生成摘要并立即用 `RemoveMessage` 删除历史 + 把摘要前置到末条 Human——即将溢出的这次调用立刻受益，而非等下一轮。

为承受「串行后 summarizer 失败会连带本轮失败」，摘要生成带 **PTL 截头重试**（自身超长时按 API round 丢头部重试）与**进程内熔断器**（同 thread 连续失败超阈值后短暂放行 CallModel）。压缩节点内部的摘要 LLM 调用由 gateway 拦截，**不外泄为助手消息**。

除主动阈值压缩外，`CallModel` 撞 `prompt-too-long`（PTL）时还有一条**反应式兜底回路**：路由回 `Summarizer` 绕阈值强制压缩后经正常拓扑重试（详见《PTL 反应式压缩回路》一节）。两条路径共用同一个摘要核 `_summarize` 与熔断器，且都在 `Summarizer` 节点名下运行——摘要不外泄的机制对二者一致生效。

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `summarizer` 节点 | `lumi/agents/core/nodes.py` | 阈值判断 → `_summarize` 生成摘要 → 当轮就地 `RemoveMessage` + 摘要 carrier；`ptl_retry` 置位时改走 `_ptl_forced_compact` 绕阈值强制压缩 |
| `_summarize` / `_ptl_forced_compact` | `lumi/agents/core/nodes.py` | `_summarize`：剔悬空 tool_use + `run_summary`（两条压缩路径共用核）；`_ptl_forced_compact`：PTL 路径的选材（保尾）+ 熔断包裹 + carrier 组装 |
| `compact` 辅助 | `lumi/agents/core/preprocessing/compact.py` | PTL 错误识别、API round 分组与截头、图像剥离、`run_summary`/`summarize_with_ptl_retry`、`select_for_ptl_compaction`（按 round 保尾选材）、per-thread 熔断器 |
| `context_window_tokens` | `lumi/utils/sizing.py` | 上下文窗口 token 数：优先取最近一条消息的真实 `usage_metadata`，其后新增消息按字节粗估（无 tiktoken） |
| `build_summary_carrier` | `lumi/agents/core/preprocessing/summary.py` | 把摘要包成 `<summary>` 标签块，构造为独立 carrier（合成 HumanMessage，声明不可显示） |
| gateway 拦截 | `lumi/gateway/bridge/core.py` | 把 `langgraph_node == "Summarizer"` 的 `on_chat_model_*` 转成 `compaction.status` 状态事件、丢弃其 stream，摘要不渲染成助手回答 |

## Graph 拓扑

```
START
  └─► Summarizer ──► PreprocessMessages ──► CallModel ──► (工具循环 / 结束)
                                              │
                                    撞 PTL ────┘ Command(goto="Summarizer", ptl_retry=True)
```

`Summarizer` 串行夹在 `START` 与 `PreprocessMessages` 之间（压缩恒在上下文注入之前，见 `context_inject.py`）。未超阈值 / 熔断打开 / 可压缩消息 < 2 条时它是近乎空跑的直通节点（只做一次廉价的 token 估算），原样放行到 `PreprocessMessages`。`CallModel` 撞 PTL 时经 `Command(goto)` 折返 `Summarizer`（`ptl_retry` 置位），压缩后再走一遍正常拓扑。

## 触发与阈值

- 阈值：`context_length × summary_threshold`（默认 200000 × 0.7 = 140000 tokens），见 `TokenConfig`（`lumi/utils/config/models.py`）。
- 度量：`context_window_tokens` 取最近一条带 `usage_metadata` 的消息的真实整窗 token（含 system prompt + tools + 历史 + cache），加其后新增消息的字节估算；完全无 usage 时（首轮 / 子代理首调）整体退化为字节估算。

## 详细流程（当轮就地压缩）

1. **熔断检查** — 同 thread summary 连续失败超 `summary_failure_circuit_threshold` 且未到 `summary_circuit_reset_seconds`：直接返回放行 `CallModel`。
2. **阈值判断** — `context_window_tokens < 阈值` → 返回放行。
3. **消息分区** — 跳过头部 SystemMessage（不删除）；尾必须是 HumanMessage（不变量，否则报错）；中间消息为待摘要内容。可压缩消息 < 2 条时直接放行。
4. **生成摘要（`_summarize`）** — 内部 `strip_images_from_messages` 把 image/document block 换成占位（防摘要调用自身撞 PTL），剔除悬空 tool_use，复用主对话 system_prompt + tools 前缀（Prompt Caching 命中）调 `run_summary`；自身撞 prompt-too-long 时按 `summary_ptl_retry_drop_ratio` 从头部丢弃整组 round 重试，至多 `summary_ptl_retry_max` 次。失败则记录熔断计数并抛出。
5. **就地替换** — 成功后清零熔断，返回 `[RemoveMessage(历史…), RemoveMessage(末条 Human), carrier, 换新 id 的末条 Human]`。摘要作独立 carrier 插在末条 Human 之前；上下文注入块不在此重建——下游 `PreprocessMessages` 的 `context_inject` hook 在压缩后的历史上扫不到 marker，自动全量重注入（见 `context_inject.py`）。

## 摘要 carrier 格式

摘要作独立的合成 HumanMessage（carrier），插在末条真实用户消息之前：

```
Human(<summary>\n{摘要文本}\n</summary>)   ← carrier（声明不可显示）
Human({用户原始消息 + context_inject 全量注入块})
```

## PTL 反应式压缩回路

`Summarizer` 只覆盖「进入用户轮时超阈值」的场景；但工具循环内 `ToolExecutor` 直接回 `CallModel` 不经 `Summarizer`，长循环中上下文只增不减，PTL 最可能发生在循环中段（此时末条是 `ToolMessage`，`Summarizer` 的「尾必须 Human」不变量不成立）。

- **触发**：`call_model` 捕获 `is_ptl_error` 后返回 `Command(goto="Summarizer", update={"ptl_retry": True})`；`ptl_retry` 已置位（刚压缩过仍超长）或非 PTL 异常则直接抛出——**每次 PTL 只换一次压缩机会**，收敛且不死循环。
- **强制压缩**（`_ptl_forced_compact`）：绕阈值门与「尾必须 Human」不变量，`select_for_ptl_compaction` 按 API round 保留尾部 `_PTL_KEEP_TAIL_ROUNDS`（=2）组（保住进行中的工具轮），其余进摘要；共用 `_summarize` + 熔断器。任何不可压 / 摘要失败都返回 `{}` 放行，靠 `ptl_retry` 守卫在重试再撞时抛原 PTL。
- **写回**：`[RemoveMessage(头部+尾部旧 id), carrier, 尾部换新 id 副本]`——尾部 round 必须删旧 id + 换新 id 重加（`add_messages` 只能 append，同 id 是原地更新排不到 carrier 之后；`tool_call_id` 配对在 content 里，不受消息 id 更换影响）。`ptl_retry` 不在此清除，`CallModel` 成功后才清。
- **条件边守卫**：LangGraph 中节点返回 `Command(goto)` 与其条件边取并集——PTL 路由步 `is_use_tool` 会被求值，`ptl_retry` 置位时返回 `END` 空分支，避免末条 `ToolMessage` 把 `OnAgentStop` 拉进同一 superstep 分发 Stop hooks。
- **不外泄**：摘要调用在 `Summarizer` 节点名下运行，gateway 的 `compaction.status` 拦截天然生效（见下节）——这正是把反应式压缩做成路由回 `Summarizer`、而非内联在 `CallModel` 里的主因。

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
