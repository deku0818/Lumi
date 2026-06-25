# ACP Client 进度 / 交接（Resume Here）

> 这是 ACP 工作的**操作性交接文档**——日后据此快速捡回继续。设计与 rationale 见
> [acp-client.md](./acp-client.md)，权限地基见 [approval-inflight.md](./approval-inflight.md)。
> 行号会随改动漂移，对照时以函数名为准。最后核实：2026-06-25，分支 `feat/acp-client`。

---

## 0. 状态速览（一眼看全）

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| 在途审批 | `ApprovalBroker`（ACP 权限的地基） | ✅ 已落地（main 线之外，在 desktop-ws-server） |
| **ACP-PR1** | 传输层 `lumi/acp/`（spawn→initialize→prompt→收文本） | ✅ 已落地 |
| **ACP-PR2** | 委派工具 `delegate_to_claude` + 事件回流子卡片 | ✅ 已落地 |
| **ACP-PR3** | 权限接入（request_permission→PermissionEngine+Broker） | ⬜ 待做（计划见 §5） |
| **ACP-PR4** | fs 回调接入（read/write→LocalFilesystemBackend） | ⬜ 待做（计划见 §6） |
| M2 | 并行 fan-out | ⬜ 远期 |
| M3 | Council / Autoloop | ⬜ 远期 |

**一句话现状**：能在 desktop 对话里 `delegate_to_claude(task, cwd)` 把任务派给真实 Claude Code，
中间过程以子卡片流式展示，返回汇总文本。**但外部 agent 的任何工具调用当前一律 fail-safe 拒绝**
（`_BridgeClient.request_permission` 硬返回 `DeniedOutcome`），故现在**只适合分析/问答类任务**；
让它改文件/跑命令要等 PR3（权限）+ PR4（fs）。

**已用对的关键事实**：adapter 包 = `@agentclientprotocol/claude-agent-acp`（当前维护版，旧两个包名已弃用、旧版 `session/new` 报 Internal error）；**认证复用本机 Claude 登录态，无需 API key**。详见 [[project_acp_adapter_package]]。

---

## 1. 如何快速捡回工作（Resume Checklist）

```bash
git checkout feat/acp-client
uv run pytest tests/acp/ -q          # 15 passed = 链路健全（3 传输 + 12 归一化/映射）
```

- **改了被 `lumi serve` 加载的 Python**（`bridge/core.py`、`tools/__init__.py`、`external_agent.py` 等）后主动 `pkill -f "lumi serve"`，desktop 会自动重拉起带新代码的 sidecar。`lumi/acp/` 纯传输层若无人 import 可不重启。
- **真连 Claude Code 冒烟**（不连 Lumi）：`scratchpad/try_claude_code.py` 思路——`AcpClient("npx","-y","@agentclientprotocol/claude-agent-acp").run(task, cwd, on_update)`，env 不传、继承本机登录。
- **在 UI 里测**：desktop 对话里说「用 delegate_to_claude 让 Claude Code 在 <绝对路径> 解释这个项目」，观察子卡片流式输出。

---

## 2. As-built 代码地图（文件 → 职责 + 关键签名）

ACP = 把外部编程 agent 当子进程派活，与 MCP（用外部工具）**对称**。`lumi/acp/` 放运行时根下
（不进 `gateway/`——主动外联能力、与 MCP client 同侧，放 gateway 会造出已拆掉的 `agents→gateway` 反向依赖）。

### `lumi/acp/client.py` — 纯传输层（不依赖 LangGraph，可独立单测）
- `AcpResult(stop_reason: str, text: str)` — frozen dataclass，一次委派的收尾。
- `UpdateHandler = Callable[[str, object], Awaitable[None]]` — `(session_id, update)` 流式回调类型。
- `class _BridgeClient(Client)` — ACP `Client` 子类。覆盖 `session_update`（转发 on_update）、
  `request_permission`（**当前 fail-safe：`return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))`**）。
  **未覆盖 `read_text_file`/`write_text_file`**（PR4 待补）。
- `class AcpClient`：
  - `__init__(command, *args, env=None)` — 给了 env 才 `{**default_environment(), **env}` 并入（否则子进程丢 PATH）。
  - `run(task, cwd, on_update=None) -> AcpResult` — **spawn → `initialize(PROTOCOL_VERSION=1)` → `new_session(cwd)` → `prompt([text_block(task)])` → 收 stop_reason**。内部 `collect` 先累积 `_agent_message_text` 到返回文本，再透传外部 on_update。
  - **生命周期 = spawn-per-delegation**：每次 `run()` 起一个子进程，`async with spawn_agent_process` 退出即关。无持久会话/连接复用。

