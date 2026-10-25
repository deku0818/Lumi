# 在途审批与提问

桌面工具审批和主代理 `ask` 通过 `ApprovalBroker` 的内存 Future 原地等待回答。一条用户轮从 prompt 到 `turn.complete` 保持同一事件流，回答不重放工具节点；checkpoint 继续负责会话历史持久化。

## Broker 与协议

实现位于 `lumi/agents/core/broker.py`，由 Bridge 注入 `LumiAgentContext.approval_broker`：

| 方法 | 语义 |
|---|---|
| `request(payload, reject_value)` | 分配 approval_id，派发 custom event，等待 Future；退出时移除注册项 |
| `resolve(approval_id, decision)` | 按 ID 唤醒请求；未知或已完成 ID 返回 False |
| `reject_all()` | 用各请求自己的拒绝/取消值收尾，返回处理数 |

```text
ToolExecutor / ask
  → broker.request
  → lumi_approval custom event
  → Bridge：approval.request / clarify.request（带 approval_id）
  → 客户端 resume {approval_id, value}
  → broker.resolve
  → 原节点继续 → 原事件流继续
```

`resume` 是非流式控制 RPC；原运行 task 与锁在等待审批时仍存活。流式调用、响应格式和事件字段见 [协议](../../protocol/README.md)。`astream_events` 显式使用 v2，custom event 带有运行归属信息。

工具审批的 `value` 可用 `{decision: "approve"|"reject", message?, set_tool_mode?}`，也兼容逐项 `decisions`；缺项或非法结果按拒绝处理。主代理提问使用问题 schema 和答案映射，见 `ask.py` 与 `ClarifyDialog.tsx`。

## 逐个工具审批

`is_use_tool` 对有工具调用的响应统一路由到 `ToolExecutor`。执行器在批次开始热重载权限，然后对每个调用独立决定、审批并执行：

- deny 拒绝，allow 立即执行；其他结果按当前审批模式处理。
- AI 每次只判断一个调用；拒绝或分类器异常只拒绝当前调用，不回落人工审批。
- 人工待审调用独立等待 Broker，已允许的兄弟调用可以立即执行。
- 结果按原 tool_call 顺序写回。只有全部未执行且存在人工拒绝时才结束本轮；规则 / AI 拒绝交回模型调整。
- Broker 的 stop 标记结束整轮，即使已有兄弟调用执行，也不再继续模型。

`human_approval()` / `auto_classify()` 是执行器内的辅助函数，图中的同名旧节点只用于恢复已有 checkpoint。权限矩阵见 [权限架构](permissions.md)。

## 停止、切换与断连

停止或切换 thread 时，`reject_pending()` 先以拒绝/取消结果收尾挂起请求，让图干净完成并保留用户消息；没有挂起请求时可硬取消运行 task。切换等待旧轮释放连接锁，停止请求先快速返回。Broker 没有单独的 cancel 方法；外层 task 取消会传播至 Future 等待，并在 finally 清理注册项。

传输断开而后端仍存活时，活跃或挂起轮可随 `GatewaySession.detach()` 原地保留。同 thread 重连时恢复会话，重发待处理审批；默认最多保留 8 小时。后端进程重启后 Future 消失，不自动重放副作用请求或继续审批。详见 [断连续接](desktop.md#断连续接会话与传输解耦)。

进程内同 thread 的运行和删除由 `sessions/thread_runs.py` 共用锁协调，避免多个连接并发写 checkpoint；连接自身的运行锁保护 Bridge 指向。这个互斥不跨后端进程。

## 无人值守与子代理

cron、dream、Workflow 临时执行单元和持久 Lumi 子代理不接人工 Broker。模式为 default / accept_edits 时使用全局无人值守设置，显式 auto / privileged 保留；deny 总是拒绝。若仍有调用进入人工审批辅助函数，无通道时只拒绝该调用。

持久 Lumi 子代理通过 `request_parent_input` 将 `ask` 问题回传主 Agent，保存问题后结束当前轮；主 Agent 回答，再用 `agent_message` 继续原实例。Codex / Claude Code 使用各自原生提问适配，不把外部工具映射为桌面 Broker 审批。执行权限见 [子代理架构](agents.md)。未采用的 ACP 设计见 [历史归档](../archive/acp-client.md)。

## 回归验证

- `tests/gateway/test_approval_broker.py`：请求寻址、并发、拒绝收尾与取消清理。
- `tests/test_per_tool_permissions.py`：同批调用互不阻塞、结果配对与局部拒绝。
- `tests/test_human_approval.py`、`tests/test_ask.py`：审批与主代理提问语义。
- `tests/gateway/test_gateway_session.py`：非流式 resume、停止与会话切换。
- `tests/gateway/test_protocol_contract.py`：RPC / 事件名称契约。
