"""授权路径管理模块

提供工作目录授权和路径安全校验功能，确保所有文件操作都在授权范围内。

两层来源（读取时 run 覆盖优先于全局兜底）：
- 进程级 ``_authorized_directories``：无 run 上下文时的兜底（测试、启动期，以及
  权限引擎重建边界时同步），单一进程全局。
- per-run ``_run_authorized_source`` contextvar：每次 agent run 由 bridge / cron 在
  run 起点注入本会话引擎的 ``authorized_directories`` 方法（实时回调，非快照）。
  设置后覆盖兜底——同进程多会话 / 多 bridge 并发时各 run 各读各的引擎，互不串扰；
  不会因为另一个会话的引擎重建边界（会重置进程全局）而把本会话「添加的目录」清洗掉。

存「实时回调」而非目录快照的原因：后台子代理经 ``asyncio.create_task`` 拷贝上下文，
若存快照则冻结在 spawn 时刻、看不到之后用户「添加文件夹」；跨工具步的 ``reload()``
重建边界同理。回调每次读取时实时求值，引擎边界在 run 内的变化都能即时反映。
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from pathlib import Path

from lumi.utils.logger import logger

# 全局兜底授权目录列表（无 run 上下文时使用）
_authorized_directories: list[Path] = []

# 当前 run 的授权目录来源（实时回调，返回 [primary, ...]）；None = 无 run 上下文，落回兜底。
_run_authorized_source: contextvars.ContextVar[Callable[[], list[Path]] | None] = (
    contextvars.ContextVar("lumi_run_authorized_source", default=None)
)


def set_run_authorized_source(source: Callable[[], list[Path]] | None) -> None:
    """注入当前 agent run 的授权目录来源（实时回调）。

    bridge 在 ``_stream`` 起点、cron 在 ``_invoke_agent`` 起点调用，传入本会话权限
    引擎的 ``authorized_directories`` 方法。读取时实时调用，故引擎边界在 run 内变化
    （添加文件夹、reload）能即时反映，后台子代理继承该回调后读到的也是引擎当前状态
    而非 spawn 时快照。contextvar 隔离使并发 run 各读各的引擎，互不串扰。

    Args:
        source: 返回授权目录列表（primary 在首位）的回调；None 表示清除。
    """
    _run_authorized_source.set(source)


def set_run_authorized_source_for(
    engine, extra_folders: tuple[str, ...] | list[str] = ()
) -> None:
    """run 起点统一注入授权目录来源——bridge / cron 共用，避免各写一份降级分支。

    有引擎：注入其 ``authorized_directories`` 方法（实时回调，子代理 / 跨步 reload 都读
    当前态）。无引擎（构造失败的降级态）：用 ``[cwd, *extra_folders]`` 的本轮快照兜底
    （无引擎可追踪、调用方在 run 内不变更这些目录，故快照一次即可，不每次读都跑 getcwd）。

    engine 鸭子类型（需 ``authorized_directories()``）；为 None 即降级。
    """
    if engine is not None:
        set_run_authorized_source(engine.authorized_directories)
    else:
        snapshot = [Path.cwd().resolve(), *(Path(f) for f in extra_folders)]
        set_run_authorized_source(lambda: snapshot)


def _run_dirs() -> list[Path] | None:
    """取当前 run 来源解析出的授权目录；无来源或解析为空时返回 None（落回兜底）。"""
    source = _run_authorized_source.get()
    if source is None:
        return None
    # source() 每次返回新列表（engine.authorized_directories 经 boundary 属性已 copy；
    # 降级/cron lambda 各自产生新列表），消费方只读，无需再 wrap 一层。
    return source() or None


def set_authorized_directory(path: str | Path) -> None:
    """设置主授权工作目录（重置全局兜底列表）。

    Args:
        path: 授权目录路径
    """
    global _authorized_directories
    resolved = Path(path).resolve()
    _authorized_directories = [resolved]
    logger.info("授权工作目录已设置: %s", resolved)


def add_authorized_directory(path: str | Path) -> None:
    """添加额外的授权目录到全局兜底列表（不影响已有目录）。

    Args:
        path: 要添加的授权目录路径
    """
    resolved = Path(path).resolve()
    if resolved not in _authorized_directories:
        _authorized_directories.append(resolved)
        logger.info("已添加授权目录: %s", resolved)


def get_authorized_directory() -> Path:
    """获取主授权工作目录。

    优先取当前 run 注入值，否则全局兜底，再否则当前工作目录。

    Returns:
        主授权工作目录的绝对路径
    """
    run = _run_dirs()
    if run:
        return run[0]
    if _authorized_directories:
        return _authorized_directories[0]
    return Path.cwd().resolve()


def get_all_authorized_directories() -> list[Path]:
    """获取所有授权目录列表。

    优先取当前 run 注入值，否则全局兜底，再否则当前工作目录。

    Returns:
        所有授权目录的绝对路径列表
    """
    run = _run_dirs()
    if run:
        return run
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
