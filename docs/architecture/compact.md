# 上下文压缩流程

## 概述

Lumi 的 compact 机制在对话历史接近模型上下文窗口时自动压缩中间消息。流程采用**串行 + 当轮就地替换**设计：`Compact` 节点位于 `CallModel` 之前的关键路径上，超阈值时**当轮**生成摘要，以 `Overwrite` 将 messages 替换为「摘要 carrier + 保留原文」——即将溢出的这次调用立刻受益，而非等下一轮。

为承受「串行后 compact 失败会连带本轮失败」，摘要生成带 **PTL 截头重试**（自身超长时按 API round 丢头部重试）与**进程内熔断器**（同 thread 连续失败超阈值后短暂放行 CallModel）。压缩节点内部的摘要 LLM 调用由 gateway 拦截，**不外泄为助手消息**。

除主动阈值压缩外，`CallModel` 撞 `prompt-too-long`（PTL）时还有一条**反应式兜底回路**：路由回 `Compact` 绕阈值强制压缩后经正常拓扑重试（详见《PTL 反应式压缩回路》一节）。两条路径共用同一个压缩核 `_compact` 与熔断器，且都在 `Compact` 节点名下运行——摘要不外泄的机制对二者一致生效。

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `compact` 节点 | `lumi/agents/core/nodes.py` | 阈值判断 → `_compact` 生成摘要 → 当轮就地 `Overwrite` 整条历史为「摘要 carrier + 保留尾部」；`ptl_retry` 置位时改走 `_ptl_forced_compact` 绕阈值强制压缩 |
| `_compact` / `_ptl_forced_compact` | `lumi/agents/core/nodes.py` | `_compact`：调用 `compact_messages` 并记录熔断；`_ptl_forced_compact`：PTL 路径的选材（保尾）并调用共用压缩核 |
| `compact` 辅助 | `lumi/agents/core/preprocessing/compact.py` | PTL 错误识别、API round 分组与截头、图像剥离、`run_compact`/`compact_with_ptl_retry`、`select_for_ptl_compaction`（按 round 保尾选材）、per-thread 熔断器 |
| `context_window_tokens` | `lumi/utils/sizing.py` | 上下文窗口 token 数：优先取最近一条消息的真实 `usage_metadata`，其后新增消息按字节粗估（无 tiktoken） |
| `build_compact_carrier` | `lumi/agents/core/preprocessing/compact.py` | 把摘要包成 `<compact>` 标签块，构造为独立 carrier（合成 HumanMessage，声明不可显示） |
| gateway 拦截 | `lumi/gateway/bridge/core.py` | 把 `langgraph_node == "Compact"` 的 `on_chat_model_*` 转成 `compaction.status` 状态事件、丢弃其 stream，摘要不渲染成助手回答 |

## Graph 拓扑

```
START
  └─► Compact ──► PreprocessMessages ──► CallModel ──► (工具循环 / 结束)
                                              │
                                    撞 PTL ────┘ error_handler → Command(goto="Compact", ptl_retry=True)
```

`Compact` 串行夹在 `START` 与 `PreprocessMessages` 之间（压缩恒在上下文注入之前，见 `context_inject.py`）。未超阈值 / 熔断打开 / 可压缩消息 < 2 条时它是近乎空跑的直通节点（只做一次廉价的 token 估算），原样放行到 `PreprocessMessages`。`CallModel` 撞 PTL 时由节点级 `error_handler` 折返 `Compact`（`ptl_retry` 置位），压缩后再走一遍正常拓扑。

## 触发与阈值

- 阈值：`模型真实窗口 × compact_threshold`（默认比例 0.7，见 `TokenConfig`，`lumi/utils/config/models.py`）。窗口取会话**实际所跑模型**的上下文长度——`provider_store.resolve(model_name).context_window`，即「用户在设置→模型里按模型配的覆盖值 > models.dev 目录探测值」，与桌面上下文环的分母同源；两者皆无时才退到 `token.context_length`（默认 200000）。用静态配置当分母会把 1M 窗口的模型按 200K 压，用量刚过 14% 就触发压缩。
- 度量：`context_window_tokens` 取最近一条带 `usage_metadata` 的消息的真实整窗 token（含 system prompt + tools + 历史 + cache），加其后新增消息的字节估算；完全无 usage 时（首轮 / 子代理首调）整体退化为字节估算。

## 详细流程（当轮就地压缩）

