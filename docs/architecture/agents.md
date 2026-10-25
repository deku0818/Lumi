# Subagent 后端

主 Agent 通过 `agent_create`、`agent_list`、`agent_message` 管理独立、可持续对话的 subagent。
旧 `agent` 工具已移除。角色文件指南见 [agents.md](../guides/agents.md)。

## 工具与执行器

- `agent_create(prompt, name, cwd=None, run_in_background=true)`：创建稳定实例并提交第一轮，`name` 必填。
- `agent_list()`：列出当前主会话拥有的实例及各自 `cwd`；只读元数据，不恢复进程或调用模型。
- `agent_message(agent_id, prompt, run_in_background=true)`：向原实例提交后续轮次。

`name="codex"` 和 `name="claude"` 为内置保留入口，直接调用原生 CLI；其他名称选择 Lumi
角色，名称必须来自当前可用清单。没有公开的 `executor` 参数，执行类型仅由后端记录。
Codex 和 Claude Code 无需 `.lumi/agents/*.md` 包装，默认关闭。用户在项目主页「子 Agent」
卡片的 ＋ 号菜单中启用或关闭；已启用的显示勾选，未启用的显示加号，「自定义 Agent」沿用
角色创建弹窗。项目配置保存在该目录的 `.lumi/agents.json`，格式为 `{"codex": true, "claude": false}`；
不继承机器级开关，也不跨项目生效。启用前只运行版本与登录状态检查，不调用模型，
不回传账户或凭据；检测未通过时提示具体原因，不保存启用状态。
它们优先使用宿主机器 PATH 中已安装、已认证的 `codex` / `claude`，再检查用户的
`~/.local/bin/`。Codex 还识别 `CODEX_CLI_PATH` 和 macOS 已安装 ChatGPT Desktop 内置的
`codex-cli/CodexCLI.app/Contents/MacOS/codex`，无需为了此安装形态再装一份独立 CLI。
查找发生在运行 Lumi 后端的机器上；远程连接不会使用本机的桌面应用程序。
Lumi 不代为安装、登录或修改外部 CLI 的凭据。

`AgentChangeDetector.peek()` 合并已有 Lumi 角色与已启用、检查通过的原生入口，项目主页、
`system-reminder` 和 `agent_create` 共用这份清单。工具描述只说明用法，不枚举 Agent。
原生名称不能由同名 Markdown 文件覆盖，详情复用项目资源弹窗，但没有角色文件编辑入口。
CLI 探测复用环境检测的命令执行和版本解析；状态按可执行文件路径缓存 30 秒，
各项目共用机器检测缓存，启用时强制重查。CLI 检测在线程中执行，不阻塞网关事件循环。
设置变更广播 `agents.changed {project}`，仅刷新对应项目的清单，主 Agent 在下次上下文注入时收到清单变化。
关闭后不能新建该类型实例；已有实例保留原角色快照、原生会话与工作目录，仍可通过
`agent_message` 续聊。原生 CLI 登录失效或程序移除不清除用户的启用偏好，但会从可用清单移除。

### Codex 客户端写入锁边界

2026-10-21 的桌面日志确认：同一原生会话的页面激活后，ChatGPT Desktop 会调用
`thread/resume` 并成为 `owner`；此后 Lumi 的独立 app-server 恢复该会话会收到
`already has an active writer`。日志没有记录页面激活的来源，不能据此认定桌面仅在后台
运行就会接管所有新会话。原生会话被用户删除后，则可能收到 `no rollout found`。

当前版本接受这两个外部会话边界，不与客户端抢锁，不自动接管、不删除锁、不杀客户端
进程，也不静默新建会话替代原上下文。原生打开按钮不保证只读，可能触发上述写入权冲突。

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
桌面实例卡片通过 `stop_subagent(agent_id)` 复用运行时的实例级停止，一次取消该实例所有运行和
排队轮次，不影响其他实例；`stop_bg_task(task_id)` 保持单轮语义。
删除主会话时递归停止全部子实例及其后台 Bash / Workflow，等待收尾并清除它们的通知。
Lumi 深度按 `agents.max_delegation_depth` 限制，达到上限移除 `agent_create` 和 `workflow`，
保留联系自己已有实例的能力；Lumi subagent 的 `ask` 只向主 Agent 转交问题，没有人工审批通道。

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
外部执行器默认完全访问、无人值守执行，新建与恢复会话均显式应用该模式：
Codex 使用 `approvalPolicy=never` 与 `sandbox=danger-full-access`；Claude Code 使用
`permission_mode=bypassPermissions` 并关闭原生命令沙箱，保留其他原生配置及 Lumi 项目显式 deny
规则，普通调用自动放行。
Claude Code 的 PreToolUse 与权限回调共用同一判定，Lumi 权限规则中的 ask 不再阻塞外部执行器。
本版不代理交互审批；完全访问下仍出现的 Codex 审批请求会拒绝并标记 `blocked`。
原生工具不会映射成 Lumi 工具执行；只读观测指人类入口，不限制子代理的文件与命令操作。
停止任务或关闭运行时会取消所属执行并关闭 SDK / app-server 资源，队列中的任务不会偷偷继续。

