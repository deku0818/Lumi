# 飞书妙记自动纪要 — 需求文档

录音 / 会议结束后，Lumi 自动读取飞书妙记的**逐字稿**，生成纪要与待办并推送到飞书。
不使用飞书自带的 AI 纪要，只取原始逐字稿，纪要由 Lumi 自己生成。

现有飞书 channel 架构见 [`feishu.md`](./feishu.md)。

---

## 设计要点：Lumi 只当传令兵

取数与生成**全部交给 agent + `lark-cli`**，Lumi 侧只负责「事件到达 → 通知 agent」一件事。

```
lark-cli event consume minutes.minute.generated_v1 --as user
   │  NDJSON，一行一个事件（含 minute_token）
   ▼
Lumi 读到事件 → 塞入通知队列 → 注入对用户不可见的消息
   ▼
agent 自主调用 lark-minutes skill 取逐字稿 → 生成纪要 → 推送到群
```

**因此 Lumi 不需要实现 OAuth、不需要管理 token、不需要调妙记 API。** 这些由 `lark-cli`
承担（Device Flow 登录 + 自动续期），是本方案相对早期设计的最大简化。

## 目标场景

`minutes.minute.generated_v1` 一个事件覆盖两个场景，无需分别处理：

- **个人录音** — 手机端录音、上传音视频文件生成的妙记
- **团队会议** — 会议云录制生成的妙记

`minute_source` 字段有值 = 会议来源，无值 = 个人录音/上传。

> **不要用 `vc.meeting.recording_ready_v1`**：只对通过 Open API 预约的会议生效，
> 手动开的会不触发，且不覆盖个人录音。

### 候选事件

`lark-cli event list` 中的相关项（均为 `user` 身份）：

| EventKey | 触发时机 | 备注 |
|---|---|---|
| `minutes.minute.generated_v1` | 妙记生成后 | **首选**，覆盖录音与会议 |
| `vc.recording.recording_transcript_generated_v1` | 逐字稿条目生成时 | 更贴近"逐字稿就绪"，但**仅连接飞书客户端时产生** |
| `vc.meeting.participant_meeting_ended_v1` | 本人参与的会议结束 | 早于逐字稿就绪，不适合直接触发 |

选型取决于下方风险 1 的实测结果。

---

## 双应用职责分离

当前环境存在**两个飞书应用**，各司其职，无需合并：

| 应用 | App ID | 职责 |
|---|---|---|
| Lumi 飞书 bot | `cli_a96e4529bd78dcbd` | 接收 IM 消息、**推送纪要到群**（已有能力） |
| lark-cli | `cli_a94d4f780e78dcc9` | **订阅事件、读妙记逐字稿**（用户身份） |

> 若后续希望统一为单一应用，需要把妙记相关 scope 与用户授权迁移到 Lumi 的 app，
> 但这会把 OAuth 负担重新拉回 Lumi 侧，**不建议**。

## 授权现状（已就绪）

`lark-cli auth login` 走 **Device Flow**（设备码 / 扫码），因此：

- 不需要 redirect_uri 白名单
- 不需要 Lumi 实现 OAuth 回调与 token 续期

已完成授权（用户：鄢楚威 `ou_6d41...`，`tokenStatus: valid`），实测有效期与官方文档一致：

| 凭证 | 有效期 | 实测值 |
|------|--------|--------|
| `access_token` | 2 小时 | `expiresAt` = 授权后 2h |
| `refresh_token` | **7 天** | `refreshExpiresAt` = 授权后 7d |

⚠️ **连续 7 天未刷新则需重新授权**。lark-cli 自行续期，但若长期停用需重新 `auth login`。
产品上应在纪要推送失败时给出明确提示，而非静默失效。

已开通的相关 scope：`minutes:minutes.basic:read`、`minutes:minutes.artifacts:read`、
`minutes:minutes:readonly`、`minutes:minutes.search:read`、`minutes:permission:apply`、
`offline_access`、`vc:record:readonly`、`vc:note:read`。

---

## 逐字稿获取（已验证）

