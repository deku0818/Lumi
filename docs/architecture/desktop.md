# Desktop 应用架构

Lumi 桌面应用（Electron + TS 前端）的内部实现。前端通过 WebSocket 复用后端的 `AgentBridge`。协议契约见 [`protocol/README.md`](../../protocol/README.md)。

> 现状：dev 模式经 `uv run lumi serve` 拉起 sidecar；打包发行走 `scripts/build-desktop.sh`——PyInstaller 打后端（onedir）经 electron-builder `extraResources` 内嵌进 app，打包版优先用内嵌后端，无则退回 PATH 上的 `lumi`（uv tool install 自装），都没有则作纯远程 client 使用。

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
                          ┌────────────────────▼─────────────────────────┐
                          │  lumi serve (FastAPI, gateway/channels/ws)    │
                          │    每条 WS = 一个 AgentBridge                 │
                          │      → LumiAgent graph (LangGraph)            │
                          └───────────────────────────────────────────────┘
```

- **main 进程**：唯一持有 sidecar 生命周期。sidecar 非主动退出（崩溃/被杀）时同端口自愈重启，renderer 的重连逻辑自动连上。
- **renderer**：纯前端，无 Node 访问（`contextIsolation`）。只通过 preload 暴露的 `getConnection()` 拿到 `ws://127.0.0.1:<port>/ws`。
- **sidecar**：headless FastAPI，启动时不引入 textual（TUI 专属模块在用到时才懒加载）。

## WS / JSON-RPC 帧协议

一条 WS 连接 = 一个会话上下文（独立 `AgentBridge`，可切换 thread）。同一时刻只跑一轮用户流式响应，但所有 RPC（流式与非流式）都 spawn 成独立 task 执行，接收循环持续读帧——流式轮运行期间 `stop` 帧随时可达；需要 `run.lock` 的方法（删会话、切模型等）在后台等锁，不会卡住接收循环。中断（approval/clarify）后等待 `resume`。

```
client → server   {id, method, params}
server → client   {id, result} | {id, error:{message}}        # RPC 响应
server → client   {method:"event", params:<wire event>}       # 流式事件
```

- **RPC 方法**：
  - 流式：`send_message`、`resume`、`run_command`（运行斜杠命令）。
  - 会话：`list_sessions`、`new_session`、`switch_session`、`load_history`、`pin_session`、`rename_session`、`delete_session`。
  - 项目 / 工作目录：`list_projects`、`add_project`、`remove_project`、`rename_project`、`set_workspace`（绑定本会话项目，会话级、不动进程 cwd）、`add_folder` / `remove_folder`（本会话临时目录）。连接 URL 另可带 `?workspace=`，open 握手即把本会话引擎 pin 到该项目。
  - 模型供应商：`list_providers`、`test_provider`、`set_provider`、`save_provider`、`delete_provider`。
  - 定时任务：`list_cron_jobs`、`create/update/delete/toggle_cron_job`、`run_cron_job`、`list_cron_runs`。
  - 其它：`stop`（中止当前流式轮）、`list_commands`（拉取斜杠命令）。
- **wire 事件**：`message.*`、`tool.*`（含 `tool.generating`）、`clarify/approval`、`turn.complete`、`error`，加握手帧 `gateway.ready` 与 cron 广播 `cron.result` / `cron.running`（进程级，不属于任何会话）。

事件名与方法名都来自 [`protocol/events.json`](../../protocol/events.json) 单一事实源：TS 端 import derive 类型，Python 端由 `tests/server/test_protocol_contract.py` 锁住一致性。

## AgentBridge 复用

`lumi/gateway/bridge.py` 是中立桥接层（TUI 与 desktop 共用），把 LangGraph 的原始事件封装为干净的 `BridgeEvent` 流。`EventKind` 成员值直接采用对外 wire 命名（`namespace.verb`），`lumi/gateway/protocol.py` 只做 `BridgeEvent → {type, session_id, payload}` 的 payload 重组，无额外映射层。

每条 WS 连接独立持有一个 `AgentBridge` 实例，`current_thread_id` 即该连接当前会话。多会话并发时各连接互不阻塞。

进程级共享资源的边界：连接干净关闭（无活跃轮）时 `bridge.close()` 只清理该 bridge 自身；MCP 子进程、
shell / 后台任务会话等全局单例由 `shutdown_shared_runtime()` 在 lifespan shutdown
时统一关闭（TUI 则在 quit 时调用）。后台任务的完成通知按归属认领——任务注册时经
`ContextVar` 捕获所属 thread_id，各连接的通知轮询只取走归属自己当前 thread（或无归属）
的通知，不会把别的会话的任务结果注入本会话。

