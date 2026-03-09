"""斜杠命令数据模型 - 定义命令类型枚举和命令元数据"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class CommandType(StrEnum):
    """命令类型枚举"""

    BUILTIN = "builtin"
    SKILL = "skill"


@dataclass(frozen=True)
class SlashCommand:
    """斜杠命令元数据（不可变）

    Attributes:
        name: 命令名称（不含 / 前缀）
        description: 命令描述
        command_type: 内置或技能
        handler: 异步执行回调
    """

    name: str
    description: str
    command_type: CommandType
    handler: Callable[..., Awaitable[None]]
