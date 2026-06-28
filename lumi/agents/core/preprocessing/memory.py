"""记忆索引 + 项目说明（LUMI.md）的 ``<system-reminder>`` 块构造。

把 ``MEMORY.md`` 索引（仅主 agent）与项目根 ``LUMI.md``（主 + 子 agent）格式化为
``<system-reminder>`` 块，由 :mod:`turn_context` 组进每轮 prepend 的上下文消息。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.core.node_helpers.messages import format_reminder
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