## 断连续接（会话与 WS 解耦）

WS 断开时若会话仍有**活跃 / 挂起轮**（典型：挂在工具审批 / ask 上），不直接 aclose，而是把会话连同其 `AgentBridge` / parked turn / `ApprovalBroker` / 挂起 Future **原地留存**，等同 thread 的 WS 重连接回——renderer 重载（Ctrl+R）、网络抖动、休眠唤醒后，审批仍在、运行轮继续。**无需 checkpoint 重放，Future 一直在内存里**。

- **`lumi/gateway/session_registry.py`**：进程内 `thread_id → 已 detached 的 GatewaySession`。只存 detached 会话；干净关闭 / TTL 到期即移除。
- **`GatewaySession.detach()`**（断开且 `should_detach()`）：摘掉死 channel（换 `_NoopChannel`，断连期事件丢弃）、停掉通知轮、登记进 registry、挂 `_DETACH_TTL_SECONDS`（8h 兜底回收）。`should_detach()` 排除**纯后台通知 meta 轮**（无用户在等），除非它自身正挂着审批。
- **`GatewaySession.reattach(channel)`**（同 thread 重连）：取消 TTL、换上新 channel、重注册广播、重起通知轮、重发 `gateway.ready`（带 `running`）并把挂起的审批 / 澄清卡片再推一遍（`bridge.pending_approval_events()`）。
- **前端配合**（`desktop/src/`）：每会话一条独立 WS（`connsRef[key]`，`key = sessionKey(backend, thread)` 复合键——IM channel 的 thread_id 按 chat_id 确定性派生，同一飞书群在本地/远程两台 server 上 thread 同名，只用 thread 当键会让两条会话在 client 里塌缩成一条，故会话身份一律带机器维度；切到别的会话不碰本会话连接）；连接 URL 带 `?thread=`（含 Ctrl+R 重载后点回会话的初次连接），后端据此在建空 bridge 前先认领回 detached 会话；`approval` / `clarify` 改按 `approval_id` 排队（渲染队首、逐个出队，并发审批不互相覆盖），重连重发按 `approval_id` 去重；`running` 据 `gateway.ready.running` 复位（断连时 `sendMessage` 的 catch 已置 false）。
- **边界**：仅 sidecar 存活的断连可救（**Case 1**）；**后端进程重启（Case 2）** 不幸存——in-memory Future 随进程消失，刻意不做落盘（见 [approval-inflight.md](./approval-inflight.md)）。`switch_session` 切回**同 thread**且有活跃轮时不收尾本轮（早返回），避免误杀正挂着的审批。

## 子代理事件归属（多层委派）

子代理（`agent` 工具）可多层委派（`agents.max_delegation_depth` 默认 3，主 agent 为第 0 层）。前端按 `parent_run_id` 把子代理事件聚成轻量分组卡片展示——**该标记仅用于展示，不参与中断的 interrupt/resume，故归属错误不影响功能**。`bridge/core.py` 维护「活跃 agent 工具 run_id → 其 `parent_ids`」的映射 `_active_agent_runs`（`dict[str, list[str]]`，`on_tool_start name=="agent"` 时存入、`on_tool_end`/`on_tool_error` 时移除），两条归属路径：

- **流式事件**（`_resolve_subagent_parent`）：从事件 `parent_ids`（langchain 的 root→直接父序）正序取首个仍活跃的 agent run。深层（孙及更深）活动据此**确定性归并到主 agent 直接派生的顶层子代理**；并行兄弟各自的事件也按各自 `parent_ids` 正确区分。
- **中断事件**（`_subagent_marker`，ask / tool_approval）：`aget_state` 拿到的是主图状态、payload 无 `parent_ids`，故 `_active_agent_runs` 存下每个活跃 run 的 `parent_ids`，据此判断祖先关系——「唯一顶层子代理」（其 `parent_ids` 不含任何活跃 run）即归属目标，与流式路径同口径。

**已知限制（均为展示层、功能零影响）：**

