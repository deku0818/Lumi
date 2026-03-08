"""工具展示渲染器注册表

按工具名称注册和查询对应的 ToolRenderer，未注册的工具返回默认渲染器。
"""

from __future__ import annotations

from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.renderers.protocol import ToolRenderer

_REGISTRY: dict[str, type] = {}
_DEFAULT_CLASS: type = DefaultRenderer


def register(name: str, renderer_cls: type) -> None:
    """注册工具渲染器类

    Args:
        name: 工具名称
        renderer_cls: 对应的渲染器类（每次 get 时实例化）
    """
    _REGISTRY[name] = renderer_cls


def get(name: str) -> ToolRenderer:
    """获取工具渲染器，每次返回新实例避免跨 ToolBlock 的状态共享

    Args:
        name: 工具名称

    Returns:
        渲染器新实例，未注册时返回 DefaultRenderer 实例
    """
    cls = _REGISTRY.get(name, _DEFAULT_CLASS)
    return cls()


def _register_builtins() -> None:
    """注册所有内置工具渲染器，模块加载时自动调用

    在函数内部导入各渲染器，避免循环导入。
    """
    from lumi.tui.renderers.ask import AskRenderer
    from lumi.tui.renderers.bash import BashRenderer
    from lumi.tui.renderers.edit import EditRenderer
    from lumi.tui.renderers.glob import GlobRenderer
    from lumi.tui.renderers.grep import GrepRenderer
    from lumi.tui.renderers.read import ReadRenderer
    from lumi.tui.renderers.skill import SkillRenderer
    from lumi.tui.renderers.agent import AgentRenderer
    from lumi.tui.renderers.todos import TodosRenderer
    from lumi.tui.renderers.write import WriteRenderer

    register("ask", AskRenderer)
    register("write", WriteRenderer)
    register("edit", EditRenderer)
    register("read", ReadRenderer)
    register("bash", BashRenderer)
    register("glob", GlobRenderer)
    register("grep", GrepRenderer)
    register("todos", TodosRenderer)
    register("agent", AgentRenderer)
    register("skill", SkillRenderer)


# 模块加载时自动注册内置渲染器
_register_builtins()
