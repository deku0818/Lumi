# 定时任务架构

定时任务系统的内部实现。用户使用指南见 [`docs/guides/cron.md`](../guides/cron.md)。

---

## 架构总览

```
┌─────────────────────────────────────────────────┐
│                  Lumi 进程                       │
│                                                 │
│  TUI / API ──启动/停止──▶ Scheduler             │
│                            │                    │
│                     AsyncIOScheduler             │
│                            │                    │
│  cron Tool ──CRUD──▶ JobStore ◀── Scheduler     │
│                            │                    │
│                     触发 _execute_job            │
│                            │                    │
│                     Agent 子会话                 │
│                            │                    │
│                     DeliveryManager              │
│                      ├─ TUIDelivery              │
│                      ├─ APIDelivery              │
│                      └─ DesktopDelivery          │
│                            │                    │
│                        RunLog                    │
└─────────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 数据模型 | `lumi/agents/cron/models.py` | Schedule、Job 定义与序列化 |
| 任务存储 | `lumi/agents/cron/job_store.py` | JSON 文件持久化，原子写入 |
| 执行日志 | `lumi/agents/cron/run_log.py` | JSONL 追加写入，自动裁剪 |
| 结果投递 | `lumi/agents/cron/delivery.py` | ABC 基类 + TUI/API 实现（Desktop 实现在 `lumi/gateway/desktop_delivery.py`，wire 信封属 gateway 层） |
| 调度引擎 | `lumi/agents/cron/scheduler.py` | APScheduler 封装，重试逻辑 |
| 运行时装配 | `lumi/agents/cron/runtime.py` | `setup_cron()` 工厂，TUI 与 desktop serve 共用 |
| 对话工具 | `lumi/agents/tools/providers/cron.py` | 7 种操作的 LangChain Tool |
| Desktop RPC | `lumi/gateway/cron_rpc.py` | WS 管理方法（list/create/update/delete/toggle/run/runs） |

Desktop 端：`lumi serve` 在 lifespan 中经 `setup_cron()` 启动调度器，`DesktopDelivery`
把任务结果（`cron.result`）与运行状态（`cron.running`）广播给所有活跃 WS 连接；
管理界面见 `desktop/src/components/CronPage.tsx`，协议见 `protocol/events.json`。

跨进程互斥：同一 workspace 的 `jobs.json` 可能同时被 TUI 与 `lumi serve` 加载，
`Scheduler.start()` 经 `<cron_dir>/scheduler.lock` 文件锁（flock）保证只有一个进程
实际调度——后启动者跳过调度但仍可管理任务（CRUD / Run now），否则每个任务会在
每个进程各执行一次。锁随进程退出（`stop()` 或进程结束）自动释放。

---

## 任务执行流程

每个定时任务触发时，Scheduler 会：

1. 创建独立的 Agent 子会话，落在专属的 `cron-` 前缀 thread 中（共用 Scheduler
   启动时创建的常驻 checkpointer 连接），像普通会话一样可回看、可续聊
2. 将任务的 `prompt` 作为输入，`tool_mode` 设为 `privileged`（跳过人工审批）
3. 使用 `asyncio.wait_for` 限制执行时间，默认超时 10 分钟
4. 执行完成后通过 DeliveryManager 广播结果到所有已注册的投递通道
5. 记录执行日志到 RunLog（含本次执行的 `thread_id`）

### 执行即会话

- 每次执行一个独立 thread（`cron-{uuid}`），desktop 端在执行记录中点击可跳转
  该会话并继续对话（续聊走普通审批模式，不再 privileged）
- cron 线程**不进入会话列表**：执行时不带 `workspace_dir` 元数据天然被过滤，
  `session_store.list_sessions` 再按 `cron-` 前缀兜底排除（续聊后也不"转正"）
- **保留策略**：每个任务只保留最近 `MAX_CRON_RUN_THREADS`（50）次执行的会话
  checkpoint，超出部分在写入新记录时清理（记录本身保留，仅 thread_id 置空）
- **级联删除**：删除任务时一并清理执行日志与全部历史会话 checkpoint
  （`Scheduler.purge_job_data`）；一次性（at）任务执行完自删时不级联（保留结果可查）
- checkpointer 初始化失败时退化为无会话模式（thread_id 为空，仅摘要可见）
- **per-run 授权 / hooks 注入**：cron 直接 `agent.graph.ainvoke`（不走 bridge `_stream`），
  故 `_invoke_agent` 起点自行经 `set_run_authorized_source_for(engine)` +
  `set_run_config_hooks(build_config_hooks(proj))` 把本 cron 项目的授权目录来源与 config
  hooks 注入 per-run contextvar，否则 filesystem/bash 工具会落回被并发 WS 会话
  `set_workspace` 清洗过的进程全局（与 bridge 共用同一降级 helper）。详见
  [permissions.md](permissions.md) / [hooks.md](hooks.md)

---

## 重试机制

任务执行失败时，系统自动判断错误类型：

**瞬态错误**（自动重试）：`asyncio.TimeoutError`、`httpx.HTTPStatusError`（429 / 5xx）、`ConnectionError`、`OSError`

**永久错误**（不重试）：`ValueError`、`KeyError` 等业务逻辑错误

重试策略采用退避间隔，最多重试 3 次：第 1 次 30s、第 2 次 60s、第 3 次 5min。成功后连续错误计数归零。

---

## 错过任务补偿

Scheduler 启动时检查每个启用的任务是否有离线期间错过的执行（coalesce 策略）：

| 调度类型 | 补偿条件 |
|----------|----------|
| 一次性（at） | 执行时间已过且从未成功执行 |
| 固定间隔（interval） | 上次执行时间 + 间隔 < 当前时间 |
| cron 表达式 | 基于 trigger 计算的下次触发时间已过 |

---

## 持久化

### Workspace 隔离

定时任务按工作目录隔离存储。每个工作目录通过 `SHA256(resolved CWD)[:12]` 生成唯一标识，数据存储在 `~/.lumi/cron/{workspace_id}/` 下。`workspace.meta` 文件记录原始工作路径和创建时间。

### 任务存储

持久化到 `~/.lumi/cron/{workspace_id}/jobs.json`：

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "每日总结",
      "schedule": { "type": "cron", "value": "0 9 * * *" },
      "prompt": "总结今天的待办事项",
      "enabled": true,
      "created_at": "2025-01-15T08:00:00",
      "consecutive_errors": 0
    }
  ]
}
```

写入采用原子操作（write-to-temp + rename）。文件损坏时自动备份为 `.bak` 并从空列表启动。

### 执行日志

每个任务的执行记录存储在 `~/.lumi/cron/{workspace_id}/runs/{job_id}.jsonl`，JSONL 格式，超过 2MB 自动裁剪旧记录。

---

## 结果投递扩展

投递通道基于 `ResultDelivery` ABC 基类，可通过继承扩展：

```python
from lumi.agents.cron.delivery import ResultDelivery
from lumi.agents.cron.run_log import RunRecord

class WebhookDelivery(ResultDelivery):
    def __init__(self, url: str):
        self._url = url

    async def deliver(self, record: RunRecord, text: str) -> None:
        # record 携带完整执行元数据（job_id/job_name/status/started_at/duration_ms/thread_id），
        # text 为面向用户的结果文本（成功为输出全文，失败为状态+错误）
        ...
```

`RunRecord` 作为值对象传递整次执行的元数据——新增字段时各通道按需取用，
无需再逐个改投递签名。