1. **深层嵌套展平**：孙 / 曾孙活动全部挂在顶层子代理卡片下，看不到层级树。属刻意取舍——子代理经 `create_agent(checkpoint=None)` 无 checkpointer，主图不保留其嵌套 task 结构。
2. **并行兄弟 + 中断无法精确归属**：同一轮并行委派 ≥2 个顶层子代理、其中之一触发 ask / tool_approval 时，因中断 payload 无 `parent_ids`，无法判断「来自哪个并行兄弟」。根因：子代理 checkpointer-less 跑在 `agent` 工具这个不透明边界内，中断冒泡到主图 `ToolExecutor` 时已丢失结构链接。
   - **已实现的兜底**（仅 `bridge/core.py`）：`_subagent_marker` 发现 ≥2 个并行顶层子代理同时活跃时，`parent_run_id` 返回空串——审批 / 提问卡片挂到主 agent，而非自信地错挂某个兄弟（**仍能正常看到并回答，回答也正确生效**，只是不归到具体子代理分组）。单链委派（祖→孙）不受影响，仍精确归到唯一顶层。
   - **真正根治**：把子代理身份从 spawn 一路串到中断 payload，再在 bridge 关联回前端的 `run_id` 卡片，跨 `agent.py` / `ask.py` / `nodes.py`（approval）/ `bridge` / 前端，且需对抗「子代理无 checkpointer」的省开销设计——暂留待需。

## 会话管理

会话列表由 LangGraph checkpoint 派生（`lumi/sessions/session_store.list_sessions`），但「置顶」「自定义标题」是用户施加的、不存在于 checkpoint 中的元数据，单独持久化：

- **`lumi/sessions/session_meta.py`** — JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），按 `thread_id` 存 `pinned`/`title`，IM 渠道会话的 `channel_title`（群名/私聊对方姓名，入站时自动同步）/`channel_kind`（group/p2p），以及模型生成的 `auto_title`（+定稿标记 `auto_title_final`），仅写非默认值且内容不变不写盘。textual-free，可在 headless 服务直接使用。
- **`list_sessions` RPC** — 合并 sidecar 元数据后注入 `title`（手动重命名 > 渠道自动名 > 自动生成标题）/`pinned`，并按 thread 前缀标注 `channel`/`channel_kind`（`gateway/session._channel_of` 是渠道判定单点，前端只消费 wire 字段），置顶项稳定排到最前。
- **标题自动生成**（`lumi/gateway/titler.py`，对齐 claude-code 的 sessionTitle 机制）— desktop 会话第 1 条可见用户消息发出时即后台生成（不等本轮跑完），第 3 条时用对话尾部 1000 字符再生成一次纠偏后定稿；模型来自 providers 分区的 `titler` 指针（`set_titler` RPC / 设置→模型面板配置，未配则跟随会话 active 模型）。完成经 `session.title` 事件广播，前端就地更新侧栏；手动重命名永远优先（触发与写入前双重检查）。IM 渠道会话有 `channel_title`，不生成。
- **删除** — `delete_session` 经 `bridge.delete_thread()` 一并清理两类 checkpoint：LangGraph 会话（`LumiAgent.adelete_thread`）+ 文件级 checkpoint（`checkpoint.delete_thread_checkpoint`），再删除 sidecar 元数据条目。渠道会话删除前持渠道侧运行锁（`ChannelManager.thread_lock`），避开在途轮把删掉的历史写回。

前端 `Sidebar` 每行 hover 出现 `⋮` 菜单（置顶 / 重命名 / 删除）；删除走二次确认弹窗（`ConfirmDialog`），删除当前会话时自动另开新会话顶上。

**IM 渠道会话在 desktop 只读旁观**：飞书等渠道会话在「全部」树里按机器级「飞书 · 绑定项目」分组（A2 方案，`channel` 字段驱动，不进项目组）；打开后顶部渠道横幅（群名 / 审批模式 / 绑定项目 / 直达渠道设置），输入区替换为只读提示。只读在服务端兜底（流式方法对渠道 thread 直接拒绝；后台通知轮对渠道 thread 不消费）——desktop 与渠道 `BridgePool` 各持独立 bridge/锁，写入会绕过渠道的会话串行化。渠道跑完一轮广播 `channel.activity`，desktop 只刷该机器会话列表、正在旁观则重载历史（切回旁观会话也强制重拉）。消息级时间戳在 `bridge.stream_response` 统一落库（`additional_kwargs["lumi"].ts`，渠道另带 per-消息 `items`），气泡头渲染「发送者 · 时刻」。

## 项目与工作目录

**项目 = 工作目录，随会话绑定**（不再是进程级单一 cwd）。会话列表按 checkpoint metadata 的 `workspace_dir` 过滤分组。

