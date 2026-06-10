# Desktop 应用架构

Lumi 桌面应用（Electron + TS 前端）的内部实现。前端通过 WebSocket 复用后端的 `AgentBridge`，与 TUI 共享同一套 Agent 运行时。协议契约见 [`protocol/README.md`](../../protocol/README.md)。

> 现状：desktop 处于开发阶段，dev 模式经 `uv run lumi serve` 拉起 sidecar；打包发行（内置可移植 Python 运行时）仍是 TODO。

---

## 进程结构

```
┌─────────────────── Electron ───────────────────┐
│  main 进程 (electron/main.cjs)                  │
│    · pickPort() 分配空闲端口                     │
│    · spawn `uv run lumi serve --port <p>`  ──┐  │
│    · createWindow() + preload 注入 ws 地址   │  │
│                                              │  │
│  renderer 进程 (desktop/src, React)          │  │
│    · Gateway: 每会话一条 WS 连接             │  │
│    · 经 window.lumi.getConnection() 取地址   │  │
└──────────────────────────────────────────────┼──┘
                                               │
                          ┌────────────────────▼─────────────────┐
                          │  lumi serve (FastAPI, lumi/server/ws) │
                          │    每条 WS = 一个 AgentBridge         │
                          │      → LumiAgent graph (LangGraph)    │
                          └───────────────────────────────────────┘
```

- **main 进程**：唯一持有 sidecar 生命周期。sidecar 非主动退出（崩溃/被杀）时同端口自愈重启，renderer 的重连逻辑自动连上。
- **renderer**：纯前端，无 Node 访问（`contextIsolation`）。只通过 preload 暴露的 `getConnection()` 拿到 `ws://127.0.0.1:<port>/ws`。
- **sidecar**：headless FastAPI，启动时不引入 textual（TUI 专属模块在用到时才懒加载）。

## WS / JSON-RPC 帧协议

一条 WS 连接 = 一个会话上下文（独立 `AgentBridge`，可切换 thread）。一次只跑一轮：run 进行时不读新帧，中断（approval/clarify/plan）后回到接收循环等待 `resume`。

```
client → server   {id, method, params}
server → client   {id, result} | {id, error:{message}}        # RPC 响应
server → client   {method:"event", params:<wire event>}       # 流式事件
```

- **RPC 方法**：
  - 流式：`send_message`、`resume`、`run_command`（运行斜杠命令）。
  - 会话：`list_sessions`、`new_session`、`switch_session`、`load_history`、`pin_session`、`rename_session`、`delete_session`。
  - 模型供应商：`list_providers`、`test_provider`、`set_provider`、`save_provider`、`delete_provider`。
  - 定时任务：`list_cron_jobs`、`create/update/delete/toggle_cron_job`、`run_cron_job`、`list_cron_runs`。
  - 其它：`stop`（中止当前流式轮）、`list_commands`（拉取斜杠命令）。
- **wire 事件**：`message.*`、`tool.*`（含 `tool.generating`）、`clarify/approval/plan.request`、`turn.complete`、`error`，加握手帧 `gateway.ready` 与 cron 广播 `cron.result` / `cron.running`（进程级，不属于任何会话）。

事件名与方法名都来自 [`protocol/events.json`](../../protocol/events.json) 单一事实源：TS 端 import derive 类型，Python 端由 `tests/server/test_protocol_contract.py` 锁住一致性。

## AgentBridge 复用

`lumi/agents/bridge.py` 是中立桥接层（TUI 与 desktop 共用），把 LangGraph 的原始事件封装为干净的 `BridgeEvent` 流。`EventKind` 成员值直接采用对外 wire 命名（`namespace.verb`），`lumi/server/protocol.py` 只做 `BridgeEvent → {type, session_id, payload}` 的 payload 重组，无额外映射层。

每条 WS 连接独立持有一个 `AgentBridge` 实例，`current_thread_id` 即该连接当前会话。多会话并发时各连接互不阻塞。

## 会话管理

会话列表由 LangGraph checkpoint 派生（`lumi/tui/session_store.list_sessions`），但「置顶」「自定义标题」是用户施加的、不存在于 checkpoint 中的元数据，单独持久化：

- **`lumi/tui/session_meta.py`** — JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），按 `thread_id` 存 `pinned`/`title`，仅写非默认值。textual-free，可在 headless 服务直接使用。
- **`list_sessions` RPC** — 合并 sidecar 元数据后注入 `title`/`pinned`，置顶项稳定排到最前。
- **删除** — `delete_session` 经 `bridge.delete_thread()` 一并清理两类 checkpoint：LangGraph 会话（`LumiAgent.adelete_thread`）+ 文件级 checkpoint（`checkpoint.delete_thread_checkpoint`），再删除 sidecar 元数据条目。

前端 `Sidebar` 每行 hover 出现 `⋮` 菜单（置顶 / 重命名 / 删除）；删除走二次确认弹窗（`ConfirmDialog`），删除当前会话时自动另开新会话顶上。

## 模型供应商管理

用户自定义的「连接 + 模型」持久化在 `~/.lumi/providers.json`（明文，`chmod 600`，含 `api_key`），由 `lumi/agents/runtime/provider_store.py` 读写——textual-free，TUI 与 desktop 共享同一份配置。

