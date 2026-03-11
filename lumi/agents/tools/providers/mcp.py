"""MCP工具提供者 - 从MCP服务器加载工具

支持两种会话模式：
- 无状态模式（默认）：每次工具调用创建新会话，适合无状态服务器
- 持久会话模式：通过 MCPSessionManager 维持长连接，适合 browsermcp 等有状态服务器
"""

import asyncio
import copy
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession

from lumi.agents.tools.interceptors import ToolArgsInterceptor
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# 需要持久会话的传输类型（stdio 子进程启停开销大，必须保持连接）
_PERSISTENT_TRANSPORTS: frozenset[str] = frozenset({"stdio"})


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
    if (
        connection.get("transport") != "streamable_http"
        or not params
        or not connection.get("url")
    ):
        return connection

    base_url = connection["url"]
    url_parts = urlparse(base_url)
    existing_params = dict(parse_qsl(url_parts.query))
    existing_params.update(params)
    new_query = urlencode(existing_params)
    new_url = urlunparse(url_parts._replace(query=new_query))

    updated_connection = connection.copy()
    updated_connection["url"] = new_url
    logger.debug(f"[MCP] 动态URL: {new_url}")
    return updated_connection


def _needs_persistent_session(server_config: dict[str, Any]) -> bool:
    """判断服务器是否需要持久会话

    Args:
        server_config: 服务器连接配置

    Returns:
        True 表示需要持久会话
    """
    return server_config.get("transport", "") in _PERSISTENT_TRANSPORTS


@dataclass(frozen=True)
class MCPToolInfo:
    """MCP 工具信息

    Attributes:
        name: 工具名称
        description: 工具描述
    """

    name: str
    description: str = ""


@dataclass(frozen=True)
class MCPServerInfo:
    """MCP 服务器状态信息

    Attributes:
        name: 服务器名称
        status: 连接状态（connected / failed / not_started）
        command: 启动命令
        args: 命令参数
        transport: 传输类型
        tools: 该服务器下的工具信息列表
        config_path: 配置文件路径
    """

    name: str
    status: str
    command: str
    args: list[str] = field(default_factory=list)
    transport: str = ""
    tools: list[MCPToolInfo] = field(default_factory=list)
    config_path: str = ""


