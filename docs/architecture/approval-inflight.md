# 在途审批（不依赖 checkpoint 的审批机制）

把工具审批与 ask 提问从「LangGraph `interrupt()` + checkpoint 重放」改造为「`asyncio.Future` 在途请求-响应」。一条用户轮从 prompt 到 `turn.complete` 是**一条不断的事件流**，审批/提问只是流内部的一次挂起，而非把流切断后重放节点。

> 状态：**已实施（as-built）**。broker 地基 + human_approval + ask 全迁 broker + 删 interrupt 残留一并落地（原计划的 PR1/2/3 合并完成）。动机源于 [ACP client 接入](./acp-client.md)——外部 agent（如 Claude Code）干活中途经 ACP `request_permission` 回调要权限，此刻外部子进程是活的，无法用 interrupt 的「重放节点」语义，必须改为在途审批。**ACP `delegate` 复用本机制尚未落地**（见末尾「后续」）。

---

## 为什么改

`interrupt()` 的恢复语义是**重放整个节点**：中断点状态存入 checkpoint，`Command(resume=value)` 把节点从头再跑一遍、让 `interrupt()` 返回 resume 值。对 Lumi 自己的工具这没问题（工具本就还没执行）。但它和两类场景天然冲突：

- **外部有状态子进程**（ACP 委派的 Claude Code）：审批发生在 `delegate` 工具已跑起来、子进程活着的中途。重放节点 = 重新 spawn 子进程，会话丢失。
- **并发审批**：单个 `interrupt()` 只能表达一个挂起点。主 agent + 多个子/外部 agent 并发要审批时，无法区分。

此外 **子 agent 无 checkpointer**（`create_agent` 不带 checkpointer），`interrupt()` 依赖 checkpointer 落盘，故旧机制下子代理审批根本不可用。在途审批用一个按 `approval_id` 寻址的 Future 注册表替代，天然支持「节点原地挂起」「并发多审批」「子代理审批」。

## 边界：checkpoint 保留，只去掉审批耦合

`checkpointer` 在 Lumi 身上承担两件独立的事，本次只动第一件：

| 用途 | 旧 | 新 |
| --- | --- | --- |
| 审批/提问的中断-恢复 | `interrupt()` 存状态 → `Command(resume=)` 重放节点 | **移除**，改 Future（broker） |
| 会话历史持久化（`list_sessions` / `load_history` 由 checkpoint 派生） | 每轮结束写 checkpoint | **保留不动** |

graph 仍带 `checkpointer` 编译，会话列表、历史加载完全不受影响。

---

## 核心机制：ApprovalBroker + custom event

`ApprovalBroker`（`lumi/gateway/bridge/broker.py`）是一个按 `approval_id` 寻址的 Future 注册表，注入到 `LumiAgentContext`（注入路径与 `permission_engine` 一致），是节点层与会话层之间唯一的耦合点。

```python
# 节点侧（human_approval / ask 内）——不再 interrupt()
decision = await ctx.approval_broker.request(
    {"type": "tool_approval", "tool_calls": tool_calls_data},  # 或 {"type": "ask", ...}
    reject_value,   # 本请求被 stop/切会话收尾时返回的"拒绝"决策（见「锁语义」）
)

# broker.request 内部：
#   1. approval_id = uuid4().hex; fut = loop.create_future()
#      registry[approval_id] = (fut, reject_value)
#   2. await adispatch_custom_event("lumi_approval", {"approval_id": ..., **payload})
#   3. return await fut          ← 节点在此原地挂起，astream_events 随之 park
#   （finally 清理 registry）
```

会话层（`gateway/session.py`）收到应答帧时，经非流式 `resume(approval_id, value)` RPC：

```python
bridge.resolve_approval(approval_id, value)
#   → broker.resolve：registry[approval_id][0].set_result(value) → 节点 await 立刻返回
```

broker 三个方法：`request(payload, reject_value)`、`resolve(approval_id, decision)`、`reject_all() -> int`（按各请求自带的 `reject_value` 收尾全部挂起，用于 stop/切会话）。**没有 cancel 方法**——硬取消统一靠取消跑 `astream_events` 的外层 task，取消会传播到挂在 broker 上的节点、`request` 的 `finally` 自动清 registry（已实测）。

