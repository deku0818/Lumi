# Sub-Agent 中断与渲染

本文档记录 desktop 前端如何渲染子代理执行反馈。

---

## Desktop 前端的子代理渲染

desktop 前端（`desktop/src/App.tsx`）把子代理内部活动透出来，让用户看到「正在调什么工具」，而非只有一个转圈的父工具行。

### 事件归属

协议里子代理的每条事件都带 `parent_run_id`（= 父 `agent` 工具的 `run_id`），父 `agent` 工具的 `tool.start` 自带 `run_id`（仅 `agent` 工具才带，见 `gateway/bridge/core.py`）。前端据此关联：

- **父卡片**：`tool.start` 创建 `ToolItem` 时，若 `payload.run_id` 非空则带上 `runId` 并初始化 `children: []`（`ToolItem` 的子代理专属字段：`runId/children/inTok/outTok`）。
- **子事件归属**（`applyChildEvent`）：带 `parent_run_id` 的 `tool.start`/`tool.complete`/`message.complete` 写进 `runId` 匹配的父卡片——子工具入 `children`，token 按 max 累计。定位父卡片**从 `s.items` 尾部反向扫**（卡片几乎总在流末尾）。
- **不进主流的**：子代理的逐字流（`message.delta`/`thinking.delta`/`message.start`）直接丢弃，不逐字渲染。
- **仍需用户处理的**：带 `parent_run_id` 的中断类事件（`approval`/`clarify`/`error`）**不**走归属，照常 fall-through 到主 switch 弹对话框——否则子代理审批会卡死（这是一个易踩的回归点）。

### 渲染（`groupItems` 把连续 agent 工具合并成段）

| 组件 | 形态 |
|---|---|
| `AgentGroup` | 段级分发：单个 → `SingleAgent`，并发多个 → `AgentFleet`（memo 比较器 `sameItems` 与 `ToolGroup` 共用） |
| `SingleAgent` | 运行中：头部统计 + `RunningWindow`（最近 `SUBAGENT_WINDOW=3` 个子工具的有限滚动窗口，新行 `subtool-enter` 推入、挤出的旧行 `subtool-leave` 淡出收起）；完成 → `DoneCard` 单行 |
| `AgentFleet` | 运行中：「运行 N 个子 Agent」面板，每行一个 agent（`FleetRow`：光点 + 名称 + 当前动作 + 工具数）；全部完成 → `DoneCard` 单行 |
| `DoneCard` | 完成态纯单行（不可展开），单个与并发共用 |

并发判定按 `groupItems` 的「相邻性」（同段连续的 `agent` 工具即合并面板）——是可接受的取舍，不精确区分真并发 vs 顺序调用。
