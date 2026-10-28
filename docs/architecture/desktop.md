# Desktop 应用架构

Lumi 桌面应用（Electron + TS 前端）的内部实现。前端通过本地 WebSocket 或原生 SSH 数据通道复用后端的 `AgentBridge`。协议契约见 [`protocol/README.md`](../../protocol/README.md)。

> 现状：dev 模式经 `uv run lumi serve` 拉起 sidecar；打包发行走 `scripts/build-desktop.sh`——PyInstaller 打后端（onedir）经 electron-builder `extraResources` 内嵌进 app，打包版优先用内嵌后端，无则退回 PATH 上的 `lumi`（uv tool install 自装），都没有则作纯远程 client 使用。

> ⚠️ **打包版与 dev 的两处环境差异**（都只在打包 + GUI 启动时暴露，dev 模式一切正常，故极难复现）：
> 1. **PATH** — GUI 启动的进程只继承 launchd 默认 PATH，读不到 shell rc 里的 nvm / `~/.local/bin`。`main.cjs` 的 `sidecarPath()` 借登录 shell（`$SHELL -ilc`）取回真实值，**与当前 PATH 合并去重**（rc 是从头求值，取不到终端里已激活的 venv / direnv）后再拉 sidecar，否则外部命令（`lark-cli` / `gh` / dev 的 `uv`）一律 ENOENT。求值是异步的且缓存 Promise，`whenReady` 一开始就 kick off，与建窗重叠——同步做会连主进程一起冻住（窗口画得出来但 IPC 全部排队）。
> 2. **CA 证书** — 冻结产物里 OpenSSL 的默认 CA 路径指向构建机，本机不存在 → ssl 默认上下文零 CA。`cli._ensure_ca_bundle()` 回退 certifi。注意故障是**局部**的：requests/httpx 显式用 certifi 不受影响，只有走默认上下文的连接（如飞书 WS）失败，症状是渠道永远「连接中」而日志无异常。

---

## 本地进程结构

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

- **main 进程**：唯一持有 sidecar 生命周期。sidecar 非主动退出（崩溃/被杀）时同端口自愈重启，renderer 的重连逻辑自动连上。单实例锁（双开时聚焦已有窗口）；sidecar 以 stdin 管道拉起并传 `--exit-with-parent`——主进程无论如何死亡（含崩溃/强杀），sidecar 读到 stdin EOF 数秒内自退，杜绝孤儿进程与新实例抢同一 checkpoint 数据库（读写悬挂表现为「会话打不开」）。正常退出（含装更新前）则经同一管道写一行 `shutdown`，sidecar 借 uvicorn 自己的停机流程收尾（先 drain 在跑的轮，checkpoint 停在完整边界），主进程最多等 5 秒、超时强杀后再退。
- **renderer**：纯前端，无 Node 访问（`contextIsolation`）。只通过 preload 暴露的 `getConnection()` 拿到 `ws://127.0.0.1:<port>/ws`。
- **sidecar**：headless FastAPI（`lumi serve`）。

## 原生 SSH 远端（0.3）

桌面远端只登记 SSH 身份，不接收服务 URL/token。`ssh-ipc.cjs` 管理主机配置、认证问题队列与受控会话 IPC；`ssh-manager.cjs` 管理系统 OpenSSH、临时密钥、取消与自动恢复。`ssh-auth.cjs` 准备私有目录和 askpass 认证代理，认证询问使用私有 Unix socket，Windows 桌面使用命名管道。`remote-files.cjs` 负责文件读取、Range 响应及主 frame 访问限制。

`ssh-config.cjs` 从用户配置与 Include 枚举明确主机别名；仅在选中主机后由 `ssh -G` 解析有效配置。SSH config 不被应用改写，密码、口令和私钥内容不进入普通连接配置、模型上下文或远端 Lumi。主动保存的私钥仅留在本机应用专用目录，文件 0600。

桌面仅附带 stdlib bootstrap，不附带远端 Python wheel；开发与打包版均由远端通过 uv 从 PyPI 安装 `lumi-harness==<桌面版本>`，不构建或上传本地 wheel。默认数据目录保持 `~/.lumi`。`lumi/remote/bootstrap.py` 经 SSH 准备 `<data>/remote-runtime/` 下的版本环境并安装 systemd 用户服务或 LaunchAgent。`lumi.remote.daemon` 持有生命周期实例锁，监听权限 0600 的 `agent.sock`，不创建 TCP 监听器；重复启动必须先取得锁，不能破坏已有 socket。旧 0.2 服务和配置没有兼容或自动迁移路径。

连接时复用相同版本；远端发布版本较旧时，在 bootstrap 锁内先安装并确认目标版本，再停止旧服务、切换并验证新服务。版本按数字逐段比较，并在取得锁后再次检查；远端较新时提示更新桌面，不自动降级，无法比较的非正式版本也不自动切换。安装完成标记与实际包版本共同决定环境是否可复用，中途失败的安装会重试。安装失败不停止旧服务；切换失败时尝试恢复旧服务。服务重启会中断未完成任务，不具备跨进程自动续跑能力。PyPI 版本可用性只在桌面发布前检查；不可用时不上传桌面 Release 产物。运行时直接执行 uv 安装并返回原始错误，不额外请求 PyPI JSON API。本地运行未发布版本时也遵循同一安装规则。

每台机器一条长连接 SSH 启动 `lumi.remote.attach`，将 stdin/stdout 原样转接 Unix socket。`remote-wire.cjs` 与 `lumi.remote.protocol` 使用 4 字节大端长度前缀的 JSON 帧，最大 16 MiB。传输握手协议为 1，包含实际运行版本、PID、数据目录与程序路径，桌面与远端版本必须相同；Gateway 业务协议仍为 2。通道 id 将控制连接和多会话复用在同一字节流中；`SshSocket` 让 renderer Gateway 继续使用既有 RPC、事件与断连续接语义。本地 sidecar 保持 WebSocket 接口。

renderer 导航、销毁或取消时释放其通道；活跃会话由既有 registry 暂存，重连按 thread 续接。SSH 断开不会停止远端常驻进程，也不自动重发副作用请求。上传与文件读取分块走同一通道，每块最多 1 MiB；断连清理未完成上传。`lumi-remote:` 协议以流方式提供主界面的远端预览与 Range 读取，子 frame 禁止调用。HTML 内容由主 frame 取回后在隔离 blob iframe 中显示。

新会话首先是 ProjectTarget（机器 id + 目录）的草稿，发送第一条消息时才创建后端会话。NewChatTarget 与 ProjectCreateDialog 复用 SSH 添加流程；切换目标保留输入，历史会话不被重新定向。DirBrowser 经目标机器 RPC 浏览目录，支持输入路径；缺失或无权限的路径返回错误，不再显示可选空目录。

侧栏底部在新对话页和项目主页跟随目标项目、机器及其控制连接；聊天页跟随活动会话。切会话时立即同步目标项目目录，连接失败也不保留上一条会话的项目名。新对话页和项目主页的模型选择修改目标机器的新会话默认模型；切走后返回的旧机器响应不覆盖当前选择。

连接界面直接使用主进程和远端 bootstrap 发出的 `connecting → checking → installing/updating/starting/restarting → attaching → ready` 状态。首次安装确认携带实际数据目录和已安装信息；后续更新不重复确认。错误保留失败阶段与原始原因（包括多行安装输出），不按文本分类或猜测原因，也不通过前端计时器推算进度。`SshQuestionProvider` 维护认证问题队列：打开的连接窗口承接本机对应请求，其余重连请求由全局 `SshPrompts` 处理。安装确认、主机指纹和密钥口令在同一连接窗口内展示；取消先结束待确认请求，再等待 SSH 子进程退出，避免安装确认挂起时断开死锁。

所有项目入口使用同一个 C 版 `DirBrowser`，机器由调用入口指定。上级 token 原样传回后端，主目录取首次成功 RPC 的返回值；面包屑保留 POSIX、Windows 盘符和 UNC 路径。Windows 虚拟盘符列表不能被选择为目录。路径编辑、目录新建与整个弹窗的 Escape 分层处理；请求序号避免过期目录响应覆盖当前路径。列表边缘渐隐随实际滚动位置变化，短列表不渐隐。

聊天输入区、选择菜单、C 文件夹弹窗和 SSH 弹窗分别采用已确认 demo 的表面参数；不叠加通用外发光。微光静止，只有正在执行的状态光点运动，并遵循系统减少动态效果偏好。搜索输入不使用黄色聚焦框，键盘菜单导航继续保留。

原服务地址入口、迁移逻辑、公网安装脚本及相关测试已移除。独立 CLI/HTTP API 的健康检查继续保留，既有 Docker/系统服务不会被自动接管或停止。

## Gateway JSON-RPC 帧协议

一条本地 WS 连接或 SSH 逻辑通道 = 一个会话上下文（独立 `AgentBridge`，可切换 thread）。同一时刻只跑一轮用户流式响应，但所有 RPC（流式与非流式）都 spawn 成独立 task 执行，接收循环持续读帧——流式轮运行期间 `stop` 帧随时可达；需要 `run.lock` 的方法（切会话、切本会话模型等）在后台等锁，不会卡住接收循环。中断（approval/clarify）后等待 `resume`。

