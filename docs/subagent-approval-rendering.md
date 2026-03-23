# Sub-Agent 审批中断与 TUI 渲染

本文档记录 LangGraph `stream_resume` 在子代理（sub-agent）审批场景下的行为，以及 Lumi TUI 层为保持渲染一致性所做的设计。

---

## 背景

Lumi 的 agent 工具（`name="agent"`）本质上是一个普通工具调用。当子代理内部的工具触发审批中断（`interrupt()`）时，LangGraph 会暂停整个 graph 执行。用户审批后，通过 `graph.astream_events(Command(resume=value))` 恢复。

问题在于：**`stream_resume` 会重放（replay）所有 agent 级别的 `on_tool_start` 事件，且携带全新的 `run_id`**。

---

## LangGraph 的 replay 行为

### 关键观察

1. `astream_events` 在 resume 时会重新发出 `on_tool_start`（`name="agent"`），但 `run_id` 是全新生成的
2. 子代理内部事件的 `parent_ids` 也会包含新的 `run_id`
3. 每次审批恢复都会触发一轮完整的 replay，N 次审批 = N 次 replay
4. replay 的 `on_tool_end`（`name="agent"`）第一次通常 `output` 为空（占位），真正的结束事件在子代理实际完成后才到达

### 需要关注的 LangGraph 源码

- `langgraph.pregel.Pregel.astream_events` — 事件流的入口，理解 replay 机制
- `langgraph.pregel.retry` — resume 时如何重新执行节点
- `langgraph.types.Command` — `resume` 字段的语义
- `langgraph.types.interrupt` — 中断点的实现，理解哪些事件会在中断前/后发出

### 相关文档

- [LangGraph Human-in-the-loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) — 审批流程的官方概念说明
- [LangGraph Streaming](https://langchain-ai.github.io/langgraph/concepts/streaming/) — `astream_events` 的事件类型和 `run_id` 语义

---

## TUI 层的应对设计

### 核心原则

> `run_id` 是逻辑标识，随 replay 变化；`widget_id` 是 DOM 标识，创建后不变。两者解耦。

### 涉及模块

| 模块 | 职责 |
|---|---|
| `agent_bridge.py` | 事件流桥接，维护 `_active_agent_runs` 集合用于识别子代理事件 |
| `subagent_tracker.py` | 子代理状态的唯一数据源，管理 register/remap/unmapped 生命周期 |
| `event_router.py` | 事件分发，replay 检测与 remap 触发 |
| `widgets/agent_group.py` | AgentGroup 渲染组件，管理 `AgentEntry` 和 DOM widget |

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
        │
        ▼
  后续子代理事件 parent_run_id=新run_id
  → dispatch_subagent 正确路由到已有 entry
```

### 关键设计点

#### 1. `_active_agent_runs` 在 resume 时不清空

`stream_resume` 不调用 `_active_agent_runs.clear()`。原因：resume 后子代理事件可能仍携带旧的 `parent_run_id`（来自 replay 之前的 run），清空会导致这些事件无法被识别为子代理事件。

#### 2. SubagentTracker 的 unmapped 机制

- `prepare_for_resume()`：为所有现有 state 添加 `__unmapped_` 前缀的别名键，保留旧 `run_id` 键
- `find_unmapped_running(args)`：通过工具参数精确匹配已有 block，支持并发 agent 以不同顺序 replay
- `remap(new_run_id, block)`：清除所有旧键，用新 `run_id` 重新注册

#### 3. AgentEntry 的 widget_id 与 run_id 分离

```python
@dataclass
class AgentEntry:
    run_id: str        # 逻辑 id，remap 后会变
    widget_id: str     # DOM id 后缀，始终为初始值，不变
```

- `add_agent` 创建 widget 时用 `run_id`（此时 `widget_id == run_id`）
- `remap_agent` 更新 `_entries` 的 key 和 `_order`，但不动 `widget_id`
- 所有 DOM 查询（`_refresh_line`、`toggle_agent_detail`、`_mount_agent_detail`、`_remove_agent_detail`）统一用 `entry.widget_id`

#### 4. `_entries` 的别名机制

`remap_agent` 后，`_entries` 中旧 key 和新 key 指向同一个 `AgentEntry` 对象。`_order` 只保留最新的 key。`_unique_entries` 属性基于 `_order` 返回去重列表，避免统计重复。

---

## 常见陷阱

1. **不要在 resume 时清空 `_active_agent_runs`** — 会导致子代理事件丢失 `parent_run_id` 匹配
2. **不要用 `run_id` 查询 DOM widget** — replay 后 `run_id` 已变，widget 的 DOM id 还是旧值
3. **replay 的空 `output` TOOL_END 不是真正结束** — 需要计数或忽略，等待带有实际 output 的事件
4. **并发多个 agent 时 replay 顺序不保证** — `find_unmapped_running` 需要通过参数匹配而非顺序匹配
