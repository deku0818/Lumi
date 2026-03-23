"""工具展示渲染器注册表

按工具名称查询对应的 ToolRenderer，未注册的工具返回默认渲染器。
所有渲染器通过 _SafeRenderer 包装，确保异常时自动回退到 DefaultRenderer。
注册由各渲染器模块的 @register_renderer 装饰器完成，本模块仅负责查询。
"""

from __future__ import annotations

import logging

from textual.widget import Widget

from lumi.tui.renderers._core import _REGISTRY
from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.renderers.protocol import ToolRenderer

logger = logging.getLogger(__name__)


class _SafeRenderer:
    """渲染器安全包装，任何方法异常时回退到 DefaultRenderer。"""

    def __init__(self, inner: ToolRenderer) -> None:
        self._inner = inner

    # ── ToolGroup 分组属性转发 ──

    @property
    def group_verb(self) -> str:
        return getattr(self._inner, "group_verb", "")

    @property
    def group_verb_active(self) -> str:
        return getattr(self._inner, "group_verb_active", "")

    @property
    def group_noun(self) -> str:
        return getattr(self._inner, "group_noun", "")

    @property
    def group_target_key(self) -> str:
        return getattr(self._inner, "group_target_key", "") or getattr(
            self._inner, "title_arg_key", ""
        )

    def render_title(self, name: str, args: dict) -> str:
        try:
            return self._inner.render_title(name, args)
        except Exception:
            logger.warning("render_title 失败，回退: %s", name, exc_info=True)
            return DefaultRenderer().render_title(name, args)

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        try:
            return self._inner.render_args(args, approval_mode=approval_mode)
        except Exception:
            logger.warning("render_args 失败，回退", exc_info=True)
            return DefaultRenderer().render_args(args)

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        try:
            return self._inner.render_summary(args, output, is_error=is_error)
        except Exception:
            logger.warning("render_summary 失败，回退", exc_info=True)
            return DefaultRenderer().render_summary(args, output, is_error=is_error)

    def render_output(self, output: str) -> Widget:
        try:
            return self._inner.render_output(output)
        except Exception:
            logger.warning("render_output 失败，回退", exc_info=True)
            return DefaultRenderer().render_output(output)


def get(name: str) -> ToolRenderer:
    """获取工具渲染器，异常时自动回退到 DefaultRenderer。

    每次返回 _SafeRenderer 包装的新实例，避免跨 ToolBlock 的状态共享。

    Args:
        name: 工具名称

    Returns:
        _SafeRenderer 包装的渲染器实例，未注册时返回 DefaultRenderer
    """
    cls = _REGISTRY.get(name, DefaultRenderer)
    return _SafeRenderer(cls())


def _register_builtins() -> None:
    """触发所有渲染器模块的导入，使装饰器执行注册。"""
    from lumi.tui.renderers import (  # noqa: F401
        agent,
        ask,
        bash,
        edit,
        glob,
        grep,
        read,
        skill,
        todos,
        write,
    )


# 模块加载时自动注册内置渲染器
_register_builtins()
