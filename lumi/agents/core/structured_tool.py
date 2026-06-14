"""ToolStrategy 结构化输出机制

将 output_schema 暴露为一个**真工具** `__structured_output__`，进 ToolExecutor 执行，
模型直接在 tool_call args 中输出结构化数据——零额外 LLM 调用。

工具闭包捕获 user_schema：
- 闭包内一次性构造的 ``Draft202012Validator`` 严格校验 args
- 失败 → return ``ToolMessage(status="error")`` 配对回灌让模型修正再调；
  ``tool_executor`` 末尾扫本轮连续失败次数，>= ``MAX_CONSECUTIVE_FAILURES``
  时强制 ``goto=END``，避免无限重试烧 token
- 通过 → return ``Command(update={structured_output, messages=[accepted]})``
  写 state 但**不**带 goto——graph 自然回到 ``CallModel``，模型看到
  ``ToolMessage("accepted")`` 后自决：end_turn / 继续推理 / 调其他工具。
  模型选择 end_turn 时 ``OnAgentStop`` 的 Stop hook 在本轮 messages 里看到这条
  accepted ToolMessage 即放行 END（hook 看的是消息序列，不是 state 字段）。

注入：用 LangChain 标准 ``InjectedToolCallId``（args_schema 字段含此 Annotated
注解，LangChain 在 invoke 时识别并自动注入）。
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Annotated, Any
from typing import Literal as TypingLiteral

from jsonschema import Draft202012Validator, SchemaError
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field, create_model

from lumi.agents.core.meta_message import iter_current_turn
from lumi.utils.logger import logger

# target 合法语法：$ 或 由标识符和 * 组成的点分路径
_TARGET_PATTERN = re.compile(r"^(?:\$|(?:[a-zA-Z_]\w*|\*)(?:\.(?:[a-zA-Z_]\w*|\*))*)$")

STRUCTURED_OUTPUT_TOOL_NAME = "__structured_output__"

INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({STRUCTURED_OUTPUT_TOOL_NAME})
"""框架内部伪工具名集合——单一事实源。``is_internal_tool`` 据此判定，消除散落各处
的 ``== STRUCTURED_OUTPUT_TOOL_NAME`` 魔法字符串匹配。

