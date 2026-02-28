"""工具注册表 - 统一管理所有工具提供者"""

import asyncio
import inspect

from langchain_core.tools.structured import StructuredTool

from lumi.utils.logger import logger


class ToolRegistry:
    """极简工具注册表 - 自动收集工具"""

    _instance = None
    _providers = {}  # {"mcp": get_mcp_tools_func, "skill": skill_module, ...}

    @classmethod
    def instance(cls):
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def register(cls, name: str, provider):
        """
        注册工具提供者

        Args:
            name: 提供者名称 (如 "mcp", "skill", "task")
            provider: 可以是:
                - async函数: async def(filter_names=None) -> List[StructuredTool]
                - 模块对象: 自动收集模块中所有StructuredTool对象

        Example:
            # 注册函数
            ToolRegistry.register("mcp", get_mcp_tools)

            # 注册模块
            from lumi.agents.tools.providers import skill
            ToolRegistry.register("skill", skill)
        """
        cls._providers[name] = provider
        logger.debug(f"Registered tool provider: {name}")

    @staticmethod
    def _collect_tools_from_module(module) -> list[StructuredTool]:
        """从模块中自动收集所有StructuredTool对象"""
        tools = []
        for name in dir(module):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, StructuredTool):
                tools.append(obj)
        return tools

    async def get_tools(
        self,
        names: list[str] | None = None,
    ) -> list[StructuredTool]:
        """
        获取工具

        Args:
            names: 按工具名称过滤

        Returns:
            List[StructuredTool]: 去重后的工具列表

        Example:
            # 获取所有工具
            tools = await registry.get_tools()

            # 获取特定名称的工具
            tools = await registry.get_tools(names=["read", "bash"])
        """
        active = self._providers

        # 为每个提供者准备加载任务
        async def load_provider(provider):
            # 如果是async函数,直接调用
            if inspect.iscoroutinefunction(provider):
                return await provider(names)
            # 如果是模块,收集其中的工具
            elif inspect.ismodule(provider):
                tools = self._collect_tools_from_module(provider)
                # 如果指定了names,进行过滤
                if names:
                    tools = [t for t in tools if t.name in names]
                return tools
            else:
                raise TypeError(
                    f"Provider must be async function or module, got {type(provider)}"
                )

        # 并发调用所有提供者
        results = await asyncio.gather(
            *[load_provider(provider) for provider in active.values()],
            return_exceptions=True,
        )

        # 聚合结果,优雅处理错误
        all_tools = []
        for provider_name, result in zip(active.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Provider '{provider_name}' failed: {result}")
            else:
                all_tools.extend(result)

        # 按工具名称去重 (最后加载的优先)
        seen = set()
        unique = []
        for tool in all_tools:
            if tool.name not in seen:
                seen.add(tool.name)
                unique.append(tool)

        return unique
