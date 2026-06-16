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

一条 WS 连接 = 一个会话上下文（独立 `AgentBridge`，可切换 thread）。同一时刻只跑一轮用户流式响应，但所有 RPC（流式与非流式）都 spawn 成独立 task 执行，接收循环持续读帧——流式轮运行期间 `stop` 帧随时可达；需要 `run.lock` 的方法（删会话、切模型等）在后台等锁，不会卡住接收循环。中断（approval/clarify/plan）后等待 `resume`。

```
client → server   {id, method, params}
server → client   {id, result} | {id, error:{message}}        # RPC 响应
server → client   {method:"event", params:<wire event>}       # 流式事件
```

- **RPC 方法**：
  - 流式：`send_message`、`resume`、`run_command`（运行斜杠命令）。
  - 会话：`list_sessions`、`new_session`、`switch_session`、`load_history`、`pin_session`、`rename_session`、`delete_session`。
  - 项目 / 工作目录：`list_projects`、`add_project`、`remove_project`、`rename_project`、`set_workspace`（切项目，进程级）、`add_folder` / `remove_folder`（本会话临时目录）。
  - 模型供应商：`list_providers`、`test_provider`、`set_provider`、`save_provider`、`delete_provider`。
  - 定时任务：`list_cron_jobs`、`create/update/delete/toggle_cron_job`、`run_cron_job`、`list_cron_runs`。
  - 其它：`stop`（中止当前流式轮）、`list_commands`（拉取斜杠命令）。
- **wire 事件**：`message.*`、`tool.*`（含 `tool.generating`）、`clarify/approval/plan.request`、`turn.complete`、`error`，加握手帧 `gateway.ready` 与 cron 广播 `cron.result` / `cron.running`（进程级，不属于任何会话）。

事件名与方法名都来自 [`protocol/events.json`](../../protocol/events.json) 单一事实源：TS 端 import derive 类型，Python 端由 `tests/server/test_protocol_contract.py` 锁住一致性。

## AgentBridge 复用

`lumi/agents/bridge.py` 是中立桥接层（TUI 与 desktop 共用），把 LangGraph 的原始事件封装为干净的 `BridgeEvent` 流。`EventKind` 成员值直接采用对外 wire 命名（`namespace.verb`），`lumi/server/protocol.py` 只做 `BridgeEvent → {type, session_id, payload}` 的 payload 重组，无额外映射层。

每条 WS 连接独立持有一个 `AgentBridge` 实例，`current_thread_id` 即该连接当前会话。多会话并发时各连接互不阻塞。

进程级共享资源的边界：连接断开时 `bridge.close()` 只清理该 bridge 自身；MCP 子进程、
shell / 后台任务会话等全局单例由 `shutdown_shared_runtime()` 在 lifespan shutdown
时统一关闭（TUI 则在 quit 时调用）。后台任务的完成通知按归属认领——任务注册时经
`ContextVar` 捕获所属 thread_id，各连接的通知轮询只取走归属自己当前 thread（或无归属）
的通知，不会把别的会话的任务结果注入本会话。

## 会话管理

会话列表由 LangGraph checkpoint 派生（`lumi/tui/session_store.list_sessions`），但「置顶」「自定义标题」是用户施加的、不存在于 checkpoint 中的元数据，单独持久化：

- **`lumi/tui/session_meta.py`** — JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），按 `thread_id` 存 `pinned`/`title`，仅写非默认值。textual-free，可在 headless 服务直接使用。
- **`list_sessions` RPC** — 合并 sidecar 元数据后注入 `title`/`pinned`，置顶项稳定排到最前。
- **删除** — `delete_session` 经 `bridge.delete_thread()` 一并清理两类 checkpoint：LangGraph 会话（`LumiAgent.adelete_thread`）+ 文件级 checkpoint（`checkpoint.delete_thread_checkpoint`），再删除 sidecar 元数据条目。

前端 `Sidebar` 每行 hover 出现 `⋮` 菜单（置顶 / 重命名 / 删除）；删除走二次确认弹窗（`ConfirmDialog`），删除当前会话时自动另开新会话顶上。

## 项目与工作目录

**项目 = 工作目录**，是会话隔离单位（会话列表按 checkpoint metadata 的 `workspace_dir` 过滤）。

