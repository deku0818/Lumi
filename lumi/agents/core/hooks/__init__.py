"""Graph 节点级事件 hook 框架。

公开 API：
- ``register_hook(event, fn)``：注册 Python callable hook（FIFO）
- ``prepend_hook(event, fn)``：注册到队列头部（YAML 配置加载用）
- ``dispatch_hooks(event, ctx, *, default_goto, mode)``：在节点内分发
- ``replace_hooks(event, hooks)``：测试 ctx manager
- ``HookContext`` / ``HookEvent`` / ``Hook`` / ``HookResult``
- ``AdditionalContext(text)`` / ``Block(reason)``：返回值语法糖
- ``HookContext.payload``：``dict[str, Any]``，形状按事件由 dispatch 调用点约定

3 形态共享同一 dispatch 内核——shell/yaml hook 由外部 wrapper 包装为 Python
``Hook`` 后调 ``register_hook``，dispatch 不感知形态。

新增事件流程：``schema.HookEvent`` 加常量 + 在 graph 对应位置加
``dispatch_hooks`` 调用，否则注册了也永不触发。
"""

# import side effect：注册内置 hooks（structured_output_stop_hook）
from lumi.agents.core.hooks import builtin  # noqa: F401
from lumi.agents.core.hooks.config_loader import load_hooks, reset_hooks
from lumi.agents.core.hooks.dispatch import (
    dispatch_hooks,
    has_hooks,
    iter_hooks,
    prepend_hook,
    register_hook,
    replace_hooks,
    unregister_hook,
)
from lumi.agents.core.hooks.schema import (
    AdditionalContext,
    Block,
    Hook,
    HookContext,
    HookEvent,
    HookResult,
)

__all__ = [
    "AdditionalContext",
    "Block",
    "Hook",
    "HookContext",
    "HookEvent",
    "HookResult",
    "dispatch_hooks",
    "has_hooks",
    "iter_hooks",
    "load_hooks",
    "prepend_hook",
    "register_hook",
    "replace_hooks",
    "reset_hooks",
    "unregister_hook",
]
