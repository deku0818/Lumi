"""MCP 工具拦截器模块

提供工具执行拦截器，用于在工具执行前后进行参数注入、日志记录等操作。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from langgraph.prebuilt import ToolRuntime

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


@dataclass
class ToolArgsInterceptor:
    """MCP 工具参数注入拦截器

    从 runtime.config["configurable"]["tool_args"] 读取参数，
    根据 config.yaml 中的 tool_args 映射关系注入到对应工具。

    配置示例 (config.yaml):
        tool_args:
          extra_match:
            - "knowledge_retrieval"
            - "qs_retrieval"
          search_depth:
            - "jina_search"

    API 调用示例:
        config = RunnableConfig(
            configurable={
                "tool_args": {
                    "extra_match": ["x", "y"],
                    "search_depth": 5
                }
            }
        )
    """

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        """拦截工具调用并注入参数

        Args:
            request: MCP 工具调用请求
            handler: 工具调用处理器

        Returns:
            工具调用结果
        """
        runtime = request.runtime

        # 无 runtime 时直接透传
        if runtime is None or not isinstance(runtime, ToolRuntime):
            return await handler(request)

        # 获取 API 传入的参数
        api_tool_args = runtime.config.get("configurable", {}).get("tool_args", {})
        if not api_tool_args:
            return await handler(request)

        # 获取配置文件中的参数映射
        param_mappings = get_config().config.tool_args.get_all_param_mappings()
        if not param_mappings:
            return await handler(request)

        # 匹配并注入参数
        tool_name = request.name
        params_to_inject: dict[str, Any] = {}

        for param_name, allowed_tools in param_mappings.items():
            if tool_name in allowed_tools and param_name in api_tool_args:
                params_to_inject[param_name] = api_tool_args[param_name]

        if params_to_inject:
            new_args = {**request.args, **params_to_inject}
            request = request.override(args=new_args)
            logger.info(
                f"[ToolArgsInterceptor] 为工具 {tool_name} 注入参数: {params_to_inject}"
            )

        return await handler(request)
