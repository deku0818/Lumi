# ACP Client（编排外部 Agent）

让 `LumiAgent` 作为 [Agent Client Protocol](https://agentclientprotocol.com)（ACP）的 **client**，把外部编程 agent（Claude Code、Codex、Gemini CLI…）当「工人」拉起来派活——Lumi 扮演 ACP 里编辑器/IDE 的角色，外部 agent 作为 ACP server 子进程。

> 📍 **要继续干活 / 快速捡回？看 [acp-progress.md](./acp-progress.md)**（操作性交接：代码地图、PR3/PR4 验证过的实施步骤、风险清单、测试方法）。本文是设计与 rationale。

> 现状：[在途审批改造](./approval-inflight.md) **已落地**；本文 **ACP-PR1（传输层）+ ACP-PR2（委派工具 + 事件回流）已落地**。PR1：`lumi/acp/` + 脱离 Lumi 的子进程握手单测；PR2：`delegate_to_claude` 工具 + bridge custom-event 分支 + 子卡片归属，desktop 可见 Claude Code 流式输出。权限（PR3）/ fs（PR4）待实施。
>
> 已定方向：走 **ACP 标准协议**（非裸 CLI/PTY 包装）；首发 worker = **Claude Code**；先做 **单点委派**（打通管道），Council / fan-out 留后。
>
> 已定决策：① `lumi/acp/` 放**运行时根下**（不进 `gateway/`——它是主动外联能力，与 MCP client 同侧；放 gateway 会造出刚拆掉的 `agents→gateway` 反向依赖）。② 委派 `cwd` **每次由 `LumiAgent` 显式指定**（不隐式跟随会话目录）。③ 传输层**直接用官方 `acp` Python SDK**（`agent-client-protocol`，见下），不自写 JSON-RPC。

---

## 心智模型：委派 = 一个工具

Lumi 已有「子 Agent 工具」（`agents/tools/providers/agent.py`）——进程内拉起子 `LumiAgent`，事件靠 `parent_run_id` 在前端缩进展示。**ACP 委派是同一形状，只是 backend 从「进程内 LumiAgent」换成「进程外 ACP agent」：**

| | 现有 sub-agent 工具 | 新 ACP 委派工具 |
| --- | --- | --- |
| backend | 进程内 `LumiAgent` | 子进程 `claude-code-acp`（stdio） |
| 派活 | `graph.ainvoke()` | ACP `session/prompt` |
| 事件回流 | LangGraph `astream_events` | ACP `session/update` → custom event |
| 权限 | 复用 `PermissionEngine` | ACP `session/request_permission` → 同一 `PermissionEngine` + `ApprovalBroker` |
| 展示 | `parent_run_id` 子 Agent 卡片 | **完全复用** `parent_run_id` |

**对 graph 零改动**：委派工具跑在现有 `ToolExecutor` 里，与普通工具无异。复杂度全部收敛在工具内部。

---

## 架构

```
LumiAgent（指挥官）
  └─ tool: delegate_to_claude(task, cwd?)         ← 新增 provider
        └─ AcpClient（lumi/acp/）
              ├─ 启动子进程: npx -y @zed-industries/claude-code-acp   (stdio)
              ├─ initialize → 协商能力（Lumi 声明可提供 fs + permission）
              ├─ session/new(cwd)
              ├─ session/prompt(task)
              │     ↓ session/update（流）         → dispatch_custom_event → BridgeEvent(parent_run_id)
              │     ↓ session/request_permission   → PermissionEngine.evaluate → ApprovalBroker
              │     ↓ fs/read_text_file·write      → LocalFilesystemBackend（边界检查+变更追踪）
              └─ 收到 stop_reason → 返回汇总文本给 LumiAgent
```

## 协议要点

ACP = JSON-RPC 2.0 over stdio，**双向**：外部 agent 干活时回调 client。

- **Client → Agent**：`initialize` / `authenticate` / `session/new` / `session/load` / `session/prompt` / `session/cancel`
- **Agent → Client**：`session/update`（流式通知：agent/thought 文本块、tool_call 状态、plan）/ `session/request_permission` / `fs/read_text_file` / `fs/write_text_file` / `terminal/*`

一个 prompt turn：client `session/prompt` → agent 边干边发 `session/update`，中途按需 `request_permission` / `fs/*` 回调 → agent 以 `stop_reason` 应答收尾。

---

## 模块划分

```
lumi/acp/                        # 纯 ACP client，不依赖 LangGraph（已落地 PR1）
  client.py     AcpClient        # 薄封装官方 acp SDK：spawn → initialize → session/new → prompt
                                 # _BridgeClient(Client) 路由回调；AcpResult(stop_reason, text)

lumi/agents/tools/providers/
  external_agent.py              # 新 provider：delegate 工具，把 AcpClient 接进运行时（PR2）
```

- 官方 `acp` SDK 已提供 **传输（stdio JSON-RPC）+ 类型（`acp.schema` Pydantic）+ 连接**，故无需自写 `transport.py` / `types.py`——`lumi/acp/` 收敛成一个薄 `client.py`。
- `lumi/acp/` 与 MCP client 对称：**MCP 让 Lumi 用外部工具，ACP 让 Lumi 用外部 agent**。纯传输层，可独立单测（`tests/acp/` 起 echo agent 子进程跑通握手）。
- provider 是「桥」，把 ACP 的三类回调接到 Lumi 三大原语（事件 / 权限 / fs）。`AcpClient.run(task, cwd, on_update)` 暴露 `on_update` 流式旁路给 provider 接事件回流。

---

## 三个回调的接法（方案核心价值）

### 事件回流 → 复用 `parent_run_id`
外部进程事件不在 LangGraph 流里。在委派工具内，每收到一条 ACP `session/update`，用 `adispatch_custom_event` 派发；因发生在工具 callback 上下文，LangChain 自动带 `parent_ids`。`bridge/core.py` 加分支把这类 custom event 映射成 `BridgeEvent`（`MESSAGE_DELTA` / `TOOL_START` / `TOOL_COMPLETE`），`parent_run_id` 自动填好 → **前端无需改动，直接渲染成子卡片**。

> 这与 [在途审批](./approval-inflight.md) 用的是同一条 `adispatch_custom_event` → `on_custom_event` 通道，bridge 的分支可统一设计。

### 权限回调 → 统一审批（依赖在途审批）
ACP `session/request_permission(tool_call)` → `PermissionEngine.evaluate()`：

- `DENY` → 直接回 reject，外部 agent 收到 no，用户无感
- `ALLOW` → 直接回 approve，用户无感
- `ASK` / 未匹配 → `await ctx.approval_broker.request(归一化 payload)`，**外部 agent 的工具调用复用 Lumi 同一张审批卡片、受 `permissions.json` 管辖**

这是把外部 agent 收编进 Lumi 安全模型的核心，也是先做在途审批的根因。

**工具名归一化**：Claude Code 报上来的是它自己的工具名/参数形状（它的 bash/edit），`PermissionEngine` 规则按 Lumi 工具名写。中间需一层映射（ACP tool kind + raw input → `evaluate()` 能认的形状）。MVP 先简单映射（bash→bash、文件写→write），后续细化。

### 文件回调 → 边界检查白嫖
ACP `fs/read_text_file` / `fs/write_text_file` → `LocalFilesystemBackend.read/write`，自动获得工作区边界检查 + 文件变更追踪。

> 是否真走 client fs 取决于 `initialize` 能力协商——Claude Code adapter 自带 runtime/tools，**可能用它自己的 fs**。MVP：Lumi 声明 fs 能力，用不用随它，不强依赖。

---

## 配置（与 MCP 对称）

新增 `.lumi/acp_agents.json`，沿用 `mcp_server.json` 的 command/args/env 格式：

```json
{
  "claude-code": {
    "command": "npx",
    "args": ["-y", "@agentclientprotocol/claude-agent-acp"],
    "env": { "ANTHROPIC_API_KEY": "..." }
  }
}
```

> adapter 包名改过两次：`@zed-industries/claude-code-acp`（弃用）→ `@zed-industries/claude-agent-acp`（弃用）→ 当前维护版 **`@agentclientprotocol/claude-agent-acp`**。前两者 npm 上的 `deprecated` 字段明确指向当前包，务必用当前包（旧版 `new_session` 会回 Internal error）。

认证由 adapter 自己 owns（env 透传），Lumi 不管外部 agent 的 auth。三级合并沿用现有 config 机制。

## 生命周期（MVP）

每次委派 spawn 一个子进程、用完关——与 sub-agent「无 checkpointer、用完即弃」一致。持久会话（跨任务保上下文，claw 风格）是性能优化，留到里程碑 2。

---

## 范围与里程碑

- **M1（本文）单点委派**：`delegate_to_claude(task)`，事件回流 + 权限 + fs 接通。验证 ACP client 链路。
- **M2 并行 fan-out**：N 个 agent 并行解同一题，结果汇总/择优。
- **M3 Council / Autoloop**：Planner→Coder→Reviewer 自迭代、worktree 隔离、投票共识。

## PR 切分（M1）

1. **ACP-PR1 — ACP client 传输层** ✅ 已落地：`lumi/acp/client.py`（薄封装 `acp` SDK：`initialize` / `session/new` / `session/prompt` + `session/update` 文本解析 + `AcpResult`），`tests/acp/` 起 echo agent 子进程跑通握手。
2. **ACP-PR2 — 委派工具 + 事件回流** ✅ 已落地：`providers/external_agent.py`（`delegate_to_claude(task, cwd)` 工具 + `_normalize_acp_update` 把 session/update 归一化 + 经 `adispatch_custom_event(LUMI_ACP_EVENT)` 回流）；`bridge/core.py` 加 `LUMI_ACP_EVENT` 分支（`_acp_event_to_bridge` 映射 message/thought/tool_start/tool_complete）+ `_SUBAGENT_TOOLS` 把 `delegate_to_claude` 纳入子卡片归属；`acp_agents.json` 配置（缺省用 npx adapter）。先不接权限/fs（外部 agent 工具调用当前一律拒绝）。
3. **ACP-PR3 — 权限接入**：`_BridgeClient.request_permission`（现 fail-safe 拒绝）→ `PermissionEngine` + `ApprovalBroker`（在途审批已落地）。
4. **ACP-PR4 — fs 回调接入**：`read_text_file` / `write_text_file` → `LocalFilesystemBackend`。**已确认必要**（见下：adapter 确实把 Read/Write mock 成 ACP fs 回调）。

---

## 待验证 → 已核实（验证 not 猜，2026-06 上网 + 装包 introspect）

1. **Python ACP SDK 成熟度** → ✅ **可用，已采用**：`agent-client-protocol`（PyPI，introspect 版本 0.10.1），提供 `Client`/`Agent` 基类、`spawn_agent_process` stdio、`acp.schema` Pydantic 类型、helper builders。**省掉自写 ~150 行 JSON-RPC**。`PROTOCOL_VERSION = 1`。
2. **adapter 是否回调 client 的 fs/permission** → ✅ **会回调**：`claude-agent-acp` 把 Read/Write 工具 mock 成 ACP `fs/*` 回调走 client，权限走 `session/request_permission`（README: "Tool calls (with permission requests)"）。⚠️ 已知它**不尊重 `.claude/settings.json` 的 deny 规则**（[issue #94](https://github.com/zed-industries/claude-agent-acp/issues/94)）——反证「权限必须由 Lumi 侧 `PermissionEngine` 管控」的方案正确性，PR3/PR4 是真护栏而非锦上添花。
3. **委派默认 `cwd`** → ✅ **已定：每次由 `LumiAgent` 显式指定**（`AcpClient.run(task, cwd)` 必传）。
4. **退路**：adapter 既然回调 permission/fs，M1 走标准 ACP 即可拿到细粒度控制，无需退回裸 CLI/PTY。
