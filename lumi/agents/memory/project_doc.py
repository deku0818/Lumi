"""项目根说明文件（CLAUDE.md 式）的加载。

``LUMI.md`` 放在项目根，记录这个项目的约定与对 Lumi 的指示，会在会话首条消息以
``<system-reminder>`` 注入上下文（主 + 子 agent 都注入）。与 style 系统提示词
（``.lumi/prompts/`` 的 SOUL/AGENTS）不同：那是「Lumi 是谁」，这是「这个项目要什么」。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.memory.paths import read_text_or_none

PROJECT_DOC_NAME = "LUMI.md"
"""项目根说明文件名。"""

MAX_DOC_BYTES = 50_000
"""注入上限；超出截断并附警告，避免单文件撑爆上下文。"""


def load_project_doc(project_dir: Path) -> str | None:
    """读项目根的 ``LUMI.md``；不存在或为空返回 None，过长则截断并附警告。"""
    content = read_text_or_none(project_dir / PROJECT_DOC_NAME)
    if content is None:
        return None
    if len(content) > MAX_DOC_BYTES:
        cut = content.rfind("\n", 0, MAX_DOC_BYTES)
        content = content[: cut if cut > 0 else MAX_DOC_BYTES] + (
            f"\n\n> 注意：{PROJECT_DOC_NAME} 过长，仅加载了一部分。"
        )
    return content