**为什么用 `adispatch_custom_event`**：它发出的事件天然走 `astream_events`，自带 `run_id` / `parent_ids`。`bridge._stream` 现成的 `_resolve_subagent_parent()` 直接给它算出 `parent_run_id`——子 Agent / ACP 外部 agent 的审批 **parent 归属白嫖现有流式归属机制**，前端渲染成子卡片下的审批无需额外工作。

> 地基**已验证**（langchain-core 1.4.3）：`adispatch_custom_event` 在 `astream_events`（显式 `version="v2"`）以 `on_custom_event` 浮现，带非空 `run_id` 与 root→parent 有序的 `parent_ids`，`data` 原样透传。`astream_events` 的 `version="v2"` 已显式锁死，防默认漂移。

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

- **审批应答从流式变非流式**。必须如此：`session.py` 的 `handle_frame` 在 `run.task` 活着时会把流式方法拒成「已有任务在执行」。原 prompt 流还活着，应答只能走轻量控制 RPC。`resume` 仍叫 `resume`（wire 方法名不变），仅由流式改非流式。
- 事件继续从**原 prompt 流**吐出，不开新流。前端事件订阅本就是连接级 `{method:"event"}` 推送，与触发的 RPC 无关 → **前端事件渲染零改动**。
- `dispatch` 在 `await fut` 之前同步完成，故 APPROVAL 卡片先到客户端，节点随后才挂起。

---

## 逐层改动清单（as-built）

### `agents/core/state.py`
- `LumiAgentContext` 新增 `approval_broker` 字段（默认 `None`，`TYPE_CHECKING` import 避免 agents→gateway 运行时依赖）。无 bridge 的 headless 调用（cron / workflow）保持 `None`。

### `agents/core/nodes.py`
- `human_approval` 改 **async**，`interrupt(...)` → `await ctx.approval_broker.request({...}, {"decision":"reject","message":...})`。DENY 快速拒绝分支、三态路由（approve→ToolExecutor / reject·cancel→END）**保留不变**，只换「怎么拿到 decision」。
- 新增 **headless 守卫**：`approval_broker is None`（cron / workflow / 后台子代理）→ fail-closed 自动拒绝并路由回 `CallModel`，让自治 agent 改用无需审批的方式，而非崩溃。

### `agents/tools/providers/ask.py`
- 删 `from __future__ import annotations`（否则 `ToolRuntime` 注解被字符串化、注入失效，registry 加载期有 fail-fast 守卫）、加显式 `args_schema=AskInput` + `runtime: ToolRuntime` 注入，改 **async**。
- `interrupt(...)` → `await ctx.approval_broker.request({"type":"ask", ...}, ASK_CANCELLED)`。
- 同样的 **headless 守卫**：`approval_broker is None` → 返回「无交互通道，自行判断后继续」让模型续跑。

### `agents/tools/providers/agent.py`
- 前台子代理 `create_agent` 后 `context.approval_broker = runtime.context.approval_broker`——子代理审批经父流 `astream_events` 浮现、白嫖 `parent_ids` 归属（旧 interrupt 无 checkpointer 不可用，broker 才解锁子代理审批）。后台子代理 detached、无活流可挂，**刻意不传播**（保持 `None`，bypass-immune 审批走 headless 守卫 fail-closed）。

### `gateway/bridge/broker.py`（新增）
- `ApprovalBroker`：`request` / `resolve` / `reject_all`。

### `gateway/bridge/core.py`
- `AgentBridge.__init__` 建 `self._broker = ApprovalBroker()`；`initialize()` 在 `create_agent` 后 `self._context.approval_broker = self._broker`（事后赋值，零改 `create_agent` 签名）。
- `_stream()` astream_events 显式 `version="v2"`；主循环新增 `kind == "on_custom_event" and name == "lumi_approval"` 分支 → 按 `data["type"]` yield `CLARIFY`（ask）/ `APPROVAL`（tool_approval，经 `_enrich_tool_approval` 富化），`parent_run_id` 复用已算的 `_resolve_subagent_parent` 结果。
- 流结束 `yield await self._turn_complete_event()`（取 state 末条 AI message 的 usage），替代旧 `_check_interrupts`。
- 新增 `resolve_approval(approval_id, value)` / `reject_pending() -> int`（委派 broker）。
- **删除**：`_check_interrupts`、`stream_resume`、`_subagent_marker`、`_INTERRUPT_TOOLS` 常量及 `_resolve_tool_call_id` 的 checkpoint_ns 特判、BYPASS「interrupt 提前 on_tool_end」补丁。
- `_active_agent_runs` 由 `dict[str, list[str]]` 改 **`set[str]`**——values（parent_ids）只被已删的 `_subagent_marker` 用，`_resolve_subagent_parent` 只用 key 成员判定，故 values 已死。审批卡片与流式事件现走**同一**归属（`_resolve_subagent_parent`），并行兄弟靠各自 `parent_ids` 能精确区分（优于旧 `_subagent_marker` 的「放弃挂主 agent」）。
- `_recover_stale_state` **保留不动**（与审批正交，处理崩溃/rewind 残留；其 `has_interrupts` 提前返回现为永假的防御性死分支，无害）。

