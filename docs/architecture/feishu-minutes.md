# 飞书妙记事件与逐字稿

开启渠道的「妙记纪要」后，飞书新生成妙记会触发用户私聊中的助手轮次。Lumi 提供逐字稿取数命令，由 Agent 询问用户下一步，如生成纪要或安排后续任务；当前提示不固定要求直接产出纪要。接入步骤见 [飞书指南](../guides/feishu.md)。

## 事件与运行链路

```text
飞书 minutes.minute.generated_v1
  → 现有 lark-oapi WS handler
  → on_minute_generated：去重，按 subscriber_ids 定位用户
  → BridgePool.minute_events
  → 通知轮询等待对应私聊空闲
  → 合成助手轮：transcript_hint + lark-cli 取数提示
  → 原私聊中的流式回答
```

`channel.py` 在现有长连接注册妙记 handler，不另开事件连接。只在机器人运行且 `minutes_enabled` 为真时处理；事件缺少 token 或订阅者 open_id 时不入队。事件去重是进程内 LRU，待处理事件也在内存中，不承诺跨进程重启恢复。

接收对象来自 `subscriber_ids[].open_id`。私聊入站与主动推送共用 `feishu_p2p_thread_id` 和机器人前缀，避免同一个人的私聊裂成两个会话。已有真实 chat_id 时沿用它，否则按 open_id 投递。处理与其他轮次共用 thread 锁，忙时等待，不混入正在生成的普通回答。

## 身份与授权

每个机器人使用自己的 lark-cli profile：保存时复用同 app 的已有 profile，或新建专属 profile；不会依赖用户全局 active profile。项目内的 shell 经 `LARKSUITE_CLI_PROFILE` 注入机器人身份。无 `cli_profile` 时诊断要求先保存并同步，启动也跳过妙记订阅。

Lumi 不自行维护 OAuth token，用户授权、存储与刷新由 lark-cli 完成。桌面 `minutes_auth.py` 包装设备码登录：

1. 核对 app 与 profile；仅允许机器人身份时先返回受限状态。
2. 用户确认后，只调整当前机器人 profile 的身份限制，再请求设备码。
3. 生成二维码与原始授权链接，等待授权完成；过期、取消和关闭设置都回收等待任务。
4. 成功后重新体检并重建订阅。本机与 SSH 后端在各自机器执行，授权不会跨机器复制。

所需用户身份权限来自 `scopes.py` 的 `MINUTES_SCOPES`：`minutes:minutes.basic:read`、`minutes:minutes.transcript:export`。开放平台授权范围与用户实际登录授予范围都需满足；应用身份权限不能替代用户身份。诊断按 CLI 的用户身份可用性判断，不能把可自动刷新的 access token 误判为必须重新登录。

## 订阅与体检

`minutes.ensure_subscription(profile)` 调用 lark-cli 的用户身份订阅接口。渠道启动和妙记体检都会尝试幂等建立订阅；失败在体检或日志显示，不阻断普通 IM 收发。

开放平台需订阅 `minutes.minute.generated_v1` 并发布应用版本。不要在 Lumi 运行时另起 `lark-cli event consume`：它不属于生产链路，而且退出可能撤销服务端订阅。调试后在体检面板重新检查，必要时重连渠道。

## 逐字稿读取

`minutes.transcript_hint` 注入 minute token、临时输出目录以及以下命令：

```bash
lark-cli minutes +detail --minute-tokens <token> --transcript --as user
```

命令在 Lumi 私有临时目录运行，逐字稿落在其 `minutes/<token>/transcript.txt`，避免污染项目。POSIX 使用局部子 shell，Windows 使用 pushd/popd，防止改变持久 shell 的后续 cwd。临时目录由 `lumi/utils/paths.py` 管理；内容带说话人与时间戳，读取仍受妙记自身的分享和导出权限约束。

当前实现收到生成事件即提供读取指引，没有固定的轮询等待或退避流程；读取失败交助手按具体错误处理。会议需开启云录制，本地录制不生成对应妙记。功能只处理开启后收到的事件，不补发历史妙记；旧记录可由用户提供链接后按需读取。

## 模块与验证

| 模块 | 职责 |
|---|---|
| `lumi/gateway/channels/feishu/channel.py` | WS handler、开关、启动订阅 |
| `lumi/gateway/channels/feishu/inbound.py` | 事件去重、排队、私聊路由与运行 |
| `lumi/gateway/channels/feishu/minutes.py` | 订阅、体检、逐字稿提示 |
| `lumi/gateway/channels/feishu/minutes_auth.py` | 设备码授权与取消 |
| `lumi/gateway/channels/feishu/lark_profile.py` | 每机器人 CLI 身份同步 |
| `tests/gateway/test_minutes_auth.py` | 授权与订阅相关回归 |
| `desktop/tests/minutes-auth.test.cjs` | 桌面授权状态与清理 |

早期短录音实验只记录了录音结束到事件到达、逐字稿可读的时间，不能推导纪要生成及投递时延。原研究记录见 [归档](../archive/feishu-minutes-research.md)。
