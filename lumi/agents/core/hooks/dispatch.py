"""Hook 注册表与 dispatch 内核。

所有形态（Python callable / Shell / YAML 包装）共享同一 dispatch——非 Python
形态在外部模块包装为 ``Hook`` 函数后调 ``register_hook``，本模块不感知形态差异。

3 模式：
- ``first_intercept``：第一个返非 None 的 hook 拦截，后续不跑。Stop /
  UserPromptSubmit 用——接管者语义。
- ``collect``：多 hook 的 AdditionalContext 合并到同一 Command；遇到首个
  Block / Command 立即拦截但已收的 reminder 一起注入。PreToolUse / PostToolUse
  用——多 reminder 共存有意义。
- ``side_effect``：所有 hook 并发跑，返回值仅 warning。SessionEnd 用。

错误隔离：每个 hook 包 try/except，单 hook 抛错 ``logger.exception`` 后继续
下一个，dispatch 不抛。Shell/YAML wrapper 内部异常走同路径——对调用方透明。
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command

from lumi.agents.core.hooks.schema import (
    AdditionalContext,
    Block,
    Hook,
    HookContext,
    HookEvent,
    HookResult,
)
from lumi.agents.core.meta_message import reminder_human_message
from lumi.utils.logger import logger

# 框架内置 hook（import 时由 builtin.py 注册）。进程全局、与项目无关。
_HOOKS: dict[HookEvent, list[Hook]] = {}

# 本 run 的项目级 config hook（来自 .lumi/hooks.json）。per-run contextvar：每个
# 会话各绑各项目、并发互不串；后台子代理继承父 run 的 contextvar。None = 无配置 hook。
_run_config_hooks: contextvars.ContextVar[dict[HookEvent, list[Hook]] | None] = (
    contextvars.ContextVar("lumi_run_config_hooks", default=None)
)


def set_run_config_hooks(hooks: dict[HookEvent, list[Hook]] | None) -> None:
    """注入当前 run 的项目级 config hook（bridge / cron 在 run 起点调用）。"""
    _run_config_hooks.set(hooks)


def _hooks_for(event: HookEvent) -> list[Hook]:
    """本 run 生效的 hook：项目级 config（优先）+ 框架 builtin（其后）。

    顺序与旧 prepend 实现一致——config hook 整体压在 builtin 之前。
    """
    config = _run_config_hooks.get()
    config_hooks = config.get(event, []) if config else []
    return [*config_hooks, *_HOOKS.get(event, [])]


def has_hooks(event: HookEvent) -> bool:
    """该事件下是否有任何 hook（config + builtin）。便宜预判，避免白白构造 payload。"""
    return bool(_hooks_for(event))


def _reminder_message(text: str) -> HumanMessage:
    """hook 注入的 reminder：声明无可显示 + ``is_hook_reminder`` 标记的合成消息
    （"给模型看的，不是用户说的话"）。不渲染为用户气泡；失败计数 / 轮边界判断
    据 ``is_hook_reminder`` 精确跳过它（而非泛跳过所有合成消息，否则会误跳后台
    通知等真实轮边界）——对齐 Claude Code 的结构化标记做法。
    """
    block = f"<system-reminder>\n{text}\n</system-reminder>\n"
    return reminder_human_message([{"type": "text", "text": block}])


def register_hook(event: HookEvent, hook: Hook) -> None:
    """注册 hook 到事件队列尾部。FIFO 即注册顺序即执行顺序。

    适合 Python 代码（import side effect / 运行时）注册内置 fallback hook。
    """
    _HOOKS.setdefault(event, []).append(hook)


def unregister_hook(event: HookEvent, hook: Hook) -> bool:
    """从事件队列移除指定 hook。命中返 True，未命中返 False。

    给 YAML 配置重载用——重载时精准移除 YAML 注册的 hook 而保留 builtin。
    """
    hooks = _HOOKS.get(event)
    if hooks and hook in hooks:
        hooks.remove(hook)
        return True
    return False


def _to_command(result: HookResult, *, default_goto: str) -> Command | None:
    """翻译 HookResult。``None``→None，``Command`` 原样，``AdditionalContext``
    包装为 ``<system-reminder>`` HumanMessage 块，``Block`` 包装为 END +
    AIMessage(reason)。
    """
    if result is None:
        return None
    if isinstance(result, Command):
        return result
    if isinstance(result, AdditionalContext):
        return Command(
            goto=default_goto,
            update={"messages": [_reminder_message(result.text)]},
        )
    if isinstance(result, Block):
        return Command(
            goto=END,
            update={"messages": [AIMessage(content=result.reason)]},
        )
    raise TypeError(f"unknown HookResult: {type(result)!r}")


async def dispatch_hooks(
    event: HookEvent,
    ctx: HookContext,
    *,
    default_goto: str = "CallModel",
    mode: Literal["first_intercept", "collect", "side_effect"] = "first_intercept",
) -> Command | None:
    """串行跑事件下的所有 hook，按 mode 决定协同行为。

    返回值：
    - ``Command``：调用方应据此路由（一般 ``return cmd``）
    - ``None``：所有 hook 放行，调用方走默认行为
    """
    hooks = _hooks_for(event)
    if not hooks:
        return None

    if mode == "side_effect":
        # 所有 hook 并发跑——side_effect 无短路语义，独立 await 浪费时间
        results = await asyncio.gather(
            *(hook(ctx) for hook in hooks), return_exceptions=True
        )
        for hook, result in zip(hooks, results):
            if isinstance(result, BaseException):
                logger.error(
                    "[hooks] %s hook %r raised; ignored: %s",
                    event,
                    hook,
                    result,
                    exc_info=result,
                )
            elif result is not None:
                logger.warning(
                    "[hooks] %s hook %r returned %s; side_effect mode ignores",
                    event,
                    hook,
                    type(result).__name__,
                )
        return None

    if mode == "first_intercept":
        for hook in hooks:
            try:
                raw = await hook(ctx)
            except Exception:
                logger.exception("[hooks] %s hook %r raised; ignored", event, hook)
                continue
            cmd = _to_command(raw, default_goto=default_goto)
            if cmd is not None:
                return cmd
        return None

    # mode == "collect"
    extra_msgs: list = []
    for hook in hooks:
        try:
            raw = await hook(ctx)
        except Exception:
            logger.exception("[hooks] %s hook %r raised; ignored", event, hook)
            continue
        if raw is None:
            continue
        if isinstance(raw, AdditionalContext):
            extra_msgs.append(_reminder_message(raw.text))
            continue
        # Command / Block：立即短路，把已收的 reminder 合并进去
        cmd = _to_command(raw, default_goto=default_goto)
        if cmd is None:
            continue
        update = dict(cmd.update or {})
        cmd_msgs = list(update.get("messages") or [])
        update["messages"] = [*extra_msgs, *cmd_msgs]
        return Command(goto=cmd.goto, update=update)

    if extra_msgs:
        return Command(goto=default_goto, update={"messages": extra_msgs})
    return None


@contextmanager
def replace_hooks(event: HookEvent, hooks: list[Hook]) -> Iterator[None]:
    """测试用：临时替换某事件下的 hooks，退出还原。

    避免测试用例直接 patch ``_HOOKS`` 全局字典，让 hook 注册的内部状态保持封装。

    Example:
        with replace_hooks("Stop", [my_test_hook]):
            cmd = await dispatch_hooks("Stop", ctx)
    """
    original = _HOOKS.get(event, []).copy()
    _HOOKS[event] = list(hooks)
    try:
        yield
    finally:
        _HOOKS[event] = original


def iter_hooks(event: HookEvent) -> list[Hook]:
    """只读暴露某事件下生效的 hook 列表（config + builtin，调用方修改不影响内部状态）。"""
    return _hooks_for(event)
