# 飞书 Channel 架构

飞书（Lark）IM channel 的内部实现——把 Lumi Agent 接到飞书机器人，私聊 / 群 @ 即可对话。
用户使用指南见 [`docs/guides/feishu.md`](../guides/feishu.md)。是首个 IM channel，设计可延伸到
企业微信等（gateway 层的 channel 中立化见 [`refactor-plan.md`](./refactor-plan.md)）。

---

## 架构总览

```
飞书开放平台
   │  wss://msg-frontier.feishu.cn/ws/v2  (lark-oapi 长连接，无需公网 webhook)
   ▼
┌──────────────────────── lumi serve 进程 ────────────────────────┐
│  FeishuChannel (channel.py)                                     │
│   ├─ lark ws.Client.start()  ← 独立 daemon 线程 + 独立 event loop │
│   │    入站事件 ──run_coroutine_threadsafe──▶ 主事件循环         │
│   ├─ FeishuInbound (inbound.py)   解析/去重/白名单/群策略/媒体/排队 │
│   ├─ FeishuStreaming (streaming.py)  CardKit 打字机卡片           │
│   └─ BridgePool (bridge_pool.py)  每 chat 一个常驻 AgentBridge    │
│                                                                 │
│  ChannelManager (manager.py)  进程级单例：起停 channel、拥有会话池 │
│  channel_rpc.py  get/save/test_channel ← desktop 经 WS RPC 调   │
│  store.py  ~/.lumi/lumi.json (channels, 600)                    │
└─────────────────────────────────────────────────────────────────┘
```

每个飞书 chat（私聊 / 群）→ 一个常驻会话 `thread_id = sanitize_thread_id(f"feishu-{chat_id}")`
→ 一个 `AgentBridge`，复用与 desktop 完全相同的 Agent 运行时（`stream_response` 产 `BridgeEvent`
流）。飞书侧只做「传输适配 + 事件折叠成卡片」，不碰 bridge / graph。

## 传输层（lark WS）

lark `ws.Client.start()` **阻塞**在 `run_until_complete(_select())`，且模块级抓
`asyncio.get_event_loop()`。故跑在**独立 daemon 线程 + 独立 event loop**，并 `patch
lark_oapi.ws.client.loop`，与 uvicorn 主 loop 隔离。入站回调在 WS 线程触发，经
`run_coroutine_threadsafe(on_message, main_loop)` 投回主 loop 喂 bridge。

**停止**：lark 无公开 stop（`auto_reconnect` 默认开，仅置 `_running=False` 它永不返回 →
每次 reload 泄漏线程+连接）。`stop()` 置 `_auto_reconnect=False` + 从主线程
`ws_loop.call_soon_threadsafe(ws_loop.stop)` 打断 `start()`，daemon 线程退出；并给
`_on_message_sync` 加 `_running` 守卫，旧实例丢弃入站。

> **部署坑**：环境有 SOCKS 代理（`ALL_PROXY`/`https_proxy=socks5://…`）时 lark WS 需
> `python-socks`，否则报 `connect failed, requires python-socks` 长连不上。已随
> 主依赖装。`open.feishu.cn` 通常直连可达，WS 端点是 `msg-frontier.feishu.cn`。

## 入站（inbound.py）

`on_message` 流水线：去重（LRU by message_id）→ 跳过机器人自身 → 白名单（`is_allowed`）→
群策略（`group_policy=mention` 时仅 `@_all` 或精确匹配 `bot_open_id` 才响应；**不做** ou_
启发式以免把真人误判为机器人）→ 解析文本（text / post）→ 收集媒体引用 → 解析发送者显示名
（`channel.directory`，群聊走群成员源、私聊走通讯录源）→ 派生 thread + 取 bridge + 运行锁 →
排队或处理。发送者名挂在 `_Pending.sender_name` 上（解析失败恒退兜底名），渲染为
`<sender>姓名</sender>` 标签行（`constants.SENDER_TAG`，纯给模型看）；每条原始消息的
`{sender, ts, text}` 另经 `message_meta` 结构化写进 `additional_kwargs["lumi"]["items"]`，
desktop 气泡只读它、不反解析正文。群名/私聊人名同时同步进 session sidecar
（`channel_title`/`channel_kind`，desktop 会话列表显示名；兜底名不写盘，解析失败有 5 分钟
重试冷却）。每轮跑完广播 `channel.activity`（desktop 刷列表/旁观重载，见 desktop.md）。