- **会话级项目绑定**：每条 WS 连接 = 一个 bridge / 引擎，引擎在 `initialize` 时直接 pin 到本会话项目——open 握手经连接 URL 的 `?workspace=` 携带（与 `?token=` 同机制），`bridge.initialize(project_dir=...)` 据此新建权限引擎、构造本项目 config hooks、写 checkpoint 元数据。**不动进程 `os.chdir`**，故同进程多会话各绑各项目、并发互不影响。`bridge.workspace_dir` 取本引擎 `project_dir`（无引擎退回 cwd），是会话项目的单一来源（`gateway.ready` / 元数据 / `system_info` 注入都据此）。
- **per-run 授权 / hooks 注入**：filesystem/bash 工具不持有引擎，故 bridge 在每轮 `_stream` 起点经 contextvar 注入本会话引擎的授权目录来源（`set_run_authorized_source_for`）与 config hooks（`set_run_config_hooks`），cron 在 `_invoke_agent` 起点同理；各 run 按 contextvar 隔离，不被并发会话重建进程全局所清洗。详见 [permissions.md](permissions.md) / [hooks.md](hooks.md)。
- **`set_workspace`（会话级改项目）**：只 rebase 本 bridge 引擎、重载本会话 config hooks、更新元数据、重置本会话当前 thread 的持久 shell——**不 chdir、不影响其它会话**。原 `_active_bridges` 进程级 rebase-all 已随 cwd 进程级模型一并移除。前端「打开项目」= 经 open 握手开一条绑定到该项目的新会话（不再先 `set_workspace` 改进程态）；`set_workspace` RPC 主要用于原地改当前会话项目（及未来复用单连接的非 desktop client）。`set_workspace` / `add_folder` / `remove_folder` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥。
- **项目清单**：纯手动登记，持久化在 `~/.lumi/lumi.json` 的 `projects` 分区（`lumi/gateway/projects.py`，复用 `_atomic_write_json`），按 `last_used` 降序。`list_projects` 返回 `{projects, current}`（current = 本会话项目）；`add_project`（缺省用目录末端名，重复添加保留用户重命名）/ `remove_project`（只删条目，不动磁盘）/ `rename_project`；`set_workspace` 成功后经 `touch_project` 刷新 `last_used`。
- **添加文件夹（本会话临时）**：`add_folder` / `remove_folder` 把目录临时加进**本连接**引擎的 `_ephemeral_workspaces`（引擎独立字段、仅内存、与会被 `reload()`/`rebase()` 从磁盘重载的 `_config.workspaces` 分离，故跨配置重载 / 项目切换存活；连接断开即失效），变更经 `<system-reminder>`（`_drain_folder_note` + `inject_text_into_message` 前置注入块，带 `injected_prefix` 计数）在下一条用户消息告知模型。WS 重连复用同一 URL（含 `?workspace=`）使新 bridge 重新 pin，前端再按 `folderStore` 重放 `add_folder`。
- **持久 shell 按会话 / 子代理隔离**：bash 的持久 shell 不再全进程共用一个，而是按 `current_thread_id` 分（会话私有，`cd`/env 不串别的会话），断连（`bridge.close`）/ 删会话（`delete_thread`）时回收，避免长跑 serve 累积孤儿进程。子代理（`agent` 工具）经 `shell_session.run_with_shell` 在 `copy_context` 副本里用专属 key 跑、拿独立 shell（`cd` 不污染父 / 兄弟、用完即弃），不继承父 shell 状态（在项目根 fresh 起）。
- **前端**：侧栏「项目」入口（`onOpenProjects`）打开 `ProjectsPage`（搜索 + 排序 + 卡片，当前项目金描边）；`NewProjectDialog` 选目录 + 命名；composer 底栏 `FolderMenu`（图标 + 数量徽标 + 增减菜单）。原生目录选择器经 Electron `lumi:pick-directory` IPC（`dialog.showOpenDialog`）。

## 模型供应商管理

用户自定义的「连接 + 模型」持久化在 `~/.lumi/lumi.json` 的 `providers` 分区（明文，`chmod 600`，含 `api_key`），由 `lumi/agents/runtime/provider_store.py` 读写——textual-free，desktop 前端经 RPC 读写。

