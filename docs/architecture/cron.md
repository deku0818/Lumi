# 定时任务架构

定时任务系统的内部实现。用户使用指南见 [`docs/guides/cron.md`](../guides/cron.md)。

---

## 架构总览

```
┌─────────────────────────────────────────────────┐
│                  Lumi 进程                       │
│                                                 │
│  lumi serve ──启动/停止──▶ Scheduler            │
│                            │                    │
│                     AsyncIOScheduler             │
│                            │                    │
│  cron Tool ──CRUD──▶ JobStore ◀── Scheduler     │
│                            │                    │
│                     触发 _execute_job            │
│                            │                    │
│                AgentBridge（cron_stream）        │
│                            │                    │
│                     DeliveryManager              │
│                      └─ BroadcastHub             │
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
| 结果投递 | `lumi/agents/cron/delivery.py` | ABC 基类 + DeliveryManager；唯一实现是 `lumi/gateway/broadcast.py` 的 `BroadcastHub`（wire 信封属 gateway 层） |
| 调度引擎 | `lumi/agents/cron/scheduler.py` | APScheduler 封装，执行与收尾 |
| 运行时装配 | `lumi/agents/cron/runtime.py` | `setup_cron()` 工厂（必填 `stream_runner`），由 `lumi serve` 的 `gateway_process` 调用 |
| 对话工具 | `lumi/agents/tools/providers/cron.py` | 7 种操作的 LangChain Tool |
| Desktop RPC | `lumi/gateway/cron_rpc.py` | WS 管理方法（list/create/update/delete/toggle/run/runs） |

Desktop 端：`lumi serve` 在 lifespan 中经 `setup_cron()` 启动调度器，`BroadcastHub`
把任务结果（`cron.result`）与运行状态（`cron.running`）广播给所有活跃 WS 连接；
管理界面见 `desktop/src/components/CronPage.tsx`，协议见 `protocol/events.json`。

跨进程互斥：同一 workspace 的 `jobs.json` 可能同时被多个 `lumi serve` 进程加载，
`Scheduler.start()` 经 `<cron_dir>/scheduler.lock` 文件锁（flock）保证只有一个进程
实际调度——后启动者跳过调度但仍可管理任务（CRUD / Run now），否则每个任务会在
每个进程各执行一次。锁随进程退出（`stop()` 或进程结束）自动释放。

---

## 任务执行流程

每个定时任务触发时，Scheduler 会：

1. 经注入的 `stream_runner`（`lumi/gateway/cron_stream.py`）新建一个 AgentBridge 执行，
   落在专属的 `cron-` 前缀 thread 中（checkpointer 由 bridge 自带），像普通会话一样可回看、可续聊
2. 将任务的 `prompt` 作为输入，`tool_mode` 设为 `auto`（分类器逐个裁决）；bridge 以
   `interactive=False` 初始化、不接审批通道，需人工审批的操作直接自动拒绝、`ask` 直接取消
3. 使用 `asyncio.wait_for` 限制执行时间，默认超时 6000 秒（100 分钟）
4. 执行完成后通过 DeliveryManager 广播结果到所有已注册的投递通道
5. 记录执行日志到 RunLog（含本次执行的 `thread_id`）

### 执行即会话

- 每次执行一个独立 thread（`cron-{uuid}`），desktop 端在执行记录中点击可跳转
  该会话并继续对话（续聊走会话自身选择的审批模式）
- cron 线程**不进入会话列表**：`session_store.list_sessions` 按 `cron-` 前缀排除
  （执行时照样带 `workspace_dir` 元数据；续聊后也不"转正"）
- **保留策略**：每个任务只保留最近 `MAX_CRON_RUN_THREADS`（50）次执行的会话
  checkpoint，超出部分在写入新记录时清理（记录本身保留，仅 thread_id 置空）
- **级联删除**：删除任务时一并清理执行日志与全部历史会话 checkpoint
  （`Scheduler.purge_job_data`）；一次性（at）任务执行完自删时不级联（保留结果可查）
- Scheduler 自持的常驻 checkpointer 只用于保留策略与级联删除；它初始化失败时这两项清理不做，
  执行本身不受影响（checkpointer 由 bridge 自带）
- **在任务所属项目里执行**：serve 下 cron 经 `cron_stream` 用 AgentBridge 跑，授权目录、
  config hooks、MCP 与普通会话同一条注入路径。`Job.project_dir` 记下创建时会话的项目
  （cron 工具取当前 run 的主授权目录），执行时 `initialize(project_dir=...)`；空串
  （存量任务 / 桌面表单创建）退回进程 cwd。详见 [permissions.md](permissions.md) /
  [hooks.md](hooks.md)

---

## 失败处理

cron 层**不做重试**：模型调用的瞬态错误（限流 / 5xx / 断连）已在模型层与 bridge
重试过，到 Scheduler 的失败（含整轮超时）按最终结果记为 `failed` / `timeout`，写入
执行日志并广播，等下一次调度。一次性（at）任务无论成败执行完即删除。

`Job.consecutive_errors` 是早期 cron 级重试留下的历史字段，仅为兼容旧 `jobs.json`
保留，不再更新。

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
