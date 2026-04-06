"""工具注册表 — 统一管理所有工具提供者。

支持两种 provider 形式:
- 异步函数: ``async (names) -> list[StructuredTool]``
- Python 模块: 自动收集模块级 ``StructuredTool`` 实例
"""

from __future__ import annotations

import asyncio
import inspect
import types
from collections.abc import Callable, Coroutine
from typing import Any

from langchain_core.tools.structured import StructuredTool

from lumi.utils.logger import logger

# provider: 异步加载函数 或 包含 StructuredTool 的模块
type ToolProvider = (
    Callable[[list[str] | None], Coroutine[Any, Any, list[StructuredTool]]]
    | types.ModuleType
)


class ToolRegistry:
    """极简工具注册表 — 单例模式，并发加载所有 provider。"""

    _instance: ToolRegistry | None = None

    def __init__(self) -> None:
        self._providers: dict[str, ToolProvider] = {}

    # ------------------------------------------------------------------
    # 单例 & 注册
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> ToolRegistry:
        """获取全局单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def register(cls, name: str, provider: ToolProvider) -> None:
        """注册工具提供者。

        Args:
            name: 提供者名称（如 ``"mcp"``, ``"bash"``）。
            provider: 异步加载函数或包含 ``StructuredTool`` 的模块。
        """
        cls.instance()._providers[name] = provider
        logger.debug(f"Registered tool provider: {name}")

    # ------------------------------------------------------------------
    # 工具获取
    # ------------------------------------------------------------------

    async def get_tools(
        self,
        names: list[str] | None = None,
    ) -> list[StructuredTool]:
        """并发加载所有 provider 并返回去重后的工具列表。

        Args:
            names: 只返回名称在此列表中的工具，``None`` 表示全部。
        """
        results = await asyncio.gather(
            *(
                self._load_provider(provider, names)
                for provider in self._providers.values()
            ),
            return_exceptions=True,
        )

        all_tools: list[StructuredTool] = []
        for provider_name, result in zip(self._providers, results):
            if isinstance(result, BaseException):
                logger.error(f"Provider '{provider_name}' failed: {result}")
            else:
                all_tools.extend(result)

        return _deduplicate(all_tools)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _load_provider(
        self,
        provider: ToolProvider,
        names: list[str] | None,
    ) -> list[StructuredTool]:
        """加载单个 provider 的工具。"""
        if inspect.iscoroutinefunction(provider):
            return await provider(names)

        if inspect.ismodule(provider):
            tools = _collect_tools_from_module(provider)
            if names:
                allowed = set(names)
                tools = [t for t in tools if t.name in allowed]
            return tools

        raise TypeError(
            f"Provider must be async function or module, got {type(provider)}"
        )


def _collect_tools_from_module(module: types.ModuleType) -> list[StructuredTool]:
    """从模块中收集所有公开的 ``StructuredTool`` 实例。"""
    tools: list[StructuredTool] = []
    for name in dir(module):
        if not name.startswith("_"):
            obj = getattr(module, name)
            if isinstance(obj, StructuredTool):
                tools.append(obj)
    return tools


def _deduplicate(tools: list[StructuredTool]) -> list[StructuredTool]:
    """按工具名称去重，先出现的优先保留。"""
    seen: set[str] = set()
    unique: list[StructuredTool] = []
    for tool in tools:
        if tool.name not in seen:
            seen.add(tool.name)
            unique.append(tool)
    return unique