1. **熔断检查** — 同 thread compact 连续失败超 `compact_failure_circuit_threshold` 且未到 `compact_circuit_reset_seconds`：直接返回放行 `CallModel`。
2. **阈值判断** — `context_window_tokens < 阈值` → 返回放行。
3. **消息分区** — 跳过头部 SystemMessage（不删除）；尾必须是 HumanMessage（不变量，否则报错）；中间消息为待摘要内容。可压缩消息 < 2 条时直接放行。
4. **生成摘要（`_compact`）** — 剔除悬空 tool_use，沿用主对话 system_prompt + tools 前缀调 `run_compact`（缓存命中因 provider 而异：OpenAI 系前缀自动缓存可复用；Anthropic 下摘要链不带主链的思考参数、也不在消息上打断点，不命中主对话缓存，按全价计）；自身撞 prompt-too-long 时先由 `strip_images_from_messages` 把 image/document block 换成占位（仅一次），仍超长再按 `compact_ptl_retry_drop_ratio` 从头部丢弃整组 round 重试，至多 `compact_ptl_retry_max` 次。失败则记录熔断计数并抛出。
5. **就地替换** — 成功后清零熔断，返回 `Overwrite([carrier, 保留原文的副本…])`。摘要作独立 carrier 插在保留原文之前；副本更新运行时 id、保留稳定 `history_id`，carrier 记录压缩前 checkpoint 索引。上下文注入块不在此重建——下游 `PreprocessMessages` 的 `context_inject` hook 在压缩后的历史上扫不到 marker，自动全量重注入（见 `context_inject.py`）。

## 摘要 carrier 格式

摘要作独立的合成 HumanMessage（carrier），插在末条真实用户消息之前：

```
Human(<compact>\n{摘要文本}\n</compact>)   ← carrier（声明不可显示）
Human({用户原始消息 + context_inject 全量注入块})
```

## PTL 反应式压缩回路

`Compact` 只覆盖「进入用户轮时超阈值」的场景；但工具循环内 `ToolExecutor` 直接回 `CallModel` 不经 `Compact`，长循环中上下文只增不减，PTL 最可能发生在循环中段（此时末条是 `ToolMessage`，`Compact` 的「尾必须 Human」不变量不成立）。

- **触发**：`call_model` 不再自捕 PTL，异常交给节点级 `error_handler`（`on_call_model_error`，`add_node(..., error_handler=)`）：`is_ptl_error` 命中则返回 `Command(goto="Compact", update={"ptl_retry": True})`；`ptl_retry` 已置位（刚压缩过仍超长）或非 PTL 异常则原样抛出——**每次 PTL 只换一次压缩机会**，收敛且不死循环。
- **强制压缩**（`_ptl_forced_compact`）：绕阈值门与「尾必须 Human」不变量，`select_for_ptl_compaction` 按 API round 保留尾部 `_PTL_KEEP_TAIL_ROUNDS`（=2）组（保住进行中的工具轮），其余进摘要；共用 `_compact` + 熔断器。任何不可压 / 摘要失败都返回 `{}` 放行，靠 `ptl_retry` 守卫在重试再撞时抛原 PTL。
- **写回**：`Overwrite([carrier, 保留原文的副本…])`——整条 messages 通道一次替换（messages 是 `DeltaChannel`，逐条 `RemoveMessage` 会多留一串待回放的删除写入）。保留消息更新运行时 id，显示侧通过稳定 `history_id` 去重；工具调用与结果使用 `tool_call_id` 配对，不受消息 id 更换影响。`ptl_retry` 不在此清除，`CallModel` 成功后才清。
- **为什么是 error_handler 而非节点内 try/except**：LangGraph 中**节点自身返回**的 `Command(goto)` 与其条件边取并集——PTL 路由步 `is_use_tool` 也会被求值，末条 `ToolMessage` 会把 `OnAgentStop` 拉进同一 superstep 误分发 Stop hooks（旧实现为此在 `is_use_tool` 挂 `ptl_retry` 守卫返回 `END`）。`error_handler` 的路由不触发条件边求值，守卫已删除，不变量由 `test_full_graph_ptl_roundtrip` 的 Stop hook 计数断言把住。
- **不外泄**：摘要调用在 `Compact` 节点名下运行，gateway 的 `compaction.status` 拦截天然生效（见下节）——这正是把反应式压缩做成路由回 `Compact`、而非内联在 `CallModel` 里的主因。

## 压缩状态隔离（gateway）

`astream_events` 会把节点内任何 chat model 调用逐字浮现为 `on_chat_model_stream`，与 `streaming=False` 无关。若不处理，摘要全文会被 bridge 当成 `message.delta` 流给前端、渲染成助手回答。

bridge 据 `event.metadata.langgraph_node == "Compact"` 拦截该节点的 `on_chat_model_*`：

- `on_chat_model_start` → `compaction.status {active: true}`
- `on_chat_model_end` / `on_chat_model_error` → `compaction.status {active: false}`
- `on_chat_model_stream` → 丢弃（摘要不外泄）

前端（`desktop/src/App.tsx`）据此把会话切到 `compacting` 状态，`StatusIndicator` 显示「正在压缩对话…」，并在 `turn.complete` / `error` 兜底清除。事件名 `compaction.status` 由 `protocol/events.json` 单一事实源定义。

## 离线强制压缩（/compact · IM 每日整理）

