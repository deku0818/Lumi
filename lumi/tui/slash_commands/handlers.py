"""技能命令和内置命令的处理器"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from lumi.utils.token_counter import str_token_counter

if TYPE_CHECKING:
    from lumi.agents.tools.config import SkillConfig


def make_skill_handler(
    skill: SkillConfig,
    send_to_agent: Callable[[str, str, str], Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """创建技能命令处理器闭包。

    拼接 skill.prompt 和用户追加文本后调用 send_to_agent。

    Args:
        skill: 技能配置
        send_to_agent: 接收 (skill_name, content, extra_text) 的异步回调

    Returns:
        接收 extra_text 参数的异步处理器
    """

    async def handler(extra_text: str = "") -> None:
        content = skill.prompt
        if extra_text:
            content = f"{content}\n\n{extra_text}"
        await send_to_agent(skill.name, content, extra_text)

    return handler


def build_skills_output(skills: list[SkillConfig], skills_dir: Path | str) -> Text:
    """构建技能列表的 Rich Text 内容。

    Args:
        skills: 技能配置列表
        skills_dir: 技能目录路径

    Returns:
        包含技能列表的 Rich Text 对象
    """
    text = Text()
    text.append("Skills\n", style="bold")

    if not skills:
        text.append("0 skills\n")
        return text

    count = len(skills)
    text.append(f"{count} skill{'s' if count != 1 else ''}\n\n")

    text.append(f"Project skills ({skills_dir})\n", style="bold")

    for skill in skills:
        tokens = str_token_counter(skill.description)
        text.append(f"{skill.name}", style="bold cyan")
        text.append(f" · ~{tokens} description tokens\n", style="dim")

    return text