```bash
lark-cli minutes +detail --minute-tokens <token> --transcript --summary --todo --as user
```

逐字稿写入 `./minutes/{minute_token}/transcript.txt`，JSON 返回 title / note_id / summary / todos。

**实测格式**（26 分钟录音 → 221 行 / 21KB）：

```
2026-07-16 17:00:06 CST|26min 50s          ← 录制时间 + 时长
Keywords:
工作流、向量、搜索、检索、框架、…             ← 飞书已抽取的关键词

Speaker 1 00:01:13.110                     ← 未识别说话人 + 毫秒时间戳
<发言内容>
鄢楚威 00:02:45.320                        ← 已识别的实名说话人
<发言内容>
```

**带说话人区分 + 毫秒级时间戳**，足以支撑按发言人归因的纪要与待办。

> 应用身份（tenant_access_token）读取会被拒（`403 / 2091005 permission deny`），
> **必须用户身份**。这正是交给 lark-cli 的原因。

---

## Lumi 侧改动点

### 1. 事件入口（新增，需先升级 lark-oapi）

**在现有 WS 长连接上加一个 handler 即可，不新增连接**（`channel.py:192` `_build_event_handler`
目前只注册了 IM 消息事件）：

```python
.register_p2_minutes_minute_generated_v1(handler)
```

⚠️ **前置：`lark-oapi` 需从 1.5.5 升级到 ≥1.7.1**。本机 1.5.5 无妙记事件支持
（186 个 register 方法中无 minutes 相关）；1.7.1 已提供 `register_p2_minutes_minute_generated_v1`
及 model `P2MinutesMinuteGeneratedV1`（字段 `minute_token` / `minute_source` / `subscriber_ids`）。

> 升级跨 minor 版本，而飞书 channel 重度依赖该 SDK（WS 长连接、CardKit 流式卡片、IM 收发），
> **升级后需回归飞书 channel**。按项目约定走 `uv` 管理，不直接改 `pyproject.toml`。

**不采用 `lark-cli event consume`** —— 它会新建一条事件连接，与 `lumi serve` 的 WS
争抢 app 唯一连接（见下方「事件连接独占」）。lark-cli 仅用于一次性订阅与 agent 取数。

### 1b. 平台侧一次性配置

1. 开发者后台 →「事件与回调」→ 事件订阅 → 添加 `minutes.minute.generated_v1`（可能需发布版本）
2. 用户级订阅（已完成）：
   ```
   lark-cli api POST /open-apis/minutes/v1/minutes/subscription \
     --data '{"event_type":"minutes.minute.generated_v1"}' --as user
   ```
   **两者缺一不可**：只调订阅接口而后台未加事件，平台不会推送。

### 2. 事件 → agent 通知（几乎全部复用）

现有 `NotificationQueue` 是通用的 `(thread_id, payload_str)` 队列，与后台任务语义无耦合：

```
enqueue(xml, thread_id)                         bg_tasks.py:117
   ↓ [2s 轮询]
notification_loop()                             inbound.py:593
   ↓ try_lock 空闲检查（会话忙则跳过，下轮重试）
   ↓ 发锚点卡（流式卡片需依附于一条消息）
_run_notification_turn()                        inbound.py:631
   ↓ run_turn(..., synthetic=True)              outbound.py:38
   ↓ synthetic_human_message → items: [] = 用户不可见
agent 自主调用工具 → 流式卡片推回群
```

**可直接复用**：空闲加锁、锚点卡、取消安全的重新入队、`_drain` 交接。

**需要处理**：

1. **独立 payload 通道** — 复用后台任务队列会带上 `"Read the output file"` 等措辞
   （`compose_notification_hint`，`bg_tasks.py:176`），且 `/stop` 会报"正在处理后台任务通知"。
   建议并行队列 + 独立的 hint 组装。
2. **`pool.chat_ids` 是内存态、懒填充** — 仅在用户发消息时写入（`inbound.py:414`），
   重启后未对话过的 chat 对轮询器不可见，事件将无法投递。需持久化映射，或由
   `feishu_thread_id(chat_id)` 推导后主动 `pool.get()` 并回填。