Compact 节点只在「即将溢出的当轮」工作；对**空闲会话**的主动压缩走另一条离线入口
（`compact.py` 文件末尾 + `AgentBridge.compact_thread`），供 `/compact` 命令（两端可用）与
IM 每日整理的 compact 阶段调用：

- **共用压缩核**：`run_compact`（与主链同前缀的 tool_call_chain → PTL 时先剥图再截头重试 → 提取
  文本）被节点与离线入口共用；离线绕开节点专属的阈值门 / 熔断器 / 「末条必须 Human」不变量。
- **判定**（`select_for_compaction`）：不设大小门，仅两条结构性前提——末条须是无 tool_calls
  的干净 AIMessage（= 已完成一轮的空闲会话），且末条之外至少有一条可删消息。
- **写回**（`build_compacted_update` → `AgentBridge.flush_offline`）：整条 messages 通道
  `Overwrite` 成 `[Human(<compact>), 保留的原文]`（state 不存 SystemMessage）。`flush_offline` 是离线写回的唯一出口，
  它挂 `OfflineFlush` 锚点；消息 id 由 LangGraph 1.2.15 自动补齐，离线 `Overwrite` 同时生成完整快照，后续恢复不再回溯压缩前的增量链。
- **一个刻意的"不带"与一个"继承"**：末条 AI 副本**不带 usage_metadata**——`context_window_tokens` 无
  usage 锚点时退化为字节估算，压缩后不会因旧 usage 误判仍超阈值；摘要载体**继承**被压历史中
  最新一条真人消息的 lumi ts（`build_compact_carrier`），判活基线（`latest_human_ts`）不随压缩
  变化（dream 失败必须挡住压缩的次序不变量见 `feishu.md`《每日记忆整理》）。
- **不外泄**：全程不经 `astream_events`，不会流到渠道/前端；`/compact` 只回一条结果消息。

压缩后首条真实 human 可能已并入摘要，`session_store._summary_from_snapshot` 不再因取不到
首条 human 丢弃会话（`first_message` 留空，标题由上层 meta 兜住）。

## 压缩前记录的渐进加载

摘要载体的 `additional_kwargs.history` 保存压缩前 checkpoint ID 和重挂消息身份；
重挂消息通过 `history_id` 保持显示身份稳定，运行时消息 ID 仍按原规则更新。
在线压缩取 `Runtime.execution_info.checkpoint_id`，离线压缩取读到的快照 ID。

`sessions/history.py` 复用 checkpoint 原文：首次返回最新 50 个展示条目，前端向上滚动时
用不透明 `next_cursor` 读取更早的一页。整段恢复并完成工具调用/结果配对后才分页；
每条连接仅缓存最近读取的一段。读取只使用已保存的历史索引，不按正文全局去重。

最新历史每次仍读取快照；只有会话空闲、快照无后续节点和待提交任务，且 checkpoint 与历史
版本未变化时，才复用已经转换的展示条目。运行中的待写入结果可能在 checkpoint ID 不变时
改变消息，因此继续重新转换。读取前后核对历史版本，跨过删除操作的旧结果不会返回或写入缓存。
删除会话和飞书 `/clear` 都推进历史版本；`/clear` 保留普通运行锁及消息生命周期版本，
让期间已经排队或仍在准备中的新消息继续处理。

页的 `order` 包含已重挂消息的顺序锚点，`insert_before` 标明页尾边界；前端据此将一页内
不同区间的旧记录合并到对应位置，处理 PTL 的用户消息、插话与工具轮交错。最新页刷新也按
共享锚点合并，保留中间已经加载的旧记录。
历史加载不修改模型上下文；压缩前用户消息只读，仍在当前上下文的用户消息保留编辑/重答锚点。
滚动补入保持当前可见消息位置，过期游标的异步结果不会恢复编辑前的旧分支。
旧 checkpoint 被清理时明确报告较早记录不可用。

编辑或重新生成仍被保留的用户消息时，沿历史索引找到该消息压缩前的最早 checkpoint，
从那里建立回退分支，仅保留目标之前的上下文与历史链接，避免旧工具结果或含有后续内容的
摘要进入新分支。前端清理旧分支缓存后重新加载。必需 checkpoint 缺失时，回退失败且不修改会话。

## 设计要点

- **当轮受益**：压缩在关键路径上、即将溢出的这次调用就用上压缩结果，不滞后一轮。
- **字节度量**：阈值/trim 不再依赖本地 tokenizer，优先真实 usage、退化字节估算（见 `lumi/utils/sizing.py`）。
- **失败兜底**：PTL 截头重试 + per-thread 熔断器，避免串行 compact 失败反复打挂本轮。
- **缓存友好**：复用主对话 system_prompt + tools 前缀。
- **不外泄**：压缩的内部 LLM 调用不进用户流，改为 `compaction.status` 指示。
- **不可变消息**：所有操作创建新对象。
