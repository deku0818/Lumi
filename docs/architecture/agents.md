# Subagent 后端

主 Agent 通过 `agent_create`、`agent_list`、`agent_message` 管理独立、可持续对话的 subagent。
旧 `agent` 工具已移除。角色文件指南见 [agents.md](../guides/agents.md)。

## 工具与执行器

- `agent_create(prompt, name=None, cwd=None, run_in_background=true)`：创建稳定实例并提交第一轮。
- `agent_list()`：列出当前主会话拥有的实例及各自 `cwd`；只读元数据，不恢复进程或调用模型。
- `agent_message(agent_id, prompt, run_in_background=true)`：向原实例提交后续轮次。

`name="codex"` 和 `name="claude"` 为内置保留入口，直接调用原生 CLI；其他名称选择 Lumi
角色，省略时使用默认 Lumi。没有公开的 `executor` 参数，执行类型仅由后端记录。
Codex 和 Claude Code 无需 `.lumi/agents/*.md` 包装。
它们优先使用宿主机器 PATH 中已安装、已认证的 `codex` / `claude`，再检查用户的
`~/.local/bin/`。Codex 还识别 `CODEX_CLI_PATH` 和 macOS 已安装 ChatGPT Desktop 内置的
`codex-cli/CodexCLI.app/Contents/MacOS/codex`，无需为了此安装形态再装一份独立 CLI。
查找发生在运行 Lumi 后端的机器上；远程连接不会使用本机的桌面应用程序。
Lumi 不代为安装、登录或修改外部 CLI 的凭据。

实例仅归属创建它的主会话 `parent_id`，不按项目目录分组或授权。同一主会话可以拥有多个
不同工作目录的 subagent；其他主会话即使使用相同目录，也不能访问这些实例。
`cwd` 默认继承创建时主会话的工作目录，支持绝对路径、`~` 和基于主会话目录的相对路径。
目录必须存在，解析后的绝对路径绑定实例并持久化；主会话之后切换目录，不会改变已有实例
的工作目录、原生会话 ID 或回传对象。`agent_message` 不接受目录覆盖。
Lumi 角色配置仍从主会话所在项目选择，所选角色可以在指定的 `cwd` 内执行。
外部 CLI 的项目规则和长期记忆仍遵循各自原生配置；独立的原生会话 ID 用于隔离对话历史。

| 执行器 | 接入 | 续聊上下文 |
|---|---|---|
| Lumi | 独立 LangGraph + checkpointer | 自己的实例 thread，使用配置的 SQLite 或 PostgreSQL 持久化后端 |
| Codex | `codex app-server`，双向 stdio JSON-RPC | 原生 thread ID；存活进程复用，重启后 `thread/resume` |
| Claude Code | 官方 Python Agent SDK，明确传入查找到的 `cli_path` | 原生 session ID，后续轮次设置 `resume` |

Claude SDK 每轮在同一个任务中打开和关闭作用域，避免 AnyIO 取消域跨任务退出。
这不丢失对话上下文；上下文由原生 session 持久化。不会使用 SDK 捆绑的另一份 CLI。

## 身份、并发与恢复

`agent_id` 标识实例，`turn_id` 标识一次消息，后台卡片 `task_id` 与 `turn_id` 相同。
新 ID 固定为 8 个小写字母数字字符：实例为 `a` + 7 位随机字符（如 `a7k3m9q2`），
轮次为 `t` + 7 位随机字符（如 `t4n8p6r1`）。生成时检查数据库，碰撞则重新生成；短 ID
直接作为持久主键，重启后保持不变，没有额外编号映射。已有长 ID 保留并可继续使用。
Codex 的原生 `thread_id`、Claude Code 的原生 `session_id` 按原样保存在内部句柄中。
同一实例 FIFO 串行，不同实例可以并行；每个委派深度最多同时执行四个实例。
每个轮次有独立的取消任务。按 `task_id` 停止只取消该轮，排队轮次不会误停正在执行的轮次；
删除主会话时递归停止全部子实例及其后台 Bash / Workflow，等待收尾并清除它们的通知。
Lumi 深度按 `agents.max_delegation_depth` 限制，达到上限移除 `agent_create` 和 `workflow`，
保留联系自己已有实例的能力；所有 Lumi subagent 均无 `ask` 工具或人工审批通道。

配置目录的 `checkpoints/subagents.db` 保存实例、角色快照、原生句柄、轮次、事件和结果回传记录。
事件先落库再广播，按递增 `seq` 分页。一个数据目录同时只能由一个运行时持有执行权。
重启后，运行轮次标记 `interrupted`，排队的业务轮次标记 `cancelled`；读取历史不会自动重做任务。
尚未开始的结果通知轮次保留，网关启动或显式派发任务时恢复投递；恢复元数据本身不会启动执行器。
主 Agent 工具调用 ID 用于重试去重，不能把传输中断当作尚未执行而自动重发。

## 结果回传

后台模式每轮完成后持久化一条归属父会话的回执，包含实例、执行器、轮次、状态和输出。
桌面或渠道的通知轮在父会话锁内认领；只有回执输入实际写入父 checkpoint 后才确认送达。
若确认前崩溃，下次先查 checkpoint 历史，避免再次注入；离线父会话的回执留待恢复后消费。
同步模式直接返回本轮结果，不再额外通知。嵌套 Lumi subagent 的回执作为父 subagent 的持久化新轮次排队。
后台 Bash / Workflow 的完成通知也转成所属 subagent 的持久化新轮次，执行结果再回传其父会话。
内部通知通过来源任务 ID 去重，重启会补齐落库与入队之间的交接；已开始的通知轮次中断时不重做，
将原始通知内容随中断结果交回上级，避免丢失已完成工作的结果。

## 权限与终止

Lumi 使用项目权限引擎及委派时的运行模式快照，无人应答的审批沿用无人值守路由。
Codex 默认 read-only sandbox，原生审批或提问均拒绝并将本轮标记 `blocked`。
Claude Code 保留原生配置，PreToolUse 拦截 Lumi 项目的显式 deny / ask，权限回调按 Lumi
权限和运行模式判断；需要人类应答时拒绝并标记 `blocked`。本版不代理交互审批。
原生工具不会映射成 Lumi 工具执行，也不会自动批准外部请求。
停止任务或关闭运行时会取消所属执行并关闭 SDK / app-server 资源，队列中的任务不会偷偷继续。

## 只读观测与模块

后端 RPC `list_subagents`、`subagent_history`、`observe_subagent` 均限当前父会话；
观察时先订阅再读取快照，`subagent.event` 按 `seq` 去重，缺失事件通过历史补齐。
记录包括用户任务、模型正文、工具调用、原生事件、归属信息和终态。
不提供人类发送消息的 RPC；普通聊天不能切换绑定到 subagent。UI 本版尚未接入这些接口。

| 模块 | 职责 |
|---|---|
| `agents/tools/providers/agent.py` | 三个工具、Lumi 角色选择、深度门控 |
| `agents/core/stream_events.py` | 主会话与 Lumi subagent 共用的工具事件 ID、参数解析 |
| `agents/runtime/subagents/` | FIFO 调度、执行器适配、生命周期、结果投递 |
| `sessions/subagents.py` | SQLite 数据与事件、回执持久化 |
| `gateway/subagents.py` | 只读历史、订阅、事件发布 |

Workflow 的临时执行单元仍使用 `create_subagent()`，保持一次性、无 checkpointer 的原有语义。
历史 `agent` 工具的内嵌渲染协议仍保留读取兼容，见 [subagent-rendering.md](subagent-rendering.md)。