3. **锚点卡文案参数化** — 当前硬编码 `"✅ 后台任务已完成，正在整理结果…"`（`inbound.py:643`）。
4. **通知轮次不注册在 `pool.run_tasks`**，`/stop` 无法取消（`inbound.py:498-502`），需确认可接受。

### 3. 纪要生成与推送（无需新代码）

agent 通过已装好的 `lark-minutes` skill（`.lumi/skills/lark-minutes/`，依赖 `lark-cli` 二进制）
自主取数并生成，推送复用现有 `send_markdown`。

### 推送目标：私聊本人

纪要推送到**用户与 Lumi bot 的私聊会话**。

**open_id 来源**：事件 payload 的 `subscriber_ids`（`List[UserId]`，含 `open_id` / `user_id` /
`union_id`）——即订阅者本人，天然就是该推送的对象，无需额外配置。

> 注意：`lark-cli event consume` 输出的是**简化后**的 payload（只有 type / event_id /
> timestamp / minute_token / title），`subscriber_ids` 需走 SDK 原生 handler 才能拿到。

**发送方式**：现有 `send_message_sync(receive_id_type="open_id", receive_id=<open_id>, ...)`
（`channel.py:380`）直接支持按 open_id 投递，无需先取 chat_id。

**需要处理的问题**：跑 agent 轮次需要一个稳定的 `thread_id`，而现有映射
`pool.chat_ids` 是**内存态、懒填充**——仅在用户主动发消息时写入（`inbound.py:414`），
重启后若用户尚未与 bot 私聊过，轮询器看不到该会话，事件将无法投递。

两种解法（实施时择一）：

1. 由 open_id 派生稳定 thread（如 `feishu-dm-{open_id}`），主动 `pool.get()` 并回填 `chat_ids`
2. 首次收到事件时查询/创建私聊 chat_id，持久化该映射

方案 1 更简单且不依赖用户是否先私聊过 bot，**推荐**。

---

## 端到端实测（2026-07-19）

一次完整的真实链路验证，38 秒个人录音：

| 时刻 | 事件 |
|------|------|
| 15:41:10 | 录音开始（38s） |
| 15:41:48 | 录音结束 |
| **15:42:07** | 事件到达（延迟 **~19s**） |
| **15:42:09** | 逐字稿可读（**距事件 0s，首次调用即成功**） |

事件 payload（经 lark-cli 提取后）：

```json
{"type":"minutes.minute.generated_v1",
 "event_id":"e2804a74f83b505744ee3271fccf4f6b",
 "timestamp":"1784446927252",
 "minute_token":"obcnwjs6iqx5c19x8693x967",
 "title":"新录音 7"}
```

### 关键结论：无需重试机制

**事件语义是「妙记已生成完成」，不是「开始生成」。事件到达时逐字稿必然就绪。**

因此**不需要**退避重试、不需要轮询——收到事件直接读，一次成功。
文档中此前列为最大风险的 `2091003 minute not ready` 退避策略，实测证明不必要。

> 注：本次为 38 秒短录音。长录音的转写耗时更久，会拉长「录音结束 → 事件到达」这一段，
> 但「事件 → 逐字稿就绪」的因果关系不变（事件本就在生成完成后触发）。

## 风险与待验证项

| # | 项 | 影响 | 状态 |
|---|---|------|------|
| 1 | 事件与逐字稿就绪的时序 | 决定是否需要重试 | ✅ **已验证：事件到达即就绪，无需重试** |
| 2 | 逐字稿格式 | 决定纪要能做多细 | ✅ 已验证，带说话人 + 毫秒时间戳 |
| 3 | 用户身份读取权限 | 决定取数路径 | ✅ 已验证，user 身份可读（应用身份被拒） |
| 4 | OAuth / redirect_uri | — | ✅ 已消解，Device Flow 无此需求 |

**全部验证完毕，无剩余技术空白。**

## ⚠️ lark-cli consume 退出会取消订阅（最隐蔽的坑）

