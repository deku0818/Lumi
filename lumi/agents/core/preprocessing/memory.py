"""记忆索引 + 项目说明（LUMI.md）的首条消息注入。

把 ``MEMORY.md`` 索引（仅主 agent）与项目根 ``LUMI.md``（主 + 子 agent）格式化为
``<system-reminder>`` 块，注入到会话首条用户消息，使模型一开局就有项目上下文与记忆目录。
与 :mod:`lumi.agents.core.preprocessing.system_info` 同款管线。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import (
    format_reminder,
    inject_text_into_message,
)
from lumi.agents.memory.project_doc import PROJECT_DOC_NAME, load_project_doc
from lumi.agents.memory.prompt import load_memory_index


def format_memory_reminder(project_dir: Path, include_memory: bool) -> str | None:
    """构造记忆 + 项目说明的 ``<system-reminder>`` 块；两者皆空返回 None。"""
    sections: list[str] = []

    doc = load_project_doc(project_dir)
    if doc:
        sections.append(f"# 项目说明（{PROJECT_DOC_NAME}）\n{doc}")

    if include_memory:
        index = load_memory_index(project_dir)
        if index:
            sections.append(f"# 你的持久记忆索引（MEMORY.md，跨会话保留）\n{index}")

    if not sections:
        return None
    intro = "以下是供你参考的上下文，仅在与当前任务高度相关时才使用："
    # 空串 + 各段，让 format_reminder 的 "\n".join 在 intro 与正文间留一空行
    return format_reminder(intro, ["", "\n\n".join(sections)])


def inject_memory_context_into_message(
    message: HumanMessage,
    project_dir: Path,
    include_memory: bool,
) -> HumanMessage:
    """把记忆 + 项目说明块注入用户消息最前面，返回新消息；无内容时原样返回。"""
    reminder = format_memory_reminder(project_dir, include_memory)
    if reminder is None:
        return message
    return inject_text_into_message(message, reminder)
