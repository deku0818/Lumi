# Workflow 多代理编排 + 后台任务中心

`workflow` 工具让主 agent 用一段**确定性 Python 脚本**编排工作：哪些并行扇出、
哪些交叉验证、哪些汇总，全由脚本钉死。主 agent 拿到的是脚本 `return` 的**结论**，
几十个子代理读过的代码量留在各自隔离的上下文里。移植自 Claude Code 内置 Workflow
工具的设计；本实现是它在 Lumi 上的 Python 落地。

## 适用边界

为三类**结构性困难**设计：要全面（分解并行覆盖）、要有把握（多视角独立验证）、
规模超出一个上下文（大范围审计 / 迁移 / 扫描）。单点查询、琐碎改动、纯对话**不要**用。

**门控（缓存安全）**：workflow 工具**始终注册**（永不增删，缓存前缀恒定）；它用不用
靠**行为门控**——工具描述里写死「仅 Ultra 档位开启、或用户明确要求时使用」。Ultra 信号
经**边沿触发 system-reminder** 传达（`bridge._drain_ultra_note`，仅在开/关切换那轮前置到当轮
消息、不碰系统提示词；reminder 一旦进历史即长驻，无需每轮重复），故 toggle Ultra 不废
system+tools 缓存前缀。详见 [thinking.md](thinking.md) 的 Ultra 一节。

## 组成

| 部件 | 路径 | 职责 |
|---|---|---|
| `WorkflowEngine` | `agents/core/workflow/engine.py` | 编译 + 执行脚本，注入钩子，管并发 / 计数 / 进度 |
| `workflow` 工具 | `agents/tools/providers/workflow.py` | LLM 入口；编译脚本 → 起后台任务 → 返回 task_id |
| `TaskRegistry` | `agents/runtime/bg_tasks.py` | 统一后台任务注册中心（bash / agent / workflow 三类同源） |
| `BgTasksDrawer` | `desktop/src/components/BgTasksDrawer.tsx` | 后台任务中心 UI（折叠卡片 + 实时进度） |

## 执行模型

脚本本身**只是编排骨架**（循环 / 条件 / 扇出由代码钉死，禁 `import` / `open`）。真正
干活靠 `agent()` 派 LLM 子代理（语义推理）；确定性的重活让子代理用 bash / filesystem
等工具完成。**本版不含 `run` / `sh` 确定性执行层**（Lumi 无沙箱）。

1. 工具调用 → 选脚本源（`path` 优先则从本地读）→ `compile()` 同步捕语法错（脚本包进
   `async def __workflow_main__()`，故顶层可 `await` / `return`）。
2. `_start_workflow_task` 注册 `TaskKind.WORKFLOW` 条目 + `asyncio.create_task`，
   **立即返回 task_id**（后台执行）。
3. 脚本跑完 → 产物 `{summary, agent_count, logs, result}` 写沙箱文件 → `COMPLETED` →
   `NotificationQueue` 把 `<task-notification>` 推给父 thread_id。

## 注入脚本的钩子

只在脚本命名空间内存在（受限 `__builtins__`，**禁 import / open / eval**）：

- `agent(prompt, *, schema=None, label=None, phase=None, agent_name=None)` — async，**LLM**。
  `schema` 非空 → 子代理走 `output_schema` 强制结构化输出，返回校验过的 dict（失败返 None）；
  否则返回最终文本。`agent_name` → `.lumi/agents` 具名子代理；缺省 → 通用子代理。
- `parallel(thunks)` — async，**屏障**：并发跑一组无参 thunk，全完成才返回；失败项落 None。
- `pipeline(items, *stages)` — async，**无屏障**：每个 item 独立穿过所有 stage（默认优先用）。
- `phase(title)` / `log(*msg)` / 全局 `args`。

并发上限 `min(16, CPU-2)`（semaphore）；终身上限 `_MAX_AGENTS=1000`。

## 子代理（共享父沙箱语义）

`agent()` 用 `create_agent(permission_engine=<父>)` 建子 LumiAgent（`checkpointer=None`），
**复用父 PermissionEngine**——天然共享工作区边界，子代理读得到父正在处理的工作文件
（review / audit 类编排能跑的前提），不需要沙箱。子代理工具集禁用
`agent / workflow / ask / cron / background_task`（防递归 + 后台 graph 无法 interrupt 的 ask）。

## 实时进度

引擎在 `_phase` 变更、agent 进入运行、agent 完成三处调 `_emit_progress` → 绑定 task_id 的
`TaskRegistry.notify_progress(task_id, {phase, done, total, running, agent_count})`。
计数语义：`_dispatched`（build 成功后 +1，含排队）= `total`；`_done` = 完成数；
`running = agent_count - done`。`_dispatched` 放在 build 成功**之后**自增——build 失败的 agent
不计入 total，保证进度条能到 100%。

## 后台任务中心（TaskRegistry → drawer）

`TaskRegistry` 是 bash / agent / workflow 三类后台任务的**单一注册中心**（bash 经
`BackgroundTaskManager.start_task` 也注册于此）。统一收口：

- **停止**：`bg_process.cancel_background_task(task_id)` 按 kind 内部分派（BASH→bg_manager 杀进程，
  AGENT/WORKFLOW→`cancel_agent_task` 取消 asyncio.Task）——ws / TUI / `background_task` 工具共用。
- **生命周期骨架**：`bg_tasks.run_background_task(task_id, output_file, produce, *, cancel_text)`
  + `make_bg_done_callback` — agent / workflow 后台任务共用收尾（写文件 / 状态 / 通知）。
- **清理**：终态任务可经 `dismiss(task_id)` 单条移除或 `clear_finished(thread_id)` 批量清；
  `_trim_terminal` 每会话终态自动保留最近 `_TERMINAL_CAP=20` 条（运行中永不清）。

### 实时推送

`TaskRegistry.set_on_change(cb)` 单槽观察者（server 层注册，TUI / 测试不订阅）。任一变更
（register / update_status / notify_progress）→ `_on_bg_task_change` → **~100ms 去抖合并** →
广播 `bg_tasks.update`（全量快照 `serialize_task`，前端按 thread_id 过滤）。协议事件 + RPC
（`list_bg_tasks` / `stop_bg_task` / `dismiss_bg_task` / `clear_finished_bg_tasks`）见
`protocol/events.json`，drawer UI 见 [desktop.md](desktop.md)。

## Trap 速查

- **新增后台 `TaskKind`**：register / `cancel_background_task`（bg_process.py）/ `format_notification`
  三处会处理 kind；停止逻辑已收口到 `cancel_background_task`，新增 kind 只改它一处。
- **威胁模型**：受限 `__builtins__` 只防误触不防对抗（脚本在主进程 `exec`，可经 dunder 遍历逃逸）。
  脚本由受信主 agent 生成（与 bash 工具同信任级），**绝不可喂不可信脚本**；真正隔离边界是子代理工具的权限引擎。
- **`serialize_task` 字段派生**：从 dataclass 字段派生（排除 `async_task` / `prompt`），新增字段
  默认上线；前端 `BgTask` 类型是唯一「该不该收」的闸门。

## 局限（相对参考 JS 版未实现）

`run` / `sh` 确定性执行层（无沙箱）、resume / 缓存、`budget` 预算扇出、`worktree` 隔离、
嵌套 `workflow()`——本版未实现。