### `lumi/agents/tools/providers/external_agent.py` — 委派工具 + 归一化
- ⚠️ **本模块禁止 `from __future__ import annotations`**（PR3 会注入 `runtime: ToolRuntime`，字符串化注解破坏注入；registry 加载期有 fail-fast 守卫）。
- `LUMI_ACP_EVENT = "lumi_acp"` — `adispatch_custom_event` 的事件名，bridge 据此识别回流。
- 默认 worker：`_DEFAULT_ARGS = ("-y", "@agentclientprotocol/claude-agent-acp")`。
- `_normalize_acp_update(update) -> dict | None` — ACP `session/update` → bridge 可映射 payload：
  `agent_message_chunk`→`{kind:"message",text}`、`agent_thought_chunk`→`{kind:"thought",text}`、
  `tool_call`→`{kind:"tool_start",name=title,tool_call_id}`、
  `tool_call_update`(status∈completed/failed)→`{kind:"tool_complete",name,tool_call_id,is_error}`。
  空文本 / `in_progress` / 其它 → `None`（跳过）。
- `_claude_code_spec()` / `_load_acp_config()` — 读 `.lumi/acp_agents.json` 的 `claude-code` 键，缺失则用默认 npx adapter。
- `delegate_to_claude(task, cwd) -> str`（`@tool`，`args_schema=DelegateInput`）— 构 AcpClient，
  `on_update` 把归一化 payload `adispatch_custom_event(LUMI_ACP_EVENT, ...)`，返回 `result.text`。

### `lumi/gateway/bridge/core.py` — 事件回流落地 + 子卡片归属
- `_SUBAGENT_TOOLS = frozenset({"agent", "delegate_to_claude"})` — 「委派型」工具集合（原硬编码 `name=="agent"` 已泛化为这个集合，共 5 处）。其 run_id 进 `_active_agent_runs`，供归属。
- `_acp_event_to_bridge(data, parent_id) -> BridgeEvent | None`（`@staticmethod`）— 归一化 payload → BridgeEvent：
  `message`→`MESSAGE_DELTA`、`thought`→`THINKING_DELTA`、`tool_start`→`TOOL_START`、`tool_complete`→`TOOL_COMPLETE`、未知→`None`。
- `_stream` 内 `on_custom_event` + `name==LUMI_ACP_EVENT` 分支调上面方法 yield。
- `_resolve_subagent_parent(run_id, parent_ids)` — 取 parent_ids（root→父序）中首个在 `_active_agent_runs` 的（≠run_id）= 子卡片 parent。**ACP 回流白嫖此机制，前端零改动。**

### 其它
- `lumi/utils/config/manager.py`：`acp_config_path` 属性（`config_dir/acp_agents.json`，对称 mcp）。
- `pyproject.toml`：`agent-client-protocol>=0.10.1`（import 名 `acp`）。
- `tests/acp/`：`_echo_agent.py`（最小 ACP agent 子进程，无网络/无副作用）、`test_acp_client.py`（3 个，起真子进程跑握手）、`test_external_agent.py`（12 个纯函数，归一化 + bridge 映射）。

---

## 3. 端到端事件回流数据流

```
LLM 调 delegate_to_claude(task, cwd)            （在 LangChain 工具 callback 上下文）
  → AcpClient.run() : spawn → initialize → new_session(cwd) → prompt(task)
  → 外部 agent 每条 session/update → _BridgeClient.session_update
      → AcpClient.collect : 累积 _agent_message_text(→ AcpResult.text)，再调 on_update
  → on_update : _normalize_acp_update → adispatch_custom_event(LUMI_ACP_EVENT, payload)
  ── LangChain 自动给 custom event 带 parent_ids（含 delegate 工具 run_id）──
  → bridge._stream astream_events(version="v2") 收到 on_custom_event
      → _resolve_subagent_parent → parent_id（delegate run_id 已在 _active_agent_runs）
      → _acp_event_to_bridge → BridgeEvent(kind=*, parent_run_id=parent_id)
  → WS event 帧 → 前端据非空 parent_run_id 渲染进 delegate 子卡片
  → run() 返回 AcpResult.text → delegate_to_claude 返回串 → 正常 ToolMessage
```

> 注意：`AcpResult.text`（client 层 `_agent_message_text`，进消息历史的工具返回值）与回流子卡片
> （agents 层 `_normalize_acp_update`，实时 UI 流）是**两条独立路径**，都读 `agent_message_chunk`
> 但逻辑不共享——改一处不会同步另一处。

