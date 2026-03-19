"""渲染器注册核心 — 装饰器 + 全局注册表。

此模块无外部依赖，作为渲染器层的最底层模块，不存在循环导入。
"""

from __future__ import annotations

_REGISTRY: dict[str, type] = {}


def register_renderer(name: str):
    """装饰器：将渲染器类注册到全局注册表。

    用法:
        @register_renderer("bash")
        class BashRenderer(BaseRenderer):
            ...
    """

    def decorator(cls: type) -> type:
        _REGISTRY[name] = cls
        return cls

    return decorator
