"""授权路径管理模块

提供工作目录授权和路径安全校验功能，确保所有文件操作都在授权范围内。
支持多个授权目录，权限引擎扩展 workspace 时同步更新。
"""

from __future__ import annotations

from pathlib import Path

from lumi.utils.logger import logger

_authorized_directories: list[Path] = []


def set_authorized_directory(path: str | Path) -> None:
    """设置主授权工作目录（重置已有列表）。

    Args:
        path: 授权目录路径
    """
    global _authorized_directories
    resolved = Path(path).resolve()
    _authorized_directories = [resolved]
    logger.info("授权工作目录已设置: %s", resolved)


def add_authorized_directory(path: str | Path) -> None:
    """添加额外的授权目录（不影响已有目录）。

    Args:
        path: 要添加的授权目录路径
    """
    resolved = Path(path).resolve()
    if resolved not in _authorized_directories:
        _authorized_directories.append(resolved)
        logger.info("已添加授权目录: %s", resolved)


def get_authorized_directory() -> Path:
    """获取主授权工作目录。

    Returns:
        主授权工作目录的绝对路径（默认为当前工作目录）
    """
    if _authorized_directories:
        return _authorized_directories[0]
    return Path.cwd().resolve()


def get_all_authorized_directories() -> list[Path]:
    """获取所有授权目录列表。

    Returns:
        所有授权目录的绝对路径列表
    """
    if _authorized_directories:
        return list(_authorized_directories)
    return [Path.cwd().resolve()]


def validate_path(path: str) -> Path:
    """验证路径是否在任一授权目录范围内。

    Args:
        path: 要验证的路径（相对路径或绝对路径）

    Returns:
        解析后的绝对路径

    Raises:
        PermissionError: 路径超出所有授权目录范围
    """
    all_dirs = get_all_authorized_directories()
    primary = all_dirs[0]
    target = Path(path)

    # 相对路径基于主授权目录解析
    if not target.is_absolute():
        target = primary / target

    resolved = target.resolve()

    for authorized in all_dirs:
        if resolved.is_relative_to(authorized):
            return resolved

    raise PermissionError(
        f"路径 '{path}' 不在授权目录范围内。"
        f"解析后的路径: {resolved}，"
        f"授权目录: {', '.join(str(d) for d in all_dirs)}"
    )
