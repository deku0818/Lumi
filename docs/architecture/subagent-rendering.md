# Sub-Agent 中断与渲染

本文档记录 LangGraph `stream_resume` 在子代理审批场景下的 replay 行为，以及 desktop 前端如何渲染子代理执行反馈。

---

## 背景

Lumi 的 agent 工具（`name="agent"`）本质上是一个普通工具调用。当子代理内部的工具触发审批中断（`interrupt()`）时，LangGraph 会暂停整个 graph 执行。用户审批后，通过 `graph.astream_events(Command(resume=value))` 恢复。

问题在于：**`stream_resume` 会重放（replay）所有 agent 级别的 `on_tool_start` 事件，且携带全新的 `run_id`**。

---

## LangGraph 的 replay 行为

1. `astream_events` 在 resume 时重新发出 `on_tool_start`（`name="agent"`），`run_id` 全新生成
2. 子代理内部事件的 `parent_ids` 包含新的 `run_id`
3. 每次审批恢复触发一轮完整 replay，N 次审批 = N 次 replay
4. replay 的 `on_tool_end` 第一次通常 `output` 为空，真正的结束事件在子代理实际完成后才到达

> 因此前端不能用 `run_id` 作为子代理卡片的稳定标识，也不能把 replay 的空 output 当作真正结束。

### 相关 LangGraph 源码

- `langgraph.pregel.Pregel.astream_events` — 事件流入口
- `langgraph.pregel.retry` — resume 时的节点重执行
- `langgraph.types.Command` — `resume` 字段语义
- `langgraph.types.interrupt` — 中断点实现

---

## Desktop 前端的子代理渲染

desktop 前端（`desktop/src/App.tsx`）把子代理内部活动透出来，让用户看到「正在调什么工具」，而非只有一个转圈的父工具行。

### 事件归属

协议里子代理的每条事件都带 `parent_run_id`（= 父 `agent` 工具的 `run_id`），父 `agent` 工具的 `tool.start` 自带 `run_id`（仅 `name==='agent'` 才发，见 `gateway/protocol.py`）。前端据此关联：

- **父卡片**：`tool.start` 创建 `ToolItem` 时，若 `payload.run_id` 非空则带上 `runId` 并初始化 `children: []`（`ToolItem` 的子代理专属字段：`runId/children/inTok/outTok`）。
- **子事件归属**（`applyChildEvent`）：带 `parent_run_id` 的 `tool.start`/`tool.complete`/`message.complete` 写进 `runId` 匹配的父卡片——子工具入 `children`，token 按 max 累计。定位父卡片**从 `s.items` 尾部反向扫**（卡片几乎总在流末尾）。
- **不进主流的**：子代理的逐字流（`message.delta`/`thinking.delta`/`message.start`）直接丢弃，不逐字渲染。
- **仍需用户处理的**：带 `parent_run_id` 的中断类事件（`approval`/`clarify`/`plan`/`error`）**不**走归属，照常 fall-through 到主 switch 弹对话框——否则子代理审批会卡死（这是一个易踩的回归点）。

### 渲染（`groupItems` 把连续 agent 工具合并成段）

| 组件 | 形态 |
|---|---|
| `AgentGroup` | 段级分发：单个 → `SingleAgent`，并发多个 → `AgentFleet`（memo 比较器 `sameItems` 与 `ToolGroup` 共用） |
| `SingleAgent` | 运行中：头部统计 + `RunningWindow`（最近 `SUBAGENT_WINDOW=3` 个子工具的有限滚动窗口，新行 `subtool-enter` 推入、挤出的旧行 `subtool-leave` 淡出收起）；完成 → `DoneCard` 单行 |
| `AgentFleet` | 运行中：「运行 N 个子 Agent」面板，每行一个 agent（`FleetRow`：光点 + 名称 + 当前动作 + 工具数）；全部完成 → `DoneCard` 单行 |
| `DoneCard` | 完成态纯单行（不可展开），单个与并发共用 |

并发判定按 `groupItems` 的「相邻性」（同段连续的 `agent` 工具即合并面板）——是可接受的取舍，不精确区分真并发 vs 顺序调用。

### resume 后的降级

子代理 resume 后 `run_id` 变化时，`applyChildEvent` 按新 `parent_run_id` 找不到旧卡片即丢弃该子事件（优雅降级，不崩）。子代理目前 `checkpointer=None`、难触发 interrupt，故 replay 场景在 desktop 端基本不出现。