### `gateway/session.py`
- `resume` 从 `_STREAMING_METHODS` 移到 `_RPC_HANDLERS`（新 `_resume` handler 调 `bridge.resolve_approval(approval_id, value)`）。**wire 方法名保留 `resume`**，params 改 `["approval_id", "value"]`。
- **删除**：`awaiting_resume` 旗标（在途等待期间 `run.lock` 由 `_run_stream` 持着，后台通知轮抢锁自然被挡）、`_INTERRUPT_KINDS`、`_pump` 末尾 `last_kind` 判定。
- `_finalize_active_turn(wait)`（stop 用 `wait=False`、switch_session 用 `wait=True`）：见「锁语义」。

### `gateway/protocol.py` + `protocol/events.json`
- `resume`：`streaming: true` 去掉（改非流式），params → `["approval_id", "value"]`。
- `approval.request` / `clarify.request` payload 增 `approval_id`（custom event 自带，经 `data` 透传，`protocol.py` 无需改）。
- 契约测试（`tests/gateway/test_protocol_contract.py`）只断言事件名/方法名集合——`resume` 仍在 `IMPLEMENTED_METHODS`、`APPROVAL/CLARIFY` 仍在 `EventKind`，故方法/事件名集合不变，契约测试无需改。

### `desktop/src`
- `gateway.ts` `resume(approvalId, value)` → `request('resume', {approval_id, value})`（非流式，不消费返回流）。
- `App.tsx` `resumeWith` / `decide`：回发时从存储的 approval/clarify payload 取 `approval_id` 带上。`turn.complete` / `error` 处理器**清掉 `approval`/`clarify`**——stop/切会话把挂起审批以拒绝收尾时不经 `decide`/`resume` 清理，靠 `turn.complete` 兜底关闭弹窗。`ApprovalDialog` / `ClarifyDialog` 组件不动。
- `types.ts` `WireEventPayloads`：clarify/approval payload 加 `approval_id`。

---

## 净效果：删多于加

中断+checkpoint 在审批链路上的擦屁股代码整片消失：`_check_interrupts`、`stream_resume`、`_subagent_marker`、`awaiting_resume`、`_INTERRUPT_TOOLS` / checkpoint_ns 稳定 id、BYPASS 提前 on_tool_end 补丁，`_active_agent_runs` 由 dict 瘦成 set。一条 run 一条流，心智大幅简化。

---

## 已定决策

1. **断连分两种，只救一种**（v0.2.1 更新）：
   - **Case 1 — WS 断连、sidecar 存活**（renderer 重载 / 网络抖动 / 休眠）：**已实现断连续接**——挂着审批的会话不 aclose 而是 detach 留存、同 thread 重连接回并重发审批卡，Future 一直在内存里，审批不丢（见 [desktop.md「断连续接」](./desktop.md)）。
   - **Case 2 — 后端进程重启**：挂起审批丢失、该轮作废、需重发。不做 sidecar 落盘（in-memory Future 随进程消失，需 checkpoint 重放才能续，刻意不做；本地 sidecar 作废一轮可接受）。
2. **锁语义 = stop/切会话以「拒绝」收尾挂起审批（保留历史），而非取消丢弃**。这是 as-built 相对原设计的关键调整：
   - 每个 `broker.request` 自带 `reject_value`（tool_approval 为 `{"decision":"reject",...}`，ask 为 `ASK_CANCELLED`）。
   - `_finalize_active_turn`：若有挂起审批（`reject_pending() > 0`）→ broker 以各请求的 `reject_value` 收尾 → 节点续跑到 reject→END → 本轮**干净完成**、checkpoint `next` 为空、下一轮不被 `_recover_stale_state` 回退 → **用户消息保留在历史里**（与旧 interrupt 行为一致）。若无挂起审批（轮在流生成中途）→ 硬取消 task。
   - `stop` 用 `wait=False`（快速回应）；`switch_session` 用 `wait=True`（等本轮释放 `run.lock` 再取锁切换，否则该轮挂在审批上持锁会死锁）。
   - 等待期间 `set_provider` 等配置类 RPC 等审批结束再生效，可接受。