class MCPSessionManager:
    """MCP 持久会话管理器

    为 stdio 等需要保持子进程存活的传输类型维护长连接会话。
    使用 AsyncExitStack 管理多个 async context manager 的生命周期。

    用法：
        manager = MCPSessionManager()
        await manager.start(mcp_config)
        tools = manager.get_tools()
        ...
        await manager.close()
    """

    def __init__(self) -> None:
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[StructuredTool] = []
        self._tool_server_map: dict[str, str] = {}  # tool_name -> server_name
        self._started: bool = False

    @property
    def is_started(self) -> bool:
        """会话管理器是否已启动"""
        return self._started

    async def start(
        self,
        mcp_config: dict[str, Any] | None = None,
        use_interceptors: bool = True,
    ) -> list[StructuredTool]:
        """启动持久会话并加载工具

        对于 stdio 类型的服务器，创建持久会话保持子进程存活；
        对于其他类型的服务器，使用默认的无状态模式加载工具。

        Args:
            mcp_config: MCP配置，为 None 时从配置文件加载
            use_interceptors: 是否使用工具拦截器

        Returns:
            加载到的所有工具列表
        """
        if self._started:
            logger.warning("[MCP] SessionManager 已启动，请先 close 再重新 start")
            return self._tools

        if mcp_config is None:
            mcp_config = _load_base_mcp_config()
        if not mcp_config:
            return []

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        tool_interceptors = [ToolArgsInterceptor()] if use_interceptors else None
        all_tools: list[StructuredTool] = []

        # 按传输类型分组
        persistent_servers: dict[str, Any] = {}
        stateless_servers: dict[str, Any] = {}
        for name, config in mcp_config.items():
            if _needs_persistent_session(config):
                persistent_servers[name] = config
            else:
                stateless_servers[name] = config

        # 为需要持久会话的服务器创建长连接
        for server_name, server_config in persistent_servers.items():
            try:
                client = MultiServerMCPClient(
                    {server_name: server_config},
                    tool_interceptors=tool_interceptors,
                )
                session = await self._exit_stack.enter_async_context(
                    client.session(server_name)
                )
                self._sessions[server_name] = session
                # 传入 session 使工具调用复用同一会话
                tools = await load_mcp_tools(
                    session,
                    server_name=server_name,
                    tool_interceptors=tool_interceptors,
                )
                for t in tools:
                    self._tool_server_map[t.name] = server_name
                all_tools.extend(tools)
                logger.info(
                    f"[MCP] 服务器 {server_name} 持久会话已建立，"
                    f"加载了 {len(tools)} 个工具"
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(
                    f"[MCP] 服务器 {server_name} 持久会话创建失败: "
                    f"{_format_exception_details(e)}"
                )

        # 无状态服务器逐个加载以记录工具归属
        for server_name, server_config in stateless_servers.items():
            try:
                client = MultiServerMCPClient(
                    {server_name: server_config},
                    tool_interceptors=tool_interceptors,
                )
                stateless_tools = await client.get_tools()
                for t in stateless_tools:
                    self._tool_server_map[t.name] = server_name
                all_tools.extend(stateless_tools)
                logger.info(
                    f"[MCP] 无状态服务器 {server_name} 加载了 "
                    f"{len(stateless_tools)} 个工具"
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(
                    f"[MCP] 无状态服务器 {server_name} 工具加载失败: "
                    f"{_format_exception_details(e)}"
                )

        self._tools = all_tools
        self._started = True
        return all_tools

    def get_tools(self) -> list[StructuredTool]:
        """获取已加载的工具列表"""
        return self._tools

    async def close(self) -> None:
        """关闭所有持久会话，释放资源

        先强制终止所有 stdio 子进程（SIGKILL），避免有 bug 的 MCP 服务器
        在优雅关闭时输出错误信息到终端。然后再清理 exit_stack。
        """
        if self._exit_stack is not None:
            # 先强制杀掉所有 stdio 子进程，避免它们在关闭时输出错误
            self._kill_subprocesses()

            try:
                await asyncio.wait_for(self._exit_stack.aclose(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("[MCP] 关闭持久会话超时（3s），强制跳过")
            except Exception as e:
                logger.error(
                    f"[MCP] 关闭持久会话时出错: {_format_exception_details(e)}"
                )
            finally:
                self._exit_stack = None
                self._sessions.clear()
                self._tools.clear()
                self._tool_server_map.clear()
                self._started = False
                logger.info("[MCP] 所有持久会话已关闭")

    @staticmethod
    def _kill_subprocesses() -> None:
        """强制终止当前进程的所有子进程。

        先发 SIGTERM 给子进程一个优雅关闭的机会，
        短暂等待后再 SIGKILL 确保彻底清理。
        """
        import signal
        import time

        try:
            child_pids = _get_child_pids(os.getpid())
            if not child_pids:
                return

            # 先 SIGTERM
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

            # 短暂等待让子进程自行退出
            time.sleep(0.3)

            # 再 SIGKILL 兜底
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass

    def get_server_info(self) -> list[MCPServerInfo]:
        """获取所有 MCP 服务器的状态信息

        Returns:
            服务器信息列表
        """
        mcp_config = _load_base_mcp_config()
        config_path = _get_mcp_config_path()

        if not mcp_config:
            return []

        # 按服务器名分组已加载的工具
        tools_by_server: dict[str, list[MCPToolInfo]] = {}
        for tool in self._tools:
            server = self._tool_server_map.get(tool.name, "")
            info = MCPToolInfo(name=tool.name, description=tool.description or "")
            tools_by_server.setdefault(server, []).append(info)

        result: list[MCPServerInfo] = []
        for name, config in mcp_config.items():
            if not self._started:
                status = "not_started"
            elif name in self._sessions:
                status = "connected"
            else:
                if name in tools_by_server:
                    status = "connected"
                else:
                    status = "failed"

            result.append(
                MCPServerInfo(
                    name=name,
                    status=status,
                    command=config.get("command", ""),
                    args=config.get("args", []),
                    transport=config.get("transport", ""),
                    tools=tools_by_server.get(name, []),
                    config_path=config_path,
                )
            )
        return result


# ── 模块级单例 ──


def _get_child_pids(parent_pid: int) -> list[int]:
    """获取指定进程的所有子进程 PID（macOS/Linux）。

    Args:
        parent_pid: 父进程 PID

    Returns:
        子进程 PID 列表
    """
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = []
            for line in result.stdout.strip().split("\n"):
                try:
                    pid = int(line.strip())
                    # 递归获取子进程的子进程
                    pids.append(pid)
                    pids.extend(_get_child_pids(pid))
                except ValueError:
                    continue
            return pids
    except Exception:
        pass
    return []


_session_manager: MCPSessionManager | None = None


def get_mcp_session_manager() -> MCPSessionManager:
    """获取全局 MCPSessionManager 单例

    Returns:
        MCPSessionManager 实例
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = MCPSessionManager()
    return _session_manager


async def get_mcp_tools(
    filter_names: list[str] | None = None,
    use_interceptors: bool = True,
) -> list[StructuredTool]:
    """
    获取MCP服务器提供的工具（静态配置）

    自动为 stdio 类型服务器创建持久会话，其他类型使用无状态模式。

    Args:
        filter_names: 可选的工具名称列表,用于过滤
        use_interceptors: 是否使用工具拦截器（默认启用）

    Returns:
        List[StructuredTool]: MCP工具列表，如果配置不存在或为空则返回空列表
    """
    mcp_config = _load_base_mcp_config()
    if not mcp_config:
        return []

    manager = get_mcp_session_manager()

    # 如果管理器尚未启动，启动它
    if not manager.is_started:
        try:
            await manager.start(mcp_config, use_interceptors=use_interceptors)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(
                f"加载MCP工具失败: {_format_exception_details(e)}. "
                f"配置的服务器: {list(mcp_config.keys())}"
            )
            return []

    all_tools = manager.get_tools()
    return _filter_tools(all_tools, filter_names)


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

    for server_name, server_config in mcp_config.items():
        try:
            single_server_config = {server_name: server_config}
            client = MultiServerMCPClient(single_server_config)
            server_tools = await client.get_tools()

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
    base_config = _load_base_mcp_config()
    if not base_config:
        return []

    final_config = copy.deepcopy(base_config)

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

    try:
        tool_interceptors = [ToolArgsInterceptor()] if use_interceptors else None
        client = MultiServerMCPClient(final_config, tool_interceptors=tool_interceptors)
        all_tools = await client.get_tools()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(
            f"加载动态MCP工具失败: {_format_exception_details(e)}. "
            f"基础配置服务器: {list(base_config.keys())}, "
            f"动态参数: {mcp_config}"
        )
        return []

    return _filter_tools(all_tools, filter_names)
