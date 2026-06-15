# Sub-Agent 中断与渲染

本文档记录 LangGraph `stream_resume` 在子代理审批场景下的行为、TUI 层为保持渲染一致性所做的设计，以及 desktop 前端的子代理执行反馈渲染。

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

---

## TUI 层的应对设计

### 核心原则

> `run_id` 是逻辑标识，随 replay 变化；`widget_id` 是 DOM 标识，创建后不变。两者解耦。

### 涉及模块

| 模块 | 职责 |
|---|---|
| `agent_bridge.py` | 事件流桥接，维护 `_active_agent_runs` 集合 |
| `subagent_tracker.py` | 子代理状态数据源，管理 register/remap/unmapped 生命周期 |
| `event_router.py` | 事件分发，replay 检测与 remap 触发 |
| `widgets/agent_group.py` | AgentGroup 渲染组件 |

### 数据流

```
stream_resume 发出新 run_id 的 on_tool_start(name="agent")
        │
        ▼
  agent_bridge._stream()
  ├─ 新 run_id 加入 _active_agent_runs
  └─ yield BridgeEvent(TOOL_START, run_id=新值)
        │
        ▼
  event_router._handle_tool_start()
  ├─ tracker.find_unmapped_running(args) → 找到已有 block
  ├─ tracker.remap(新run_id, block) → 更新 tracker 内部映射
  ├─ agent_group.remap_agent(旧run_id, 新run_id) → 更新 entries key
  └─ 跳过创建新 AgentGroup 条目
```

### 关键设计点

1. **`_active_agent_runs` 在 resume 时不清空** — 旧 `parent_run_id` 的子代理事件仍需识别
2. **unmapped 机制** — `prepare_for_resume()` 将活跃状态移入 unmapped 列表，`find_unmapped_running()` 通过参数匹配复用
3. **widget_id 与 run_id 分离** — AgentEntry 的 `widget_id` 始终为初始值，DOM 查询统一用 `widget_id`
4. **`_entries` 别名** — remap 后旧 key 和新 key 指向同一个对象，`_unique_entries` 基于 `_order` 去重

---

## 常见陷阱

1. **不要在 resume 时清空 `_active_agent_runs`** — 会导致子代理事件丢失匹配
2. **不要用 `run_id` 查询 DOM widget** — replay 后 `run_id` 已变
3. **replay 的空 output TOOL_END 不是真正结束** — 需要等待带有实际 output 的事件
4. **并发多个 agent 时 replay 顺序不保证** — 需要通过参数匹配而非顺序匹配

### 相关 LangGraph 源码

- `langgraph.pregel.Pregel.astream_events` — 事件流入口
- `langgraph.pregel.retry` — resume 时的节点重执行
- `langgraph.types.Command` — `resume` 字段语义
- `langgraph.types.interrupt` — 中断点实现

---

## Desktop 前端的子代理渲染

TUI 把子代理事件聚合进 `AgentGroup`（见上）；desktop 前端（`desktop/src/App.tsx`）做的是同一件事的 React 版——把子代理内部活动透出来，让用户看到「正在调什么工具」，而非只有一个转圈的父工具行。

### 事件归属

协议里子代理的每条事件都带 `parent_run_id`（= 父 `agent` 工具的 `run_id`），父 `agent` 工具的 `tool.start` 自带 `run_id`（仅 `name==='agent'` 才发，见 `server/protocol.py`）。前端据此关联：

- **父卡片**：`tool.start` 创建 `ToolItem` 时，若 `payload.run_id` 非空则带上 `runId` 并初始化 `children: []`（`ToolItem` 的子代理专属字段：`runId/children/inTok/outTok`）。
- **子事件归属**（`applyChildEvent`）：带 `parent_run_id` 的 `tool.start`/`tool.complete`/`message.complete` 写进 `runId` 匹配的父卡片——子工具入 `children`，token 按 max 累计（与 TUI `agent_group.record_tokens` 同口径）。定位父卡片**从 `s.items` 尾部反向扫**（卡片几乎总在流末尾）。
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

### 与 TUI 的差异

desktop 这套是独立实现，**不含** TUI 的 replay/remap 机制（`SubagentTracker.remap`）：子代理 resume 后 `run_id` 变化时，`applyChildEvent` 按新 `parent_run_id` 找不到旧卡片即丢弃该子事件（优雅降级，不崩）。子代理目前 `checkpointer=None`、难触发 interrupt，故 replay 场景在 desktop 端基本不出现。token 格式化口径由 `lib/utils.ts::fmtTokens` 与 TUI `_format_tokens` 各持一份（注释互相标注「同口径」）。
