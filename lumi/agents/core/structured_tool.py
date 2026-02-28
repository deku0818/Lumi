"""ToolStrategy 结构化输出机制

将 output_schema 伪装为工具，嵌入 Agent 循环，
模型直接在 tool_call args 中输出结构化数据——零额外 LLM 调用。
"""

import copy
import re
from typing import Any

from langchain_core.tools import StructuredTool

from lumi.utils.logger import logger

# target 合法语法：$ 或 由标识符和 * 组成的点分路径
_TARGET_PATTERN = re.compile(r"^(?:\$|(?:[a-zA-Z_]\w*|\*)(?:\.(?:[a-zA-Z_]\w*|\*))*)$")

STRUCTURED_OUTPUT_TOOL_NAME = "__structured_output__"

STRUCTURED_OUTPUT_INSTRUCTION = (
    "\n\n## Structured Output Requirement\n"
    f"You have a special tool named `{STRUCTURED_OUTPUT_TOOL_NAME}`. "
    "When you have completed the user's task and are ready to give your final answer, "
    f"you MUST call the `{STRUCTURED_OUTPUT_TOOL_NAME}` tool with the result. "
    "Do NOT reply with plain text as your final answer — always use the tool to output "
    "the structured result. You may use other tools as needed before calling it."
)


def create_structured_output_tool(output_schema: dict[str, Any]) -> StructuredTool:
    """从 JSON Schema 创建伪工具

    Args:
        output_schema: JSON Schema 定义，描述期望的输出结构

    Returns:
        StructuredTool 实例，名称为 __structured_output__
    """
    schema = {**output_schema}
    if "title" not in schema:
        schema["title"] = "StructuredOutput"
    if "description" not in schema:
        schema["description"] = (
            "Output the final structured result for the user's task."
        )

    def _noop(**kwargs):
        return kwargs

    async def _anoop(**kwargs):
        return kwargs

    tool = StructuredTool.from_function(
        func=_noop,
        coroutine=_anoop,
        name=STRUCTURED_OUTPUT_TOOL_NAME,
        description=(
            "Use this tool to output the final structured result. "
            "Call it when you have completed the task."
        ),
        args_schema=None,
    )
    # 直接设置 args_schema 为 JSON Schema 原始字典，
    # bind_tools 会将其序列化为 tool definition
    tool.args_schema = schema
    return tool


def is_structured_output_call(tool_calls: list[dict]) -> bool:
    """检测 tool_calls 中是否包含伪工具调用"""
    return any(tc.get("name") == STRUCTURED_OUTPUT_TOOL_NAME for tc in tool_calls)


def apply_output_enrich(data: dict, enrich_rules: list[dict]) -> dict:
    """将静态数据按 target 表达式注入到结构化输出中

    所有异常和不匹配情况均静默处理，仅记录日志。

    Args:
        data: 结构化输出的原始数据
        enrich_rules: 注入规则列表，每条含 target（路径表达式）和 data（合并数据）

    Returns:
        注入后的新数据（不修改原始输入）
    """
    result = copy.deepcopy(data)
    for i, rule in enumerate(enrich_rules):
        try:
            target = rule.get("target", "$")
            enrich_data = rule.get("data", {})
            if not enrich_data:
                continue
            if not _TARGET_PATTERN.match(target):
                logger.warning(
                    "[OutputEnrich] rule #%d target '%s' 语法无效，跳过", i, target
                )
                continue
            matched = _inject_at_path(result, target, enrich_data)
            if not matched:
                logger.warning(
                    "[OutputEnrich] rule #%d target '%s' 未匹配到任何节点，数据未注入",
                    i,
                    target,
                )
        except Exception:
            logger.error(
                "[OutputEnrich] rule #%d 执行失败，跳过: %r", i, rule, exc_info=True
            )
    return result


def _inject_at_path(data: dict, target: str, enrich_data: dict) -> bool:
    """返回 True 表示至少命中一个节点"""
    if target == "$":
        data.update(enrich_data)
        return True
    return _navigate_and_inject(data, target.split("."), enrich_data)


def _navigate_and_inject(current, parts: list[str], enrich_data: dict) -> bool:
    """返回 True 表示至少命中一个节点"""
    if not parts:
        if isinstance(current, dict):
            current.update(enrich_data)
            return True
        return False
    part, rest = parts[0], parts[1:]
    if part == "*":
        if isinstance(current, list) and current:
            hit = False
            for item in current:
                hit = _navigate_and_inject(item, rest, enrich_data) or hit
            return hit
        return False
    elif isinstance(current, dict) and part in current:
        return _navigate_and_inject(current[part], rest, enrich_data)
    return False


def extract_structured_args(tool_calls: list[dict]) -> dict | None:
    """提取伪工具的 args

    Returns:
        伪工具的 args 字典，如果未找到返回 None
    """
    for tc in tool_calls:
        if tc.get("name") == STRUCTURED_OUTPUT_TOOL_NAME:
            return tc.get("args", {})
    return None
