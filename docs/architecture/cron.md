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
│                      └─ APIDelivery              │
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
| 结果投递 | `lumi/agents/cron/delivery.py` | ABC 基类 + TUI/API 实现 |
| 调度引擎 | `lumi/agents/cron/scheduler.py` | APScheduler 封装，重试逻辑 |
| 对话工具 | `lumi/agents/tools/providers/cron.py` | 7 种操作的 LangChain Tool |

---

## 任务执行流程

每个定时任务触发时，Scheduler 会：

1. 创建独立的 Agent 子会话（`checkpoint=None`，与主对话隔离）
2. 将任务的 `prompt` 作为输入，`tool_mode` 设为 `privileged`（跳过人工审批）
3. 使用 `asyncio.wait_for` 限制执行时间，默认超时 10 分钟
4. 执行完成后通过 DeliveryManager 广播结果到所有已注册的投递通道
5. 记录执行日志到 RunLog

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
from datetime import datetime

class WebhookDelivery(ResultDelivery):
    def __init__(self, url: str):
        self._url = url

    async def deliver(
        self,
        job_name: str,
        output: str,
        *,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> None:
        # 实现 HTTP POST 推送
        ...
```

广播时附带 `started_at`（执行开始时间）和 `duration_ms`（执行耗时毫秒）元数据。