**`lark-cli event consume minutes.minute.generated_v1` 退出时会调
`/open-apis/minutes/v1/minutes/unsubscription` 取消服务端订阅。**

`minutes` 属于带 **PreConsume hook** 的 EventKey：consume 启动时调 `subscription` 注册，
优雅退出（SIGTERM / stdin EOF）时调 `unsubscription` 注销。lark-cli 自身文档写明：

> for EventKeys with a PreConsume hook (those that register server-side subscriptions
> via OAPI), `kill -9` skips the OAPI unsubscribe and leaks server-side subscriptions

即**「优雅退出 = 撤销订阅」是它的设计意图**（它假设订阅是临时借用的），而本功能需要订阅
长期存在，两者诉求相反。

**症状**：用 lark-cli 调试完事件后停掉它，Lumi 侧妙记功能**静默失效**——WS 连接正常、
handler 正常、无任何报错，就是永远收不到事件。实测踩坑耗时最久的就是这一条。

**处置：已自愈，无需人工干预。** 飞书 channel 每次启动都会调
`_ensure_minute_subscription()`（`channel.py`）幂等重建订阅：

```bash
# 等价于 channel 启动时自动执行的命令
lark-cli api POST /open-apis/minutes/v1/minutes/subscription \
  --data '{"event_type":"minutes.minute.generated_v1"}' --as user
```

订阅接口只认 user_access_token 而 Lumi 只持有 app 身份，故 shell out 调 lark-cli
（agent 取逐字稿本就依赖它，非新增依赖）。失败只记 warning 不阻断 channel 启动——
IM 收发不依赖订阅，只是妙记纪要不可用。

这一步同时兜住了其它失效来源（user token 过期、换机器重新授权），并且**把静默失效
变成启动时可见的一行日志**：

```
INFO | channel.start - 妙记事件订阅已确认
WARNING | channel.start - 妙记事件订阅失败，妙记纪要将不可用: <原因>
```

> 前提：`lark-cli` 在 PATH 且已 `auth login`。二者缺一时上述 warning 会写明原因。

## ⚠️ 事件连接独占（重要运维约束）

**一个飞书 app 全局只允许一条事件长连接**，第二个连接会被平台拒绝：

```
another event bus is already connected to this app
(1 remote event connection(s) detected via API)
```

实测踩坑记录：

- `lumi serve` 的飞书 channel 持有 `cli_a96e...` 的 WS 长连接
- `lark-cli event consume` 想连同一 app → 被拒，且**静默表现为收不到任何事件**
  （`RECEIVED` 恒为 0，不报错），极难排查
- `lumi serve` 由桌面端以 `--exit-with-parent` 拉起，**kill 后会被自动重启**，
  必须完全退出 Lumi 桌面应用才能释放
- 连接释放在平台侧**有延迟**，可用 `GET /open-apis/event/v1/connection`
  （**需应用身份**，user token 报 `99991668`）查 `online_instance_cnt` 确认归零

**判断连接是否真正建立**：stderr 出现 `[source] feishu-websocket: connected` 才算连上；
只有 `ready event_key=...` 而无此行 = WS 未建立，事件永远不会到。

### 对最终方案的影响

**生产方案不受此约束** —— Lumi 在自己已有的那条 WS 长连接上注册妙记 handler，
不新增连接，天然无冲突。此约束仅影响**用 lark-cli 做本地验证**的场景：
验证期间必须完全退出 Lumi 桌面应用。

## 前提条件

- 会议场景必须开**云录制**（本地录制不生成妙记）
- 读取受妙记自身分享权限约束（`2091005 permission deny` = 该妙记权限设置中无导出权限）

---

## 附：飞书文档查询方法

官方文档站是 SPA，直接抓取拿不到正文。其后台 JSON 接口可直接取 markdown：

```
GET https://open.feishu.cn/document_portal/v1/document/get_detail?fullPath=<去掉 /document 前缀的路径>
```

例：`/uAjLw4CM/ukTMukTMukTM/minutes-v1/minute/events/generated`