- **进程级单一工作目录**：工作目录是进程级状态（`os.chdir`），同一时刻整个 app 只有一个；在任一窗口切项目对所有会话生效（与 `set_provider` 的全局性同类）。`set_workspace`（`bridge.py`）`chdir` 后重建权限边界、重置共享 `"default"` shell；为避免其它会话的引擎边界与 cwd 脱节，经进程级弱引用注册表 `_active_bridges` 让**每个**存活 bridge 的权限引擎一并 `rebase` 到新目录（各自保留本会话的临时目录）。前端切项目后另开新会话。`set_workspace` / `add_folder` / `remove_folder` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥。
- **项目清单**：纯手动登记，持久化在 `~/.lumi/projects.json`（`lumi/server/projects.py`，复用 `_atomic_write_json`），按 `last_used` 降序。`list_projects` 返回 `{projects, current}`；`add_project`（缺省用目录末端名，重复添加保留用户重命名）/ `remove_project`（只删条目，不动磁盘）/ `rename_project`；`set_workspace` 成功后经 `touch_project` 刷新 `last_used`。
- **添加文件夹（本会话临时）**：`add_folder` / `remove_folder` 把目录临时加进**本连接** bridge 的可访问范围（`engine.add_ephemeral_workspace`，仅内存、不持久化、连接断开即失效），变更经 `<system-reminder>`（`_drain_folder_note` + `prepend_reminder`）在下一条用户消息告知模型。WS 重连得到全新 bridge 后，前端按 `folderStore` 重放 `add_folder` 恢复后端状态。
- **前端**：侧栏「项目」入口（`onOpenProjects`）打开 `ProjectsPage`（搜索 + 排序 + 卡片，当前项目金描边）；`NewProjectDialog` 选目录 + 命名；composer 底栏 `FolderMenu`（图标 + 数量徽标 + 增减菜单）。原生目录选择器经 Electron `lumi:pick-directory` IPC（`dialog.showOpenDialog`）。

## 模型供应商管理

用户自定义的「连接 + 模型」持久化在 `~/.lumi/providers.json`（明文，`chmod 600`，含 `api_key`），由 `lumi/agents/runtime/provider_store.py` 读写——textual-free，TUI 与 desktop 共享同一份配置。

- **数据模型**：一个 **profile** = 一套连接（`name` / `base_url` / `api_key`）+ 该连接下的一组 `models`；`active` 指向「某 profile 下的某个 model」。协议（OpenAI / Anthropic 客户端）仍由 model 名经 `model_manager.detect_model_type` 自动判定，无需配置。`provider_store` 兼容旧格式（单 `model` 字段、`active` 为字符串 id），读取时自动迁移并把失效 `active` 归位到首个可用模型。
- **运行时生效**：`LumiAgentContext` 增加 `base_url` / `api_key` 两个字段（`state.py`）；`call_model` 经 `_provider_kwargs()`（`nodes.py`）仅在非空时透传给 `create_llm`，空则沿用 env / SDK 默认。`AgentBridge._apply_active()` 把当前 `active` 应用到 context，**下一轮** `call_model` 生效。
- **RPC**：`list_providers`（列全部 profile + active）、`save_provider` / `delete_provider`（增删改，返回刷新后的 `{profiles, active}`）、`set_provider`（切换 active，返回 `{active, model}`）、`test_provider`（用给定连接对模型发最小请求验证可达，15s 短超时、不缓存不重试）。`set/save/delete_provider` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥，避免轮内改掉共享 context。
- **前端**：`SettingsDialog` + `ProvidersPanel` 完成增 / 删 / 改 / 测试；`ModelPicker`（顶栏）做快速切换。
- **TUI 对应**：`/model` 命令打开 `ModelScreen`（`lumi/tui/screens/model_screen.py`）——把「供应商 × 模型」拍平成列表，**仅切换**；增删改在桌面端配置页完成，二者共享 `~/.lumi/providers.json`。

## 上下文用量指示器

composer 右下角（发送键左侧）一粒圆环，实时反映「当前对话占用 / 模型上下文窗口」，落地见 `desktop/src/components/ContextMeter.tsx`。

- **数据**：占用量取最近一次模型调用的 `usage.input_tokens`（含缓存命中部分，即「当前上下文」），由 `App.tsx` 的 `ctxFromUsage` 从 `message.complete` / `turn.complete` 事件提炼写入 `SessionState.ctx`（回合中流式刷新）；窗口取 active 模型的上下文长度——`list_providers` 在每个 profile 附 `context: {model: context_length}`（来自 models.dev catalog，与思考能力 `thinking` 同源 `lookup(m)`），前端按 `activeModel` 派生 `contextWindow`。
- **形态**：默认仅圆环，颜色即档位（绿 `<60%` / 金 `60–85%` / 红 `>85%`，取主题 `--color-success/accent/error`）；点击经 Radix DropdownMenu 向上弹出明细——大进度条 + 已用/总量 + 分项（输入 / 输出 / 缓存命中，取 `input_token_details` / `output_token_details`）+ 当前模型与窗口。临界态（`>85%`）圆环发光呼吸 + 红色「上下文将满」提示条。数据未就绪（无 usage 或窗口未知 = 0）时静默不渲染。

## 定时任务管理

cron 子系统是进程级资源（与会话无关）：serve 在 lifespan 中经 `lumi/agents/cron/runtime.setup_cron()`（TUI 共用的装配工厂）启动调度器，RPC 实现在 `lumi/server/cron_rpc.py`，不经 AgentBridge。内部机制（执行即会话、保留策略、级联删除）见 [`cron.md`](cron.md)。

