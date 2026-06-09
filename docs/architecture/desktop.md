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

- **RPC 方法**：`send_message`、`resume`（流式）、`list_sessions`、`new_session`、`switch_session`、`load_history`、`pin_session`、`rename_session`、`delete_session`。
- **wire 事件**：`message.*`、`tool.*`、`clarify/approval/plan.request`、`turn.complete`、`error`，加握手帧 `gateway.ready`。

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

## 关键文件

| 文件 | 职责 |
|---|---|
| `desktop/electron/main.cjs` | sidecar 生命周期、窗口、端口分配 |
| `desktop/src/gateway.ts` | WS JSON-RPC 客户端（带指数退避重连） |
| `desktop/src/App.tsx` | 会话状态机、事件路由、聊天流渲染 |
| `desktop/src/components/Sidebar.tsx` | 会话列表 + 右键菜单 + 内联重命名 |
| `lumi/server/ws.py` | FastAPI WS 端点 + RPC dispatch |
| `lumi/server/protocol.py` | BridgeEvent → wire 序列化 |
| `lumi/agents/bridge.py` | LangGraph ↔ 前端中立桥接层 |
| `lumi/tui/session_meta.py` | 会话用户元数据 sidecar |
| `protocol/events.json` | 协议单一事实源 |
