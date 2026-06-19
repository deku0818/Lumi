# Lumi 后端：代码仓库整理与架构演进计划

> 本文是一次全仓审计（12 个子系统并行深读 + 依赖图实测 + 逐条 grep/wc 核验）的产物，作为分阶段重构的贯穿依据。
>
> **审计基线已逐项核验**：文件 LOC、死代码零引用、`execution_mode` 链路、`_atomic_write_json` 跨模块依赖、`api/app.py` 启动方式、ruff/test 现状均已实测确认。
>
> **一处诚实修正**：`execution_mode` **不是死参数**。`bridge.stream_response → LumiAgentState.execution_mode → nodes.py 的 get_policy/plan 守卫` 是完整活链路，且 `ws.py:322` 确实从 params 读取它。准确表述是「后端 + WS 都活，仅前端 `gateway.ts` 未暴露它」。

## 决策（已与用户确认）

1. **推进范围**：全部做完（阶段 0 → 4）。
2. **Gateway 方向**：一步到位 channel 抽象（Channel → GatewaySession → 瘦 Bridge），用 stub `TelegramChannel` 验证抽象成立。
3. **api/ 处置**：删除整个 `lumi/api/` 包（零引用、产出原始 LangGraph 事件与 WS 协议分裂）。

---

## 1. 现状全景

Lumi 后端真实结构 = **以 `agents/core` LangGraph 图为中枢、向外辐射五个支撑子系统、再被一层「事实上 desktop 专用」的 WS 网关包裹**。文档宣称的「中立 gateway + 多 channel」尚不存在：`bridge.py`（1376 行 god object）承担事件封装 / 会话生命周期 / provider CRUD / 权限审批富化 / 文件 checkpoint / 通知队列 7+ 职责；`ws.py`（737 行）把连接生命周期、25+ RPC handler、`run.lock` 并发协调、进程级广播去抖全部硬编码进 WebSocket 端点。**传输无关的会话 / channel 抽象整个缺失**，新增 Telegram 必须重写 `ws.py`。同时存在分层倒置：`utils`（底座）反向 import `agents.runtime`；`sessions` 反向 import `runtime.checkpoint._atomic_write_json`（私有符号被 5 模块当公共 API 用）。

