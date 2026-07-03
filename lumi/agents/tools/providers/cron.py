"""CronTool：管理主动行为定时任务。

提供 create、list、update、delete、run、pause、runs 七种操作，
运行时依赖（Scheduler、JobStore、RunLog）在应用启动时通过 ``init_cron_tool()`` 注入。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import ScheduleType
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.cron.service import CronService
from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# 运行时依赖：应用启动时通过 init_cron_tool() 注入
# ---------------------------------------------------------------------------
_scheduler: Scheduler | None = None
_job_store: JobStore | None = None
_run_log: RunLog | None = None


def init_cron_tool(scheduler: Scheduler, job_store: JobStore, run_log: RunLog) -> None:
    """在应用启动时调用，注入运行时依赖。"""
    global _scheduler, _job_store, _run_log  # noqa: PLW0603
    _scheduler = scheduler
    _job_store = job_store
    _run_log = run_log


def _require_deps() -> tuple[Scheduler, JobStore, RunLog]:
    """获取运行时依赖，未初始化时抛出友好错误。"""
    if _scheduler is None or _job_store is None or _run_log is None:
        raise RuntimeError("cron 工具尚未初始化，请先调用 init_cron_tool()")
    return _scheduler, _job_store, _run_log


def _service() -> CronService:
    """由注入的运行时依赖构造 CronService。"""
    scheduler, job_store, run_log = _require_deps()
    return CronService(scheduler, job_store, run_log)


def _require_job_id(job_id: str | None, operation_name: str) -> str:
    """校验 job_id 非空，返回有效值或用户可读的错误消息。"""
    if not job_id:
        raise ValueError(f"{operation_name}需要提供 job_id 参数")
    return job_id


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------


class CronInput(BaseModel):
    """cron 工具的输入参数。"""

    operation: Literal["create", "list", "update", "delete", "run", "pause", "runs"] = (
        Field(description="操作类型：create/list/update/delete/run/pause/runs")
    )
    name: str | None = Field(
        default=None, description="任务名称（create 必填，update 可选）"
    )
    schedule: str | None = Field(
        default=None,
        description="调度规则，四种格式：相对时间 +10m/+2h/+1d（从现在起，一次性）；ISO 8601 时间点如 2025-01-15T09:00:00（一次性）；固定间隔 30s/5m/2h/1d；5 字段 cron 表达式如 */5 * * * *",
    )
    prompt: str | None = Field(
        default=None,
        description="执行载荷，发送给 Agent 的提示词（create 必填，update 可选）",
    )
    job_id: str | None = Field(
        default=None, description="任务 ID（除 create/list 外必填）"
    )
    limit: int = Field(default=20, description="runs 操作返回的最大记录数")


# ---------------------------------------------------------------------------
# 各操作实现
# ---------------------------------------------------------------------------


async def _handle_create(name: str, schedule_raw: str, prompt: str) -> str:
    """创建新任务。"""
    job = await _service().create(name, schedule_raw, prompt)
    sched = job.schedule

    if sched.type == ScheduleType.AT:
        run_at = datetime.fromisoformat(sched.value)
        return (
            f"✅ 任务已创建：{job.name}（ID: {job.id}）\n"
            f"   将在 {run_at:%Y-%m-%d %H:%M:%S} 执行"
        )
    return f"✅ 任务已创建：{job.name}（ID: {job.id}，调度: {sched.type.value} {sched.value}）"


async def _handle_list() -> str:
    """列出所有任务。"""
    jobs = await _service().get_all()
    if not jobs:
        return "当前没有任何定时任务。"
    lines: list[str] = []
    for j in jobs:
        status = "✅ 启用" if j.enabled else "⏸️ 暂停"
        lines.append(
            f"- {j.name}（ID: {j.id}）| 调度: {j.schedule.type.value} {j.schedule.value} | {status}"
        )
    return "\n".join(lines)


async def _handle_update(
    job_id: str,
    schedule_raw: str | None,
    prompt: str | None,
    name: str | None,
) -> str:
    """修改任务。"""
    job = await _service().update(
        job_id, name=name, schedule_raw=schedule_raw, prompt=prompt
    )
    return f"✅ 任务已更新：{job.name}（ID: {job.id}）"


async def _handle_delete(job_id: str) -> str:
    """删除任务。"""
    job = await _service().delete(job_id)
    return f"✅ 任务已删除：{job.name}（ID: {job_id}）"


async def _handle_run(job_id: str) -> str:
    """立即执行一次任务。"""
    await _service().trigger(job_id)
    return f"✅ 任务 {job_id} 已触发执行（异步），请稍后使用 runs 查看结果"


async def _handle_pause(job_id: str) -> str:
    """切换任务启用状态。"""
    service = _service()
    job = await service.get_or_raise(job_id)
    job = await service.set_enabled(job_id, not job.enabled)

    if job.enabled:
        return f"▶️ 任务已恢复：{job.name}（ID: {job.id}）"
    return f"⏸️ 任务已暂停：{job.name}（ID: {job.id}）"


async def _handle_runs(job_id: str, limit: int) -> str:
    """查看最近执行记录。"""
    records = await _service().recent_runs(job_id, limit=limit)
    if not records:
        return f"任务 {job_id} 暂无执行记录。"
    lines: list[str] = []
    for r in records:
        line = (
            f"- [{r.status}] {r.started_at:%Y-%m-%d %H:%M:%S} | "
            f"耗时 {r.duration_ms}ms | {r.output_summary[:80]}"
        )
        if r.error:
            line += f" | 错误: {r.error}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------

CRON_DESCRIPTION = """管理主动行为定时任务，支持以下操作：

- **create**: 创建新任务（需要 name、schedule、prompt）
- **list**: 列出所有任务
- **update**: 修改任务（需要 job_id，可选 schedule、prompt、name）
- **delete**: 删除任务（需要 job_id）
- **run**: 立即执行一次任务（需要 job_id）
- **pause**: 暂停/恢复任务（需要 job_id）
- **runs**: 查看最近执行记录（需要 job_id，可选 limit）"""


@tool(description=CRON_DESCRIPTION, args_schema=CronInput)
async def cron(
    operation: str,
    name: str | None = None,
    schedule: str | None = None,
    prompt: str | None = None,
    job_id: str | None = None,
    limit: int = 20,
) -> str:
    """管理主动行为定时任务。"""
    try:
        match operation:
            case "create":
                if not name:
                    return "❌ 创建任务需要提供 name 参数"
                if not schedule:
                    return "❌ 创建任务需要提供 schedule 参数"
                if not prompt:
                    return "❌ 创建任务需要提供 prompt 参数"
                return await _handle_create(name, schedule, prompt)

            case "list":
                return await _handle_list()

            case "update":
                return await _handle_update(
                    _require_job_id(job_id, "更新任务"), schedule, prompt, name
                )

            case "delete":
                return await _handle_delete(_require_job_id(job_id, "删除任务"))

            case "run":
                return await _handle_run(_require_job_id(job_id, "执行任务"))

            case "pause":
                return await _handle_pause(_require_job_id(job_id, "暂停任务"))

            case "runs":
                return await _handle_runs(
                    _require_job_id(job_id, "查看执行记录"), limit
                )

            case _:
                return f"❌ 未知操作: {operation}"

    except (ValueError, KeyError) as e:
        return f"❌ {e}"
    except RuntimeError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.error("[CronTool] 操作 %s 发生意外错误", operation, exc_info=True)
        return f"❌ 内部错误: {type(e).__name__}: {e}"