---

## 4. 决策日志

1. **`lumi/acp/` 放运行时根下**，不进 `gateway/`（与 MCP client 同侧；避免 agents→gateway 反向依赖）。
2. **委派 `cwd` 每次由 `LumiAgent` 显式指定**（`delegate_to_claude` 必填参数，不隐式跟随会话目录）。
3. **传输层直接用官方 `acp` SDK**（省自写 ~150 行 JSON-RPC + 类型，故无 `transport.py`/`types.py`）。
4. **PR 编号用 `ACP-PR*` 前缀**，避免与 approval-inflight 文档撞号。
5. **acp/ 保持 framework-free**：PR3/PR4 的 permission/fs 能力经 provider 层（`runtime.context`）取出后以**纯 Callable 注入** AcpClient，acp/ 只 import `acp` + typing。

---

## 5. ACP-PR3 — 权限接入（经核实的实施计划）

**目标**：外部 agent 工具调用 → `PermissionEngine.evaluate()` → 命中 ASK/UNMATCHED/越界走 `ApprovalBroker.request()`，复用现有 `tool_approval` 全套（payload / bridge 富化 / resolve）。

### 已核实的缝隙
- `PermissionEngine.evaluate(tool_name, tool_args) -> PermissionDecision`（`permissions/engine.py`）。
  **四态：`ALLOW / DENY / ASK / UNMATCHED`**（UNMATCHED 在 default 模式应走审批，不可默认放行）。
  另有 `check_workspace_boundary(tool_name, tool_args) -> bool`、`get_boundary_violations(...)`。
  **规则按 Lumi 工具名 + Lumi 参数键写**（`command`/`cmd`、`file_path`/`path`）——Claude Code 的工具名/参数不归一化就全部落 UNMATCHED。
- `ApprovalBroker.request(payload, reject_value)`（`gateway/bridge/broker.py`）。tool_approval payload =
  `{"type":"tool_approval","tool_calls":[{"id","name","args"}]}`，reject_value = `{"decision":"reject","message":...}`；
  resolve 回 `{"decision":"approve"|"reject"|"cancel","message",...}`。
- 节点范式（`nodes.py` human_approval / `ask.py`）：从 `runtime.context.approval_broker` 取 broker → `None` 则 fail-closed → `await broker.request(payload, reject_value)`。
- ACP SDK 形状（已 introspect）：`request_permission(self, options: list[PermissionOption], session_id, tool_call: ToolCallUpdate, **kwargs)`。
  - `PermissionOption.option_id` + `.kind`（`allow_once/allow_always/reject_once/reject_always`）。
  - `ToolCallUpdate.kind`（`read/edit/delete/move/search/execute/think/fetch/switch_mode/other`）+ `.raw_input`（dict，Claude Code 原始参数）+ `.tool_call_id` + `.title`。
  - **放行**：从 `options` 挑 `kind∈(allow_once,allow_always)` 的 `option_id` → `AllowedOutcome(outcome="selected", option_id=<id>)`（⚠️ 字面量是 **`"selected"`** 不是 `"allowed"`；`option_id` 与 alias `optionId` 因 `populate_by_name` 两者皆可）。
  - **拒绝**：`DeniedOutcome(outcome="cancelled")`。

### 分步
1. **`lumi/acp/client.py`**：`_BridgeClient` 加 `on_permission` 注入（类型 `Callable[[list, object], Awaitable[str|None]]`，入参 `(options, tool_call)`、返回选中 option_id 或 None）。⚠️ **options 不在 ToolCallUpdate 内**，是 request_permission 的独立参数，故签名必须带 options。重写 `request_permission`：无注入→`DeniedOutcome`；有→调回调，None→deny，否则→`AllowedOutcome(outcome="selected", option_id=...)`。`run`/`_connect` 透传 `on_permission`。`from acp.schema import AllowedOutcome`。
2. **`external_agent.py`**：`delegate_to_claude` 加 `runtime: ToolRuntime`；取 `runtime.context.permission_engine` / `.approval_broker`；写 `on_permission` 闭包：`_acp_to_lumi(tool_call)` 归一化 → `engine.evaluate` + `check_workspace_boundary` → ALLOW+边界内→挑 allow option_id；DENY→None；ASK/UNMATCHED/越界→`broker.request(tool_approval payload, reject_value)` 按 decision 返回。**broker is None（headless）→ fail-closed 拒绝**。
3. **工具名归一化** `_acp_to_lumi(tool_call) -> (name, args)`（纯函数）：`execute→bash{command}`、`edit/write→write{file_path}`、`read→read{file_path}`、`delete/move→write`（写类触发审批）、`search→grep{pattern}`、其它→保守按写处理走审批。bash 必须把**整条命令**塞进 `command`（engine 会拆复合命令）。
4. **测试**：纯函数测 `_acp_to_lumi` + 挑 option_id；mock `engine.evaluate` 三态。**禁止起子进程/跑真命令**（[[feedback_no_dangerous_commands_in_tests]]）。