**媒体**：
- 图片（image / post 内嵌 / 被回复消息的图）→ 下载 → 走仓库统一压缩管线
  （`media.maybe_resize_and_downsample_image` + `compress_image_with_token_budget`，满足
  5MB/2000px 硬约束 + token 预算）→ base64 Anthropic content block，与 desktop 发图同构。
- 文件 → 下载到 `<系统临时区>/lumi/feishu/<thread>/`（如 Linux `/tmp/lumi/feishu/<thread>/`）→ `bridge.add_folder()` 授权该目录给会话权限
  引擎 → `<attached-file>路径</attached-file>` 注入正文，agent 用 `read` 读（PDF 渲染、文本直读）。
- 回复某条消息时，一并拉取**被回复消息**里的图片/文件（用父消息 id 下载）。

**忙时排队 + 合并**（同会话同一时刻只跑一轮）：
- 上一轮在跑（运行锁被占）时，新消息存入 `_queues[thread]`（上限 `_MAX_QUEUE=10`，满则丢弃
  并提示）；忙判与 `async with lock` 相邻、其间无 `await`，事件循环上原子，避免 TOCTOU。
- 持锁者跑完后 `_drain`：把期间积压的消息**合并成一轮**（`merge_messages`：单条带发送者前缀；
  多条加 `<system-reminder>` + 编号列表、每条带发送者，媒体-only 占位保序），循环直到队空。
- 媒体下载在持锁后用 `asyncio.gather` 并发。
- 所有"拿锁跑用户轮"的入口统一走 `_locked_drain`：持锁 + 登记 `pool.run_tasks`（/stop 取消用）。

**斜杠命令**（解析在 `channels/commands.py`，渠道无关）：命令按类别天然定可用范围，无 surface
标注机制——skill 命令（含 `/dream`）恒全 surface；渠道系统命令（`SYSTEM_COMMANDS`：/stop
/clear /help）恒仅 IM，不进 `bridge.list_commands()`（desktop 有终止/删除按钮）。

- **skill 命令**：`_run_batch` 里仅"单条成批 + 纯文本"时识别，语法命中后对照
  `bridge.list_commands()`，命中走 `bridge.stream_command`（与 desktop 同一约定的 skill
  blocks），未知 `/xxx` 按普通文本喂模型。混批/带媒体一律当普通文本。
- **系统命令**：`on_message` 早期识别、即时执行不排队（/stop 恰是忙时才有意义）。
  `/stop` = 取消当前用户轮（`run_tasks` 登记的 task；先 `reject_pending` 再 cancel）+
  并发停掉本 thread 全部运行中后台任务（`bg_process.cancel_thread_bg_tasks`）+ 清积压队列
  （停到轮才清），取消后 `_drain_after_cancel` 接手取消窗口内入队的消息；通知 poller 持锁的
  轮不登记（cancel 会杀轮询），如实回复"无法中断"。`/clear` = 持锁 `delete_thread` +
  `delete_meta` + 广播（与 desktop 删除同口径），完成后接手持锁窗口入队的消息。`/help` =
  直答彩色 header 卡片（`available_commands` 模块级函数，不为此建桥）。
- 群聊 mention 模式下显示名可含空格（"Lumi Bot"），解析对 @ 开头文本取第一个空白后跟
  `/` 的位置作命令起点，误切由已知命令表兜底。

## 身份目录（directory.py + caching.py）

`FeishuDirectory`：`open_id → 显示名` / `chat_id → 群名` 的唯一出口，让 agent 在群聊里分得清谁
说的（每条消息带 `<sender>姓名</sender>` 标签）。两个数据源：群成员接口 `im.v1.chat_members.get`（群内显示名，
**不受**通讯录可见范围限制，群场景首选且覆盖新人）、通讯录接口 `contact.v3.users.batch`（私聊
等无群上下文时的退路），共享同一 `open_id → 名` 缓存。

- **缓存**：`CachingDirectory[K, V]`（`caching.py`）线程安全懒加载——命中不调 API，未命中的交 fetch
  在 executor 里批量解析，只把成功项写回，失败项用兜底名（`用户_xxxxxx` / `群_xxxxxx`）**不**写缓存
  保留重试。进程内缓存，重启清空。