```
┌─────────────────────────────────────────────────────────────┐
│  入口层   cli.py (typer)  ·  api/app.py (失活,与WS协议分裂)    │
├─────────────────────────────────────────────────────────────┤
│  网关层   server/ws.py ◄── 名为中立、实为 desktop 专用         │
│           bridge.py (god object, 1376行)  protocol.py         │
│           cron_rpc  desktop_delivery  projects               │
│           [协议契约: protocol/events.json ← 单一事实源 ✓]      │
├─────────────────────────────────────────────────────────────┤
│  图核心   agents/core/  graph + nodes(773,含180行权限路由)    │
│           hooks  workflow  structured_tool                   │
├──────┬──────────┬───────────┬───────────┬───────────────────┤
│ tools│permissions│  runtime  │   cron    │   sessions        │
│ (fs  │(engine424 │(checkpoint│(scheduler │ (从checkpoint     │
│ 1073)│ +全局可变 │ 700+原子写│  648)     │  派生,反向依赖    │
│      │ workspace)│ 私有外泄) │           │  runtime私有)     │
├──────┴──────────┴───────────┴───────────┴───────────────────┤
│  底座   utils/  ── 但 model_manager/llm_chain/model_catalog  │
│         反向 import agents.runtime (分层倒置 ↑)               │
│         真通用: logger jsonc token_counter thread_id image   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 跨切面问题清单（按严重度）

### 🔴 高 — 阻碍愿景 / 安全 / 大规模误导

| # | 问题 | 涉及文件 |
|---|------|---------|
| H1 | **Channel/Adapter 抽象缺失** — 新增 IM channel 须重写整套 `ws.py` | `server/ws.py`, `agents/bridge.py` |
| H2 | **`bridge.py` god object（1376 行）** — 7+ 不相干职责混一类 | `agents/bridge.py` |
| H3 | **分层倒置：`utils` 反向依赖 `agents`** — 靠延迟 import 规避循环 | `utils/model_manager.py`, `utils/model_catalog.py`, `utils/llm_chain.py` |
| H4 | **`_atomic_write_json` 私有符号被 5 模块跨层 import** | `runtime/checkpoint.py` + provider_store / session_meta / server.projects / model_catalog |
| H5 | **`docs/architecture/permissions.md` 描述已重构掉的旧设计**（`ToolEffect(Flag)` 等已不存在；示例代码会报错） | `docs/architecture/permissions.md`, `CLAUDE.md` |
| H6 | **整文件死代码（实测零引用）** — `general_tools.py` 320 + `clipboard.py` 101 + `api/app.py` 139 = 560 行 | `utils/general_tools.py`, `utils/clipboard.py`, `api/` |
| H7 | **`mcp.py` 三处死代码链路约 180 行** | `tools/providers/mcp.py` |
| H8 | **`is_use_tool` 两遍权限评估（180 行路由巨函数）** — DENY 预检被完整评估覆盖 | `agents/core/nodes.py` |
| H9 | **`App.tsx` 2118 行 + payload 全程 `any`** — 协议消费层与 UI 交织无法复用 | `desktop/src/App.tsx`, `desktop/src/types.ts` |

### 🟡 中 — 职责过载 / 重复 / 文档漂移

| # | 问题 | 涉及 |
|---|------|------|
| M1 | 职责过载大文件群 | `nodes 773` · `checkpoint 700` · `session 660` · `filesystem 1073` · `scheduler 648` · `engine 424` |
| M2 | 进程级广播 `_bg_dirty`/`_desktop_delivery`/`_broadcast_tasks` 绑死 WS 模块全局 | `server/ws.py` |
| M3 | cron CRUD 双实现（工具 vs `cron_rpc.py`），校验语义已分叉 | `tools/providers/cron.py`, `server/cron_rpc.py` |
| M4 | `workspace.py` 全局可变状态 = 第二事实源，多 Engine 互相覆盖 | `permissions/workspace.py`, `engine.py` |
| M5 | 相对时间格式化两份重复且不一致（`5 minutes ago` vs `5m ago`） | `sessions/session_store.py`, `runtime/checkpoint.py` |
| M6 | 卸载/截断逻辑两份重复（时间戳精度不一致），后者还是死代码 | `core/node_helpers/execution.py`, `messages.py` |
| M7 | `filesystem` `GrepInput` 12 字段声明两遍须手工同步 | `tools/providers/filesystem/__init__.py` |
| M8 | TUI 残留文档/命名/死字段（`summary.md` 路径全失效、`GlobalConfig` 死字段等） | 多处 |
| M9 | **ruff 零配置** — 禁 import-as / 无用 import 无机器护栏 | `pyproject.toml` |
| M10 | `curl`/`wget`/`ping` 列入只读白名单（可落盘，绕过审批风险） | `tools/capability.py` |

### 🟢 低 — 命名 / 防御性过度 / 命名陷阱

L2 `session` 同名异义（`lumi/sessions/` 聊天 vs `runtime/session.py` shell 进程） · L3 `tool_mode` vs `execution_mode` 两套 mode · L4 RPC 分派两套约定 · L6 残留 `SimpleAgent` 命名 · L7 logger 获取不统一 · L8 `EventKind.CLARIFY` 成员名丢 `.request` 后缀 · L9 多处「为不可能场景写错误处理」违例 · L10 可变实例/全局状态违背不可变偏好。

> 注：`test/`（单数）是用户本地临时草稿、不入库，**不在整理范围**。

---

## 3. Gateway / 多 Channel 目标架构

**本质**：后端 = 一个 Agent 运行时 + 一套事件契约；channel = 把事件流搬运到某传输上的薄适配器。引入三层清晰分离：

```
   Channel 层      WsChannel │ TgChannel │ TuiChannel      ← 只懂"传输"
   (传输适配)       send(frame)/recv()/lifecycle
                          │ 注入 frame ↕ 收 frame
   Session 层      GatewaySession                          ← 传输无关
   (会话编排)       run.lock + 通知轮询 + 中断状态机
                   + RPC 分发表(注册式) + BridgeEvent→wire frame
                          │ stream BridgeEvent
   Bridge 层(瘦)   AgentBridge: LangGraph事件→BridgeEvent   ← 唯一职责
   ~400行          会话切换/删除/resume (仅图编排)
                          │ 协作对象(被 RPC handler 调用)
   Services        ProviderService · ApprovalEnricher · CheckpointService · FolderManager
   进程级 fan-out   BroadcastHub (cron.running / bg_tasks.update 去抖, sink 注册)  ← 跨 channel 共享
