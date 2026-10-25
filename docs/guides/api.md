# Gateway 与文件 API

独立服务通过 `lumi serve` 提供 WebSocket JSON-RPC 对话和 HTTP 文件传输。没有独立的 REST 聊天端点，也不提供 OpenAI Chat Completions 兼容服务。桌面原生远端使用 [SSH 数据通道](deploy.md)，不在 UI 中填写服务 URL。

## 启动与鉴权

```bash
lumi serve --host 127.0.0.1 --port 8765
```

令牌可用启动前的 `LUMI_TOKEN` 或 `--token` 配置，客户端在查询参数 `token` 中传递并做 URL 编码。未配置令牌时校验放行；服务默认只监听 loopback。后端所选项目、模型和工具环境均在服务所在机器。

## WebSocket 对话

连接 `/ws?token=<编码后的令牌>`。服务器先发送 `gateway.ready`；客户端使用以下信封，`id` 用于请求与响应配对：

```json
{"id": 1, "method": "new_session", "params": {"workspace": "/absolute/project"}}
```

目录须存在。收到该请求的成功响应后，当前连接已绑定新会话：

```json
{"id": 2, "method": "send_message", "params": {"content": "概括 README", "tool_mode": "auto"}}
```

响应形状为 `{id, result}` 或 `{id, error: {message}}`。执行中的事件独立发送，不属于某个 RPC 的响应：

```json
{"method": "event", "params": {"type": "message.delta", "session_id": "<thread_id>", "payload": {"text": "..."}}}
```

客户端应持续接收事件直到轮次完成，并独立处理 RPC 响应；不能假设每次读取都是当前请求的结果。`turn.start` 提供用户消息 ID，`message.*` / `tool.*` 提供过程，`turn.complete` 表示轮次结束，错误通过 error 事件或请求错误返回。

已有会话使用 `switch_session {thread_id}`；历史通过 `load_history` 分页读取。`stop` 中止当前轮。人工审批 / 提问通过 `resume {approval_id, value}` 在原轮中原地恢复，**resume 不开新流**。没有交互客户端时应选择无人值守模式；权限语义见 [权限指南](permissions.md)。

同连接一次运行一轮；运行中的 `send_message` 可作为插话，立即返回 queued，稍后由 `message.injected` 确认注入。不要在连接中断后无条件重发可能产生副作用的请求。后端存活时可带 `thread` 重连续接，进程重启不自动续跑任务。

所有 RPC、参数与事件字段以 [protocol/events.json](../../protocol/events.json) 为准；生产者、消费者和验证方式见 [协议维护说明](../../protocol/README.md)。

## 文件 HTTP 端点

| 请求 | 参数 / 请求体 | 返回 |
|---|---|---|
| `GET /file` | query：`path`、可选 `token` | 文件内容与 MIME；最大 128 MiB |
| `HEAD /file` | 同上 | 文件存在性与响应头 |
| `POST /upload` | query：`name`、可选 `token`；body：原始文件字节 | JSON `{path}`，后端落盘的绝对路径；最大 128 MiB |

上传不是 multipart 表单。获取上传路径后，通过 `send_message.params.files` 的路径数组引用附件。`path` 指向后端文件，不是客户端本地文件；本机直接引用已有路径时须确保运行后端能读取它。

文件端点与 WS 使用相同令牌。`/file` 不限制在项目目录内，实际访问受后端进程的操作系统权限约束。实现与端点测试分别在 `lumi/gateway/channels/ws.py` 和 `tests/server/`。
