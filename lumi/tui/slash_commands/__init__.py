"""斜杠命令系统公共接口。"""

from lumi.tui.slash_commands.handlers import build_skills_output, make_skill_handler
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.parser import (
    extract_command_prefix,
    is_command_mode,
    parse_command_input,
)
from lumi.tui.slash_commands.registry import CommandRegistry

__all__ = [
    "CommandRegistry",
    "CommandType",
    "SlashCommand",
    "extract_command_prefix",
    "build_skills_output",
    "is_command_mode",
    "make_skill_handler",
    "parse_command_input",
]
