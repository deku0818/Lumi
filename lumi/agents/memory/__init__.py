"""按项目隔离的持久记忆 + 项目说明（AGENTS.md）按需读取规则。

- ``paths``：记忆目录单一事实源（``~/.lumi/memory/projects/<项目>/``）。
- ``prompt``：记忆行为说明（系统提示词）+ MEMORY.md 索引加载。
- ``project_doc``：``AGENTS.md`` 按需读取规则。
"""

from __future__ import annotations

from lumi.agents.memory.paths import (
    ENTRYPOINT_NAME,
    ensure_memory_dir,
    is_memory_path,
    memory_dir,
    memory_entrypoint,
)
from lumi.agents.memory.project_doc import PROJECT_DOC_NAME
from lumi.agents.memory.prompt import build_memory_instructions, load_memory_index

__all__ = [
    "ENTRYPOINT_NAME",
    "PROJECT_DOC_NAME",
    "build_memory_instructions",
    "ensure_memory_dir",
    "is_memory_path",
    "load_memory_index",
    "memory_dir",
    "memory_entrypoint",
]
