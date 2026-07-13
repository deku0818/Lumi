"""框架内置 hooks。模块加载时由 ``__init__.py`` import 触发自动注册。"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from lumi.agents.core.hooks.dispatch import register_hook
from lumi.agents.core.hooks.schema import AdditionalContext, HookContext, HookResult
from lumi.agents.core.meta_message import is_reminder_message, iter_current_turn
from lumi.agents.core.structured_tool import (
    STRUCTURED_OUTPUT_REMINDER,
    STRUCTURED_OUTPUT_TOOL_NAME,
)
from lumi.utils.logger import logger

# 本轮最多拉回模型几次去补结构化输出——超过则放弃 END，避免模型一直纯文本结束
# 时 OnAgentStop↔CallModel 无限循环直到 GraphRecursionError。
MAX_STOP_PULLBACKS = 3


def _is_accepted_structured(msg) -> bool:
    """是否一条 accepted（非 error）的 ``__structured_output__`` ToolMessage。"""
    return (
        isinstance(msg, ToolMessage)
        and msg.name == STRUCTURED_OUTPUT_TOOL_NAME
        and getattr(msg, "status", None) != "error"
    )


def _pullback_count(messages) -> int:
    """本轮已注入过几次结构化输出 reminder（拉回次数）。

    本轮窗口由 ``iter_current_turn`` 界定。从新到旧遇 accepted structured ToolMessage
    即停（已成功）；每条 hook reminder 计 +1（按 ``is_reminder_message`` 标记识别，不
    再嗅探 reminder 文本，措辞改了也不失效）。
    """
    count = 0
    for msg in iter_current_turn(messages):
        if _is_accepted_structured(msg):
            break
        if is_reminder_message(msg):
            count += 1
    return count


def _accepted_in_current_turn(messages) -> bool:
    """本轮是否已有 accepted ``__structured_output__`` ToolMessage。

    本轮窗口由 ``iter_current_turn`` 界定（跳过 hook reminder，真实 HumanMessage /
    后台通知为边界）。
    """
    return any(_is_accepted_structured(msg) for msg in iter_current_turn(messages))


async def structured_output_stop_hook(ctx: HookContext) -> HookResult:
    """模型未调任何工具想结束、但 ``output_schema`` 仍要求结构化输出时拦截。

    1. 未启用 schema → 放行
    2. 本轮已有 accepted ``__structured_output__`` ToolMessage → 放行 END
    3. 否则注入 ``<system-reminder>`` 拉回 ``CallModel``

    本 hook 只管"模型放弃调工具"这一种情况；"调了但反复失败"由 tool_executor
    末尾的 ``MAX_CONSECUTIVE_FAILURES`` 兜底强制 ``goto=END``，不经过本 hook。
    """
    if not ctx.state.get("output_schema"):
        return None
    messages = ctx.state.get("messages")
    if _accepted_in_current_turn(messages):
        return None
    if _pullback_count(messages) >= MAX_STOP_PULLBACKS:
        # 已多次拉回但模型仍不调结构化输出工具——放弃，让 OnAgentStop 正常 END，
        # 避免无限 OnAgentStop↔CallModel 循环耗到 GraphRecursionError。
        logger.warning(
            "[structured_output_stop_hook] 已拉回 %d 次模型仍未输出结构化结果，放弃并结束本轮",
            MAX_STOP_PULLBACKS,
        )
        return None
    return AdditionalContext(STRUCTURED_OUTPUT_REMINDER)


register_hook("Stop", structured_output_stop_hook)


# 目标驱动（/goal）：会话有活跃 goal 时，模型想结束前判定条件是否成立——未成立注入
# reminder 拉回、成立/impossible 清 goal 放行。注册在 structured_output 之后、auto_dream
# 之前：未达成时 goal 返 AdditionalContext short-circuit 掉 dream（会话没结束，正确）；
# 达成/impossible 时 goal 返 None，dispatch 继续到 dream（会话结束，dream 正常触发）。
from lumi.agents.core.hooks.goal import goal_stop_hook  # noqa: E402

register_hook("Stop", goal_stop_hook)


# 后台记忆综合（auto dream）：会话结束按门控触发离线综合。dream 模块顶层仅轻量依赖
# （schema / dream_lock / normalize），不引入 core.graph / tools（均延迟 import），故此处
# import 不致循环。门控阶梯保证默认（config 关 / 子 agent / 非记忆会话）零成本放行。
from lumi.agents.memory.dream import auto_dream_stop_hook  # noqa: E402

register_hook("Stop", auto_dream_stop_hook)


# 上下文注入（env / agent / skill / 记忆索引 / LUMI.md）：marker 比对 + 条目级 diff，
# 详见 context_inject 模块 docstring。
from lumi.agents.core.preprocessing.context_inject import (  # noqa: E402
    context_inject_hook,
)

register_hook("UserPromptSubmit", context_inject_hook)
