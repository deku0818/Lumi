"""MCP工具提供者 - 从MCP服务器加载工具

支持两种会话模式：
- 无状态模式（默认）：每次工具调用创建新会话，适合无状态服务器
- 持久会话模式：通过 MCPSessionManager 维持长连接，适合 browsermcp 等有状态服务器
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import wraps
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession

from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from langgraph.prebuilt import ToolRuntime

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# ── 拦截器 ──


@dataclass
class ToolArgsInterceptor:
    """MCP 工具参数注入拦截器

    从 runtime.config["configurable"]["tool_args"] 读取参数，
    根据 config.yaml 中的 tool_args 映射关系注入到对应工具。
    """

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        runtime = request.runtime

        if runtime is None or not isinstance(runtime, ToolRuntime):
            return await handler(request)

        api_tool_args = runtime.config.get("configurable", {}).get("tool_args", {})
        if not api_tool_args:
            return await handler(request)

        param_mappings = get_config().config.tool_args.get_all_param_mappings()
        if not param_mappings:
            return await handler(request)

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


# ── 常量 ──

# 需要持久会话的传输类型（stdio 子进程启停开销大，必须保持连接）
_PERSISTENT_TRANSPORTS: frozenset[str] = frozenset({"stdio"})

# stdio 子进程 stderr 默认输出到 sys.stderr，会污染 TUI 界面。
# 用 devnull 替代，将 MCP 子进程的 stderr 静默丢弃。
_DEVNULL = open(os.devnull, "w")  # noqa: SIM115  # 模块级单例，避免每次调用泄漏 fd

# ── 配置加载 ──


def _get_mcp_config_path() -> str:
    """获取MCP配置文件路径"""
    return str(get_config().mcp_config_path)


def _load_base_mcp_config() -> dict[str, Any]:
    """加载基础MCP配置。返回空 dict 表示无可用配置。"""
    config_path = _get_mcp_config_path()

    if not os.path.exists(config_path):
        logger.info("MCP配置文件不存在，跳过MCP工具加载")
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            mcp_config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"MCP配置文件加载失败。文件路径: {config_path}, 错误: {e}")
        return {}

    if not isinstance(mcp_config, dict) or not mcp_config:
        logger.info("MCP配置为空，跳过MCP工具加载")
        return {}

    return mcp_config


# ── URL 工具 ──


def _apply_url_params(
    connection: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """为 streamable_http 连接动态添加 URL 查询参数。

    非 streamable_http 或缺少 url/params 时原样返回。
    """
    if (
        connection.get("transport") != "streamable_http"
        or not params
        or not connection.get("url")
    ):
        return connection

    url_parts = urlparse(connection["url"])
    merged = dict(parse_qsl(url_parts.query))
    merged.update(params)
    new_url = urlunparse(url_parts._replace(query=urlencode(merged)))

    updated = connection.copy()
    updated["url"] = new_url
    logger.debug(f"[MCP] 动态URL: {new_url}")
    return updated


def _make_quiet_stdio_client(original_stdio_client: Any) -> Any:
    """包装 stdio_client，将 errlog 重定向到 devnull 以避免污染 TUI"""

    @wraps(original_stdio_client)
    def wrapper(server: Any, errlog: Any = None) -> Any:
        return original_stdio_client(server, errlog=_DEVNULL)

    return wrapper


def _filter_tools(
    tools: list[StructuredTool], filter_names: list[str] | None
) -> list[StructuredTool]:
    """按名称过滤工具列表。filter_names 为空时返回全部。"""
    if not filter_names:
        return tools
    return [t for t in tools if t.name in filter_names]


def _needs_persistent_session(server_config: dict[str, Any]) -> bool:
    """判断服务器是否需要持久会话"""
    return server_config.get("transport", "") in _PERSISTENT_TRANSPORTS


def _format_exception_details(e: Exception) -> str:
    """格式化异常详情，特别处理 ExceptionGroup 以提取子异常信息"""
    if isinstance(e, ExceptionGroup):
        sub_errors = "; ".join(f"{type(sub).__name__}: {sub}" for sub in e.exceptions)
        return f"{type(e).__name__}: {e}. 子异常详情: [{sub_errors}]"
    return f"{type(e).__name__}: {e}"


# ── 子进程管理 ──


def _collect_descendant_pids(parent_pid: int) -> list[int]:
    """递归收集指定进程的所有后代 PID（跨平台）。"""
    try:
        if sys.platform == "win32":
            return _collect_descendant_pids_windows(parent_pid)
        return _collect_descendant_pids_unix(parent_pid)
    except (OSError, subprocess.SubprocessError):
        return []


def _collect_descendant_pids_unix(parent_pid: int) -> list[int]:
    """Unix: 通过 pgrep 递归收集后代 PID"""
    result = subprocess.run(
        ["pgrep", "-P", str(parent_pid)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    pids: list[int] = []
    for line in result.stdout.strip().split("\n"):
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        pids.append(pid)
        pids.extend(_collect_descendant_pids(pid))
    return pids


def _collect_descendant_pids_windows(parent_pid: int) -> list[int]:
    """Windows: 通过 wmic 递归收集后代 PID"""
    result = subprocess.run(
        [
            "wmic",
            "process",
            "where",
            f"ParentProcessId={parent_pid}",
            "get",
            "ProcessId",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    pids: list[int] = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or line == "ProcessId":
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        pids.append(pid)
        pids.extend(_collect_descendant_pids(pid))
    return pids


def _kill_child_processes() -> None:
    """强制终止当前进程的所有子进程。

    Unix: SIGTERM → 短暂等待 → SIGKILL 兜底。
    Windows: taskkill /F /PID。
    """
    child_pids = _collect_descendant_pids(os.getpid())
    if not child_pids:
        return

    if sys.platform == "win32":
        for pid in child_pids:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        return

    # Unix: 先 SIGTERM 再 SIGKILL
    for pid in child_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    time.sleep(0.3)

    for pid in child_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@dataclass(frozen=True)
class MCPToolInfo:
    """MCP 工具信息"""

    name: str
    description: str = ""


@dataclass(frozen=True)
class MCPServerInfo:
    """MCP 服务器状态信息"""

    name: str
    status: str
    command: str
    args: list[str] = field(default_factory=list)
    transport: str = ""
    tools: list[MCPToolInfo] = field(default_factory=list)
    config_path: str = ""


# ── 会话管理 ──


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
        """启动持久会话并加载工具。

        对于 stdio 类型服务器，创建持久会话保持子进程存活；
        对于其他类型服务器，使用默认的无状态模式加载工具。
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

        interceptors = [ToolArgsInterceptor()] if use_interceptors else None

        # 按传输类型分组
        persistent: dict[str, Any] = {}
        stateless: dict[str, Any] = {}
        for name, config in mcp_config.items():
            target = persistent if _needs_persistent_session(config) else stateless
            target[name] = config

        # Patch stdio_client 以抑制 MCP 子进程 stderr 输出（避免污染 TUI）
        all_tools = await self._load_tools_with_quiet_stdio(
            persistent, stateless, interceptors
        )

        self._tools = all_tools
        self._started = True
        return all_tools

    async def _load_tools_with_quiet_stdio(
        self,
        persistent: dict[str, Any],
        stateless: dict[str, Any],
        interceptors: list[ToolArgsInterceptor] | None,
    ) -> list[StructuredTool]:
        """在 stdio stderr 被静默的上下文中加载所有服务器工具。"""
        import langchain_mcp_adapters.sessions as _mcp_sessions

        original = _mcp_sessions.stdio_client
        _mcp_sessions.stdio_client = _make_quiet_stdio_client(original)
        try:
            tools: list[StructuredTool] = []
            await self._start_persistent_servers(persistent, interceptors, tools)
            await self._start_stateless_servers(stateless, interceptors, tools)
            return tools
        finally:
            _mcp_sessions.stdio_client = original

    async def _start_persistent_servers(
        self,
        servers: dict[str, Any],
        interceptors: list[ToolArgsInterceptor] | None,
        out_tools: list[StructuredTool],
    ) -> None:
        """为需要持久会话的服务器创建长连接并加载工具。"""
        for server_name, server_config in servers.items():
            try:
                client = MultiServerMCPClient(
                    {server_name: server_config},
                    tool_interceptors=interceptors,
                )
                session = await self._exit_stack.enter_async_context(
                    client.session(server_name)
                )
                self._sessions[server_name] = session
                tools = await load_mcp_tools(
                    session,
                    server_name=server_name,
                    tool_interceptors=interceptors,
                )
                self._register_tools(server_name, tools, out_tools)
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

    async def _start_stateless_servers(
        self,
        servers: dict[str, Any],
        interceptors: list[ToolArgsInterceptor] | None,
        out_tools: list[StructuredTool],
    ) -> None:
        """逐个加载无状态服务器工具。"""
        for server_name, server_config in servers.items():
            try:
                client = MultiServerMCPClient(
                    {server_name: server_config},
                    tool_interceptors=interceptors,
                )
                tools = await client.get_tools()
                self._register_tools(server_name, tools, out_tools)
                logger.info(
                    f"[MCP] 无状态服务器 {server_name} 加载了 {len(tools)} 个工具"
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(
                    f"[MCP] 无状态服务器 {server_name} 工具加载失败: "
                    f"{_format_exception_details(e)}"
                )

    def _register_tools(
        self,
        server_name: str,
        tools: list[StructuredTool],
        out_tools: list[StructuredTool],
    ) -> None:
        """将工具注册到 server_name 映射并追加到输出列表。"""
        for t in tools:
            self._tool_server_map[t.name] = server_name
        out_tools.extend(tools)

    def get_tools(self) -> list[StructuredTool]:
        """获取已加载的工具列表"""
        return self._tools

    async def close(self) -> None:
        """关闭所有持久会话，释放资源。

        先强制终止所有 stdio 子进程（SIGKILL），避免有 bug 的 MCP 服务器
        在优雅关闭时输出错误信息到终端。然后再清理 exit_stack。
        """
        if self._exit_stack is None:
            return

        _kill_child_processes()

        try:
            await asyncio.wait_for(self._exit_stack.aclose(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("[MCP] 关闭持久会话超时（3s），强制跳过")
        except RuntimeError:
            # anyio cancel scope 不允许跨 task 退出，
            # 子进程已由 _kill_child_processes() 终止，忽略即可
            pass
        except Exception as e:
            logger.error(f"[MCP] 关闭持久会话时出错: {_format_exception_details(e)}")
        finally:
            self._exit_stack = None
            self._sessions.clear()
            self._tools.clear()
            self._tool_server_map.clear()
            self._started = False
            logger.info("[MCP] 所有持久会话已关闭")

    def get_server_info(self) -> list[MCPServerInfo]:
        """获取所有 MCP 服务器的状态信息"""
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

        return [
            MCPServerInfo(
                name=name,
                status=self._resolve_server_status(name, tools_by_server),
                command=config.get("command", ""),
                args=config.get("args", []),
                transport=config.get("transport", ""),
                tools=tools_by_server.get(name, []),
                config_path=config_path,
            )
            for name, config in mcp_config.items()
        ]

    def _resolve_server_status(
        self,
        server_name: str,
        tools_by_server: dict[str, list[MCPToolInfo]],
    ) -> str:
        """根据会话状态和已加载工具推断服务器连接状态。"""
        if not self._started:
            return "not_started"
        if server_name in self._sessions or server_name in tools_by_server:
            return "connected"
        return "failed"


# ── 模块级单例 ──

_session_manager: MCPSessionManager | None = None


def get_mcp_session_manager() -> MCPSessionManager:
    """获取全局 MCPSessionManager 单例"""
    global _session_manager
    if _session_manager is None:
        _session_manager = MCPSessionManager()
    return _session_manager


# ── 公共 API ──


async def get_mcp_tools(
    filter_names: list[str] | None = None,
    use_interceptors: bool = True,
) -> list[StructuredTool]:
    """获取MCP服务器提供的工具（静态配置）。

    自动为 stdio 类型服务器创建持久会话，其他类型使用无状态模式。
    """
    mcp_config = _load_base_mcp_config()
    if not mcp_config:
        return []

    manager = get_mcp_session_manager()

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

    return _filter_tools(manager.get_tools(), filter_names)


async def get_mcp_tools_by_server(
    filter_names: list[str] | None = None,
) -> dict[str, list[StructuredTool]]:
    """按服务器分组获取MCP工具"""
    mcp_config = _load_base_mcp_config()
    if not mcp_config:
        return {}

    tools_by_server: dict[str, list[StructuredTool]] = {}

    for server_name, server_config in mcp_config.items():
        try:
            client = MultiServerMCPClient({server_name: server_config})
            server_tools = await client.get_tools()
            filtered = _filter_tools(server_tools, filter_names)
            if filtered:
                tools_by_server[server_name] = filtered
                logger.debug(
                    f"[MCP] 从服务器 {server_name} 加载了 {len(filtered)} 个工具"
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(
                f"从服务器 {server_name} 加载工具失败: {_format_exception_details(e)}"
            )

    return tools_by_server


async def get_dynamic_mcp_tools(
    filter_names: list[str] | None = None,
    mcp_config: dict[str, dict[str, Any]] | None = None,
    use_interceptors: bool = True,
) -> list[StructuredTool]:
    """获取动态配置的MCP工具。

    支持在运行时通过 mcp_config 参数动态修改MCP服务器的URL参数，
    用于实现多租户场景下的参数隔离。
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
        interceptors = [ToolArgsInterceptor()] if use_interceptors else None
        client = MultiServerMCPClient(final_config, tool_interceptors=interceptors)
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