- **启动预热**：`warmup()` 后台拉 bot 所在所有群 + 群成员灌入缓存（best-effort、不阻断启动、失败
  只记 warning）；`channel.start()` 里 `create_task` 起、`stop()` 取消（存引用防 GC）。
- **群成员补刷防狂刷**：`_fetch_members_for` 带 per-chat 冷却（成功 60s）+ 空结果指数退避
  （5s→300s），避免无权限的群每条消息刷整群。
- **所需应用权限**：`im:chat`（列群 / 群成员 / 群信息）、`contact:user.base:readonly`（通讯录批量）。
  未授权也能跑，显示名全部退化成兜底名。

## 出站事件泵（outbound.py）

`run_turn` 消费 `bridge.stream_response` 的 `BridgeEvent` 流，折叠成飞书消息（只处理主 agent
事件，子代理 `parent_run_id` 非空的内部活动不外显）：

| EventKind | 处理 |
|---|---|
| `message.delta` | 喂打字机流式卡片 |
| `tool.start` / `tool.complete` | 驱动「正在…」忙碌状态行 |
| `clarify.request` | ask 已禁用，正常不出现；防御性 `resolve_approval(ASK_CANCELLED)` |
| `approval.request` | 泄漏的工具审批 → 一律自动 `resolve_approval(reject)`，永不弹审批卡 |
| `turn.complete` / `error` / 异常 / 取消 | 收尾流式卡（`ended` 标志保证只收尾一次） |

**审批语义**：`tool_mode` 取配置（`auto` AI 审批 / `privileged` 自动放行），两档下「泄漏的人工
审批触点」（DENY / bypass-immune / 分类器异常回落）一律自动拒绝。**ask 工具已禁用**——`BridgePool`
默认 `disabled_tools=["ask"]`（经 `AgentBridge.initialize → create_agent(tools=…)`），模型无从
调用，遇需澄清时自行判断而非弹卡片。

## 后台任务完成通知（notification poller）

后台任务（bash `run_in_background` / agent / workflow）完成时，通知按归属 thread 入队到进程级
`NotificationQueue`。desktop 的通知轮对渠道会话**刻意跳过**（旁观连接不写共享 thread），飞书
会话由 `FeishuInbound.notification_loop` 认领（`channel.start()` 起、`stop()` cancel 后 await
收尾，保证卡片在 streaming 停掉前关闭）：

- 每 `NOTIFICATION_POLL_INTERVAL`（2s）tick：队列空直接跳过；遍历 `BridgePool.chat_ids`
  （thread→chat 映射，放**池上**随配置热重载存活——重载重建 inbound 但保留池）逐 thread
  `has_for` 精确快查，运行锁空闲才认领（忙时让行，下 tick 再来）。
- 认领流程：先发「✅ 已完成」**锚点卡**（流式卡片必须回复某条消息才能创建；锚点发送失败不
  drain，通知留队下 tick 重试）→ drain 精确归属的通知 → `run_turn(is_meta=True)` 注入 meta
  轮，模型读输出文件、结果经流式卡片推回群里 → `hub.on_channel_activity` 刷 desktop 旁观。
- 健壮性：被取消（channel 停止/重载）时已 drain 的通知**重新入队**，新 poller 认领不丢结果；
  单 thread 异常只记日志不杀轮询；持锁期间排队的入站消息由 poller 跑完后经 `_drain` 兜底取走。

配套约束在工具层：bash `run_in_background` 命令自带 shell 后台符 `&` 时直接报错
（`capability.has_background_operator`，引号/转义/heredoc/算术扩展感知）——否则被追踪的
wrapper shell 瞬间退出、真实进程脱管；`bg_process` 同时以独立进程组起任务（`start_new_session`
+ `killpg`），取消/超时连同命令内 fork 的后代一起终止。

## 流式卡片（streaming.py + throttle.py + update_queue.py）

CardKit「编辑同一张卡片」打字机：
- `Throttle`：双阈值（250ms / 64 字符）+ `loop.call_later` 主动定时，静默期也刷尾部。
- `UpdateQueue`：至多 1 in-flight + 1 pending 合并，压低 HTTP QPS 防限流。
- 失效换卡（`is_card_invalid` 错误码 → 换 card_id 重建，epoch 作废旧卡更新）。
- 超长截断（`_render_card_text` 取尾部 `STREAM_MAX_CHARS=20000`）；CardKit 全程不可用时
  `_fallback_send` 降级到普通 markdown 卡（同样截断）。
