"""Workspace 标识工具模块

根据工作目录生成唯一标识，用于 cron、session 等按目录隔离存储。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def get_workspace_dir(cwd: Path | None = None) -> str:
    """返回 resolved CWD 的字符串形式。

    Args:
        cwd: 工作目录，默认为当前目录。

    Returns:
        resolved 绝对路径字符串。
    """
    path = cwd or Path.cwd()
    return str(path.resolve())


def get_workspace_id(cwd: Path | None = None) -> str:
    """SHA256(resolved CWD)[:12] 作为 workspace 标识。

    Args:
        cwd: 工作目录，默认为当前目录。

    Returns:
        12 位 hex 字符串。
    """
    workspace_dir = get_workspace_dir(cwd)
    return hashlib.sha256(workspace_dir.encode()).hexdigest()[:12]