- **数据模型**：一个 **profile** = 一套连接（`name` / `base_url` / `api_key`）+ 该连接下的一组 `models`；`active` 指向「某 profile 下的某个 model」。协议（OpenAI / Anthropic 客户端）仍由 model 名经 `model_manager.detect_model_type` 自动判定，无需配置。`provider_store` 兼容旧格式（单 `model` 字段、`active` 为字符串 id），读取时自动迁移并把失效 `active` 归位到首个可用模型。
- **运行时生效**：`LumiAgentContext` 增加 `base_url` / `api_key` 两个字段（`state.py`）；`call_model` 经 `_provider_kwargs()`（`nodes.py`）仅在非空时透传给 `create_llm`，空则沿用 env / SDK 默认。`AgentBridge._apply_active()` 把当前 `active` 应用到 context，**下一轮** `call_model` 生效。
- **RPC**：`list_providers`（列全部 profile + active）、`save_provider` / `delete_provider`（增删改，返回刷新后的 `{profiles, active}`）、`set_provider`（切换 active，返回 `{active, model}`）、`test_provider`（用给定连接对模型发最小请求验证可达，15s 短超时、不缓存不重试）。`set/save/delete_provider` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥，避免轮内改掉共享 context。
- **前端**：`SettingsDialog` + `ProvidersPanel` 完成增 / 删 / 改 / 测试；`ModelPicker`（顶栏）做快速切换。
- **TUI 对应**：`/model` 命令打开 `ModelScreen`（`lumi/tui/screens/model_screen.py`）——把「供应商 × 模型」拍平成列表，**仅切换**；增删改在桌面端配置页完成，二者共享 `~/.lumi/providers.json`。

## 定时任务管理

cron 子系统是进程级资源（与会话无关）：serve 在 lifespan 中经 `lumi/agents/cron/runtime.setup_cron()`（TUI 共用的装配工厂）启动调度器，RPC 实现在 `lumi/server/cron_rpc.py`，不经 AgentBridge。内部机制（执行即会话、保留策略、级联删除）见 [`cron.md`](cron.md)。

- **结果广播**：`lumi/server/desktop_delivery.py` 的 `DesktopDelivery` 把任务结果（`cron.result`）与运行状态（`cron.running`）推给所有活跃 WS 连接——wire 信封格式属 server 层，agents 层只定义 `ResultDelivery` 抽象。无连接时不缓存：结果已落 RunLog，重连后经 `list_cron_runs` 查询。
- **前端结构**（`CronPage.tsx` + `App.tsx`）：
  - 侧栏「定时任务」分组（任务名 + 未读角标 + 运行中脉冲点）→ 点击进入**任务会话视图**：主区为最近一次执行的完整对话（composer 可续聊），右侧 `RunsRail` 列历次执行，蓝点 = 未读、点开即消失。
  - 顶部「定时任务」导航入口 → 管理页（卡片网格 + 新建 / 编辑 / 删除 + 详情）。
  - App 持有 cron 数据单一来源（jobs / 未读计数 / 已读集合，后两者持久化 localStorage）；cron 事件广播到每条 WS 连接，前端按 `job_id:started_at` 去重。

## 桌面通知

回复完成与等待用户处理的中断（审批 / 提问 / 计划）会触发系统通知，**仅在该会话非当前活动、或窗口未聚焦时**弹出（你正盯着时不打扰）。通知经主进程 `Notification`（`electron/main.cjs`）发出——renderer 的 HTML5 `Notification` 在 macOS dev 下不可靠；点击通知经 `lumi:focus` IPC 把窗口带回前台并切到对应会话。判定用 `document.hasFocus()` 而非 `document.hidden`（切到别的应用时窗口仍可见，`hidden` 恒为 false）。

## 国际化（i18n）

`desktop/src/i18n.ts` 提供 `useI18n()` hook（`t` / `lang` / `setLang`），支持中文 / English，偏好存 localStorage（`lumi-lang`）。所有 UI 文案经 `t(key)` 取用，不硬编码。

## 关键文件

| 文件 | 职责 |
|---|---|
| `desktop/electron/main.cjs` | sidecar 生命周期、窗口、端口分配 |
| `desktop/src/gateway.ts` | WS JSON-RPC 客户端（带指数退避重连） |
| `desktop/src/App.tsx` | 会话状态机、事件路由、聊天流渲染 |
| `desktop/src/components/Sidebar.tsx` | 会话列表 + 右键菜单 + 内联重命名 |
| `desktop/src/components/{SettingsDialog,ProvidersPanel,ModelPicker}.tsx` | 模型供应商配置 + 快速切换 |
| `desktop/src/components/CronPage.tsx` | 定时任务管理页 + 任务会话视图 Runs 栏 |
| `desktop/src/i18n.ts` | 国际化（中文 / English） |
| `lumi/server/ws.py` | FastAPI WS 端点 + RPC dispatch |
| `lumi/server/protocol.py` | BridgeEvent → wire 序列化 |
| `lumi/server/cron_rpc.py` | 定时任务 RPC 方法实现 |
| `lumi/server/desktop_delivery.py` | cron 结果 → WS 广播投递通道 |
| `lumi/agents/bridge.py` | LangGraph ↔ 前端中立桥接层 |
| `lumi/agents/runtime/provider_store.py` | 模型供应商 profile 持久化（`~/.lumi/providers.json`） |
| `lumi/tui/screens/model_screen.py` | TUI `/model` 模型切换弹窗 |
| `lumi/tui/session_meta.py` | 会话用户元数据 sidecar |
| `protocol/events.json` | 协议单一事实源 |
