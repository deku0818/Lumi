# 在途审批（不依赖 checkpoint 的审批机制）

把工具审批与 ask 提问从「LangGraph `interrupt()` + checkpoint 重放」改造为「`asyncio.Future` 在途请求-响应」。一条用户轮从 prompt 到 `turn.complete` 是**一条不断的事件流**，审批/提问只是流内部的一次挂起，而非把流切断后重放节点。

> 现状：设计已定稿，待实施。本文是后续 PR 的实施依据。动机源于 [ACP client 接入](./acp-client.md)——外部 agent（如 Claude Code）干活中途经 ACP `request_permission` 回调要权限，此刻外部子进程是活的，无法用 interrupt 的「重放节点」语义，必须改为在途审批。

---

## 为什么改

`interrupt()` 的恢复语义是**重放整个节点**：中断点状态存入 checkpoint，`Command(resume=value)` 把节点从头再跑一遍、让 `interrupt()` 返回 resume 值。对 Lumi 自己的工具这没问题（工具本就还没执行）。但它和两类场景天然冲突：

- **外部有状态子进程**（ACP 委派的 Claude Code）：审批发生在 `delegate` 工具已跑起来、子进程活着的中途。重放节点 = 重新 spawn 子进程，会话丢失。
- **并发审批**：单个 `interrupt()` 只能表达一个挂起点。主 agent + 多个子/外部 agent 并发要审批时，无法区分。

在途审批用一个按 `approval_id` 寻址的 Future 注册表替代，天然支持「节点原地挂起」与「并发多审批」。

## 边界：checkpoint 保留，只去掉审批耦合

`checkpointer` 在 Lumi 身上承担两件独立的事，本次只动第一件：

| 用途 | 现状 | 本次 |
| --- | --- | --- |
| 审批/提问的中断-恢复 | `interrupt()` 存状态 → `Command(resume=)` 重放节点 | **移除**，改 Future |
| 会话历史持久化（`list_sessions` / `load_history` 由 checkpoint 派生） | 每轮结束写 checkpoint | **保留不动** |

graph 仍带 `checkpointer` 编译，会话列表、历史加载完全不受影响。

---

## 核心机制：ApprovalBroker + custom event

一个注入到 `LumiAgentContext` 的 `ApprovalBroker`（注入路径与 `permission_engine` 一致）是节点层与会话层之间唯一的耦合点。

```python
# 节点侧（human_approval / ask 内）——不再 interrupt()
decision = await ctx.approval_broker.request({
    "type": "tool_approval",          # 或 "ask"
    "tool_calls": tool_calls_data,
})

# broker.request 内部：
#   1. approval_id = new_id(); fut = loop.create_future(); registry[approval_id] = fut
#   2. await adispatch_custom_event("lumi_approval", {"approval_id": ..., **payload})
#   3. return await fut          ← 节点在此原地挂起，astream_events 随之 park
```

会话层收到应答帧时：

```python
# 非流式 RPC，秒回，不抢 run.lock
bridge.resolve_approval(approval_id, {"decision": "approve", ...})
#   → registry.pop(approval_id).set_result(decision)   → 节点 await 立刻返回
```

**为什么用 `adispatch_custom_event`**：它发出的事件天然走 `astream_events`，自带 `run_id` / `parent_ids`。`bridge._stream` 现成的 `_resolve_subagent_parent()` 直接给它算出 `parent_run_id`——子 Agent / ACP 外部 agent 的审批 **parent 归属白嫖现有机制**，前端渲染成子卡片下的审批无需额外工作。

> 待验证（地基）：确认当前 langchain-core 版本下 `adispatch_custom_event` 在 `astream_events` 以 `on_custom_event` 浮现且带 `parent_ids`。PR1 第一件事跑通它。

---

## 端到端时序

```
旧（interrupt + checkpoint）:
  prompt流 → 命中审批 → interrupt() → astream 提前结束 → _check_interrupts 发 APPROVAL
          → run.task 完成、run.lock 释放、awaiting_resume=True
          → 【新的流式 resume RPC】→ Command(resume) → 重放 human_approval 节点 → 继续

新（Future 在途）:
  prompt流 ────────────────────────────────────────────────────► 一条流，全程不断
     │ 命中审批 → broker.request → dispatch APPROVAL 事件（内联流出）→ 节点 await Future
     │                                            run.task 仍活 · run.lock 仍持
     │ ◄── 【非流式 resume RPC】resolve Future ──┐
     │ 节点 await 返回 → ToolExecutor → 继续 ◄───┘
     └ … → turn.complete → 流结束、锁释放
```

要点：

- **审批应答从流式变非流式**。必须如此：`session.py` 的 `handle_frame` 在 `run.task` 活着时会把流式方法拒成「已有任务在执行」（L501）。原 prompt 流还活着，应答只能走轻量控制 RPC。
- 事件继续从**原 prompt 流**吐出，不开新流。前端事件订阅本就是连接级 `{method:"event"}` 推送，与触发的 RPC 无关 → **前端事件渲染零改动**。
- `dispatch` 在 `await fut` 之前同步完成，故 APPROVAL 卡片先到客户端，节点随后才挂起。

---

## 逐层改动清单

### `agents/core/nodes.py`
- `human_approval()`（L364-446）：`interrupt(...)`（L411）→ `await ctx.approval_broker.request(...)`。DENY 快速拒绝分支（L386-409）、三态路由（approve→ToolExecutor / reject·cancel→END，L424-445）**保留不变**，只换「怎么拿到 decision」。
- `auto_classify()`、`is_use_tool()`、`permissions/routing.py`：**不动**（本就不用 interrupt）。

