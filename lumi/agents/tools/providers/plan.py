"""Plan Mode 工具提供者 - 提供进入/退出计划模式的功能

该模块提供 EnterPlanMode 和 ExitPlanMode 工具。
EnterPlanMode 让 Agent 进入只读的探索和规划阶段；
ExitPlanMode 让 Agent 提交计划供用户审批，用户可批准或拒绝。

工具的 description 和 response 内容从 style 文件加载（如 lumi/styles/code/prompts/tools/），
用户可在 .lumi/prompts/tools/ 下覆盖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command, interrupt

from lumi.agents.tools.loader import _parse_md_file
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


# ── 文件加载 ──


def _resolve_tool_md(tool_name: str) -> Path | None:
    """按优先级查找工具配置 MD 文件。

    查找顺序：用户 .lumi/prompts/tools/ → style 内置 → None。
    用户配置优先，便于覆盖 style 默认值。
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


def _load_tool_md(tool_name: str) -> dict | None:
    """加载并解析工具配置 MD 文件，未找到返回 None。"""
    path = _resolve_tool_md(tool_name)
    if path is None:
        return None
    result = _parse_md_file(str(path))
    if result is None:
        logger.warning(f"解析 {path} 失败")
    return result


# ── EnterPlanMode ──


def _load_enter_plan_mode() -> tuple[str, str]:
    """加载 EnterPlanMode 的 (description, response)。

    Raises:
        RuntimeError: 未找到配置文件或关键字段缺失
    """
    result = _load_tool_md("EnterPlanMode")
    if result is None:
        raise RuntimeError(
            "未找到 EnterPlanMode.md 配置文件。"
            "请确保 style 目录或 .lumi/prompts/tools/ 下存在该文件。"
        )

    desc = result.get("description", "").strip()
    resp = result.get("prompt", "").strip()

    if not desc:
        raise RuntimeError("EnterPlanMode.md 缺少 description 字段")
    if not resp:
        raise RuntimeError("EnterPlanMode.md 缺少 body 内容（response）")

    return desc, resp


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
    result = _load_tool_md("ExitPlanMode")
    if result is None:
        raise RuntimeError(
            "未找到 ExitPlanMode.md 配置文件。"
            "请确保 style 目录或 .lumi/prompts/tools/ 下存在该文件。"
        )

    raw = result.get("raw_metadata", {})
    desc = result.get("description", "").strip()
    approved = (raw.get("approved") or "").strip()
    rejected = (raw.get("rejected") or "").strip()

    if not desc:
        raise RuntimeError("ExitPlanMode.md 缺少 description 字段")
    if not approved:
        raise RuntimeError("ExitPlanMode.md 缺少 approved 字段")
    if not rejected:
        raise RuntimeError("ExitPlanMode.md 缺少 rejected 字段")

    return desc, approved, rejected


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

    if user_response == PLAN_REJECTED:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=_rejected_response,
                        tool_call_id=tool_call_id,
                    )
                ],
                "tool_cancelled": True,
            },
        )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=_approved_response,
                    tool_call_id=tool_call_id,
                )
            ],
        },
    )
