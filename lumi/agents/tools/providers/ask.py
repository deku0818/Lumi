"""Ask User 工具提供者 - 让 Agent 在执行过程中向用户提问

经在途审批 Broker（``runtime.context.approval_broker``）原地挂起并等待用户回答，
替代原 LangGraph ``interrupt()`` 中断机制（见 docs/architecture/approval-inflight.md）。
"""

# 注意：本模块**不能**加 `from __future__ import annotations`。它会把 `runtime: ToolRuntime`
# 注解字符串化，导致 langchain 认不出该注入参数、不注入 → "missing runtime"。registry
# 加载期对此有 fail-fast 守卫（见 tests/test_agent_delegation_depth.py）。

from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

# 取消信号常量，前端和 ask 工具共享
ASK_CANCELLED = "__ask_cancelled__"


class QuestionOption(BaseModel):
    """问题选项定义"""

    label: str = Field(
        description="用户将看到并选择的该选项显示文本。应简洁明了（1-5 个词），并清楚描述该选择。"
    )
    description: str = Field(
        description="对该选项含义的说明，或在选择后将会发生什么。适用于提供关于权衡或影响的上下文信息。"
    )


class Question(BaseModel):
    """问题定义"""

    question: str = Field(
        description='向用户提出的完整问题。应清晰、具体，并以问号结尾。示例："我们应该使用哪个库来进行日期格式化？"如果 multiSelect 为 true，应相应调整表述方式，例如："你希望启用哪些功能？"'
    )
    header: str = Field(
        description='作为标签/徽标显示的非常简短的标题（最多 12 个字符）。示例："认证方式"、"库"、"方案"。'
    )
    options: list[QuestionOption] = Field(
        description='该问题的可选项。必须包含 2-4 个选项。除非启用了 multiSelect，否则每个选项都应是彼此区分、互斥的选择。这里不应包含 "Other" 选项，该选项会自动提供。',
        min_length=2,
        max_length=4,
    )
    multiSelect: bool = Field(  # noqa: N815
        description="设置为 true 以允许用户选择多个选项，而不仅限于一个。当选项不是互斥关系时使用。"
    )


class AskInput(BaseModel):
    """Ask 工具输入 —— 显式 args_schema，避免注入参数 runtime / tool_call_id 漏进模型 schema。"""

    questions: list[Question] = Field(
        description="要向用户提出的问题（1-4 个问题）", min_length=1, max_length=4
    )


ASK_USER_DESCRIPTION = """仅当你被一个真正属于用户的决策阻塞时，用此工具向用户提问——即从请求本身、代码或常规默认值都无法确定答案的决策。这可以帮助你：
1. 收集用户偏好或需求
2. 澄清含糊不清的指令
3. 在实现过程中获得关于实现选择的决策
4. 向用户提供可选择的行动方向

何时不要问：
- 有常规默认值的选择——直接按默认做，在回复中说明即可，不要打断用户
- 你能从代码或文档中自行验证的事实——去验证，不要问

使用说明：
- 用户始终可以选择"Other"来提供自定义文本输入
- 使用 multiSelect: true 可允许为同一个问题选择多个答案
- 如果你推荐某个特定选项，请将其放在选项列表的第一位，并在标签末尾添加"(Recommended)"
"""


def _build_question_data(index: int, question: Question) -> dict[str, Any]:
    """将 Question 模型转换为审批数据所需的字典格式。"""
    options: list[dict[str, str]] = [
        {"label": opt.label, "description": opt.description} for opt in question.options
    ]
    # 末尾自动添加自定义输入选项
    options.append({"label": "", "description": "输入内容"})

    return {
        "id": index,
        "question": question.question,
        "header": question.header,
        "options": options,
        "multiSelect": question.multiSelect,
    }


@tool(description=ASK_USER_DESCRIPTION, args_schema=AskInput)
async def ask(
    questions: list[Question],
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """向用户提问并等待回答（经在途审批 Broker 挂起）"""
    questions_data = [_build_question_data(i, q) for i, q in enumerate(questions)]

    # 无审批通道（headless：cron / workflow / 后台子代理，context.approval_broker 为 None）：
    # 无法向用户提问，返回提示让自治 agent 自行判断后继续，而非崩溃。
    broker = runtime.context.approval_broker
    if broker is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="No interactive channel is available to ask the user in this environment; proceed using your best judgment without asking.",
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    # reject_value=ASK_CANCELLED：本提问被 stop / 切会话收尾时按"用户取消作答"处理，
    # 让本轮干净完成、保留历史。
    user_response = await broker.request(
        {
            "type": "ask",
            "tool_call_id": tool_call_id,
            "questions": questions_data,
        },
        ASK_CANCELLED,
    )

    # 用户取消：设置 tool_cancelled 标记，由条件边路由到 END
    if user_response == ASK_CANCELLED:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="User declined to answer questions",
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
                    content=user_response,
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