- **数据模型**：一个 **profile** = 一套连接（`name` / `base_url` / `api_key`）+ 该连接下的一组 `models`；`active` 指向「某 profile 下的某个 model」。协议（OpenAI / Anthropic 客户端）仍由 model 名经 `model_manager.detect_model_type` 自动判定，无需配置。`provider_store` 兼容旧格式（单 `model` 字段、`active` 为字符串 id），读取时自动迁移并把失效 `active` 归位到首个可用模型。
- **运行时生效**：`LumiAgentContext` 增加 `base_url` / `api_key` 两个字段（`state.py`）；`call_model` 经 `_provider_kwargs()`（`nodes.py`）仅在非空时透传给 `create_llm`，空则沿用 env / SDK 默认。`AgentBridge._apply_active()` 把当前 `active` 应用到 context，**下一轮** `call_model` 生效。
- **RPC**：`list_providers`（列全部 profile + active）、`save_provider` / `delete_provider`（增删改，返回刷新后的 `{profiles, active}`）、`set_provider`（切换 active，返回 `{active, model}`）、`test_provider`（用给定连接对模型发最小请求验证可达，15s 短超时、不缓存不重试）。`set/save/delete_provider` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥，避免轮内改掉共享 context。
- **前端**：`SettingsDialog` + `ProvidersPanel` 完成增 / 删 / 改 / 测试；`ModelPicker`（顶栏）做快速切换。

## 上下文用量指示器

composer 右下角（发送键左侧）一粒圆环，实时反映「当前对话占用 / 模型上下文窗口」，落地见 `desktop/src/components/ContextMeter.tsx`。

- **数据**：占用量取最近一次模型调用的 `usage.input_tokens`（含缓存命中部分，即「当前上下文」），由 `App.tsx` 的 `ctxFromUsage` 从 `message.complete` / `turn.complete` 事件提炼写入 `SessionState.ctx`（回合中流式刷新）；窗口取 active 模型的上下文长度——`list_providers` 在每个 profile 附 `context: {model: context_length}`（来自 models.dev catalog，与思考能力 `thinking` 同源 `lookup(m)`），前端按 `activeModel` 派生 `contextWindow`。
- **形态**：默认仅圆环，颜色即档位（绿 `<60%` / 金 `60–85%` / 红 `>85%`，取主题 `--color-success/accent/error`）；点击经 Radix DropdownMenu 向上弹出明细——大进度条 + 已用/总量 + 分项（输入 / 输出 / 缓存命中，取 `input_token_details` / `output_token_details`）+ 当前模型与窗口。临界态（`>85%`）圆环发光呼吸 + 红色「上下文将满」提示条。数据未就绪（无 usage 或窗口未知 = 0）时静默不渲染。

## 定时任务管理

cron 子系统是进程级资源（与会话无关）：serve 在 lifespan 中经 `lumi/agents/cron/runtime.setup_cron()`（TUI 共用的装配工厂）启动调度器，RPC 实现在 `lumi/gateway/cron_rpc.py`，不经 AgentBridge。内部机制（执行即会话、保留策略、级联删除）见 [`cron.md`](cron.md)。

- **结果广播**：`lumi/gateway/desktop_delivery.py` 的 `DesktopDelivery` 把任务结果（`cron.result`）与运行状态（`cron.running`）推给所有活跃 WS 连接——wire 信封格式属 server 层，agents 层只定义 `ResultDelivery` 抽象。无连接时不缓存：结果已落 RunLog，重连后经 `list_cron_runs` 查询。
- **前端结构**（`CronPage.tsx` + `App.tsx`）：
  - 侧栏「定时任务」分组（任务名 + 未读角标 + 运行中脉冲点）→ 点击进入**任务会话视图**：主区为最近一次执行的完整对话（composer 可续聊），右侧 `RunsRail` 列历次执行，蓝点 = 未读、点开即消失。
  - 顶部「定时任务」导航入口 → 管理页（卡片网格 + 新建 / 编辑 / 删除 + 详情）。
  - App 持有 cron 数据单一来源（jobs / 每任务未读 run 集合〔按 run 的 `thread_id` 记，看一条消一条〕/ 已读集合，后两者持久化 localStorage）；`cron.result` 经会话连接与控制连接双路径消费（远程机器通常只有控制连接，靠它兜底未读），前端按 `job_id:started_at` 去重，进任务视图时按当前可见 run 对账未读（被保留策略清理/超窗口而够不着的 tid 剔除，避免徽标卡死）。

## 后台任务中心（drawer）

右侧可开关的后台任务面板，纳管 **bash / agent / workflow** 三类后台任务（`TaskRegistry`
单一注册中心）。引擎 / 工具侧设计见 [`workflow.md`](workflow.md)。

