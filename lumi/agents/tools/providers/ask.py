"""Ask User 工具提供者 - 提供向用户提问的功能

该模块提供 ask_user_question 工具，让 Agent 在执行过程中可以向用户提问，
通过中断机制暂停执行并等待用户回答。
"""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field


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


ASK_USER_DESCRIPTION = """当你在执行过程中需要向用户提问时，使用此工具。这可以帮助你：
1. 收集用户偏好或需求
2. 澄清含糊不清的指令
3. 在实现过程中获得关于实现选择的决策
4. 向用户提供可选择的行动方向。

使用说明：
- 用户始终可以选择"Other"来提供自定义文本输入
- 使用 multiSelect: true 可允许为同一个问题选择多个答案
- 如果你推荐某个特定选项，请将其放在选项列表的第一位，并在标签末尾添加"(Recommended)"
"""


@tool(description=ASK_USER_DESCRIPTION)
def ask(
    questions: Annotated[
        list[Question],
        Field(
            description="要向用户提出的问题（1-4 个问题）", min_length=1, max_length=4
        ),
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """向用户提问并等待回答"""
    # 构建问题列表，添加 ID
    questions_data = []
    for i, q in enumerate(questions):
        # 构建选项列表，末尾添加自定义输入选项
        options = [
            {"label": opt.label, "description": opt.description} for opt in q.options
        ]
        options.append({"label": "", "description": "输入内容"})

        questions_data.append(
            {
                "id": i,
                "question": q.question,
                "header": q.header,
                "options": options,
                "multiSelect": q.multiSelect,
            }
        )

    # 构建中断数据
    interrupt_data = {
        "type": "ask",
        "tool_call_id": tool_call_id,
        "questions": questions_data,
    }

    # 触发中断，等待用户回答
    user_response = interrupt(interrupt_data)

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