- **结果广播**：`lumi/server/desktop_delivery.py` 的 `DesktopDelivery` 把任务结果（`cron.result`）与运行状态（`cron.running`）推给所有活跃 WS 连接——wire 信封格式属 server 层，agents 层只定义 `ResultDelivery` 抽象。无连接时不缓存：结果已落 RunLog，重连后经 `list_cron_runs` 查询。
- **前端结构**（`CronPage.tsx` + `App.tsx`）：
  - 侧栏「定时任务」分组（任务名 + 未读角标 + 运行中脉冲点）→ 点击进入**任务会话视图**：主区为最近一次执行的完整对话（composer 可续聊），右侧 `RunsRail` 列历次执行，蓝点 = 未读、点开即消失。
  - 顶部「定时任务」导航入口 → 管理页（卡片网格 + 新建 / 编辑 / 删除 + 详情）。
  - App 持有 cron 数据单一来源（jobs / 未读计数 / 已读集合，后两者持久化 localStorage）；cron 事件广播到每条 WS 连接，前端按 `job_id:started_at` 去重。

## 后台任务中心（drawer）

右侧可开关的后台任务面板，纳管 **bash / agent / workflow** 三类后台任务（`TaskRegistry`
单一注册中心）。引擎 / 工具侧设计见 [`workflow.md`](workflow.md)。

- **实时推送**：`TaskRegistry.set_on_change` 观察者（server 层注册）→ `_on_bg_task_change`
  **~100ms 去抖合并** → 复用 `DesktopDelivery` 广播 `bg_tasks.update`（全量快照，前端按
  thread_id 过滤）。RPC：`list_bg_tasks`（初始拉取）/ `stop_bg_task` / `dismiss_bg_task` /
  `clear_finished_bg_tasks`，stop / dismiss 带会话归属校验。
- **前端结构**（`BgTasksDrawer.tsx` + `App.tsx`）：
  - 头部 `PanelRight` 图标开关（有任务才出现，运行中带脉动金点），默认关。
  - drawer = 一摞**可独立折叠的任务卡片**（kind 图标 + 名称 + 状态光点/勾/叉 + chevron）；
    运行中默认展开、终态默认折叠。workflow 卡片画实时聚合进度（phase + 进度条 + 在跑数）。
  - 清理：终态卡片 hover 出现灰色移除 ✕，或头部「清除已完成」；运行中只能停止。
  - 运行中任务的 Duration 由本地每秒 tick 实时跳（仅面板打开且有任务在跑时计时）。
  - demo `.demos/lumi-bg-drawer-detail.html`。

## 桌面通知

回复完成与等待用户处理的中断（审批 / 提问 / 计划）会触发系统通知，**仅在该会话非当前活动、或窗口未聚焦时**弹出（你正盯着时不打扰）。通知经主进程 `Notification`（`electron/main.cjs`）发出——renderer 的 HTML5 `Notification` 在 macOS dev 下不可靠；点击通知由主进程聚焦窗口并经 `lumi:notify-click` 回传 tag 切到对应会话。判定用 `document.hasFocus()` 而非 `document.hidden`（切到别的应用时窗口仍可见，`hidden` 恒为 false）。

macOS 关窗后应用驻留 Dock，sidecar 保持运行，Dock 唤起（activate）重建窗口后直接复用；其他平台关窗即退出，sidecar 由 `before-quit` 清理。

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
| `desktop/src/components/ContextMeter.tsx` | 上下文用量指示器（圆环 + 明细 popover） |
| `desktop/src/components/CronPage.tsx` | 定时任务管理页 + 任务会话视图 Runs 栏 |
| `desktop/src/components/BgTasksDrawer.tsx` | 后台任务中心 drawer（折叠卡片 + 实时进度） |
| `desktop/src/components/{ProjectsPage,NewProjectDialog,FolderMenu}.tsx` | 项目管理页 + 新建项目 + 添加文件夹菜单 |
| `desktop/src/i18n.ts` | 国际化（中文 / English） |
| `lumi/server/ws.py` | FastAPI WS 端点 + RPC dispatch |
| `lumi/server/projects.py` | 项目清单持久化（`~/.lumi/projects.json`） |
| `lumi/server/protocol.py` | BridgeEvent → wire 序列化 |
| `lumi/server/cron_rpc.py` | 定时任务 RPC 方法实现 |
| `lumi/server/desktop_delivery.py` | cron 结果 → WS 广播投递通道 |
| `lumi/agents/bridge.py` | LangGraph ↔ 前端中立桥接层 |
| `lumi/agents/runtime/provider_store.py` | 模型供应商 profile 持久化（`~/.lumi/providers.json`） |
| `lumi/tui/screens/model_screen.py` | TUI `/model` 模型切换弹窗 |
| `lumi/tui/session_meta.py` | 会话用户元数据 sidecar |
| `protocol/events.json` | 协议单一事实源 |