### 仍需决策/验证
- **D1 「始终允许」语义**：Lumi 审批卡片的 `always_allow_exact`/`set_tool_mode`/`accept_edits` 选项对 ACP 子进程无意义（下个 ACP tool_call 不复用 Lumi 规则）——决定屏蔽，还是 approve 选了 allow_always 类就回 ACP 的 `allow_always` option 让 Claude Code 自己记。
- **D2 归一化保真度（必须实测，非猜）**：Claude Code 实际发的 `kind` 值 + `raw_input` 键名（execute 的命令键、edit 的路径键）需起一次真实只读委派抓 `session/update` 样本核对。
- **D3 tool_mode**：是否让 ACP 委派尊重 `runtime.state["tool_mode"]`（privileged 自动放行 / auto 走分类器），MVP 可先只做 default + DENY 免疫。

---

## 6. ACP-PR4 — fs 回调接入（经核实的实施计划）

**目标**：外部 agent 的 `fs/read_text_file` / `fs/write_text_file` → `LocalFilesystemBackend`，白嫖工作区边界 + 变更追踪。已确认 adapter **会**把 Read/Write mock 成 ACP fs 回调（且不尊重 `.claude/settings.json`），故 PR4 是真护栏。

### 已核实的缝隙（含两个大坑）
- `LocalFilesystemBackend`（`tools/providers/filesystem/backend.py`，单例 `get_backend()`）：
  - `read(file_path, offset=0, limit=...) -> str` — ⚠️ **不调 `validate_path`，无边界检查**（已亲自核实：read 体 :168-192 无 validate_path，只 write:195 / edit:220 调）。**PR4 接 read 必须自己先 `validate_path(path)`**，否则外部 agent 能读授权目录外任意文件。另：返回**带行号**文本，而 ACP `ReadTextFileResponse.content` 要**原始内容**——需无行号读路径。
  - `write(file_path, content) -> dict` — 调 `validate_path`（越界抛 `PermissionError`）+ 变更追踪（`record_pre_write`）。⚠️ **文件已存在即报错，不覆盖**；外部 agent 的 Write 通常是覆盖语义——需决策。
- `validate_path(path) -> Path`（`permissions/workspace.py`）：越界抛 `PermissionError`。per-run 授权来源 `set_run_authorized_source_for(engine, extra_folders)`。
- `FileChangeTracker`（`runtime/file_tracker.py`）：`backend.write/edit` 内自动 `record_pre_*`，**复用 backend.write 即免费拿到变更追踪**（前提 tracker 处于 active turn）。
- ACP SDK fs：`ReadTextFileRequest(path, session_id, line?, limit?)` / `ReadTextFileResponse(content)`；
  `WriteTextFileRequest(content, path, session_id)` / `WriteTextFileResponse`（空响应）。
  `ClientCapabilities.fs = FileSystemCapabilities(read_text_file, write_text_file)`。
  ⚠️ **当前 `_connect` 的 `initialize` 没传 `client_capabilities` → fs 默认全 False → adapter 不回调 fs**。PR4 必须传 `ClientCapabilities(fs=FileSystemCapabilities(read_text_file=True, write_text_file=True))`。
  ⚠️ `_BridgeClient` 不 override read/write 会继承 Protocol 的 `...` 体返回 None（对非 optional 的 fs/read 是非法响应）——**必须显式 override 且 fail-closed，不能沉默返回 None**。

### 分步
1. **`lumi/acp/client.py`**：`_BridgeClient` 加 `read_file`/`write_file` 两个 framework-free Callable 注入；override `read_text_file`（→`ReadTextFileResponse(content=...)`）、`write_text_file`（→`WriteTextFileResponse()`）；无注入→fail-closed 抛 `RequestError`（不返回 None）。
2. **`_connect`/`initialize`**：注入了回调时传 `client_capabilities=ClientCapabilities(fs=FileSystemCapabilities(read_text_file=True, write_text_file=True))`（声明即兑现）。`run` 透传。
3. **`external_agent.py`**：用 `runtime`（PR3 已加）构造接 `get_backend()` 的回调：read 回调先 `validate_path` 补边界 → 读**无行号**原文 → catch `PermissionError`；write 回调 `get_backend().write(...)`，处理「已存在」语义 → catch `PermissionError`。
4. **测试**：扩 `_echo_agent.py` 增会回调 fs 的变体；临时目录 + 纯断言验越界被拒 / read 无行号 / write 落盘且被 tracker 登记 / 未声明能力不回调。

