# Agent 运行时架构

Lumi 的主对话由 `lumi/agents/core/graph.py` 中的 `LumiAgent` 装配 LangGraph 节点与边，再由 `AgentBridge` 向桌面、SSH 和 IM 渠道提供同一套运行能力。

## 图与状态

```text
START → Compact → PreprocessMessages → CallModel
                                       ├─ 有工具 → ToolExecutor → CallModel / END
                                       └─ 无工具 → OnAgentStop → END / CallModel
CallModel 的 prompt-too-long 异常 → Compact → 正常拓扑重试
OfflineFlush → END（无入边，仅作离线写回锚点）
```

`is_use_tool()` 只区分有无工具。权限判断、AI 或人工审批和工具执行都在 `ToolExecutor` 内逐个进行，图中只注册当前节点。

`LumiAgentState` 保存 messages、结构化输出、终止标记、PTL 重试标记与委派深度。messages 使用 `DeltaChannel`，每 100 次更新存完整快照，其余保存增量。工具审批模式、模型连接、系统提示词、权限引擎、Broker 和项目目录放在 `LumiAgentContext`，由 `Runtime` 传入节点。`tool_mode` 不在 state 中，支持运行时切换。

`__structured_output__` 是注册的真工具：参数通过 JSON Schema 校验，成功写入结构化结果，失败以工具结果交模型修正。Stop hook 可提醒模型补交结构化输出；连续失败和拉回次数均有上限。见 [Hooks](hooks.md)。

## 压缩与历史

压缩在上下文注入之前，在线阈值压缩、PTL 强制压缩与离线压缩共用写回构造。摘要载体不可见，保留的原文继续参与模型上下文；正在回答的真人诉求原样保留。压缩后的上下文 marker 重新建立，避免保留无法解释的增量链。

PTL 经 `CallModel` 的节点级 error handler 折返 `Compact`，压缩后重试；标记未清除又撞 PTL 时直接抛错。使用 error handler 避免节点内 `Command(goto=...)` 与条件边同时路由。

模型上下文与显示历史分开：桌面首屏加载最近 50 个展示条目，再按需读取压缩前 checkpoint。重挂消息通过稳定身份合并；编辑保留消息时先恢复必要的压缩前状态，避免把旧未来带入重答。所有离线状态更新统一走 `AgentBridge.flush_offline` 和 `OfflineFlush`。详见 [压缩架构](compact.md)、[历史操作](../guides/checkpoint.md)。

## 权限与子代理

每个工具按 `deny > ask > allow > unmatched` 独立判断，再按审批模式处理；没有只读、记忆目录或内部批次免审。项目目录确定配置与相对路径，不构成文件访问边界。同批已允许的工具不等待兄弟调用审批，结果按原调用顺序写回。详见 [权限架构](permissions.md)、[在途审批](approval-inflight.md)。

持久子代理由 `agent_create` / `agent_list` / `agent_message` 管理，实例归属主会话。Lumi 子代理有独立 checkpointer，保留 `ask` 并将问题交主 Agent；Codex / Claude Code 使用原生会话。Workflow 的临时执行单元不持久化，也不提供 `ask` 或继续委派工具。具体模式和外部执行器权限以 [子代理架构](agents.md) 为准。

## 网关与并发

`GatewaySession` 负责传输无关的 RPC、运行锁与通知编排，`AgentBridge` 将图事件转换为 `BridgeEvent`。本地桌面经 WebSocket，远端经原生 SSH 逻辑通道，飞书经进程内会话池驱动 Bridge。`protocol/events.json` 维护 wire 契约；变更时还需对齐生产者、消费者与契约测试。

进程内同一 thread 的运行与删除共用 `sessions/thread_runs.py` 的锁；连接自身的锁另保护 Bridge 指向。会话列表来自 checkpoint，置顶、标题、模型与目标存 `session_meta.json`。合成输入通过 `additional_kwargs["lumi"]["items"] = []` 声明不可见，显示侧不从模型文本反解析 UI 内容。详见 [桌面架构](desktop.md)。

## 停机与持久化

`run_control.drain_all()` 请求在 super-step 边界停机，`bootstrap.drain_runs()` 在拆子系统之前调用。Drain 保留完整 checkpoint，但不在新进程自动续跑；下一轮由 `_recover_stale_state` 收尾中断状态。用户停止要求及时响应，可取消节点中途的模型调用，再由 `persist_partial_reply` 修复部分回复。

`astream_events(version="v2")` 的 control 通过 config 注入 parent runtime；相关上游私有接口由 `tests/test_drain.py` 锁住。checkpoint 使用 SQLite 或 PostgreSQL，可选加密与迁移约束见 [备份恢复](../guides/backup.md)。

## 关键验证

| 契约 | 测试 |
|---|---|
| 逐个权限与审批 | `tests/test_per_tool_permissions.py`、`tests/test_unattended_routing.py` |
| 增量消息与离线更新 | `tests/test_delta_channel.py`、`tests/gateway/test_offline_flush_contract.py` |
| PTL 路由 | `tests/test_call_model_ptl.py` |
| 停机与中断收尾 | `tests/test_drain.py`、`tests/gateway/test_persist_partial_reply.py` |
| 协议名称 | `tests/gateway/test_protocol_contract.py` |
