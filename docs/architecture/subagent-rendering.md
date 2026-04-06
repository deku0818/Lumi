# Sub-Agent 审批中断与 TUI 渲染

本文档记录 LangGraph `stream_resume` 在子代理审批场景下的行为，以及 TUI 层为保持渲染一致性所做的设计。

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