```
client → server   {id, method, params}
server → client   {id, result} | {id, error:{message}}        # RPC 响应
server → client   {method:"event", params:<wire event>}       # 流式事件
```

- **RPC 方法**：
  - 流式：`send_message`、`run_command`（运行斜杠命令）、`regenerate` / `edit_resend`（时间旅行，见下方「重新生成 / 编辑重发」）。
  - 控制：`resume`（带 approval_id 原地回答审批/提问，非流式）、`stop`。
  - 会话：`list_sessions`、`new_session`、`switch_session`、`load_history`、`pin_session`、`rename_session`、`delete_session`。
  - 项目 / 工作目录：`list_projects`、`add_project`、`remove_project`、`rename_project`、`set_default_project`（设为/取消默认项目，至多一个，见下方「聊天必须绑定项目」）、`set_workspace`（绑定本会话项目，会话级、不动进程 cwd）。连接选项携带 `workspace`（本地 WS 为查询参数、SSH 为 open 帧字段），握手即将本会话引擎绑定到该项目。
  - 模型供应商：`list_providers`、`test_provider`、`set_provider`、`save_provider`、`delete_provider`。
  - 定时任务：`list_cron_jobs`、`create/update/delete/toggle_cron_job`、`run_cron_job`、`list_cron_runs`。
  - 其它：`stop`（中止当前流式轮）、`list_commands`（拉取斜杠命令；项目主页经目标机器的控制连接带 `workspace` 按项目取）。
- **独立 HTTP API**：`lumi serve` 仍提供鉴权的 `GET /file` 和 `POST /upload`，供 API 客户端使用。原生 SSH 桌面通过数据通道传输文件，不派生 HTTP 地址。
- **wire 事件**：`turn.start`（真实用户轮开始，带该轮用户消息 id 及附件后端路径 `files`——乐观气泡里的 base64 图片据此换成落盘路径，与重载历史同源显示）、`message.*`、`tool.*`（含 `tool.generating`）、`clarify/approval`、`turn.complete`、`error`，加握手帧 `gateway.ready`。
- **进程级广播事件**：`cron.result` / `cron.running` / `bg_tasks.update` / `mcp.status` 不属于任何会话。前端在 `App.tsx` 的 `PROCESS_EVENTS` 集合里声明它们，会话连接与控制连接都转给同一个 `handleEvent`——远程机器通常没有活跃会话连接，只有控制连接，不转发它的定时/后台任务在界面上就是静止的。本机经两条连接各收一次，故这些处理器一律按机器整段覆盖或自带去重。机器断连/被移除时 `clearMachineSnapshots` 清掉它那份快照，否则等不到「结束」那一帧的任务会永远显示运行中。

事件名与方法名都来自 [`protocol/events.json`](../../protocol/events.json) 单一事实源：TS 端 import derive 类型，Python 端由 `tests/gateway/test_protocol_contract.py` 锁住一致性。

## AgentBridge 复用

`lumi/gateway/bridge/core.py` 是中立桥接层（desktop 与 IM 渠道共用），把 LangGraph 的原始事件封装为干净的 `BridgeEvent` 流。`EventKind` 成员值直接采用对外 wire 命名（`namespace.verb`），`lumi/gateway/protocol.py` 只做 `BridgeEvent → {type, session_id, payload}` 的 payload 重组，无额外映射层。

每条会话通道独立持有一个 `AgentBridge` 实例，`current_thread_id` 即该连接当前会话。多会话并发时各连接互不阻塞。

进程级共享资源的边界：连接干净关闭（无活跃轮）时 `bridge.close()` 只清理该 bridge 自身；MCP 子进程、
shell / 后台任务会话等全局单例由 `shutdown_shared_runtime()` 在 lifespan shutdown
时统一关闭。后台任务的完成通知按归属认领——任务注册时经
`ContextVar` 捕获所属 thread_id，各连接的通知轮询只取走归属自己当前 thread（或无归属）
的通知，不会把别的会话的任务结果注入本会话。通知续跑沿用当前会话的工具审批模式，
不会把用户选择的特权 / AI 审批模式重置为普通模式。

## 断连续接（会话与传输解耦）

通道断开时若会话仍有**活跃 / 挂起轮**（典型：挂在工具审批 / ask 上），不直接 aclose，而是把会话连同其 `AgentBridge` / parked turn / `ApprovalBroker` / 挂起 Future **原地留存**，等同 thread 的通道重连接回——renderer 重载（Ctrl+R）、网络抖动、休眠唤醒后，审批仍在、运行轮继续。**无需 checkpoint 重放，Future 一直在内存里**。

- **`lumi/gateway/session_registry.py`**：进程内 `thread_id → 已 detached 的 GatewaySession`。只存 detached 会话；干净关闭 / TTL 到期即移除。
- **`GatewaySession.detach()`**（断开且 `should_detach()`）：摘掉死 channel（换 `_NoopChannel`，断连期事件丢弃）、停掉通知轮、登记进 registry、挂 `_DETACH_TTL_SECONDS`（8h 兜底回收）。`should_detach()` 排除**纯后台通知 meta 轮**（无用户在等），除非它自身正挂着审批。
- **`GatewaySession.reattach(channel)`**（同 thread 重连）：取消 TTL、换上新 channel、重注册广播、重起通知轮、重发 `gateway.ready`（带 `running` 与本轮开始时刻 `run_started_at`，epoch 毫秒）并把挂起的审批 / 澄清卡片再推一遍（`bridge.pending_approval_events()`）。
- **前端配合**（`desktop/src/`）：每会话一条独立逻辑通道（`connsRef[key]`，`key = sessionKey(backend, thread)` 复合键——IM channel 的 thread_id 按 chat_id 确定性派生，同一飞书群在本地/远程两台 server 上 thread 同名，只用 thread 当键会让两条会话在 client 里塌缩成一条，故会话身份一律带机器维度；切到别的会话不碰本会话连接）；连接选项带 `thread`（本地为 URL 查询参数、SSH 为 open 帧字段，含 Ctrl+R 重载后点回会话的初次连接），后端据此在建空 bridge 前先认领回 detached 会话；`approval` / `clarify` 改按 `approval_id` 排队（渲染队首、逐个出队，并发审批不互相覆盖），重连重发按 `approval_id` 去重；`running` 据 `gateway.ready.running` 复位（断连时 `sendMessage` 的 catch 已置 false）；状态行计时由会话状态里的 `runStart` 推算（开轮时取本地时刻，重连 / 重载取 `run_started_at`），切会话、重连都不归零。
- **边界**：仅 sidecar 存活的断连可救（**Case 1**）；**后端进程重启（Case 2）** 不幸存——in-memory Future 随进程消失，刻意不做落盘（见 [approval-inflight.md](./approval-inflight.md)）。`switch_session` 切回**同 thread**且有活跃轮时不收尾本轮（早返回），避免误杀正挂着的审批。

## 子代理观测与历史兼容

持久子代理使用独立的 `subagent.event` 与按 seq 分页的历史，不进入主对话 reducer。桌面通过 `useSubagents`、观察器和时间线投影，按任务标题和角色名称展示实例，列表卡片与选中详情共享实时记录，多轮任务、正文、工具与问题连续展示；视图只读，后续任务由主 Agent 经 `agent_message` 派发。完整边界和模块见 [子代理架构](agents.md)。

旧 `agent` 工具产生的 `parent_run_id` 内嵌渲染仍保留读取兼容，见 [历史说明](../archive/subagent-rendering.md)。不能将该兼容路径的无 checkpointer、子事件丢弃等限制套用到当前持久子代理。

## 会话管理

会话列表由 LangGraph checkpoint 派生（`lumi/sessions/session_store.list_sessions`），但「置顶」「自定义标题」是用户施加的、不存在于 checkpoint 中的元数据，单独持久化：