列入即同时获得两项语义：(1) 不暴露给用户 hook（PreToolUse/PostToolUse payload 过滤）；
(2) 纯内部批次绕过权限审批走快速路径。**仅限真正安全的框架控制流伪工具**——新增内部
工具加到这里一处即全部生效。"""


def is_internal_tool(name: str) -> bool:
    """是否框架内部伪工具（见 ``INTERNAL_TOOL_NAMES``）。"""
    return name in INTERNAL_TOOL_NAMES


STRUCTURED_OUTPUT_INSTRUCTION = (
    "\n\n## Structured Output Requirement\n"
    f"You have a special tool named `{STRUCTURED_OUTPUT_TOOL_NAME}`. "
    "When you have completed the user's task and are ready to give your final answer, "
    f"you MUST call the `{STRUCTURED_OUTPUT_TOOL_NAME}` tool with the result. "
    "Do NOT reply with plain text as your final answer — always use the tool to output "
    "the structured result. You may use other tools as needed before calling it."
)

STRUCTURED_OUTPUT_REMINDER = (
    "You produced no tool_calls but output_schema is still required. "
    f"You MUST call `{STRUCTURED_OUTPUT_TOOL_NAME}` now to provide the structured result."
)

# 本轮连续失败上限——触达后 ``tool_executor`` 强制 ``goto=END``，避免模型陷入
# "失败 → 重试 → 失败"循环烧 token。数值是经验取舍：太小会因偶发拼写错提前
# 放弃，太大则浪费多轮 token。调整时直接改这里，无需改其他地方。
MAX_CONSECUTIVE_FAILURES = 5


def format_structured_output_abort_message(fails: int) -> str:
    """触达连续失败上限时回灌给调用方的 user-facing 文案。"""
    return (
        f"已尝试输出结构化结果 {fails} 次但均未通过校验，"
        "为避免无限重试本轮任务已停止。请检查 output_schema 是否合理"
        "（必填字段、pattern / enum 约束等），或简化要求后重试。"
    )


# === JSON Schema 校验 ===


def _build_validator(schema: dict) -> Draft202012Validator | None:
    """schema → validator；空 / 非法 schema 返回 ``None`` 表示无约束。

    schema 自身错误（``SchemaError``）不让 LLM 背锅——log + 返回 None 视作通过。
    """
    if not isinstance(schema, dict) or not schema:
        return None
    try:
        return Draft202012Validator(schema)
    except SchemaError:
        logger.warning("[structured_output] schema 自身非法，跳过校验", exc_info=True)
        return None


def _format_validator_errors(
    args: dict, validator: Draft202012Validator | None
) -> list[str]:
    """跑 validator 收集错误，返回 ``"<path>: <message>"`` 形态的列表。

    schema 自身错误是 lazy 的：构造期间不抛，``iter_errors`` 才抛 ``UnknownType``
    等异常。这里兜底视作"无约束"——schema 写错不让 LLM 背锅。
    """
    if validator is None:
        return []
    try:
        errors: list[str] = []
        for err in validator.iter_errors(args):
            path = "/".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(f"{path}: {err.message}")
        return errors
    except Exception:
        logger.warning(
            "[structured_output] schema 校验过程异常，跳过校验", exc_info=True
        )
        return []


def validate_structured_output(args: dict, schema: dict) -> list[str]:
    """按 JSON Schema 校验 args，返回错误列表（空=通过）。"""
    return _format_validator_errors(args, _build_validator(schema))


def count_consecutive_structured_output_failures(messages: list) -> int:
    """统计本轮（最后一条**真实** ``HumanMessage`` 之后）尾部连续失败次数。

    从尾部反向扫描，遇名为 ``__structured_output__`` 的 ``ToolMessage`` 时：
    - ``status="error"`` → 计数 +1
    - 其他（成功）→ 立即 break（本轮已有 accepted）
    本轮窗口由 ``iter_current_turn`` 界定（跳过 hook reminder，真实 HumanMessage /
    后台通知为边界）。从新到旧遇 structured ToolMessage：``error`` 计 +1，accepted
    即停（本轮已成功）。

    用途：``tool_executor`` 末尾据此判断是否触达 ``MAX_CONSECUTIVE_FAILURES``。
    """
    count = 0
    for msg in iter_current_turn(messages):
        if isinstance(msg, ToolMessage) and msg.name == STRUCTURED_OUTPUT_TOOL_NAME:
            if getattr(msg, "status", None) == "error":
                count += 1
            else:
                break
    return count


# === JSON Schema → Pydantic args model ===


def _json_type_to_python(prop_schema: dict[str, Any]) -> Any:
    """JSON Schema 单字段 type → Python 类型注解。

    基本类型直接映射；``enum`` 优先转 ``Literal``；``array`` 递归 ``items``；
    ``object`` / 高级特性（``$ref`` / ``anyOf`` 等）降级为 ``dict`` / ``Any``。
    严格 required / pattern 等约束由 ``validate_structured_output`` 用原 schema 担保。
    """
    if not isinstance(prop_schema, dict):
        return Any

    enum_values = prop_schema.get("enum")
    if enum_values:
        try:
            return TypingLiteral[tuple(enum_values)]  # type: ignore[valid-type]
        except Exception:
            return Any

    json_type = prop_schema.get("type")
    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        items = prop_schema.get("items")
        if isinstance(items, dict):
            return list[_json_type_to_python(items)]  # type: ignore[misc]
        return list
    if json_type == "object":
        return dict
    if json_type == "null":
        return type(None)
    return Any


def _user_schema_to_pydantic_model(
    user_schema: dict[str, Any],
    *,
    name: str = "StructuredOutput",
) -> type[BaseModel]:
    """JSON Schema → Pydantic model 作为 ``StructuredTool.args_schema``。

    所有字段 optional（宽松解包）；``required`` / ``pattern`` 等严格性由服务端
    ``validate_structured_output`` 用原 user_schema 担保，校验错误统一走 jsonschema
    的友好路径，而非 Pydantic 的拒绝。
    """
    properties = user_schema.get("properties") or {}

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _json_type_to_python(prop_schema)
        description = (
            prop_schema.get("description", "") if isinstance(prop_schema, dict) else ""
        )
        fields[prop_name] = (
            py_type | None,
            Field(default=None, description=description),
        )

    # LangChain 自动注入 tool_call id 并从模型可见的 input_schema 中剔除该字段。
    fields["tool_call_id"] = (Annotated[str, InjectedToolCallId], "")

    model = create_model(user_schema.get("title") or name, **fields)
    model.__doc__ = user_schema.get("description") or (
        "Output the final structured result for the user's task."
    )
    return model


# === 真工具构造 ===


def create_structured_output_tool(output_schema: dict[str, Any]) -> StructuredTool:
    """从 JSON Schema 创建 ``__structured_output__`` 真工具（进 ToolExecutor 执行）。

    走 ``json.dumps`` key 的 ``lru_cache``：同一 schema 在单进程内多轮调用
    （call_model 注入 + tool_executor 执行）只构造一次 Pydantic model / Validator。
    """
    key = json.dumps(output_schema, sort_keys=True, default=str)
    return _create_structured_output_tool_cached(key)


@lru_cache(maxsize=64)
def _create_structured_output_tool_cached(schema_json: str) -> StructuredTool:
    """``lru_cache`` 包装层：以 schema 的 JSON 串作 key 命中复用。"""
    return _build_structured_output_tool(json.loads(schema_json))


def _failure_message(content: str, tool_call_id: str) -> ToolMessage:
    """失败回灌的标准 ``ToolMessage``：``status="error"`` 让本框架（及 LLM）清晰
    区分失败与成功，``count_consecutive_structured_output_failures`` 据此计数。
    """
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=STRUCTURED_OUTPUT_TOOL_NAME,
        status="error",
    )


def _build_structured_output_tool(user_schema: dict[str, Any]) -> StructuredTool:
    """构造工具实例：闭包捕获 ``user_schema`` + 一次性构造 ``Validator``。

    失败 → return ``ToolMessage(status="error")``，ToolNode 透传给下游让模型修正；
    连续失败超过 ``MAX_CONSECUTIVE_FAILURES`` 时由 ``tool_executor`` 强制 END 兜底。
    """
    args_model = _user_schema_to_pydantic_model(user_schema)
    validator = _build_validator(user_schema)
    required_fields = set(user_schema.get("required") or [])

    async def _structured_output_call(
        tool_call_id: str,
        **raw_args: Any,
    ) -> Command | ToolMessage:
        """schema 校验 + 写 ``state.structured_output``。

        失败 → ``ToolMessage(status="error")`` 配对回灌；
        通过 → ``Command(update={structured_output, messages})`` 不带 goto。
        """
        # Pydantic 解包后所有 optional 字段都出现在 kwargs（值=None）；剥掉 None 还原
        # "未提供"语义，让 jsonschema 的 required 校验生效。但 required 字段保留其
        # 显式 None——否则可空必填字段（type:[X,"null"]）会被误判为缺失而永久校验失败。
        args = {
            k: v for k, v in raw_args.items() if v is not None or k in required_fields
        }

        validation_errors = _format_validator_errors(args, validator)
        if validation_errors:
            return _failure_message(
                "Schema validation failed:\n"
                + "\n".join(f"- {e}" for e in validation_errors)
                + f"\n\nPlease call `{STRUCTURED_OUTPUT_TOOL_NAME}` "
                "again with corrected args.",
                tool_call_id,
            )

        logger.debug(
            "[structured_output] 校验通过，写 state.structured_output 让模型自决"
        )
        return Command(
            update={
                "structured_output": args,
                "messages": [
                    ToolMessage(
                        content="Structured output accepted.",
                        tool_call_id=tool_call_id,
                        name=STRUCTURED_OUTPUT_TOOL_NAME,
                    )
                ],
            },
        )

    tool_description = (
        "Use this tool to output the final structured result. Fill the structured "
        "fields directly. Call exactly once when you have completed the task."
    )

    return StructuredTool.from_function(
        coroutine=_structured_output_call,
        name=STRUCTURED_OUTPUT_TOOL_NAME,
        description=tool_description,
        args_schema=args_model,
    )


# === output_enrich：静态数据按路径注入 ===


def apply_enrich_to_command(
    merged: Command, enrich_rules: list[dict] | None
) -> Command:
    """``merged.update`` 里有 ``structured_output`` 时按规则做 enrich；失败回原值。

    工具闭包拿不到 ``state.output_enrich``，enrich 必须在节点层做。这里把
    "取 structured_output → apply_output_enrich → 异常静默回灌"封到一处，
    让 ``tool_executor`` 只剩 wiring。
    """
    if not enrich_rules or not isinstance(merged, Command):
        return merged
    structured = (merged.update or {}).get("structured_output")
    if not structured:
        return merged
    try:
        merged.update["structured_output"] = apply_output_enrich(
            structured, enrich_rules
        )
    except Exception:
        logger.error(
            "[structured_output] enrich 执行失败，返回未注入的原始结构化输出",
            exc_info=True,
        )
    return merged


def apply_output_enrich(data: dict, enrich_rules: list[dict]) -> dict:
    """将静态数据按 target 表达式注入到结构化输出中。

    所有异常和不匹配情况均静默处理，仅记录日志。返回注入后的新数据（不改原始）。
    """
    if not enrich_rules:
        return data
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
