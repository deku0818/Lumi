"""命令注册表 - 管理所有已注册的斜杠命令"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from lumi.utils.logger import logger

from .models import CommandType, SlashCommand

if TYPE_CHECKING:
    from lumi.agents.tools.loader import SkillConfig


class CommandRegistry:
    """命令注册表 - 管理所有已注册的斜杠命令。

    内部使用 dict[str, SlashCommand] 存储，对外暴露不可变视图（tuple）。
    """

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, command: SlashCommand) -> bool:
        """注册命令。

        Args:
            command: 要注册的斜杠命令

        Returns:
            注册成功返回 True，名称重复时返回 False 并记录警告
        """
        if command.name in self._commands:
            logger.warning("命令名称重复，注册被拒绝: /%s", command.name)
            return False
        self._commands[command.name] = command
        return True

    def unregister(self, name: str) -> bool:
        """移除命令。

        Args:
            name: 要移除的命令名称

        Returns:
            移除成功返回 True，命令不存在返回 False
        """
        if name in self._commands:
            del self._commands[name]
            return True
        return False

    def match(self, prefix: str) -> tuple[SlashCommand, ...]:
        """按前缀模糊匹配已注册命令。

        Args:
            prefix: 命令名称前缀，空字符串返回全部命令

        Returns:
            匹配的命令不可变元组
        """
        if not prefix:
            return tuple(self._commands.values())
        return tuple(
            cmd for cmd in self._commands.values() if cmd.name.startswith(prefix)
        )

    def get(self, name: str) -> SlashCommand | None:
        """精确查找命令。

        Args:
            name: 命令名称

        Returns:
            匹配的命令，不存在返回 None
        """
        return self._commands.get(name)

    def sync_skills(
        self,
        skills: list[SkillConfig],
        make_handler: Callable[[SkillConfig], Callable[..., Awaitable[None]]],
    ) -> None:
        """同步技能命令：移除已删除的，添加新增的，不影响内置命令。

        Args:
            skills: 最新的技能配置列表
            make_handler: 接收 SkillConfig 返回异步处理器的工厂函数
        """
        new_skill_names = {s.name for s in skills}

        # 移除已删除的技能命令
        to_remove = [
            name
            for name, cmd in self._commands.items()
            if cmd.command_type == CommandType.SKILL and name not in new_skill_names
        ]
        for name in to_remove:
            del self._commands[name]

        # 添加新增的或更新已有的技能命令（不覆盖内置命令）
        for skill in skills:
            existing = self._commands.get(skill.name)
            if existing is not None and existing.command_type != CommandType.SKILL:
                # 内置命令不受影响，跳过同名技能
                continue
            command = SlashCommand(
                name=skill.name,
                description=skill.description,
                command_type=CommandType.SKILL,
                handler=make_handler(skill),
            )
            self._commands[command.name] = command

    @property
    def all_commands(self) -> tuple[SlashCommand, ...]:
        """返回所有已注册命令的不可变元组。"""
        return tuple(self._commands.values())