- **`lumi/sessions/session_meta.py`** — JSON sidecar（`~/.lumi/checkpoints/session_meta.json`），按 `thread_id` 存 `pinned`/`title`，IM 渠道会话的 `channel_title`（群名/私聊对方姓名，入站时自动同步）/`channel_kind`（group/p2p），以及模型生成的 `auto_title`（+定稿标记 `auto_title_final`），仅写非默认值且内容不变不写盘。textual-free，可在 headless 服务直接使用。
- **`list_sessions` RPC** — 合并 sidecar 元数据后注入 `title`（手动重命名 > 渠道自动名 > 自动生成标题）/`pinned`，并按 thread 前缀标注 `channel`/`channel_kind`（`gateway/session._channel_of` 是渠道判定单点，前端只消费 wire 字段），置顶项稳定排到最前。
- **标题自动生成**（`lumi/gateway/titler.py`，对齐 claude-code 的 sessionTitle 机制）— desktop 会话第 1 条可见用户消息发出时即后台生成（不等本轮跑完），第 3 条时用对话尾部 1000 字符再生成一次纠偏后定稿；模型来自 providers 分区的 `titler` 指针（`set_titler` RPC / 设置→模型面板配置，未配则跟随会话 active 模型）。完成经 `session.title` 事件广播，前端就地更新侧栏；手动重命名永远优先（触发与写入前双重检查）。IM 渠道会话有 `channel_title`，不生成。
- **运行与删除共用线程锁** — `sessions/thread_runs.py` 维护进程内 `thread_id → ThreadRunLock`，desktop 用户轮/通知轮、飞书用户轮/通知轮/记忆整理、cron 都取同一把锁，避免多个连接并发写同一 checkpoint。连接自己的 `run.lock` 另保护 bridge 的可变指向。线程锁同时登记持有者与等待者，弱引用回收无人使用的锁。
- **删除（先停后删）** — `delete_session` 经 `cancel_and_hold()` 封住新运行，取消当前轮和排队者，等待持锁区（包括取消后的写回）全部退出，然后独占删除：关闭 detached bridge、取消后台任务、清理 checkpoint / 上传附件 / 持久 shell、丢弃通知与 sidecar 元数据。无需枚举连接或渠道池，cron 在初始化阶段也受保护；cron 保留策略与任务清理同样使用删除屏障。飞书删除取消只结束当前合成轮，长期轮询继续；停机取消仍会结束轮询。删除前积压的入站消息与妙记事件不会重新创建会话，删除完成后的新消息可以开始新轮。

前端 `Sidebar` 每行 hover 出现 `⋮` 菜单（置顶 / 重命名 / 删除）；删除走二次确认弹窗（`ConfirmDialog`），删除当前会话时自动另开新会话顶上。

### 历史记录的渐进加载

`load_history(thread_id, cursor?, limit?)` 首次返回最近 50 个展示条目，默认页大小可通过 `limit` 调整（1～200）。`next_cursor` 非空时继续向前读取；原文来自既有 checkpoint，不另建消息存储。工具调用与结果在整段恢复时配对，分页不截断工具结果。

页内 `order` 包含已重挂消息的顺序锚点，`insert_before` 标明页尾边界；前端按稳定身份合并，避免工具轮与保留用户消息、插话错序。首次返回的 `user_message_ids` 标明当前模型上下文中的可编辑用户消息；归档用户消息只读。`message.complete.message_id` 将实时助手气泡与后续历史快照关联，避免重复。