### 仍需决策/验证
- read 行号剥离（复用 backend 还是 PR4 自写薄读）；read 边界放回调里还是给 backend 加 `read_checked`。
- write「已存在」覆盖 vs 拒绝。
- ACP `line`(1-based)+`limit`(行数) vs backend `offset`(0-based)+`limit` 精确映射。
- fs 错误回 ACP 的方式（`RequestError` vs content 里回错误文本）——查 adapter 期望（验证非猜）。

---

## 7. 风险与未决清单（接手必读）

| # | 风险 | 现状 / 影响 | 处理时机 |
| --- | --- | --- | --- |
| 1 | **取消传播 / 子进程回收** | stop 走外层 task.cancel → 传到 `await conn.prompt()`；SDK `spawn_stdio_transport` 的 finally 做 terminate→kill，但 CancelledError 传播期多个 await 可能被二次取消、漏杀 `npx` 留孤儿。**无测试覆盖。** | **PR3 前补「delegate 跑一半 stop」e2e**；不可靠则 AcpClient 持 `proc` 句柄 cancel 时 `proc.kill()` 兜底 |
| 2 | spawn-per-delegation，无持久会话 | 跨任务上下文丢失 + npx 冷启动开销 | 接受（M1 有意）；持久会话留 M2 |
| 3 | 并发多委派归属 | `_active_agent_runs`(set)+`_resolve_subagent_parent` 理论成立，但「on_tool_end 已 discard 而尾随 update 仍在飞」的窗口边界未实测 | M2 fan-out 前实测不串扰 |
| 4 | 工具名映射保真度 | 未实现；映射不保真→误放行(漏洞)或误拒(卡死)。adapter 不尊重 `.claude/settings.json`，Lumi 侧映射是唯一护栏 | PR3 核心，先简单映射后细化 |
| 5 | **cwd 边界未校验** | 已核实：`delegate_to_claude` 的 `cwd` 完全不过边界检查（`extract_paths_from_tool_call` 只认 bash + `file_path`/`path`，不含 cwd）；模型可把任意目录派给外部 agent | M3 worktree 前必修（建议 PR3/PR4 入口校验 cwd 在授权目录内） |
| 6 | headless 无 broker | PR3 接 broker 后若不加守卫，cron/workflow 下遇权限请求会挂死 | PR3 同步加 `broker is None → 拒绝`（对齐 human_approval/ask） |
| 7 | adapter 版本/认证 | 必须 `@agentclientprotocol/claude-agent-acp`；旧两包弃用、旧版 new_session 报错；无需 key 复用本机登录 | 接受（已用对）；配置注释固化包名 |
| 8 | fs 未接 | 未接时文件操作不过边界/追踪；但当前因 request_permission 全拒，文件操作根本到不了 fs 回调，暂无暴露 | PR4（PR3 放行后才成真实暴露面） |

---

## 8. M2 / M3 展望

- **M2 并行 fan-out**：N agent 并行解同题、汇总/择优。前置 = PR3（否则 worker 全被拒）。当前 `delegate_to_claude` 单点，并行靠 ToolExecutor 并发多次调用（graph 零改动），需实测风险 #3。**持久会话**（连接复用/`session/load`）是此阶段性能优化，要重构 AcpClient 生命周期。
- **M3 Council / Autoloop**：Planner→Coder→Reviewer 自迭代、worktree 隔离、投票共识。前置 = M2 + PR4 + cwd 边界（风险 #5）。worktree 隔离要先把 cwd 纳入边界管控。

---

## 9. 测试方法速查

- **自动化（无 key/无网）**：`uv run pytest tests/acp/ -q` → 15 passed。
- **真连 Claude Code（无 key，本机登录）**：`AcpClient("npx","-y","@agentclientprotocol/claude-agent-acp").run(task, cwd, on_update)`，纯文本任务可通；工具/文件操作当前被拒（PR3/PR4 前的预期边界）。
- **UI**：desktop 委派 → 看子卡片流式（PR2 验收点）。