3. **命名**：wire 方法名保留 `resume`（仅改非流式），不改名，减小前端 diff。
4. **ask 同批迁移**：ask 与 human_approval 一起改 broker——二者共用 `resume` 入口，迁 human_approval 后 tool_approval 应答必须非流式，ask 若仍 interrupt 则其恢复需流式，同名方法无法兼具两种语义，故必须同批。
5. **broker 注入** = bridge 在 `create_agent` 后事后赋值（与 `add_allow_rule`/`add_workspace` 风格一致，避免 agents→gateway 反向依赖）。
6. **权限富化留 bridge** = `ApprovalEnricher` 从旧 `_check_interrupts` 调用点平移到新 `on_custom_event` 分支，节点只发裸 `tool_calls`。
7. **headless fail-closed** = 无 broker（cron / workflow / 后台子代理）时 human_approval 自动拒绝、ask 提示继续，不崩溃。

---

## 对 ACP 的红利

原生工具审批与外部 agent 审批是**同一条机制**：

- ACP `delegate` 工具收到 Claude Code 的 `request_permission` → 同样 `await ctx.approval_broker.request(归一化后的 payload, reject_value)`。
- 它发的 APPROVAL 事件带 `parent_run_id`（custom event 自带，前提是 dispatch 发生在该 agent run 仍在 `_active_agent_runs` 活跃窗口内）→ 前端直接渲染成 Claude Code 子卡片下的审批。
- 并发挂起审批靠 `approval_id` 区分——新机制天生支持，旧的单一 interrupt 做不到。

---

## 后续

- **已落地（v0.2.1）**：WS 断连续接（Case 1）——见上「已定决策 1」与 [desktop.md「断连续接」](./desktop.md)。审批的 in-memory Future 现可跨 WS 重连存活。
- **未落地**：ACP `delegate` 复用 broker（`request_permission` → broker 的 payload 归一化）。整条 ACP 链路是纯设计稿且依赖本在途审批先落地（已落地），归一化的 payload 形状随 broker 契约定稿后再核对。两份文档的 PR 编号曾撞号——后续 ACP 工作建议用 `ACP-PR*` 编号避免与本文档混淆。
- **未落地**：「一个 thread 同时只能有一个活会话」目前无强制——多客户端 / 同会话双开 + 快重连竞态下，displaced 会话 aclose 会误杀同 thread 共享 shell、新旧会话可能短暂双绑同 thread。单 app 单用户不触发；远程多机时建议补**线程级会话注册表 + 互斥**统一解决。

---

## 测试覆盖

- **broker**（`tests/gateway/test_approval_broker.py`）：`request` 挂起 → `resolve` 唤醒返回正确 decision；并发多 `approval_id` 互不串扰；`reject_all` 按各请求 `reject_value` 收尾并返回处理数；端到端取消（外层 task cancel 传播到挂起节点、registry 不泄漏）。
- **human_approval 节点**（`tests/test_human_approval.py`）：approve/reject/cancel 三态路由 + set_tool_mode + DENY 快速拒绝 + headless 无 broker fail-closed；真实图 e2e——stop 经 `reject_all` 收尾后 checkpoint `next` 为空、用户消息保留。
- **ask 工具**（`tests/test_ask.py`）：审批 payload 构造、Command 返回、取消置 flag、headless 无 broker 续跑。
- **会话编排**（`tests/gateway/test_gateway_session.py`）：resume 非流式经 broker resolve；stop/switch_session 取消进行中的轮并补发 `turn.complete`；挂在审批上 stop → 以拒绝干净跑完（非硬取消）。
- **子代理归属**（`tests/gateway/test_subagent_attribution.py`）：`_resolve_subagent_parent` 多层委派 + 并行兄弟各按自身 parent_ids 归属。
- **协议契约**（`tests/gateway/test_protocol_contract.py`）：`IMPLEMENTED_METHODS` 与 `events.json` 事件名/方法名集合一致。