## 问题转交与回答

Lumi 复用现有 `ask` 的问题 schema 和结果配对，通过 context 中的 `request_parent_input`
记录问题并干净结束图；Claude Code 捕获 `AskUserQuestion`，Codex 捕获
`item/tool/requestUserInput`。Codex 不返回空答案，而是先发出 `turn/interrupt`，再关闭自己持有的
app-server 后转交问题；中断失败或超时也保留问题，不依赖自然完成事件。原生 thread ID 保持不变。
三者统一结束当前轮次，以 `blocked` 回传完整 `questions`。
问题只保存在 `turn.complete.questions`，与轮次终态在同一 SQLite 事务落库，同步工具结果和后台持久化回执共用 `next_action` 指引：
主 Agent 先依据上下文和可验证的信息回答，仅缺少用户决策时才调用自己的 `ask`；取得答案后
用 `agent_message` 把问题与答案发给同一 `agent_id`，继续原任务。用户取消时不发送虚构答案。

回答沿用原来的 Lumi checkpoint、Codex thread 或 Claude session，开启新一轮，不在原生提问
调用上原地挂起。等待期间释放执行槽位；问题离线或重启后仍可通过既有回执恢复投递。
不增加新工具、RPC 或子会话输入框；后续完成结果与其他轮次使用相同的回传和去重机制。

## 只读观测与模块

后端 RPC `list_subagents`、`subagent_history`、`observe_subagent` 均限当前父会话；
观察时先订阅再读取快照，`subagent.event` 按 `seq` 去重，缺失事件通过历史补齐。
记录包括用户任务、模型正文、工具调用、原生事件、归属信息和终态。
不提供人类发送消息的 RPC；普通聊天不能切换绑定到 subagent。
Desktop 右侧按实例展示运行状态与空闲历史，点击后在侧栏观察，也可展开到主区域；没有执行器
切换标签，其他实例通过返回列表选择。详情按轮次显示委派、正文、工具参数与输出、转交主 Agent 的问题与选项和错误，不显示轮次跳转栏或原始事件。
问题内容与主会话 ask 共用展示组件，子会话没有作答控件；文件结果复用 `FileCards` 和现有预览入口，保留本机/远程通道。
运行中的思考过程复用主会话底部状态组件：显示光点与状态文字，点击箭头展开当前思考；
进入正文、工具执行或结束后收起，不作为历史记录单独展示。已完成轮次默认折叠，打开过的轮次与阅读位置在查看方式或实例切换时保留；历史分页加载完成后再恢复阅读位置。

`useSubagents` 只使用当前主会话的连接，事件由独立观察器消费，不进入主对话 reducer。
列表通过现有后台任务通知刷新；详情按需订阅，关闭后调用 `unobserve_subagent`，不影响执行。
新轮次排队时，正在运行的轮次仍默认展开；手动展开或收起优先。最终输出仅与当前实例的根回复
去重，嵌套回复不参与该判断。
实时流不推进确认游标；打开、重连、聚焦及轮次完成时，从已确认的持久历史游标补齐并按 seq
去重，历史晚于实时流到达时也不会重复追加正文。原始事件仍保留在后端历史中。
本机 Codex 的原生打开按钮使用限定 UUID 的专用 Electron IPC；远程记录只提供会话 ID 复制，
不把远端会话映射成本机打开操作。普通 Markdown 的外链协议限制保持独立。

| 模块 | 职责 |
|---|---|
| `agents/tools/providers/agent.py` | 三个工具、Lumi 角色选择、深度门控 |
| `agents/core/stream_events.py` | 主会话与 Lumi subagent 共用的工具事件 ID、参数解析 |
| `agents/runtime/subagents/` | FIFO 调度、执行器适配、生命周期、结果投递 |
| `sessions/subagents.py` | SQLite 数据与事件、回执持久化 |
| `gateway/subagents.py` | 只读历史、订阅、事件发布与实例级停止 |
| `desktop/src/hooks/useSubagents.ts` | 当前主会话实例、详情订阅与视图状态 |
| `desktop/src/lib/subagentObserver.ts` | 实时投递与持久历史分页对齐 |
| `desktop/src/lib/subagentTimeline.ts` | 三种执行器事件投影为统一多轮记录 |
| `desktop/src/components/Subagents.tsx` | 实例列表和只读详情 |

Workflow 的临时执行单元仍使用 `create_subagent()`，保持一次性、无 checkpointer 的原有语义。
历史 `agent` 工具的内嵌渲染协议仍保留读取兼容，见 [归档说明](../archive/subagent-rendering.md)。
