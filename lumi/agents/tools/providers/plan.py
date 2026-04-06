"""Plan Mode 工具提供者 - 提供进入/退出计划模式的功能

EnterPlanMode 让 Agent 进入只读的探索和规划阶段；
ExitPlanMode 让 Agent 提交计划供用户审批，用户可批准或拒绝。

工具的 description 和 response 内容从 style 文件加载（如 lumi/styles/code/prompts/tools/），
用户可在 .lumi/prompts/tools/ 下覆盖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command, interrupt

from lumi.agents.tools.loader import _parse_md_file
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


# ── 文件加载 ──


def _resolve_tool_md(tool_name: str) -> Path | None:
    """按优先级查找工具配置 MD 文件。

    查找顺序：用户 .lumi/prompts/tools/ -> style 内置 -> None。
    """
    config = get_config()

    # 1. 用户 .lumi/prompts/tools/
    user_path = config.prompts_dir / "tools" / f"{tool_name}.md"
    if user_path.exists():
        return user_path

    # 2. style 内置
    from lumi.styles import get_style_prompts_dir

    try:
        style_path = (
            get_style_prompts_dir(config.active_style) / "tools" / f"{tool_name}.md"
        )
        if style_path.exists():
            return style_path
    except ValueError:
        pass

    return None


def _load_tool_md(tool_name: str) -> dict[str, Any] | None:
    """加载并解析工具配置 MD 文件，未找到返回 None。"""
    path = _resolve_tool_md(tool_name)
    if path is None:
        return None
    parsed = _parse_md_file(str(path))
    if parsed is None:
        logger.warning("解析 %s 失败", path)
    return parsed


def _require_tool_field(
    parsed: dict[str, Any], field: str, tool_name: str, *, from_raw: bool = False
) -> str:
    """从解析结果中提取必填字段，缺失时抛出 RuntimeError。"""
    if from_raw:
        value = (parsed.get("raw_metadata", {}).get(field) or "").strip()
    else:
        value = (parsed.get(field) or "").strip()
    if not value:
        raise RuntimeError(f"{tool_name}.md 缺少 {field} 字段")
    return value


# ── EnterPlanMode ──


def _load_enter_plan_mode() -> tuple[str, str]:
    """加载 EnterPlanMode 的 (description, response)。

    Raises:
        RuntimeError: 未找到配置文件或关键字段缺失
    """
    parsed = _load_tool_md("EnterPlanMode")
    if parsed is None:
        raise RuntimeError(
            "未找到 EnterPlanMode.md 配置文件。"
            "请确保 style 目录或 .lumi/prompts/tools/ 下存在该文件。"
        )

    description = _require_tool_field(parsed, "description", "EnterPlanMode")
    response = _require_tool_field(parsed, "prompt", "EnterPlanMode")
    return description, response


_enter_description, _enter_response = _load_enter_plan_mode()

# 供外部模块（如 app.py plan reminder 注入）使用
plan_mode_response = _enter_response


@tool(description=_enter_description)
def EnterPlanMode() -> str:  # noqa: N802
    """进入计划模式，开始只读的代码探索和方案设计阶段"""
    return _enter_response


# ── ExitPlanMode ──

PLAN_REJECTED = "__plan_rejected__"


def _load_exit_plan_mode() -> tuple[str, str, str]:
    """加载 ExitPlanMode 的 (description, approved_response, rejected_response)。

    Raises:
        RuntimeError: 未找到配置文件或关键字段缺失
    """
    parsed = _load_tool_md("ExitPlanMode")
    if parsed is None:
        raise RuntimeError(
            "未找到 ExitPlanMode.md 配置文件。"
            "请确保 style 目录或 .lumi/prompts/tools/ 下存在该文件。"
        )

    description = _require_tool_field(parsed, "description", "ExitPlanMode")
    approved = _require_tool_field(parsed, "approved", "ExitPlanMode", from_raw=True)
    rejected = _require_tool_field(parsed, "rejected", "ExitPlanMode", from_raw=True)
    return description, approved, rejected


_exit_description, _approved_response, _rejected_response = _load_exit_plan_mode()


@tool(description=_exit_description)
def ExitPlanMode(  # noqa: N802
    plan_file_path: Annotated[
        str,
        "The path to the plan file you wrote during the planning phase.",
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """退出计划模式，提交计划供用户审批"""
    user_response = interrupt(
        {
            "type": "ExitPlanMode",
            "tool_call_id": tool_call_id,
            "plan_file_path": plan_file_path,
        }
    )

    response_content = (
        _rejected_response if user_response == PLAN_REJECTED else _approved_response
    )

    update: dict = {
        "messages": [
            ToolMessage(
                content=response_content,
                tool_call_id=tool_call_id,
            )
        ],
    }
    if user_response == PLAN_REJECTED:
        update["tool_cancelled"] = True

    return Command(update=update)