- **实时推送**：`TaskRegistry.set_on_change` 观察者（server 层注册）→ `_on_bg_task_change`
  **~100ms 去抖合并** → 复用 `DesktopDelivery` 广播 `bg_tasks.update`（本机进程级快照，前端
  按机器分段替换合并、按 thread + backend 过滤展示——同名飞书群跨机不串）。RPC：`list_bg_tasks`（初始拉取）/ `stop_bg_task` / `dismiss_bg_task` /
  `clear_finished_bg_tasks`，stop / dismiss 带会话归属校验。
- **前端结构**（`BgTasksDrawer.tsx` + `App.tsx`）：
  - 头部 `PanelRight` 图标开关（有任务才出现，运行中带脉动金点），默认关。
  - drawer = 一摞**可独立折叠的任务卡片**（kind 图标 + 名称 + 状态光点/勾/叉 + chevron）；
    运行中默认展开、终态默认折叠。workflow 卡片画实时聚合进度（phase + 进度条 + 在跑数）。
  - 清理：终态卡片 hover 出现灰色移除 ✕，或头部「清除已完成」；运行中只能停止。
  - 运行中任务的 Duration 由本地每秒 tick 实时跳（仅面板打开且有任务在跑时计时）。
  - demo `.demos/lumi-bg-drawer-detail.html`。

## 输入栏文件附件

`+` 按钮 / 拖拽 / 粘贴均经 `addFiles()` 加入附件，按 MIME 分两路：

- **图片**：`FileReader` 读成 base64 data URL，发送时拆为 Anthropic 原生 `image` 块（后端按模型再转 OpenAI/Bedrock），气泡里渲染缩略图。
- **其它文件（PDF / 视频 / docx…）**：经 Electron `webUtils.getPathForFile`（`preload.cjs` 暴露；Electron 33 起 `File.path` 已移除）取**绝对路径**，发送时以 `<attached-file>路径</attached-file>` 文本块注入消息，Agent 用 `read` 工具按路径读取。**不预授权**——能否读取交给现有权限引擎 / 工作区边界。取不到路径（如非文件系统来源的拖拽）不静默吞掉，经 toast 提示失败。

`<attached-file>` 是纯模型侧约定（agent 用 read 读取路径）：前端经 wire `files` 参数只发路径数组，`bridge._build_user_message` 统一拼标签块注入 content（`injected_prefix` 计数）并把 `{path, name}` 写进显示声明 `lumi.items` 的 `files`；历史恢复的文件胶囊直接读 items，不解析正文。标签名 `ATTACHED_FILE_TAG` 定义在 `lumi/utils/constants.py`。气泡内文件渲染成品牌金描边胶囊（仅文件名 + hover tooltip）。

## present_files 文件预览

Agent 产出文件后调 `present_files` 工具把它们呈现给用户。后端 `lumi/agents/tools/providers/present_files.py` 只做本地元数据收集（无对象存储）：单次 `os.stat`（避免 isfile→getsize 的 TOCTOU）+ `mimetypes` 猜 MIME + 按扩展名分类 `kind`，返回 `{path, name, mime_type, size, kind}` 的 JSON 列表（不存在 / 非常规文件返回 `{path, error}`，顺序与输入一致）。这是常驻工具，走现有 `tool.start/complete` 事件流——**协议无新增事件**，前端按工具名 `present_files` 特化渲染。

