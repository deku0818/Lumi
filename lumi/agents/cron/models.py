"""数据模型：Schedule（调度规则）和 Job（任务定义）。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# 间隔简写的单位映射（秒）
_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

# 间隔简写正则：数字 + 单位字母
_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")

# 相对时间简写正则：+数字+单位字母（如 +10m、+2h）
_RELATIVE_RE = re.compile(r"^\+(\d+)([smhd])$")


def parse_interval_to_seconds(value: str) -> int:
    """将间隔简写（如 30s、5m、2h、1d）解析为秒数。

    Args:
        value: 间隔简写字符串，格式为 `<数字><单位>`，单位支持 s/m/h/d。

    Returns:
        对应的秒数。

    Raises:
        ValueError: 格式不匹配或数值为 0。
    """
    match = _INTERVAL_RE.match(value)
    if not match:
        raise ValueError(
            f"无效的间隔格式 '{value}'，请使用 <数字><单位> 格式，如 30s、5m、2h、1d"
        )
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        raise ValueError(f"间隔值必须大于 0，收到: {value}")
    return amount * _UNIT_SECONDS[unit]


class ScheduleType(StrEnum):
    """调度类型枚举。"""

    AT = "at"  # 一次性，绝对时间点 → DateTrigger
    INTERVAL = "interval"  # 固定间隔 → IntervalTrigger
    CRON = "cron"  # cron 表达式 → CronTrigger


@dataclass(frozen=True)
class Schedule:
    """调度规则值对象，不可变。

    支持三种调度类型：
    - at: ISO 8601 绝对时间点（如 2025-01-15T09:00:00）
    - interval: 固定间隔简写（如 30s、5m、2h、1d）
    - cron: 标准 5 字段 cron 表达式（如 */5 * * * *）
    """

    type: ScheduleType
    value: str

    def to_trigger(self) -> DateTrigger | IntervalTrigger | CronTrigger:
        """将调度规则转换为 APScheduler trigger 对象。

        Returns:
            对应类型的 APScheduler trigger。

        Raises:
            ValueError: 调度规则值无法解析。
        """
        match self.type:
            case ScheduleType.AT:
                return DateTrigger(run_date=datetime.fromisoformat(self.value))
            case ScheduleType.INTERVAL:
                seconds = parse_interval_to_seconds(self.value)
                return IntervalTrigger(seconds=seconds)
            case ScheduleType.CRON:
                return CronTrigger.from_crontab(self.value)

    @staticmethod
    def parse(raw: str) -> Schedule:
        """解析用户输入为 Schedule，自动识别调度类型。

        识别顺序：
        1. 相对时间简写（如 +10m、+2h）— 转换为绝对时间的 AT 类型
        2. 间隔简写（如 30s、5m、2h、1d）— 正则匹配
        3. ISO 8601 时间点（如 2025-01-15T09:00:00）— datetime.fromisoformat
        4. 5 字段 cron 表达式（如 */5 * * * *）— CronTrigger.from_crontab

        Args:
            raw: 用户输入的调度规则字符串。

        Returns:
            解析后的 Schedule 对象。

        Raises:
            ValueError: 输入不符合任何合法格式。
        """
        raw = raw.strip()
        if not raw:
            raise ValueError("调度规则不能为空")

        # 1. 尝试相对时间简写（如 +10m、+2h），转换为绝对时间
        rel_match = _RELATIVE_RE.match(raw)
        if rel_match:
            amount = int(rel_match.group(1))
            unit = rel_match.group(2)
            if amount <= 0:
                raise ValueError(f"相对时间必须大于 0，收到: {raw}")
            delta_seconds = amount * _UNIT_SECONDS[unit]
            run_at = datetime.now() + timedelta(seconds=delta_seconds)
            return Schedule(type=ScheduleType.AT, value=run_at.isoformat())

        # 2. 尝试间隔简写
        if _INTERVAL_RE.match(raw):
            # 验证能正确解析（包括数值 > 0 检查）
            parse_interval_to_seconds(raw)
            return Schedule(type=ScheduleType.INTERVAL, value=raw)

        # 2. 尝试 ISO 8601 时间点
        try:
            datetime.fromisoformat(raw)
            return Schedule(type=ScheduleType.AT, value=raw)
        except ValueError:
            pass

        # 3. 尝试 5 字段 cron 表达式
        try:
            CronTrigger.from_crontab(raw)
            return Schedule(type=ScheduleType.CRON, value=raw)
        except (ValueError, KeyError):
            pass

        raise ValueError(
            f"无法识别调度规则 '{raw}'，支持以下格式：\n"
            "  - 相对时间：如 +10m、+2h（从现在起）\n"
            "  - ISO 8601 时间点：如 2025-01-15T09:00:00\n"
            "  - 固定间隔：如 30s、5m、2h、1d\n"
            "  - cron 表达式（5 字段）：如 */5 * * * *"
        )


@dataclass
class Job:
    """任务定义，包含调度规则和执行载荷。

    Attributes:
        id: 任务唯一标识，默认 uuid4 前 12 位。
        name: 任务名称。
        schedule: 调度规则。
        prompt: 执行载荷，发送给 Agent 的提示词。
        enabled: 是否启用。
        created_at: 创建时间。
        consecutive_errors: 连续错误计数，用于重试退避。
    """

    name: str
    schedule: Schedule
    prompt: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    consecutive_errors: int = 0

    def to_dict(self) -> dict:
        """将 Job 序列化为字典，用于 JSON 持久化。

        Returns:
            包含所有字段的字典，schedule 和 created_at 转换为可序列化格式。
        """
        return {
            "id": self.id,
            "name": self.name,
            "schedule": {
                "type": self.schedule.type.value,
                "value": self.schedule.value,
            },
            "prompt": self.prompt,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "consecutive_errors": self.consecutive_errors,
        }

    @staticmethod
    def from_dict(data: dict) -> Job:
        """从字典反序列化为 Job 对象。

        Args:
            data: 包含 Job 字段的字典，通常来自 JSON 文件。

        Returns:
            反序列化后的 Job 实例。
        """
        schedule_data = data["schedule"]
        return Job(
            id=data["id"],
            name=data["name"],
            schedule=Schedule(
                type=ScheduleType(schedule_data["type"]),
                value=schedule_data["value"],
            ),
            prompt=data["prompt"],
            enabled=data.get("enabled", True),
            created_at=datetime.fromisoformat(data["created_at"]),
            consecutive_errors=data.get("consecutive_errors", 0),
        )