- 工具忙碌期 spinner 动画；纯工具轮（无正文）收尾落「✅ 已完成」而非空白卡。
- 孤儿 buf 由 `_cleanup_loop` 按 `STREAM_BUF_TTL=300s` 驱逐。

## 会话池（bridge_pool.py）

`thread_id → (AgentBridge, asyncio.Lock)`。运行锁串行化同会话的轮次。每 chat 一个常驻
bridge（含 graph / 权限引擎 / checkpoint），**刻意不做 TTL 回收**——进程存活期一直驻留、复用
checkpoint。`close_all` 回收前先 `reject_pending` + 等锁（5s 上限）避免 use-after-close。

## 配置与生命周期

- **存储**：`~/.lumi/lumi.json` 的 `channels` 分区（含密钥，chmod 600 原子写，照抄 `provider_store` 范式），
  与 config.json 解耦。凭证支持 `${ENV_VAR}`，启动时 `os.path.expandvars` 解析。
- **ChannelManager**（进程级单例）：`lumi serve` 的 lifespan 经 `channels_runtime()` 起它；
  desktop 经 `save_channel` RPC 改配置后 `reload()` **停旧起新**（`_reload_lock` 串行化）。
  **BridgePool 由 manager 拥有、跨传输重连存活**——改凭证/拨开关只重启 WS 连接，不清空进行中
  的会话；只在禁用 / workspace 变更 / 进程退出时回收会话池。
- **状态灯**：`status()` 返回 `off | stopped | connecting | connected | error`。connected 以
  lark WS 实际连接（`_ws_client._conn` 非空）+ bot_open_id 为准，掉线/重连期间如实回落
  connecting；启动失败（未装 lark / 缺凭证 / 异常）→ `error` 带原因，UI 直接显示。

## RPC（channel_rpc.py，照抄 cron_rpc 进程级分发）

`CHANNEL_METHODS = {get_channels, save_channel, test_channel}`，接 `session.py` 的 `_dispatch`
+ `IMPLEMENTED_METHODS` + `protocol/events.json`（契约测试锁一致）。`save_channel` 复用
`save_feishu` 返回的 cfg 传给 `reload(cfg)` 省一次读盘。`test_channel` 用给定凭证临时建 client
拉机器人信息验证连通性，不动运行中的 channel。

## 前端

desktop `设置 → 渠道`（`ChannelsPanel.tsx`，进 `SettingsDialog`）：渠道卡片列表（飞书状态灯 +
开关 + 编辑、企业微信「即将支持」）→ 飞书表单（凭证 / 审批模式 / 群策略 / 白名单 / 绑定项目 +
测试连接 + 保存并重连）。**绑定项目**（`WorkspacePicker`）不再手填路径：从该机器已登记的项目
（`list_projects`）里下拉选，可内联「新建项目」（`DirBrowser` + `add_project`）；切换已绑定项目
会弹确认提醒（保存后回收进行中的飞书会话、历史不丢），无项目时空态引导新建。空 = serve 进程
当前目录（兜底）。状态灯走品牌「光」语言（`.chan-orb` 复用 `lumi-breathe` 光晕，error 态红光 +
显示具体原因）。

## 关键文件

| 文件 | 职责 |
|---|---|
| `channels/feishu/channel.py` | lark WS 连接 + 收发 + 生命周期 + 状态 |
| `channels/feishu/inbound.py` | 入站解析 / 媒体 / 排队合并 / 驱动 run / 后台任务通知轮询 |
| `channels/feishu/outbound.py` | BridgeEvent 事件泵 |
| `channels/feishu/streaming.py` `throttle.py` `update_queue.py` | CardKit 打字机卡片 |
| `channels/feishu/bridge_pool.py` | 每 thread 常驻 bridge + 运行锁 + thread→chat 映射 |
| `channels/feishu/directory.py` | 成员/群名解析（open_id → 显示名）+ 两层缓存 + 启动预热 |
| `channels/feishu/caching.py` | 通用线程安全懒加载缓存 `CachingDirectory[K, V]` |
| `channels/feishu/lark_call.py` | lark SDK 同步调用错误样板 |
| `channels/{config,store,manager}.py` | 配置模型 / sidecar 存储 / 进程级管理器 |
| `channel_rpc.py` | get/save/test_channel RPC |
