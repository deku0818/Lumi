"""MCP工具提供者 - 从MCP服务器加载工具"""

import copy
import json
import os
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from lumi.agents.tools.interceptors import ToolArgsInterceptor
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


def _format_exception_details(e: Exception) -> str:
    """
    格式化异常详情，特别处理 ExceptionGroup 以提取子异常信息

    Args:
        e: 异常对象

    Returns:
        格式化后的异常详情字符串
    """
    if isinstance(e, ExceptionGroup):
        sub_errors = "; ".join(f"{type(sub).__name__}: {sub}" for sub in e.exceptions)
        return f"{type(e).__name__}: {e}. 子异常详情: [{sub_errors}]"
    return f"{type(e).__name__}: {e}"


def _get_mcp_config_path() -> str:
    """获取MCP配置文件路径"""
    return str(get_config().mcp_config_path)


def _load_base_mcp_config() -> dict[str, Any]:
    """加载基础MCP配置"""
    config_path = _get_mcp_config_path()

    if not os.path.exists(config_path):
        logger.info("MCP配置文件不存在，跳过MCP工具加载")
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            mcp_config = json.load(f)
    except Exception as e:
        logger.error(f"MCP配置文件加载失败。文件路径: {config_path}, 错误: {e}")
        return {}

    if not mcp_config or not isinstance(mcp_config, dict):
        logger.info("MCP配置为空，跳过MCP工具加载")
        return {}

    return mcp_config


def _filter_tools(
    tools: list[StructuredTool], filter_names: list[str] | None
) -> list[StructuredTool]:
    """按名称过滤工具列表"""
    if not filter_names:
        return tools
    return [t for t in tools if t.name in filter_names]


def _apply_url_params(
    connection: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """
    为 streamable_http 连接动态添加 URL 参数

    Args:
        connection: MCP服务器连接配置
        params: 要添加到URL的参数

    Returns:
        更新后的连接配置
    """
    # 合并条件判断，简化 early return
    if (
        connection.get("transport") != "streamable_http"
        or not params
        or not connection.get("url")
    ):
        return connection

    base_url = connection["url"]

    # 解析URL并合并参数
    url_parts = urlparse(base_url)
    existing_params = dict(parse_qsl(url_parts.query))
    existing_params.update(params)
    new_query = urlencode(existing_params)
    new_url = urlunparse(url_parts._replace(query=new_query))

    updated_connection = connection.copy()
    updated_connection["url"] = new_url
    logger.debug(f"[MCP] 动态URL: {new_url}")
    return updated_connection


async def get_mcp_tools(
    filter_names: list[str] | None = None,
    use_interceptors: bool = True,
) -> list[StructuredTool]:
    """
    获取MCP服务器提供的工具（静态配置）

    Args:
        filter_names: 可选的工具名称列表,用于过滤
        use_interceptors: 是否使用工具拦截器（默认启用）

    Returns:
        List[StructuredTool]: MCP工具列表，如果配置不存在或为空则返回空列表
    """
    mcp_config = _load_base_mcp_config()
    if not mcp_config:
        return []

    # 初始化MCP客户端并获取所有工具
    try:
        tool_interceptors = [ToolArgsInterceptor()] if use_interceptors else None
        client = MultiServerMCPClient(mcp_config, tool_interceptors=tool_interceptors)
        all_mcp_tools = await client.get_tools()
    except (KeyboardInterrupt, SystemExit):
        raise  # 永不吞掉系统异常
    except Exception as e:
        logger.error(
            f"加载MCP工具失败: {_format_exception_details(e)}. "
            f"配置的服务器: {list(mcp_config.keys())}"
        )
        return []

    # 按名称过滤
    return _filter_tools(all_mcp_tools, filter_names)


async def get_mcp_tools_by_server(
    filter_names: list[str] | None = None,
) -> dict[str, list[StructuredTool]]:
    """
    按服务器分组获取MCP工具

    Args:
        filter_names: 可选的工具名称列表,用于过滤

    Returns:
        Dict[str, List[StructuredTool]]: 服务器名称到工具列表的映射
    """
    mcp_config = _load_base_mcp_config()
    if not mcp_config:
        return {}

    tools_by_server: dict[str, list[StructuredTool]] = {}

    # 为每个服务器单独获取工具
    for server_name, server_config in mcp_config.items():
        try:
            single_server_config = {server_name: server_config}
            client = MultiServerMCPClient(single_server_config)
            server_tools = await client.get_tools()

            # 按名称过滤
            filtered_tools = _filter_tools(server_tools, filter_names)

            if filtered_tools:
                tools_by_server[server_name] = filtered_tools
                logger.debug(
                    f"[MCP] 从服务器 {server_name} 加载了 {len(filtered_tools)} 个工具"
                )

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(
                f"从服务器 {server_name} 加载工具失败: {_format_exception_details(e)}"
            )
            continue

    return tools_by_server


async def get_dynamic_mcp_tools(
    filter_names: list[str] | None = None,
    mcp_config: dict[str, dict[str, Any]] | None = None,
    use_interceptors: bool = True,
) -> list[StructuredTool]:
    """
    获取动态配置的MCP工具

    支持在运行时通过mcp_config参数动态修改MCP服务器的URL参数，
    用于实现多租户场景下的参数隔离。

    Args:
        filter_names: 可选的工具名称列表,用于过滤
        mcp_config: MCP服务器动态参数配置
        use_interceptors: 是否使用工具拦截器（默认启用）

    Returns:
        List[StructuredTool]: MCP工具列表
    """
    # 加载基础配置
    base_config = _load_base_mcp_config()
    if not base_config:
        return []

    # 深拷贝以避免修改原始配置
    final_config = copy.deepcopy(base_config)

    # 应用动态参数
    if mcp_config:
        for server_name, params in mcp_config.items():
            if server_name not in final_config:
                logger.warning(
                    f"[MCP] 动态配置引用了不存在的服务器 '{server_name}'。"
                    f"可用的服务器: {list(final_config.keys())}"
                )
                continue
            if params:
                final_config[server_name] = _apply_url_params(
                    final_config[server_name], params
                )
                logger.debug(f"[MCP] 为服务器 {server_name} 应用动态参数: {params}")

    # 初始化MCP客户端并获取工具
    try:
        tool_interceptors = [ToolArgsInterceptor()] if use_interceptors else None
        client = MultiServerMCPClient(final_config, tool_interceptors=tool_interceptors)
        all_tools = await client.get_tools()
    except (KeyboardInterrupt, SystemExit):
        raise  # 永不吞掉系统异常
    except Exception as e:
        logger.error(
            f"加载动态MCP工具失败: {_format_exception_details(e)}. "
            f"基础配置服务器: {list(base_config.keys())}, "
            f"动态参数: {mcp_config}"
        )
        return []

    # 按名称过滤
    return _filter_tools(all_tools, filter_names)