```

### Channel 抽象契约

```python
# lumi/gateway/channel.py (新增)
class Channel(Protocol):
    async def send(self, frame: dict) -> None: ...      # wire frame 出站
    def recv(self) -> AsyncIterator[dict]: ...          # JSON-RPC 帧入站
    async def on_open(self) -> GatewaySession: ...
    async def on_close(self) -> None: ...
```
`run.lock`、通知轮询、`_pump`/`_stream_gen`、RPC 分发表全部从 `ws.py` 上移到 `GatewaySession`。`ws.py` 退化为 `WsChannel`（约 80 行）。

### bridge.py 职责拆分对照

| 当前混在 bridge 的职责 | 拆分去向 |
|---|---|
| LangGraph 事件 → BridgeEvent | **留在瘦 `AgentBridge`**（唯一职责） |
| 会话切换/删除/resume | **留在 `AgentBridge`** |
| provider CRUD | → `ProviderService` |
| 权限审批富化 `_enrich_tool_approval` | → `ApprovalEnricher`（并与 permissions 层合并） |
| 文件 checkpoint / rewind | → `CheckpointService` |
| add/remove folder、提醒注入 | → `FolderManager` |
| 通知队列 `drain_notification_hint` | → `GatewaySession` 通知轮询 + `runtime.bg_tasks.NotificationQueue` |

### 新增 Telegram channel：只实现 `TelegramChannel`（传输映射 + 注册），**不碰** bridge / session / services / protocol / events.json / BroadcastHub。若需要改它们 = 抽象边界划错。

### 共享事件契约
`protocol/events.json` 是做得最好的解耦点，强化为所有 channel 的硬契约。**唯一缺口**：payload 只列字段名不列类型 → 补类型后 `WireEvent` 做成按 `type` 判别的 discriminated union，消除 `App.tsx` 全部 `payload as any`。

### 目标目录树（lumi/）

```
lumi/
├── cli.py                          [保留]
├── gateway/                        [新增] 替代 server/ + 吸收 bridge
│   ├── bridge.py                   [移动+拆分] 1376→~400
│   ├── session.py                  [新增] GatewaySession(从 ws.py 提取)
│   ├── protocol.py                 [移动] server/protocol.py
│   ├── broadcast.py                [新增] BroadcastHub
│   ├── rpc.py                      [新增] 统一 RPC 注册表(合并 ws + cron_rpc)
│   ├── channels/
│   │   ├── ws.py                   [拆分] WsChannel ~80行
│   │   └── telegram.py             [stub] 验证抽象
│   └── services/                   [新增] provider/approval/checkpoint/folder + cron_rpc + projects
├── agents/{core,tools,permissions,runtime,cron}/   (见 §4)
├── sessions/                       [保留, 解除对 runtime 私有依赖]
├── models/                         [新增] catalog/manager/chain/provider_store (消除 utils↔agents 倒置)
├── utils/                          [瘦身]
│   ├── atomic_io.py                [新增] _atomic_write_json 提为公共
│   └── [删除] general_tools.py, clipboard.py
├── styles/
└── api/                            [删除整个包]
```

---

## 4. agents/ 内部结构优化

- **core/**：`is_use_tool` 180 行权限路由下沉 permissions 为 `route_decision(...)` 纯函数，消除 core→permissions 延迟 import；合并 H8 两遍评估；`preprocessing/` 与 `node_helpers/` 边界重划合并 message_injection；清 `SimpleAgent` 残留。
- **tools/**：`filesystem/__init__.py`(1073) 拆 backend/grep/media_read/tools；删 mcp 死代码；`capability.py` 硬编码集合 → 每个 tool 自带 readonly metadata（单一事实源）+ 收紧 curl/wget；抽后台任务启动样板。
- **permissions/**：接收下沉的 `route_decision`；消除 `workspace.py` 全局可变（修子 Agent 串扰）；`__init__` 不再 re-export tools 符号；重写 permissions.md。
- **runtime/**：`session.py`(660) 拆 shell/bg；`checkpoint.py`(700) 拆持久化/diff/管理器/清理；`_atomic_write_json` → `utils/atomic_io.py`；`provider_store`+`model_*` 合并到 `lumi/models/`。
- **cron/**：`scheduler.py`(648) 拆 JobRunner/重试/Compensation/锁；抽 `CronService` 统一 CRUD（消除 M3）；统一 logger。
- **hooks/workflow**：删未接线事件 + `side_effect` 死分支；抽共享 `run_subprocess_with_timeout`（5 处重复 SIGTERM→SIGKILL）；`WorkflowEngine` 拆 SubAgentFactory + ProgressTracker。

---

## 5. 分阶段路线图（每阶段独立可交付、保持可运行、测试可通过）

### 阶段 0 — 清扫与护栏（低风险）
删 `general_tools.py`/`clipboard.py`/`api/`（560 行）+ mcp 三处死链路（180 行）+ nodes/messages/execution 死代码 + TUI 死字段；删 `tests/tui/` 空壳；重写 `permissions.md` + 修 `summary.md`/`plan.md`/`checkpoint.md` 失效路径 + 修 CLAUDE.md 权限文件名；**新增 `[tool.ruff]`**（ICN 禁 import-as / F401 / I isort）；全仓清 `SimpleAgent`/`TUI` 残留命名。

### 阶段 1 — 提公共原语，断开分层倒置
`_atomic_write_json` → `utils/atomic_io.py`（5 importer 改路径）；统一相对时间格式化为单一 util；统一卸载实现（删死拷贝）；统一 logger 获取；收敛 `PATH_ARG_KEYS`/能力常量单点。

### 阶段 2 — 拆大文件（行为不变）
filesystem→4 模块；session 拆 shell/bg；checkpoint 拆 4 层；scheduler 拆 JobRunner 等；nodes 权限路由下沉 permissions + 合并两遍评估；engine 抽边界公共方法 + 删防御代码。**前置补测**：先补 `sessions/`/`ws.py`/`bridge.py` 覆盖作安全网。

### 阶段 3 — 收拢模型层 + 合并双实现
建 `lumi/models/`（移 model_*+provider_store+llm_chain）；抽 `CronService` 统一 cron CRUD；`events.json` 补 payload 类型 + `WireEvent` discriminated union。

### 阶段 4 — Gateway / 多 Channel 抽象（核心）
建 `lumi/gateway/`；从 `ws.py` 提取 `GatewaySession`；从模块全局提取 `BroadcastHub`；统一 RPC 注册表；从 bridge 拆 `ProviderService`/`ApprovalEnricher`/`CheckpointService`/`FolderManager`，bridge 瘦化到 ~400；`ws.py`→薄 `WsChannel`；**实现 stub `TelegramChannel` 验证抽象**（若无需改 session/bridge/services 则抽象正确）。分多 PR：BroadcastHub → GatewaySession 提取 → bridge service 拆分 → channel 化。

---

## 6. 统一规范

- **命名**：import-as 全面禁止（ruff ICN）；`runtime/session.py`→`shell_session.py`、`SessionManager`→`ShellSessionManager`（与 `lumi/sessions/` 区分）；跨模块复用符号去下划线放公共位置；`EventKind` 成员名 = wire 值（`CLARIFY_REQUEST`）。
- **目录**：测试镜像源码分层 `tests/{gateway,core,tools,...}/`；`__init__.py` 门面策略统一（不混用 re-export 与全路径）。
- **分层单向**：`utils` ← `models` ← `agents/*` ← `gateway`；函数体内延迟 import = 边界划错红旗，重构后消除。
- **事实源单点**：tool readonly metadata / WorkspaceBoundary / `provider_store.resolve` / `utils/atomic_io` / 相对时间格式化各唯一来源。
- **文档同步**：`protocol/events.json` 协议唯一事实源；架构文档随重构更新，加 CI 校验「文档引用的 `lumi/**.py` 路径真实存在」防漂移。