### `agents/tools/providers/ask.py`
- `interrupt()`（L92）→ `await ctx.approval_broker.request({"type": "ask", ...})`。**ask 同批迁移**——否则为它还得保留 interrupt+checkpoint 耦合，重构收益归零。

### `LumiAgentContext`
- 新增字段 `approval_broker`。bridge 建 context 时注入（与 `permission_engine` 同处）。

### `gateway/bridge/core.py`
- `_stream()`（L446-696）：加分支 `kind == "on_custom_event" and name == "lumi_approval"` → `yield BridgeEvent(APPROVAL/CLARIFY, data=..., parent_run_id=parent_id)`。
- **删除** `_check_interrupts()`（L677 调用 + 定义）、`stream_resume()`（L390-399）。
- 新增 `resolve_approval(approval_id, decision)` + 每连接一个 `ApprovalBroker` 的生命周期持有。
- **简化/删除**中断擦屁股代码：`_recover_stale_state()` 的审批分支、`_resolve_tool_call_id` 的 `_INTERRUPT_TOOLS` 特判（L707-708）、L600-606「BYPASS 工具 interrupt 提前 on_tool_end」补丁、`checkpoint_ns` 稳定 id。

### `gateway/session.py`
- `resume` 从 `_STREAMING_METHODS`（L51）移到 `_RPC_HANDLERS`（L423）；handler 调 `bridge.resolve_approval(...)`。**wire 方法名保留 `resume`**（仅由流式改非流式），减小前端 diff。
- `awaiting_resume` 标记（L151/338/557/612/616）→ **删**。在途等待期间 `run.lock` 由 `_run_stream` 持着（L562），后台通知轮在 L614 抢锁自然被挡，无需该旗标。
- `_INTERRUPT_KINDS`（L48）、`_pump` 末尾 `last_kind` 判定（L557）→ 删。
- **锁语义**：`_switch_session`（L317）与 `_stop`（L194）在取锁/切换前**先取消活跃 `run.task`**。语义＝「切走/停止 = 放弃当前挂起的审批」。`_stop` 已是 cancel run.task（L196-200），`switch_session` 需补一步先 cancel 再取锁，避免等锁卡死。

### `gateway/protocol.py` + `protocol/events.json`
- `resume`：`streaming: true` → 非流式；`params` 改为 `["approval_id", "decision", "message?", "set_tool_mode?"]`。
- `approval.request` / `clarify.request` payload 增 `approval_id`。
- 契约测试（`tests/server/test_protocol_contract.py`）随之更新。

### `desktop/src`
- `gateway.ts` `resume(value)`（L138）→ 非流式调用，不消费返回流；带上事件里的 `approval_id`。
- `ApprovalDialog` / `App.tsx decide()`（L1343-1354）：回发时带 `approval_id`。**UI 与渲染不变**。

---

## 净效果：删多于加

中断+checkpoint 在审批链路上的擦屁股代码整片消失：`_check_interrupts`、`stream_resume`、`_recover_stale_state` 审批分支、`awaiting_resume`、`_INTERRUPT_TOOLS`、BYPASS 提前 on_tool_end 补丁、`checkpoint_ns` 稳定 id。一条 run 一条流，心智大幅简化。

---

## 已定决策

1. **重启不幸存**：后端进程重启 → 挂起审批丢失、该轮作废、需重发。不做 sidecar 落盘（本地 sidecar 开发期重启频繁，作废一轮可接受）。
2. **锁语义**：`switch_session` / `stop` = 取消挂起审批（见上 `gateway/session.py`）。等待期间 `set_provider` 等配置类 RPC 等审批结束再生效，可接受。
3. **命名**：wire 方法名保留 `resume`（仅改非流式），不改名，减小前端 diff。
4. **ask 同批迁移**：ask 与 human_approval 一起改 broker，彻底去掉 interrupt 耦合。

---

## 对 ACP 的红利

改造后，原生工具审批与外部 agent 审批是**同一条机制**：

- ACP `delegate` 工具收到 Claude Code 的 `request_permission` → 同样 `await ctx.approval_broker.request(归一化后的 payload)`。
- 它发的 APPROVAL 事件带 `parent_run_id`（custom event 自带）→ 前端直接渲染成 Claude Code 子卡片下的审批。
- 并发挂起审批靠 `approval_id` 区分——新机制天生支持，旧的单一 interrupt 做不到。

---

## PR 切分

1. **PR1 — broker 机制地基**：`ApprovalBroker` + custom event 跑通（验证 `adispatch_custom_event` 浮现），先不接线，单测 `request` / `resolve` / `cancel`。
2. **PR2 — 迁移 human_approval**：节点改 await broker，`_stream` 加 `on_custom_event` 分支，session `resume` 改非流式，端到端跑通原生工具审批。
3. **PR3 — 迁移 ask + 删中断残留**：ask 改 broker，删 `_check_interrupts` / `stream_resume` / `awaiting_resume` / stale 审批分支，协议 + 前端调整。
4. **PR4 —（与 ACP 方案合流）** `delegate` 工具复用 broker。

每个 PR 独立可验证；PR2 末原生审批即完整工作。

---

## 测试要点

- broker：`request` 挂起 → `resolve` 唤醒返回正确 decision；`cancel`（stop）令 await 抛 `CancelledError`；并发多 `approval_id` 互不串扰。
- 节点：approve/reject/cancel 三态路由与 DENY 快速拒绝行为不变（沿用现有审批测试，仅替换底层等待机制）。
- 会话编排：审批卡片亮着时 `stop` / `switch_session` 能取消挂起轮并补发 `turn.complete`；后台通知轮在审批期间不插入。
- 协议契约：`resume` 非流式、`approval_id` 字段、`IMPLEMENTED_METHODS` 与 `events.json` 一致。
