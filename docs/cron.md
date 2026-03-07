# Cron 定时任务

Lumi 内置定时任务系统，让 Agent 能够按照设定的时间规则主动执行任务并投递结果。基于 APScheduler 实现调度，支持一次性、固定间隔和 cron 表达式三种调度方式。

---

## 快速上手

在对话中直接告诉 Lumi 你想定时做什么，Agent 会自动调用 `cron` 工具创建任务：

```
用户：每天早上 9 点帮我总结一下今天的待办事项
Agent：✅ 任务已创建：每日待办总结（ID: a1b2c3d4e5f6，调度: cron 0 9 * * *）
```

也可以手动指定调度规则：

```
用户：每 30 分钟检查一下服务器状态
用户：2025-03-10T14:00:00 提醒我开会
```

---

## 调度规则

支持三种格式，`cron` 工具的 `schedule` 参数会自动识别：

| 类型 | 格式 | 示例 | 说明 |
|------|------|------|------|
| 固定间隔 | `<数字><单位>` | `30s`、`5m`、`2h`、`1d` | 单位：s(秒) m(分) h(时) d(天) |
| 一次性 | ISO 8601 | `2025-03-10T14:00:00` | 执行后自动删除 |
| cron 表达式 | 5 字段 | `*/5 * * * *`、`0 9 * * *` | 分 时 日 月 周 |

**cron 表达式常用示例：**

| 表达式 | 含义 |
|--------|------|
| `*/5 * * * *` | 每 5 分钟 |
| `0 9 * * *` | 每天 9:00 |
| `0 9 * * 1-5` | 工作日 9:00 |
| `0 */2 * * *` | 每 2 小时 |
| `0 0 1 * *` | 每月 1 号 0:00 |

---

## cron 工具操作

`cron` 工具支持 7 种操作：

### create — 创建任务

| 参数 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 任务名称 |
| `schedule` | ✅ | 调度规则字符串 |
| `prompt` | ✅ | 发送给 Agent 的提示词 |

```
cron(operation="create", name="每日总结", schedule="0 9 * * *", prompt="总结今天的待办事项")
```

### list — 列出所有任务

无需参数，返回所有任务的名称、调度规则和启用状态。

### update — 修改任务

| 参数 | 必填 | 说明 |
|------|------|------|
| `job_id` | ✅ | 任务 ID |
| `name` | | 新名称 |
| `schedule` | | 新调度规则 |
| `prompt` | | 新提示词 |

### delete — 删除任务

需要 `job_id`，同时从调度器和持久化存储中移除。

### run — 立即执行

需要 `job_id`，立即触发一次执行，不影响正常调度计划。

### pause — 暂停/恢复

需要 `job_id`，切换任务的启用状态。暂停后不再触发，恢复后继续按原调度执行。

### runs — 查看执行记录

需要 `job_id`，返回最近 20 条执行记录（可通过 `limit` 参数调整），包含状态、耗时和输出摘要。

---

## 任务执行

每个定时任务触发时，Scheduler 会：

1. 创建一个独立的 Agent 子会话（`checkpoint=None`，与主对话隔离）
2. 将任务的 `prompt` 作为输入，`tool_mode` 设为 `privileged`（跳过人工审批）
3. 使用 `asyncio.wait_for` 限制执行时间，默认超时 10 分钟
4. 执行完成后通过 DeliveryManager 广播结果到所有已注册的投递通道
5. 记录执行日志到 RunLog

---

## 结果投递

任务执行结果通过 `DeliveryManager` 广播到所有已注册的投递通道。广播时会附带 `started_at`（执行开始时间）和 `duration_ms`（执行耗时毫秒）元数据。当前内置两种：

| 通道 | 说明 |
|------|------|
| TUIDelivery | 将结果持久化到 TUI 通知面板（`Ctrl+N` 切换显示），通过 `add_notification()` 推送 |
| APIDelivery | 通过 SSE 推送（含 `started_at`、`duration_ms` 字段），无活跃连接时缓存结果（最多 50 条） |

**SSE 订阅端点：** `GET /api/cron/events`

投递通道基于 `ResultDelivery` ABC 基类，可通过继承扩展 Webhook、飞书通知等第三方通道：

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

---

## 重试机制

任务执行失败时，系统会自动判断错误类型：

**瞬态错误**（自动重试）：
- `asyncio.TimeoutError`
- `httpx.HTTPStatusError`（429 / 5xx）
- `ConnectionError`、`OSError`

**永久错误**（不重试）：
- `ValueError`、`KeyError` 等业务逻辑错误

重试策略采用退避间隔，最多重试 3 次：

| 重试次数 | 等待时间 |
|----------|----------|
| 第 1 次 | 30 秒 |
| 第 2 次 | 60 秒 |
| 第 3 次 | 5 分钟 |

成功执行后连续错误计数自动归零。

---

## 持久化

### 任务存储

任务持久化到 `~/.lumi/cron/jobs.json`，格式：

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

写入采用原子操作（write-to-temp + rename），防止崩溃导致数据损坏。文件损坏时自动备份为 `.bak` 并从空列表启动。

### 执行日志

每个任务的执行记录存储在 `~/.lumi/cron/runs/{job_id}.jsonl`，JSONL 格式（每行一条），超过 2MB 自动裁剪旧记录。

---

## 架构

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

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 数据模型 | `lumi/agents/cron/models.py` | Schedule、Job 定义与序列化 |
| 任务存储 | `lumi/agents/cron/job_store.py` | JSON 文件持久化，原子写入 |
| 执行日志 | `lumi/agents/cron/run_log.py` | JSONL 追加写入，自动裁剪 |
| 结果投递 | `lumi/agents/cron/delivery.py` | ABC 基类 + TUI/API 实现 |
| 调度引擎 | `lumi/agents/cron/scheduler.py` | APScheduler 封装，重试逻辑 |
| 对话工具 | `lumi/agents/tools/providers/cron.py` | 7 种操作的 LangChain Tool |