- **边界约束**：`present_files` 的 `filepaths`（列表）经 `boundary.py` 的 `_PATH_LIST_ARG_KEYS` 逐项提取参与工作区边界检查，与 `bash`/`filesystem` 同等受限——不是绕过权限读任意文件的后门。
- **聊天卡片**（`FileCards`，`desktop/src/components/PresentedFiles.tsx`）：present_files 在 `groupItems` 里单独成段（不并入灰色工具组），解析一次 JSON 缓存在段上（随 `items` 记忆化）。每个文件渲染成单色类型图标卡片（按 `kind` 选 lucide 字形，不上彩色）+ 文件名 + 类型/大小 + 「Show in Folder」按钮。**卡片层不加载任何文件字节**。
- **右侧停靠预览面板**（`PreviewPanel`）：点卡片在聊天区右侧滑出（可拖宽、持久化 `lumi-preview-width`、Esc/✕ 关、切会话自动关）。打开时经 `lumi:path-exists` 探测一次存在性：缺失 → `MissingState`（文件已移动/改名/删除 + 重新检查）；存在 → 按类型渲染。
- **预览分型**：图片 / PDF / HTML 经 `lumi-file://` 协议内嵌（`<img>`/`<iframe>`），文本 / Markdown 经 `fetch().text()` 读取后渲染；视频 / 音频 / Office / 未知类型 → 统一 `NoPreview`（提示 + 「用系统应用打开」）。**大小门控**：`> 50MB`（UI 阈值，用元数据 `size` 判定不读文件）走 `NoPreview` 提示「文件较大」。**HTML 安全**：`sandbox="allow-scripts"`（不带 `allow-same-origin`）——脚本可运行让交互页正常，但 iframe 是 opaque origin，对 `lumi-file` 的 fetch 跨域被拦，恶意页读不到本地文件外传。
- **`lumi-file://` 协议**：`electron/main.cjs` 以 `protocol.registerSchemesAsPrivileged` + `protocol.handle` 实现，让 renderer 在 http origin 下安全引用本地文件（绕过 `file://` 限制）。URL 形如 `lumi-file://local/<abs-path>`（固定 host=local，自定义 standard scheme 不允许空 host；各路径段 `encodeURIComponent`）。handler 先 `stat`，`> MAX_SERVE_BYTES`(128MB) 返 413（兜底防超大文件读进内存撑爆主进程），否则按 `PREVIEW_MIME` 设 content-type 返回。
- **IPC**：`lumi:open-path`（`shell.openPath`）/ `lumi:reveal-path`（`shell.showItemInFolder`）/ `lumi:path-exists`（异步 `fs.promises.access`，避免离线网络盘同步阻塞主进程），经 `preload.cjs` 暴露为 `window.lumi.{openPath,revealInFolder,pathExists}`。

## 应用内通知（toast）

`desktop/src/components/Toast.tsx` 提供可复用的轻量通知通道：模块级 store（pub/sub）+ 根部挂一次的 `<ToastHost/>`，任意模块 `import { toast }` 后调 `toast.error/success/info(msg)` 即可，无需 context / prop 透传。顶部居中细条幅，按 kind 上语义色，下拉淡入 + 自动消失（带退场过渡），多条纵向堆叠。与「桌面通知」区分：toast 是前台即时反馈，系统通知用于会话非活动 / 窗口失焦时。

## 桌面通知

回复完成与等待用户处理的中断（审批 / 提问）会触发系统通知，**仅在该会话非当前活动、或窗口未聚焦时**弹出（你正盯着时不打扰）。通知经主进程 `Notification`（`electron/main.cjs`）发出——renderer 的 HTML5 `Notification` 在 macOS dev 下不可靠；点击通知由主进程聚焦窗口并经 `lumi:notify-click` 回传 tag 切到对应会话。判定用 `document.hasFocus()` 而非 `document.hidden`（切到别的应用时窗口仍可见，`hidden` 恒为 false）。

macOS 关窗后应用驻留 Dock，sidecar 保持运行，Dock 唤起（activate）重建窗口后直接复用；其他平台关窗即退出，sidecar 由 `before-quit` 清理。

## 国际化（i18n）

`desktop/src/i18n.ts` 提供 `useI18n()` hook（`t` / `lang` / `setLang`），支持中文 / English，偏好存 localStorage（`lumi-lang`）。所有 UI 文案经 `t(key)` 取用，不硬编码。

## 界面字体

设置→通用页可让用户从**本机已装字体**里挑界面字体并调正文字号，偏好存 localStorage（`lumi-font`，`{family, size}` JSON），落地见 `desktop/src/font.ts`（`useUiFont` hook）+ `desktop/src/components/FontPicker.tsx`。与 `theme.ts` 同构：运行时把覆写写到 `document.documentElement` 的 CSS 变量上。

- **覆盖机制**：默认字体栈是 `index.css` 的 `--font-fallback`（**唯一真相**）；`@theme` 的 `--font-sans` = `var(--ui-font, var(--font-fallback))`，故选字体只需把 `--ui-font` 设到根元素，`body` 与所有 `font-sans` / `font-heading` 工具类（含 Dialog 标题）一并跟随。字号同理：`body` 用 `font-size: var(--ui-font-size, 13px)`，仅非默认字号才写 `--ui-font-size`，默认时移除让 CSS 回落。`font.ts` 设字体时走 `cssFamily()` 转义族名（防引号/反斜杠破坏声明），并追加 `var(--font-fallback)` 保证西文字体缺中文字形时回退。
- **本机字体枚举**：经浏览器 `queryLocalFonts()`（Local Font Access API）取族名去重排序。**权限收口**：`electron/main.cjs` 的 `setPermissionRequestHandler` / `setPermissionCheckHandler` **仅放行 `local-fonts`**，其余权限（camera / mic / geolocation / clipboard…）一律拒绝。该 API 需 user activation，故首次枚举在点击打开下拉的处理器内同步触发（非 effect），避免打包版丢激活；不可用 / 被拒时静默返回空并在面板提示「无法访问本机字体」。列表渲染封顶 `MAX_VISIBLE` 行（多出靠搜索收窄），避免上百字体一次性挂载造成开屏卡顿。

