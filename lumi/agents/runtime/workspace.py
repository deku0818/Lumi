"""会话项目目录上下文：隔离并发 run，统一解析工具的本机路径。"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from pathlib import Path

from langgraph.runtime import get_runtime

from lumi.agents.memory.paths import resolve_under_project

_project_source: contextvars.ContextVar[Callable[[], Path] | None] = (
    contextvars.ContextVar("lumi_project_source", default=None)
)


def set_project_source(source: Callable[[], Path] | None) -> None:
    """注入当前 run 的项目目录来源；子任务继承，回调可反映会话切换。"""
    _project_source.set(source)


def get_project_directory() -> Path:
    """当前 run 的项目目录；支持直接 Graph 调用，无上下文时取进程 cwd。"""
    source = _project_source.get()
    if source is not None:
        return source()
    try:
        runtime = get_runtime()
        context = runtime.context if runtime is not None else None
    except RuntimeError:  # 独立调用工具，不在 Graph run 内
        context = None
    if context is not None:
        if context.permission_engine is not None:
            return context.permission_engine.project_dir
        if context.project_dir is not None:
            return context.project_dir.resolve()
    return Path.cwd().resolve()


def resolve_tool_path(path: str | Path) -> Path:
    """展开 ~，相对路径基于会话项目根解析；绝对路径可指向任意目录。"""
    return resolve_under_project(path, get_project_directory())
