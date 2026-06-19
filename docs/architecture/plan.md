# Plan Mode 架构

Plan Mode 的内部实现。用户使用指南见 [`docs/guides/plan.md`](../guides/plan.md)。

---

## 核心机制

Plan Mode 通过执行模式策略（`lumi/agents/permissions/mode_policy.py`）实现工具限制，而非替换工具集。这保证了工具定义不变，最大化 Prompt Caching 命中率。

### 状态流转

执行模式记录在 `LumiAgentState` 的 `execution_mode` 字段（`lumi/agents/core/state.py`），取值 `"normal"`（默认）/ `"plan"` / `"readonly"` / 自定义。该字段不由 graph 内部翻转，而是由前端在每轮发消息时随 `send_message` 的 `execution_mode` 参数传入（WS → `AgentBridge.stream_response(..., execution_mode=...)` → 写入初始 state）。

```
前端切到 plan 模式 → send_message(execution_mode="plan")
  Agent 在 plan 期间探索代码、写计划文件到 .lumi/plans/*.md
  ExitPlanMode 工具调用 → interrupt 等待用户审批
    批准 → 返回 approved 响应，前端切回 normal 继续执行
    拒绝 → 返回 rejected 响应 + tool_cancelled=True，留在 plan 继续修改
```

### 策略守卫

`PLAN_POLICY`（`lumi/agents/permissions/mode_policy.py`）：

- `allow_write = False`
- `path_filter = _is_under_lumi_plans`：仅允许写入 `.lumi/plans/` 下的 `.md` 文件

`is_use_tool()`（`lumi/agents/core/nodes.py`）路由函数中，`execution_mode != "normal"` 时调用 `check_policy()`，被拦截的写入操作路由到 `PolicyReject` 节点。

`EnterPlanMode` / `ExitPlanMode` 工具在 `lumi/agents/tools/providers/plan.py`，且都登记在 `capability.py` 的 `_ALWAYS_READONLY` 中（只读，跳过权限审批）。`ExitPlanMode` 通过 `interrupt({"type": "ExitPlanMode", ...})` 暂停等待审批，拒绝时返回 `PLAN_REJECTED`（`"__plan_rejected__"`）哨兵并置 `tool_cancelled`。

---

## 工具提示词格式

### EnterPlanMode.md

```markdown
---
description: |
  工具描述（LLM 看到的 tool description）...
---

进入计划模式后的响应内容（plan mode reminder）...
```

- `description`：YAML front-matter 中的工具描述
- body：进入 plan mode 后返回给 LLM 的响应内容

### ExitPlanMode.md

```markdown
---
description: |
  工具描述...
approved: |
  计划被批准后的响应内容...
rejected: |
  计划被拒绝后的响应内容...
---
```

如果配置文件缺失或关键字段为空，启动时抛出 `RuntimeError`。

---

## Desktop / WS 集成

- **模式传递**：desktop 前端切到 plan 模式后，`send_message` 带上 `execution_mode: "plan"`（WS JSON-RPC params），经 `lumi/gateway/channels/ws.py` 透传给 `AgentBridge.stream_response(execution_mode=...)`。
- **计划审批事件**：`ExitPlanMode` 触发 `interrupt` 后，`AgentBridge` 发出 `plan.request` 事件（`EventKind.PLAN`）。因前端无文件系统访问，服务端在 `_enrich_plan()` 中读出 `plan_file_path` 对应文件正文，附加为 `plan_content` 一并下发（事件 payload 见 `protocol/events.json` 的 `plan.request`）。
- **审批组件**：`desktop/src/components/PlanDialog.tsx` 展示计划内容，用户批准 → `resume("approved")`，拒绝 → `resume(PLAN_REJECTED)`（前后端共用同一 `"__plan_rejected__"` 哨兵）。`App.tsx` 在收到 `plan.request` 时挂起会话状态，等待用户响应后 resume。