## 可调宽边栏

三栏布局（左侧会话栏 + 右侧后台任务栏 / 任务执行记录栏）均可拖拽调宽，各自宽度存 localStorage（`lumi-sidebar-width` / `lumi-bg-width` / `lumi-runs-width`），越界或脏值回退默认值。统一封装在 `desktop/src/components/ResizeHandle.tsx`：`useResizableWidth(key, def, min, max)` 是单一事实源（lazy-init + 自带边界钳制的 setter + useEffect 持久化，与 `font.ts` / `theme.ts` 同构），`<ResizeHandle>` 作为 flex 兄弟节点的拖拽分隔条（`edge` 决定加宽方向）。拖拽期间给 `body` 挂 `resizing-col` 类，全局停用过渡并统一 `col-resize` 光标，让边栏即时跟手（亦压制 `BgTasksDrawer` 的开关动画，松手恢复）。

## 关键文件

| 文件 | 职责 |
|---|---|
| `desktop/electron/main.cjs` | sidecar 生命周期、窗口、端口分配 |
| `desktop/src/gateway.ts` | WS JSON-RPC 客户端（指数退避自动重连，超 `MAX_RETRY` 转 `failed` 态等用户手动重连；`setUrl` 支持改址重连；URL 可带 `?workspace=` open 握手 pin 项目） |
| `desktop/src/App.tsx` | 会话状态机、事件路由、聊天流渲染 |
| `desktop/src/components/Sidebar.tsx` | 会话列表 + 右键菜单 + 内联重命名 |
| `desktop/src/components/ResizeHandle.tsx` | 边栏拖拽调宽（`useResizableWidth` hook + 分隔条，宽度持久化） |
| `desktop/src/components/{SettingsDialog,ProvidersPanel,ModelPicker}.tsx` | 模型供应商配置 + 快速切换 |
| `desktop/src/components/ContextMeter.tsx` | 上下文用量指示器（圆环 + 明细 popover） |
| `desktop/src/components/Toast.tsx` | 可复用应用内轻量通知通道（`toast.error/success/info`） |
| `desktop/src/components/PresentedFiles.tsx` | present_files 文件卡片 + 右侧停靠预览面板（含缺失态 / 大小门控 / `lumi-file://` URL） |
| `lumi/agents/tools/providers/present_files.py` | present_files 工具（本地元数据收集，无对象存储） |
| `desktop/src/{font.ts,components/FontPicker.tsx}` | 界面字体偏好（本机字体枚举 + 字号）+ CSS 变量覆写 |
| `desktop/src/components/CronPage.tsx` | 定时任务管理页 + 任务会话视图 Runs 栏 |
| `desktop/src/components/BgTasksDrawer.tsx` | 后台任务中心 drawer（折叠卡片 + 实时进度） |
| `desktop/src/components/{ProjectsPage,NewProjectDialog,FolderMenu}.tsx` | 项目管理页 + 新建项目 + 添加文件夹菜单 |
| `desktop/src/i18n.ts` | 国际化（中文 / English） |
| `lumi/gateway/channels/ws.py` | FastAPI WS 端点 + RPC dispatch |
| `lumi/gateway/projects.py` | 项目清单持久化（`~/.lumi/lumi.json` 的 `projects` 分区） |
| `lumi/gateway/protocol.py` | BridgeEvent → wire 序列化 |
| `lumi/gateway/cron_rpc.py` | 定时任务 RPC 方法实现 |
| `lumi/gateway/desktop_delivery.py` | cron 结果 → WS 广播投递通道 |
| `lumi/gateway/bridge.py` | LangGraph ↔ 前端中立桥接层 |
| `lumi/agents/runtime/provider_store.py` | 模型供应商 profile 持久化（`~/.lumi/lumi.json` 的 `providers` 分区） |
| `lumi/sessions/session_store.py` | 会话列表从 checkpoint 派生 |
| `lumi/sessions/session_meta.py` | 会话用户元数据 sidecar |
| `protocol/events.json` | 协议单一事实源 |
