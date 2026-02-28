"""授权路径管理模块

提供工作目录授权和路径安全校验功能，确保所有文件操作都在授权范围内。
"""

from pathlib import Path

from lumi.utils.logger import logger


_authorized_directory: Path | None = None


def set_authorized_directory(path: str | Path) -> None:
    """设置授权工作目录

    Args:
        path: 授权目录路径
    """
    global _authorized_directory
    _authorized_directory = Path(path).resolve()
    logger.info(f"授权工作目录已设置: {_authorized_directory}")


def get_authorized_directory() -> Path:
    """获取授权工作目录

    Returns:
        授权工作目录的绝对路径（默认为当前工作目录）
    """
    if _authorized_directory is not None:
        return _authorized_directory
    return Path.cwd().resolve()


def validate_path(path: str) -> Path:
    """验证路径是否在授权目录范围内

    Args:
        path: 要验证的路径（相对路径或绝对路径）

    Returns:
        解析后的绝对路径

    Raises:
        PermissionError: 路径超出授权目录范围
    """
    authorized = get_authorized_directory()
    target = Path(path)

    # 如果是相对路径，基于授权目录解析
    if not target.is_absolute():
        target = authorized / target

    resolved = target.resolve()

    if not resolved.is_relative_to(authorized):
        raise PermissionError(
            f"路径 '{path}' 不在授权目录 '{authorized}' 范围内。"
            f"解析后的路径: {resolved}"
        )

    return resolved