向上加载保持当前可见消息位置及工具组展开状态；重连刷新保留已加载的旧记录，编辑重发后清理旧分支缓存。过期请求不覆盖新状态，失败提供重试；缺失 checkpoint 返回 `history_unavailable`。首次快照同时返回 `pending`、usage 和模型窗口信息，较早分页只更新历史条目。索引和旧会话兼容细节见 [compact.md](compact.md#压缩前记录的渐进加载)。

### 重新生成 / 编辑重发（时间旅行）

仍在当前模型上下文中的用户气泡 hover 出操作条（`HoverActions`，与助手侧复制按钮同一个组件）：**重新生成**原样重答，**编辑**把气泡原位换成可编辑框（`EditBubble`；Cancel 零副作用，Save 才提交）。两者都以该条消息为锚，截断当前对话中其后的内容并重新生成；仅存在压缩前记录中的用户消息只读。

- **锚点是消息 id，不是序号**。`turn.start` 事件在开轮即广播本轮用户消息的落库 id，前端给乐观气泡上锚（`anchorLastUser`）；历史回放的 id 来自 `load_history` 的 `message_id`。之所以走事件而非 RPC 返回值：id 是「轮的事实」而非「轮的结果」，中途 stop 的轮同样需要它，且不必让每个流式入口都记得回传。本地列表可能含后端没有的条目（发送失败残留、系统命令），按序号对齐会指错消息。
- **`regenerate` / `edit_resend` 都是单个原子流式 RPC**：截断与重发共处一轮、持同一把 `run.lock`。拆成「截断 RPC + send」会留出竞态窗口，中间任何失败都让编辑文本连同被删历史一起丢失。
- **后端截断**（`AgentBridge.rewind_before_message`）经 `message_checkpoint` 定位目标消息压缩前的最早状态；如需恢复，从该 checkpoint 分叉并 `Overwrite` 为目标之前的消息，否则用 `RemoveMessage` 截断当前状态。两者都经 `OfflineFlush` 写回，剥掉新末条的 `ctx_digest` marker（见 [compact.md](compact.md) 的 marker 不变量）。必需 checkpoint 缺失时拒绝回退且不改当前状态。
- **重答的消息是重建的，不是原样重投**：checkpoint 里的 content 已烤入原轮的上下文注入块，原样重投会与新一轮注入叠加（token 逐次翻倍且新旧 env 互相矛盾）。重建 = `strip_injected_prefix` 回到用户原样输入 → 交 `_build_user_message` 走与新消息完全相同的构造流水线，附件标签由显示声明（`items[].files`）重新派生。该构造对重发幂等。
- 截断后消息会以**新 id** 重挂，故前端重挂气泡时清掉旧 `messageId`、等本轮 `turn.start` 重新上锚——留着旧 id 会让下一次时间旅行指向已删除的消息。
- 渠道旁观 / cron 观测 / 运行中 / 有在途审批时不提供该操作（服务端另有流式方法的渠道只读兜底）。

### 运行中插话

agent 运行时仍可发消息：消息不打断当前轮，而是在**下一个工具边界**（或模型想结束时）注入当前轮，模型据此调整。飞书渠道同一套规则（见 [feishu.md](feishu.md)），只少了撤回。

- **收件箱在 bridge**（`agents/core/inbox.py`，挂在主 agent 的 `LumiAgentContext.inbox`，子代理为 None）。运行中的 `send_message` 不开流：`bridge.interject` 按普通用户消息构造（`_build_user_message`，id / 显示声明齐全，回放与时间旅行同常规消息）后进箱，立即应答 `{queued, message_id}`。id 由前端预生成——`message.injected` 可能先于应答到达。上限 10 条。
- **上传后已空闲**：带预生成 `message_id` 的消息仍先发 `message.injected`，再以同一 id 发 `turn.start` 开轮；托盘照常移入消息流，不依赖上传前的运行态。
- **注入点**：`tool_executor` 在本批 tool_result 之后追加（provider 要求 tool_result 紧跟 tool_use）；`on_agent_stop` 有插话就注入并拉回 `CallModel`。消息原样注入、不加说明（tool_result 之后的独立用户消息本身即可辨），每条打 `INTERJECTION_KEY`；压缩保留未答完诉求时开轮消息与插话一并原样保住（`compact.pending_humans`）。
- **只在真实用户轮开放**（`Inbox.open`，`_stream_user_turn` 期间为 True）：后台通知等系统轮里到达的消息不混进系统轮回复。没赶上的（系统轮 / 轮已结束 / 无工具边界的 `/compact`）由 `GatewaySession._flush_interjections` 在轮 task 收尾时另起一轮（`stream_interjection_turn`：先发 `message.injected` 再走常规 `turn.start`）。判空与起轮之间无 `await`，不漏发不重发。
- **停止 / 撤回**：`stop` 先取走收件箱、随结果 `returned` 交还，前端撤出托盘、正文退回输入框；`withdraw_message` 撤回单条（已注入返回 false，以注入事件为准）。切会话 / 断开时随轮作废。
- **前端**（方案 C）：待插入消息存 `SessionState.pending`、渲染在输入框上方托盘（金色虚线 + 光点 + 撤回），**不进 `items`**——流式文字与工具卡都追加在 items 末尾，混进去会排错位置。`message.injected` 时收尾当前流式气泡、把对应条目移入消息流并放一次落地光晕（`.interject-land`）。运行中按钮单键变形：输入框空 = 停止，有内容 = 发送。斜杠命令要单独开轮，运行中不发、提示稍后。`load_history` 带回 `pending`，重连 / 重载后托盘不丢。

**IM 渠道会话在 desktop 只读旁观**：侧栏按机器和 `workspace_dir` 组织项目，各机器项目组扁平并列、按最近活跃排序，组头「＋」在该项目新建。顶部搜索框统一搜索会话（`Cmd/Ctrl+K` 聚焦），搜索时临时展开命中项目，清空后恢复原折叠状态。飞书等渠道会话与桌面会话在组内统一排列，飞书会话保留来源及私聊 / 群聊图标，悬停详情显示机器人名（`channel` 字段驱动；机器人启用前必须绑项目，不存在无项目的渠道会话）。会话状态与操作菜单共用行尾位置，悬停不改变标题宽度。打开渠道会话后，顶部显示渠道横幅（群名 / 审批模式 / 绑定项目 / 直达渠道设置），输入区替换为只读提示。只读在服务端兜底（流式方法对渠道 thread 直接拒绝；后台通知轮对渠道 thread 不消费）——desktop 与渠道 `BridgePool` 各持独立 bridge/锁，写入会绕过渠道的会话串行化。渠道跑完一轮广播 `channel.activity`，desktop 只刷该机器会话列表、正在旁观则重载历史（切回旁观会话也强制重拉）。消息级时间戳在 `bridge.stream_response` 统一落库（`additional_kwargs["lumi"].ts`，渠道另带 per-消息 `items`），气泡头渲染「发送者 · 时刻」。

## 项目与工作目录

**项目 = 工作目录，随会话绑定**（不再是进程级单一 cwd）。会话列表按 checkpoint metadata 的 `workspace_dir` 过滤分组。

- **会话级项目绑定**：每条 WS 连接 = 一个 bridge / 引擎，引擎在 `initialize` 时直接 pin 到本会话项目——open 握手经连接 URL 的 `?workspace=` 携带（与 `?token=` 同机制），`bridge.initialize(project_dir=...)` 据此新建权限引擎、构造本项目 config hooks、写 checkpoint 元数据。**不动进程 `os.chdir`**，故同进程多会话各绑各项目、并发互不影响。`bridge.workspace_dir` 取本引擎 `project_dir`（无引擎退回进程 cwd 兜底展示值），是会话项目的单一来源（`gateway.ready` / 元数据 / `system_info` 注入都据此）；`bridge.workspace_bound` 是与之独立的布尔态——`workspace_dir` 在未绑定时仍会给出 cwd 兜底值（仅供展示），只有 `workspace_bound` 才代表"真绑定了项目"。
- **聊天必须绑定项目**：`GatewaySession.handle_frame` 对 `send_message` / `run_command` 在 `workspace_bound=False` 时直接拒绝（`{"error":{"message":"请先选择项目再开始对话"}}`），不放行静默退回进程 cwd 的未绑定会话——堵住"不选项目直接聊天"落在非预期 cwd 的问题。关卡刻意只加在 desktop WS 聊天入口：cron（后台调度，不经 WS）、飞书 channel（自有一道更严的关卡：`workspace` 必填，未绑定项目直接拒绝启动，见 [feishu.md](feishu.md)）都不受影响，两者都不经过 `handle_frame`。前端配合：`gateway.ready` 的 `workspace_bound` 字段告诉前端是否该把 `payload.workspace` 当真项目写进本地状态（未绑定时只是兜底展示值，写进去会污染侧栏项目分组）；已有会话仍绑定真实项目；新会话先保留目标草稿，发送前必须明确工作目录，不放行空 `workspace=''` 的会话。「不在项目中工作」显式读取所选机器主目录作为工作目录，不登记项目，也不绕过后端绑定检查。
- **per-run 项目 / hooks 注入**：bridge 在每轮 `_stream` 起点通过 `set_project_source` 注入会话项目目录，通过 `set_run_config_hooks` 注入 config hooks。contextvar 隔离并发 run；serve 下 cron 也走同一入口。项目目录用于文件路径解析和 Bash 初始 cwd，不限制文件访问范围。详见 [permissions.md](permissions.md) / [hooks.md](hooks.md)。
- **`set_workspace`（会话级改项目）**：只 rebase 本 bridge 引擎、重载本会话 config hooks、更新元数据、重置本会话当前 thread 的持久 shell——**不 chdir、不影响其它会话**。原 `_active_bridges` 进程级 rebase-all 已随 cwd 进程级模型一并移除。前端「打开项目」= 经 open 握手开一条绑定到该项目的新会话（不再先 `set_workspace` 改进程态）；`set_workspace` RPC 主要用于原地改当前会话项目（及未来复用单连接的非 desktop client）。`set_workspace` 在 `_dispatch` 中持 `run.lock`，与运行中的轮次互斥。
- **项目清单**：纯手动登记，持久化在 `~/.lumi/lumi.json` 的 `projects` 分区（`lumi/gateway/projects.py`，复用 `_atomic_write_json`），按 `last_used` 降序。`list_projects` 只返回 `{projects}`——不下发「当前项目」：项目随会话绑定，项目页高亮由前端按活动会话的 `workspaceDir` 决定（同机器才高亮）；`add_project`（缺省用目录末端名，重复添加保留用户重命名）/ `remove_project`（只删条目，不动磁盘）/ `rename_project`。`last_used` 在真正用到项目时经 `touch_project` 刷新：带 `?workspace=` 打开会话连接且绑定成功、`switch_session` 本次确认了绑定、`set_workspace` 成功。
- **默认项目**：每条目可带 `default: bool`，`set_default_project(path, default)` 保证至多一个默认（设新的自动顶掉旧的；取消默认只清目标自身，不碰其它条目）。点击“新对话”或在目标选择器中切机器时，重新查询 `list_projects()`，共用 `defaultProjectTarget` 选择默认项目；无默认时取列表首项，列表为空时保留机器、等待用户选项目。冷启动只预选本地已登记的默认项目。`ProjectsPage` 项目卡「⋯」菜单可设为/取消默认，卡片名旁挂静止金星标记（`.proj-star`，设为默认那一刻光环扩散一次）。
  - **默认的兜底两条**：清单原本为空时 `add_project` 直接把首个项目标为默认（新用户建完就能开聊）；清单非空但**所有条目都不含 `default` 键**（= 该机制上线前登记、从没碰过它）时，`list_projects` 一次性把最近使用的回填为默认并落盘。判据是「有没有这个键」而非「值真不真」——主动取消过默认的人条目上是 `default: False`，带键即不回填，用户意愿不会被覆盖。
- **持久 shell 按会话 / 子代理隔离**：bash 的持久 shell 不再全进程共用一个，而是按 `current_thread_id` 分（会话私有，`cd`/env 不串别的会话），断连（`bridge.close`）/ 删会话（`delete_thread`）时回收，避免长跑 serve 累积孤儿进程。子代理（`agent` 工具）经 `shell_session.run_with_shell` 在 `copy_context` 副本里用专属 key 跑、拿独立 shell（`cd` 不污染父 / 兄弟、用完即弃），不继承父 shell 状态（在项目根 fresh 起）。
- **前端**：侧栏「项目」入口（`onOpenProjects`）打开 `ProjectsPage`（搜索 + 排序 + 卡片，当前项目金描边）；`DirBrowser`（经 `list_dir` RPC 浏览后端目录）新建项目。

## 多机界面：连接态即可用性

「先选机器」的界面（设置的 供应商 / 渠道 / MCP / 环境 四页 + 项目页 + 定时页）共用
`MachineTabs.tsx`：`MachinesProvider` 在 App 根部一次性下发 `{machines, conn, err, reconnect}`
（逐层透传要穿过五六个组件），消费方用 `useMachine(id)` 拿三态。

- **三态而非两态**：`connected`（`open`）/ `retrying`（`connecting`，以及 `closed` 但 Gateway 仍在
  退避重连）/ `stopped`（`failed` 退避耗尽，或 1008 令牌无效——这两种 Gateway 不再自行重试）。
  把 `closed` 当"已放弃"会让退避期间的界面跟着 `connecting↔closed` 来回跳；把 `connecting` 当
  "已连上"则会把空面板闪出来（点一次重连闪五次，退避 5 轮各一次）。
- **`MachineScope`**：选择条 + 内容。内容只在 `connected` 时渲染，否则换成同尺寸占位（`retrying`
  = 光点呼吸 +「正在连接…」无按钮；`stopped` = WifiOff + 原因 +「重新连接」）——配置读不到也写不进去，
  与其显示一份空表单让人白填（保存请求发不出去且被 `.catch` 静默吞掉），不如只留重连入口。占位
  容器尺寸两态一致，故切换时不闪不跳。选中的机器被删/停用时自动落回第一台可用机器（否则 pill 被
  过滤掉、只剩一台时选择条整个不渲染，页面卡在一个再也切不回来的空选中态）。
- **`useConnectedEffect(machine, fn, deps)`**：取数 effect 挂靠连接态——各面板的加载 effect 都在
  `MachineScope` 之上（状态要跨机器切换存活），机器没连上时照样触发、请求立即被 Gateway reject
  且无人重试，面板会钉死在空态直到换机器。该 hook 让 fn 只在 `connected` 时跑、重连成功自动重跑，
  供应商 / MCP / 环境三页的列表加载共用（渠道页因叠加 `active` 条件仍自持 offline 判断）。
  **约定：凡按机器取数一律走它**（项目主页概览、定时执行记录、飞书绑定项目选择同样接入）；App 本身
  是 context 提供方用不了 hook，项目列表与开局查默认项目直接依赖 `machineConn[machine] === 'open'`
  写 effect，语义相同。启动查询和项目页刷新共用 `refreshProjects`，查询及项目修改返回的列表按机器保存，晚到的其它机器响应不会覆盖当前机器的列表。目标机器的列表未成功拉到时为 `null`（列表区留空），不渲染成「还没有项目」。
- **停摆唤醒**：`stopped` 里的退避耗尽不是终态——窗口重获焦点 / `online` 时 App 对全部控制连接与
  会话连接调 `Gateway.wake()`（仅 `failed` 且非主动关闭 / 非 1008 才重连）。离开久了（睡眠、断网
  超过约 15s 的退避窗口）回来即自愈，连上后上面挂在连接态的取数自动重跑，无需逐处写补拉。
- **弹窗必须放在 `MachineScope` 之外**（飞书配置、MCP 服务器表单）：瞬断会卸载作用域内容，正在
  输入的凭证会跟着没——这正是 `SettingsDialog` 给渠道页加 `forceMount` 要防的事。作用域外的
  新建入口（项目页/定时页的「新建」按钮）则各自 `disabled`。
- **失败原因**：`Gateway.lastError`（1008 → `auth`，其余 → `unreachable`，连上即清空）经 App 的
  `machineErr` 随 context 下发。SSH 主机的准备与失败阶段由连接进度面板展示，并支持重试；Gateway 的连接态决定业务面板是否可用。原服务地址 / token 表单已移除，不能将旧 WS 鉴权状态当作 SSH 用户认证提示。

## 模型供应商管理

用户自定义的「连接 + 模型」持久化在 `~/.lumi/lumi.json` 的 `providers` 分区（明文，`chmod 600`，含 `api_key`），由 `lumi/models/provider_store.py` 读写——textual-free，desktop 前端经 RPC 读写。

- **数据模型**：一个 **profile** = 一套连接（`name` / `base_url` / `api_key`）+ 该连接下的一组 `models`；`active` 指向「某 profile 下的某个 model」。新 profile 显式保存 `protocol`（openai / responses / anthropic / gemini）、`compatibility` 与界面 `preset`，连接测试与实际调用共用。旧 profile 未指定接口时保留名称判定与 SDK 路由；接口和思考回传见 [thinking.md](thinking.md)。`provider_store` 兼容旧格式（单 `model` 字段、`active` 为字符串 id），读取时自动迁移并把失效 `active` 归位到首个可用模型。
- **运行时生效**：模型是会话属性，每轮开跑前 `bridge.align_session_model()` 对齐 `context.model_name` / `provider`，连接由 `provider_store.resolve()` 解析。
- **RPC**：`list_providers`（列全部 profile + active）、`save_provider` / `delete_provider`（增删改，返回刷新后的 `{profiles, active}`）、`set_provider`（切换 active，返回 `{active, model}`）、`test_provider`（用给定连接对模型发最小请求验证可达，15s 短超时、不缓存不重试）。provider 写类 RPC（`set/save/delete_provider`、`set_effort`、`set_classifier`、`set_titler`）改的是机器级配置（同步读改写，单事件循环内不交错），**不持** `run.lock`——持锁只会让改档位 / 默认模型挂到本轮结束，新配置在下一轮开跑前对齐生效；只有改本会话运行时 context 的 `set_session_model` 持锁。
- **前端**：`SettingsDialog` + `ProvidersPanel` 完成增 / 删 / 改 / 测试；`ModelPicker`（顶栏）做快速切换。

## 上下文用量指示器

composer 右下角（发送键左侧）一粒圆环，实时反映「当前对话占用 / 模型上下文窗口」，落地见 `desktop/src/components/ContextMeter.tsx`。

- **数据**：占用量取最近一次模型调用的 `usage.input_tokens`（含缓存命中部分，即「当前上下文」），由 `App.tsx` 的 `ctxFromUsage` 从 `message.complete` / `turn.complete` 事件提炼写入 `SessionState.ctx`（回合中流式刷新）；窗口取 active 模型的上下文长度——`list_providers` 在每个 profile 附 `context: {model: context_length}`（来自 models.dev catalog，与思考能力 `thinking` 同源 `lookup(m)`），前端按 `activeModel` 派生 `contextWindow`。
- **形态**：默认仅圆环，颜色即档位（绿 `<60%` / 金 `60–85%` / 红 `>85%`，取主题 `--color-success/accent/error`）；点击经 Radix DropdownMenu 向上弹出明细——大进度条 + 已用/窗口容量 + 分项（输入 / 输出 / 缓存命中，取 `input_token_details` / `output_token_details`）+ 当前模型名称。模型名称过长时省略，悬停查看全名。临界态（`>85%`）圆环发光呼吸 + 红色「上下文将满」提示条。数据未就绪（无 usage 或窗口未知 = 0）时静默不渲染。

## 定时任务管理

cron 子系统是进程级资源（与会话无关）：serve 在 lifespan 中经 `lumi/agents/cron/runtime.setup_cron()` 启动调度器，RPC 实现在 `lumi/gateway/cron_rpc.py`，不经 AgentBridge。内部机制（执行即会话、保留策略、级联删除）见 [`cron.md`](cron.md)。

- **结果广播**：`lumi/gateway/broadcast.py` 的 `BroadcastHub` 把任务结果（`cron.result`）与运行状态（`cron.running`）推给所有活跃 WS 连接——wire 信封格式属 server 层，agents 层只定义 `ResultDelivery` 抽象。无连接时不缓存：结果已落 RunLog，重连后经 `list_cron_runs` 查询。
- **前端结构**（`CronPage.tsx` + `App.tsx`）：
  - 定时任务不进侧栏；在顶部「定时任务」管理页的执行记录里点某次执行，进入**任务会话视图**：主区为最近一次执行的完整对话（composer 可续聊），右侧统一右栏（`RightRail`）置顶「执行记录」模块（`RunsSection`）列历次执行，蓝点 = 未读、点开即消失。
  - 顶部「定时任务」导航入口 → 管理页（卡片网格 + 新建 / 编辑 / 删除 + 详情）。
  - **未读是派生的，不是累积的**：唯一的本地事实源是已读集合 `readRuns`（run 的 `thread_id` → true，持久化 localStorage），侧栏角标 = 该任务 `run_threads`（`list_cron_jobs` 带回的近期可跳转 run，窗口同 `MAX_CRON_RUN_THREADS`）减去 `readRuns`，与执行记录模块蓝点同源。因此桌面端离线期间执行的 run 重连后照样算未读——事件累积做不到这点。正在看某任务时新到的 run 直接记为已读，避免角标在用户眼皮底下跳出来。
  - App 持有 cron 数据单一来源（jobs / `readRuns` / 按机器分段的运行中 job id）；`cron.result` 经会话连接与控制连接双路径消费（远程机器通常只有控制连接），前端按 `job_id:started_at` 去重。

## 后台任务中心（统一右栏模块）

统一右栏里的「后台任务」模块，纳管 **bash / agent / workflow** 三类后台任务（`TaskRegistry`
单一注册中心）。引擎 / 工具侧设计见 [`workflow.md`](workflow.md)。

- **实时推送**：`TaskRegistry.set_on_change` 观察者（server 层注册）→ `BroadcastHub.on_bg_task_change`
  **~100ms 去抖合并** → 广播 `bg_tasks.update`（本机进程级快照，前端
  按机器分段替换合并、按 thread + backend 过滤展示——同名飞书群跨机不串）。RPC：`list_bg_tasks`（初始拉取）/ `read_bg_task_output`（输出预览）/ `stop_bg_task` / `dismiss_bg_task` /
  `clear_finished_bg_tasks`，读 / stop / dismiss 带会话归属校验。
- **广播是全量快照**，故凡随快照走的字段都必须有界：`prompt` 截 1000 字、workflow 的
  `last_log` 截 300 字；`notify_progress` 对**相同快照直接返回**（后台 agent 逐超步上报时
  首尾几步是重复的），workflow 脚本 `log()` 驱动的推送另有 500ms 节流（`log` 是脚本里
  `print` 的别名，循环打印会把广播顶在去抖上限）。前端 `replaceBackendTasks` 逐条按值比对
  复用旧对象、整段未变返回原引用，否则每次广播都让整列卡片重渲染。
- **卡片信息量**：卡片要回答「它在做什么」和「它跑出了什么」——
  - *任务内容*：agent 用 prompt、workflow 用工具的 `description`、bash 用完整命令（`label`，
    不另存一份免得每次快照多带一遍）。它归展开后的详情，卡面上不占位。
  - *运行中的活动*：agent 逐超步上报 `{tool, tools_done}`（`tools_done` 取 `max` 单调递增，
    否则压缩删历史时计数会当场回退）；workflow 上报 `phase / done / total / last_log`。
    卡面压成一行 `kind · 时长 · 活动 · N 次工具`（分隔点跟着后一格走，窄栏换行不留孤点）。
  - *输出*：`streams_output` 标记该任务是否边跑边写输出文件（仅 bash——它的 stdout 直接
    重定向进去；agent / workflow 完成时才一次性写）。据此，只有 bash 在运行中每 2s 续拉
    尾部（末 8KB，截断时标注完整体积），其余只在需要时拉一次，避免对必空的文件空转。
    卡面只留末尾一行（还没吐字则「等待输出…」），全文在详情里定高滚动。
- **前端结构**（`BgTasksDrawer.tsx` 的 `BgTasksSection` + `RightRail.tsx` + `App.tsx`）：
  - 挂在**统一右栏**（`RightRail`，见「可调宽边栏」）的模块卡里：chat / 定时任务会话两视图
    通用，有任务整卡出现、无任务隐藏；节头带「N 运行中」（无运行中时退回总数）；
    收放钮收起态亮脉动金点（含直播中的定时执行）。
  - **按状态分两组**：运行中是一摞紧凑卡片（kind 图标 + 名称 + 光点 + **常驻停止钮** /
    元信息一行 / workflow 进度条 / 一行动静）；终态全部收进「已完成 N」一行折叠开关 +
    「清除」，展开后是一行一个的紧凑列表（名称 + 勾/叉 + 用时 + hover 移除 ✕）。
    分组是刻意的——终态任务每会话可攒到 20 条（`_TERMINAL_CAP`），平铺会把正在跑的挤出视野。
  - **详情单开**：点卡片 / 已完成行就地展开（任务内容或命令原文、退出码、错误、输出尾部），
    同时只开一个——右栏只有 256 宽，两份摊开就得来回滚。展开开关是 div（内含停止 / 移除
    button，button 不能嵌 button），故自带 `role/tabIndex/Enter·Space`。
  - 运行中任务的 Duration 由本地每秒 tick 实时跳；**秒表与输出轮询都以右栏展开为前提**
    ——栏收起时卡片只是被移出视口、组件仍挂着，不设门就会在没人看的时候持续拉取。
    输出轮询另有一道门：只有「详情摊开了」或「边跑边写且卡面那行要跟着走」才拉。
  - demo `.demos/bg-tasks-grouped.html`（现行分组版，含改版前后对照）、
    `.demos/lumi-bg-drawer-detail.html`、`.demos/bg-task-card.html`、
    `.demos/bg-task-long-text.html`、`.demos/unified-rail.html`。

## 输入栏文件附件

`+` 按钮 / 拖拽 / 粘贴均经 `addFiles()` 加入附件，按 MIME 分两路：

- **图片**：`FileReader` 读成 base64 data URL，发送时拆为 Anthropic 原生 `image` 块（后端按所选模型接口适配），气泡里渲染缩略图。
- **其它文件（PDF / 视频 / docx…）**：经 Electron `webUtils.getPathForFile`（`preload.cjs` 暴露；Electron 33 起 `File.path` 已移除）取**绝对路径**，发送时以 `<attached-file>路径</attached-file>` 文本块注入消息，Agent 用 `read` 工具按路径读取。读取遵循现有权限规则。取不到路径（如非文件系统来源的拖拽）不静默吞掉，经 toast 提示失败。

`<attached-file>` 是纯模型侧约定（agent 用 read 读取路径）：前端经 wire `files` 参数只发路径数组，`bridge._build_user_message` 统一拼标签块注入 content（`injected_prefix` 计数）并把 `{path, name}` 写进显示声明 `lumi.items` 的 `files`；历史恢复的文件胶囊直接读 items，不解析正文。标签名 `ATTACHED_FILE_TAG` 定义在 `lumi/utils/constants.py`。气泡内文件渲染成品牌金描边胶囊（仅文件名 + hover tooltip）。

**远程后端**：附件路径指向前端本机，在目标机器上不存在。`gateway.resolveAttachments(atts, remote)` 本地直接返回路径，远端通过 SSH 分块上传并返回对端路径，每块最多 1 MiB，单文件上限 128 MiB。附件 `File` 留在输入栏条目上，上传排在乐观气泡之后，失败则恢复正文与附件。SSH 图片与普通文件统一分块上传，消息只携带对端路径；本地图片继续使用内联消息块。取消或断连清理未完成的独占上传目录。

**上传保留**：`~/.lumi/uploads/` 下的文件（远程附件 + 内联图片）随会话删除回收——`bridge.delete_thread` 删 checkpoint 前先读出该会话消息 `lumi.items[].files` 声明的路径，删后经 `uploads.remove_uploads` 只删落在 uploads 目录里的那些（本地后端附件是用户原文件，绝不碰）。已被压缩 / 时间旅行截掉的消息声明的附件不在当前历史里，不回收。office 预览缓存（`~/.lumi/cache/office_preview/`）在 `gateway_process()` 启动时清掉 7 天前生成的产物（`office_rpc.prune_cache`）。

## artifacts 制品文件预览

Agent 产出文件后调 `artifacts` 工具把它们作为「制品」呈现给用户。后端 `lumi/agents/tools/providers/artifacts.py` 只做本地元数据收集（无对象存储）：单次 `os.stat`（避免 isfile→getsize 的 TOCTOU）+ `mimetypes` 猜 MIME + 按扩展名分类 `kind`，返回 `{path, name, mime_type, size, kind}` 的 JSON 列表（不存在 / 非常规文件返回 `{path, error}`，顺序与输入一致）。这是常驻工具，走现有 `tool.start/complete` 事件流——**协议无新增事件**，前端按工具名 `artifacts` 特化渲染。

- **权限规则**：`artifacts` 默认 allow；用户配置的 deny / ask 仍参与权限评估。
- **聊天卡片**（`FileCards`，`desktop/src/components/Artifacts.tsx`）：artifacts 在 `groupItems` 里单独成段（不并入灰色工具组），解析一次 JSON 缓存在段上（随 `items` 记忆化）。每个文件渲染成单色类型图标卡片（按 `kind` 选 lucide 字形，不上彩色）+ 文件名 + 类型/大小 + 「Show in Folder」按钮。**卡片层不加载任何文件字节**。
- **预览浮卡**（`PreviewWindow` + `PreviewContent`）：与执行记录、后台任务共用 `RightRail`。顶部一行文件 tab、更多菜单、放大/缩回及关闭；同路径重复打开切回已有 tab，关闭当前 tab 后选相邻文件，最后一个 tab 关闭后移除浮卡。小窗缩放真实内容，点击放大后原地铺满工作区；各 tab 内容保持挂载，切换和缩放保留模型与滚动状态。整组收放图标在放大时移到右上角，收起时恢复普通布局。切会话自动关闭全部 tab，预览状态同时校验机器与会话身份。内容仍在打开时探测存在性，缺失时显示 `MissingState` 与重新检查入口。
- **预览分型**：本地图片 / PDF / HTML 经 `lumi-file:` 协议，文本经 `lumi:read-text` 读取前 500 KiB。远端图片走 `lumi-remote:`，文本使用 Range 读取前 500 KiB；PDF 与 HTML 由主 frame 取回后生成 blob iframe。超过 50 MiB 的 UI 预览进入文件较大提示。HTML 使用 `sandbox="allow-scripts"`，不带 `allow-same-origin`；本地文件跨域读取被阻止，远端协议在 webRequest 层拒绝子 frame 请求。
- **`lumi-file://` 协议**：`electron/main.cjs` 以 `protocol.registerSchemesAsPrivileged` + `protocol.handle` 实现，让 renderer 在 http origin 下安全引用本地文件（绕过 `file://` 限制）。URL 形如 `lumi-file://local/<abs-path>`（固定 host=local，自定义 standard scheme 不允许空 host；各路径段 `encodeURIComponent`）。handler 先 `stat`，`> MAX_SERVE_BYTES`(128MB) 返 413（兜底防超大文件读进内存撑爆主进程），否则按 `PREVIEW_MIME` 设 content-type 返回。
- **IPC**：`lumi:open-path`（`shell.openPath`）/ `lumi:reveal-path`（`shell.showItemInFolder`）/ `lumi:path-exists`（异步 `fs.promises.access`，避免离线网络盘同步阻塞主进程），经 `preload.cjs` 暴露为 `window.lumi.{openPath,revealInFolder,pathExists}`。

## 应用内通知（toast）

`desktop/src/components/Toast.tsx` 提供可复用的轻量通知通道：模块级 store（pub/sub）+ 根部挂一次的 `<ToastHost/>`，任意模块 `import { toast }` 后调 `toast.error/success/info(msg)` 即可，无需 context / prop 透传。顶部居中细条幅，按 kind 上语义色，下拉淡入 + 自动消失（带退场过渡），多条纵向堆叠。与「桌面通知」区分：toast 是前台即时反馈，系统通知用于会话非活动 / 窗口失焦时。

## 桌面通知

回复完成与等待用户处理的中断（审批 / 提问）会触发系统通知，**仅在该会话非当前活动、或窗口未聚焦时**弹出（你正盯着时不打扰）。通知经主进程 `Notification`（`electron/main.cjs`）发出——renderer 的 HTML5 `Notification` 在 macOS dev 下不可靠；点击通知由主进程聚焦窗口并经 `lumi:notify-click` 回传 tag 切到对应会话。判定用 `document.hasFocus()` 而非 `document.hidden`（切到别的应用时窗口仍可见，`hidden` 恒为 false）。

macOS 关窗后应用驻留 Dock，sidecar 保持运行，Dock 唤起（activate）重建窗口后直接复用；其他平台关窗即退出，`before-quit` 拦下本次退出、等 sidecar 优雅停完（≤5s）再退。

## 国际化（i18n）

`desktop/src/i18n.ts` 提供 `useI18n()` hook（`t` / `lang` / `setLang`），支持中文 / English，偏好存 localStorage（`lumi-lang`）。所有 UI 文案经 `t(key)` 取用，不硬编码。

**原生菜单的文案同样以 `i18n.ts` 为单一事实源**：右键菜单在主进程构建（`electron/main.cjs::installContextMenu`，Electron 不自带右键菜单，不装则右键全程无反应），但主进程不留平行词表——renderer 在语言 effect 里把翻好的 label 对象经 `lumi:menu-labels` 推过去，故新增语言只改 `i18n.ts` 一处即可。真相源是 renderer 的 localStorage（主进程读不到），且 Electron 的 role 自带 label 是硬编码英文、不随系统语言变，所以方向只能是 renderer → main。推来之前 label 为 `undefined`，Electron 回落到 role 自带英文，只有无 role 的「复制链接」自带兜底。

## 界面字体

设置→通用页可让用户从**本机已装字体**里挑界面字体并调正文字号，偏好存 localStorage（`lumi-font`，`{family, size}` JSON），落地见 `desktop/src/font.ts`（`useUiFont` hook）+ `desktop/src/components/FontPicker.tsx`。与 `theme.ts` 同构：运行时把覆写写到 `document.documentElement` 的 CSS 变量上。

- **覆盖机制**：默认字体栈是 `index.css` 的 `--font-fallback`（**唯一真相**）；`@theme` 的 `--font-sans` = `var(--ui-font, var(--font-fallback))`，故选字体只需把 `--ui-font` 设到根元素，`body` 与所有 `font-sans` / `font-heading` 工具类（含 Dialog 标题）一并跟随。字号同理：`body` 用 `font-size: var(--ui-font-size, 13px)`，仅非默认字号才写 `--ui-font-size`，默认时移除让 CSS 回落。`font.ts` 设字体时走 `cssFamily()` 转义族名（防引号/反斜杠破坏声明），并追加 `var(--font-fallback)` 保证西文字体缺中文字形时回退。
- **本机字体枚举**：经浏览器 `queryLocalFonts()`（Local Font Access API）取族名去重排序。**权限收口**：`electron/main.cjs` 的 `setPermissionRequestHandler` / `setPermissionCheckHandler` 共用一份 `ALLOWED_PERMS` 白名单，只放行 `local-fonts`（本条）与 `clipboard-sanitized-write`（`navigator.clipboard.writeText`——消息/凭证/文件路径的复制按钮），其余（camera / mic / geolocation…）一律拒绝。该 API 需 user activation，故首次枚举在点击打开下拉的处理器内同步触发（非 effect），避免打包版丢激活；不可用 / 被拒时静默返回空并在面板提示「无法访问本机字体」。列表渲染封顶 `MAX_VISIBLE` 行（多出靠搜索收窄），避免上百字体一次性挂载造成开屏卡顿。

## 可调宽边栏

左侧会话栏与统一右栏可拖拽调宽。预览浮卡与执行记录、后台任务同列排列：有预览时使用 `lumi-preview-width`（默认 440px），否则使用 `lumi-rail-width-v2`（默认 256px）；左侧保持 `lumi-sidebar-width`。各宽度由 `useResizableWidth` 持久化到 localStorage，越界或脏值回退默认值。放大预览时暂时隐藏左侧栏和聊天区域，缩回后恢复原布局与右栏宽度；浮卡和 iframe 不重挂载。

**统一右栏（`RightRail.tsx`）**：执行记录 / 后台任务等以 `RailSection` 模块卡竖排（玻璃材质、节头计数、独立折叠、键盘可达），定时任务会话置顶「执行记录」，通用模块两视图同构往下叠。预览与任务共用整栏一颗收放钮（预览放大时移入右上角）：浮于聊天区右上角（与左侧栏收起钮同线 y≈27、同 ghost/icon-sm 款式），收起时栏宽归零、聊天区铺到窗口边、钮贴右缘；开合状态（`lumi-rail-open`）与宽度两视图共用。收起即停一切隐藏开销（秒表 tick / 执行记录拉取 / 屏外动画合成）。

**侧栏材质**：左右侧栏、文件预览与子代理窗口外壳共用 `--sidebar-background`，随深浅主题切换。右栏模块的内卡通过轻微明暗差区分层级；子代理正文沿用主会话画布，文件预览内容保留自身显示背景。预览放大后继续使用同一外壳底色。

**悬浮栏的层级约定**：两条悬浮玻璃栏（左侧会话栏 / 统一右栏）在 `App.tsx` 里都挂在 `<main>` **之外**、与左侧栏同级的 flex 行上——放进 `<main>` 会被其内的 topStrip 压低一截，顶边对不齐。预览浮卡作为统一右栏的第一个模块；放大时保持同一组件实例，仅切换为铺满工作区的布局。新增右栏模块时挂进 `RightRail` 的 children 即可。

## 应用内更新

`electron-updater` + GitHub Releases。状态机全在主进程（`desktop/electron/updater.cjs`），renderer 只订阅 `UpdateState`（`idle / checking / latest / available / downloading / ready / error`）并触发检查与安装，入口是设置→关于（`AboutPanel.tsx`）与侧栏底部的提示条（`Sidebar.tsx` 的 `UpdateBar`，仅在此刻能装时才出现）。启动 15s 后首检，此后每 6h 一次；dev 下 `isUpdaterActive()` 恒 false，检查是空操作。

- **平台差异**：Win / Linux 全自动（后台下载 → `ready` → 用户点重启安装）。**macOS 停在 `available`，把下载交给浏览器**——CI 未做代码签名（`desktop-build.yml` 的 `CSC_IDENTITY_AUTO_DISCOVERY=false`），而 Squirrel.Mac 校验新旧版本签名同源，未签名的包装不上。故 mac 设 `autoDownload = false`，只调 `checkForUpdates()`（该阶段纯读 `latest-mac.yml`，不启动 Squirrel 代理）。拿到 Developer ID 证书并在 CI 配好 `CSC_LINK` 后，把 `MANUAL_DOWNLOAD` 改 `false` 即转全自动，无需改 CI。
- **Windows 安装器形态**：NSIS 的 `oneClick` **默认为 `true`** —— 只写 `"target": ["nsis"]` 出的是一键静默安装器，无向导、无进度、直接落 `%LOCALAPPDATA%\Programs\Lumi`，用户选不了盘。故显式配 `oneClick: false` + `allowToChangeInstallationDirectory: true`（模板链路：`ONE_CLICK` 未定义 → `assistedInstaller.nsh` → `MUI_PAGE_DIRECTORY`；两者矛盾时 `NsisTarget` 会直接抛 `InvalidConfigurationError`，故构建能过即说明配置生效）。**`perMachine` 必须保持 `false`**：置 `true` 装进 `Program Files`，写入需提权，于是每次自动更新都弹 UAC，与「后台下载 → 静默安装」的更新链直接冲突。目录页另有两个内置行为——`instFilesPre` 会把用户选的路径补上 `${APP_FILENAME}` 子目录（选 `D:\` 得到 `D:\Lumi`），`skipPageIfUpdated` 让更新时跳过该页。**注意 macOS 上无法验证**：electron-builder 打完 NSIS 要用 wine 跑一次安装器生成 uninstaller，Apple Silicon 上 qemu 模拟 x86 会撞 page size 断言（`anon_mmap_fixed`）必崩，本地打 Windows 包只能到「exe 已生成」为止，向导实貌需真机或 CI 产物确认
- **安装前必须收 sidecar**：`quitAndInstall()` 之前先 `stopSidecar()`，否则更新后的新实例会与残留的旧 sidecar 抢同一 checkpoint 数据库（表现为「会话打不开」，与单实例锁防的是同一类事故）。代价是正在跑的 agent 任务会被打断——会话历史存在 LangGraph checkpoint 里不会丢，但**未完成的任务不会续跑**，UI 文案不要暗示相反的事。
- **Release 必须带元数据**：`latest*.yml`（客户端比对版本）与 `.blockmap`（差量下载）少传任一，应用内更新就查不到新版本。macOS 另需 `.zip`——Squirrel.Mac 只认 zip，dmg 仅供人工首次安装。
- **产物命名**：`${productName}-${version}-${os}-${arch}.${ext}`（`${os}` = `mac`/`win`/`linux`，注意 `${platform}` 是**构建机**的 `process.platform`，不是目标平台）。带 `${os}` 是必需的——平台原先全靠扩展名隐式区分，而 `.zip` 是通用扩展名，mac 的更新包一旦与其他平台的 zip 同名就会撞车。带 `${arch}` 更是硬要求：**electron-updater 靠文件名里的架构串选包**（`Provider.findFile` 优先匹配含 `process.arch` 的文件，mac 另有 `MacUpdater.filterFilesForArch`），命名里丢掉 arch 会让用户下到错架构的包。改名有一次性代价：差量下载靠 `Provider.getBlockMapFiles` 在**新**文件名上替换版本号来推导旧包的 blockmap 地址，跨越改名的那一次更新推出的是从未存在过的旧文件名 → 404 → 回退全量下载。安全，但那一版每个用户都要重下完整包。

  注意 **AppImage 不遵守 `${arch}`**：`builder-util` 的 `getArtifactArchName` 对 AppImage / rpm / flatpak 把 `x64` 硬编码改名为 `x86_64`（deb / snap 则是 `amd64`），配置层改不动。于是 Linux x64 包叫 `-linux-x86_64.AppImage`，而 `Provider.findFile` 按 `includes(process.arch)` 找的是 `"x64"` —— 匹配不上，会回退到列表第一个。当前无害，因为 Linux 的元数据本就按架构分成两个文件、每份只有一个候选；但**若哪天把 Linux 两个架构并进同一份 yml，选包就会失灵**。
- **构建矩阵**：五个 build job —— Windows x64、macOS arm64 + x64、Linux x64 + arm64。**没有 Windows arm64**：`sqlite-vec`（`langgraph-checkpoint-sqlite` 的传递依赖）没有 `win_arm64` wheel 也没有源码分发包，uv 建 venv 即失败；x64 包在 Windows on ARM 上经模拟层可用。更新元数据的文件名规则三平台**不一致**，`merge-update-metadata` job 据此收尾：
  - **macOS**（`latest-mac.yml`）：两个架构共用同一文件名，附到 Release 时互相覆盖，留下的那份只列一种架构 → 必须把两份的 `files` 并成一份，两种机器才各取所需。Windows 若将来加回 arm64，其 `latest.yml` 同理。
  - **Linux**：`electron-builder` 只给非 x64 的 linux 加架构后缀（`updateInfoBuilder.getArchPrefixForUpdateFile`，与读取端 `Provider.getChannelFilePrefix` 对应），`latest-linux.yml` 与 `latest-linux-arm64.yml` 本就是两个文件 → 无需合并，脚本对它是恒等操作。
  - 合并后顶层 `path` 回退指向 x64（x64 包能在 arm 上经 Rosetta / Windows on ARM 模拟层跑，反之不行）；该字段只服务不认架构的旧客户端。
- **版本**：`app.getVersion()` 读 `desktop/package.json`，该值由 `build-desktop.sh` 打包时从 `pyproject.toml`（版本事实源）`npm pkg set` 写入，故**打包产物的版本恒准**，更新比对不受影响。dev 下显示的则是仓库里那个值，只在有人手动同步时才是最新的——无功能影响，不必为它加特例。绕过 `build-desktop.sh` 直接 `npm run dist` 会出版本号停留在仓库值的包。

## 关键文件

| 文件 | 职责 |
|---|---|
| `desktop/electron/main.cjs` | sidecar 生命周期、窗口、端口分配 |
| `desktop/electron/updater.cjs` | 应用内更新状态机（Win/Linux 全自动、macOS 半自动） |
| `desktop/src/{update.ts,components/AboutPanel.tsx}` | 更新状态订阅 hook + 设置→关于面板 |
| `desktop/src/gateway.ts` | 本地 WebSocket / SSH JSON-RPC 客户端，负责请求、握手和指数退避；`rebase` 更新连接信息，`wake` 恢复已耗尽重试的连接 |
| `desktop/src/gatewayConnections.ts` | 控制连接复用、聊天连接的 thread 身份、断线续接与历史对账；App 通过回调接收状态和事件 |
| `desktop/src/messageDelivery.ts` | 本地图片内联、远程附件上传与消息派发；失败交回调用方恢复草稿 |
| `desktop/src/lib/sshFormState.ts` | SSH 表单字段初始化及字段、临时凭证、连接过程的独立类型 |
| `desktop/src/App.tsx` | 会话状态机、事件路由、聊天流渲染 |
| `desktop/src/components/Sidebar.tsx` | 会话列表 + 右键菜单 + 内联重命名 |
| `desktop/src/components/ResizeHandle.tsx` | 边栏拖拽调宽（`useResizableWidth` hook + 分隔条，宽度持久化） |
| `desktop/src/components/{SettingsDialog,ProvidersPanel,ModelPicker}.tsx` | 模型供应商配置 + 快速切换 |
| `desktop/src/components/ContextMeter.tsx` | 上下文用量指示器（圆环 + 明细 popover） |
| `desktop/src/components/Toast.tsx` | 可复用应用内轻量通知通道（`toast.error/success/info`） |
| `desktop/src/components/Artifacts.tsx` | artifacts 制品文件卡片 + 右侧停靠预览面板（含缺失态 / 大小门控 / `lumi-file://` URL） |
| `lumi/agents/tools/providers/artifacts.py` | artifacts 工具（本地元数据收集，无对象存储） |
| `desktop/src/{font.ts,components/FontPicker.tsx}` | 界面字体偏好（本机字体枚举 + 字号）+ CSS 变量覆写 |
| `desktop/src/components/CronPage.tsx` | 定时任务管理页 + 执行记录模块（`RunsSection`） |
| `desktop/src/components/RightRail.tsx` | 统一右栏（收放钮 + `RailSection` 模块卡容器） |
| `desktop/src/components/PreviewWindow.tsx` | 多 tab 预览浮卡、文件操作菜单与应用内放大 |
| `desktop/src/lib/previewState.ts` | 文件去重、相邻 tab 选择与会话隔离状态 |
| `desktop/src/components/BgTasksDrawer.tsx` | 后台任务模块（`BgTasksSection`，运行中卡片 + 已完成折叠组） |
| `desktop/src/components/{ProjectsPage,DirBrowser}.tsx` | 项目管理页 + 目录浏览（新建项目） |
| `desktop/src/i18n.ts` | 国际化（中文 / English） |
| `lumi/gateway/channels/ws.py` | FastAPI WS / HTTP 传输端点，RPC 编排在 GatewaySession |
| `lumi/gateway/projects.py` | 项目清单持久化（`~/.lumi/lumi.json` 的 `projects` 分区） |
| `lumi/gateway/protocol.py` | BridgeEvent → wire 序列化 |
| `lumi/gateway/cron_rpc.py` | 定时任务 RPC 方法实现 |
| `lumi/gateway/broadcast.py` | 进程级广播中枢 `BroadcastHub`（含 cron 结果投递通道） |
| `lumi/gateway/bridge/core.py` | LangGraph ↔ 前端中立桥接层 |
| `lumi/models/provider_store.py` | 模型供应商 profile 持久化（`~/.lumi/lumi.json` 的 `providers` 分区） |
| `lumi/sessions/session_store.py` | 会话列表从 checkpoint 派生 |
| `lumi/sessions/session_meta.py` | 会话用户元数据 sidecar |
| `protocol/events.json` | 协议单一事实源 |


## 已确认的界面与动效规范

`index.css` 统一四层低对比暖灰表面，保留 `--color-accent` 品牌金；圆角使用 4 / 8 / 12 / 18 / 22px / full。常用控件高 24 / 28 / 32px，40px 仅作为显式大尺寸变体。同组操作保持相同高度、字号与内边距，审批和澄清卡底栏固定 32px。输入岛聚焦使用 200ms 淡金双环，聚焦后保持稳定。

时长令牌为 60 / 120 / 200 / 300 / 450ms，搭配 out / snap / overshoot 曲线。`lib/motion.ts` 将四套已选质量-弹簧-阻尼参数采样为原生 CSS `linear()`，分别用于图标按压、浮层、复制反馈和面板；无需新增动画库。系统 `prefers-reduced-motion` 会关闭逐字、持续光点和位移动效。`SegmentIndicator` 依据真实控件尺寸，用 120ms 共享底板过渡，支持字体、语言与窗口宽度变化；设置的垂直导航保持原有布局。

助手正文只对流式新增字素做 6px→清晰、90ms 消解，按原始 Markdown offset 识别新增内容，代码块保持高亮与可选文本；首次实时出现的消息包括首个 delta 都可动画；已展示消息重新挂载以已有文本为基线，切会话、重连和历史加载不重播。大批 delta 的 30ms 字素级联延迟封顶 180ms，避免视觉积压。代码块共用 `Markdown` 的语言栏和局部复制，沿用 1.5 秒对勾反馈。本地新消息以 `arrival` 标记记录是否已展示；用户消息上锚前后保持稳定 key，助手消息在各 delta 间保留相同标记。已展示标记用 WeakSet 记录，重挂载不重播。

思考、工具明细和工具组共用 200ms Grid 折叠，收起内容设为 `inert`；工具组默认运行 / 出错时展开，用户明确开合后不再抢回选择。运行状态光核呼吸，等待确认用稳定金点，连接失败用静止红点；没有主轮次与后台任务时隐藏状态行。后台仍运行则显示任务数量，点击打开当前会话右栏，完成记录继续保留在右栏。


### 供应商配置预设

添加供应商采用两步表单：先选择预设（百炼、DeepSeek、Kimi、Anthropic、OpenAI、
MiniMax、OpenRouter、Gemini 或自定义），再填写连接与模型。返回选择页保留本次草稿。
接口类型仅在自定义表单显示；兼容规则位于默认折叠的高级设置。既有模型目录映射、
上下文和输出上限覆盖保留。测试请求携带所选 `protocol` / `compatibility`，保存后实际
调用使用相同配置；原生 Gemini 由 `langchain-google-genai` 提供。
