"""MCP工具提供者 - 从MCP服务器加载工具

支持两种会话模式：
- 无状态模式（默认）：每次工具调用创建新会话，适合无状态服务器
- 持久会话模式：通过 MCPSessionManager 维持长连接，适合 browsermcp 等有状态服务器
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from langchain_core.tools.structured import StructuredTool
from langchain_mcp_adapters import sessions
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import ToolRuntime
from mcp import ClientSession

from lumi.utils.hashing import short_hash
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# ── 拦截器 ──


@dataclass
class ToolArgsInterceptor:
    """MCP 工具参数注入拦截器

    从 runtime.config["configurable"]["tool_args"] 读取参数，
    根据 config.json 中的 tool_args 映射关系注入到对应工具。
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

# 单个服务器连接+加载工具的超时上限。必须有界：端口被其它程序占用（TCP 可连但
# 永不响应）或服务假死时，连接会无限挂起。池加载已后台化（不阻塞会话就绪），
# 超时对齐 Claude Code 的默认 30s，npx 冷启动拉包也从容。
_SERVER_START_TIMEOUT = 30.0

# stdio 子进程 stderr 默认输出到 sys.stderr，会污染 TUI 界面。
# 用 devnull 替代，将 MCP 子进程的 stderr 静默丢弃。
_DEVNULL = open(os.devnull, "w")  # noqa: SIM115  # 模块级单例，避免每次调用泄漏 fd

# ── 配置加载 ──

# 当前会话项目根：get_tools(project_dir=...) 进入时 set，get_mcp_tools 未显式传参时读它。
_current_project_dir: ContextVar[Path | None] = ContextVar(
    "lumi_mcp_project_dir", default=None
)


def _global_mcp_config_path() -> Path:
    """全局层配置路径 = 该机器固定位置。

    显式覆盖优先：``--config-dir``（get_config().discovery.cli_config_dir）> ``LUMI_CONFIG_DIR``；
    都没有则恒为 ``~/.lumi/mcp_server.json``。**刻意跳过 cwd/.lumi 发现**——两层模型下
    「cwd 到底算全局还是某个项目」有歧义，全局层必须是稳定的每机器位置。
    """
    override = get_config().discovery.cli_config_dir or os.getenv("LUMI_CONFIG_DIR")
    base = Path(override).expanduser().resolve() if override else Path.home() / ".lumi"
    return base / "mcp_server.json"


def _read_json_dict(path: Path) -> dict[str, Any]:
    """读取单个 mcp_server.json；不存在/损坏/非 dict 一律返回空 dict。"""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"MCP配置文件加载失败。文件路径: {path}, 错误: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_server_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """单个 server 配置归一化：剥离 Lumi 元字段 ``disabled``、补推缺省 ``transport``。

    ``disabled`` 绝不能下传给 langchain adapter（它 ``**params`` 全透传，混入未知键会
    TypeError）；``transport`` 缺省按有无 url 推断（Claude Desktop 风格配置不写该键，
    而 adapter 的 create_session 强制要求）。会话池与连接测试共用，两路行为恒一致。
    """
    out = {k: v for k, v in cfg.items() if k != "disabled"}
    if "transport" not in out:
        out["transport"] = "streamable_http" if out.get("url") else "stdio"
    return out


def _strip_disabled(config: dict[str, Any]) -> dict[str, Any]:
    """丢弃被禁用的 server，其余逐个归一化（见 :func:`_normalize_server_config`）。"""
    return {
        name: _normalize_server_config(cfg)
        for name, cfg in config.items()
        if isinstance(cfg, dict) and cfg.get("disabled") is not True
    }


# merged 配置按两文件 mtime 缓存（同 PermissionEngine 热重载思路）：每次建 agent
# 都要读（wait_ready 探空配置 + get_mcp_tools 各一次），不缓存则每个子代理/cron/
# workflow 构建都重复读盘解析。key=(全局路径, 项目路径)。
_merged_cache: dict[tuple[str, str], tuple[int, int, dict[str, Any]]] = {}


def _mtime_ns(path: Path | None) -> int:
    if path is None:
        return -1
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _load_merged_mcp_config(project_dir: Path | None) -> dict[str, Any]:
    """分层合并的 MCP 配置（全局 ∪ 项目，项目同名覆盖），并剥离 ``disabled``。

    全局层由 :func:`_global_mcp_config_path` 决定；项目层为
    ``<project_dir>/.lumi/mcp_server.json``（仅当其路径 ≠ 全局层时叠加）。
    返回可直接下传 adapter 的配置。
    """
    global_path = _global_mcp_config_path()
    project_path = (
        project_dir / ".lumi" / "mcp_server.json" if project_dir is not None else None
    )
    if project_path == global_path:
        project_path = None
    key = (str(global_path), str(project_path))
    mtimes = (_mtime_ns(global_path), _mtime_ns(project_path))
    cached = _merged_cache.get(key)
    if cached is not None and (cached[0], cached[1]) == mtimes:
        return cached[2]
    merged = dict(_read_json_dict(global_path))
    if project_path is not None:
        merged.update(_read_json_dict(project_path))
    result = _strip_disabled(merged)
    _merged_cache[key] = (mtimes[0], mtimes[1], result)
    return result


def _config_hash(config: dict[str, Any]) -> str:
    """merged 配置的稳定 hash（key 排序 → {a,b} 与 {b,a} 同 hash）。用于判断池是否真变。"""
    return short_hash(json.dumps(config, sort_keys=True, ensure_ascii=False), 16)


def _make_quiet_stdio_client(original_stdio_client: Any) -> Any:
    """包装 stdio_client，将 errlog 重定向到 devnull 以避免污染 TUI"""

    @wraps(original_stdio_client)
    def wrapper(server: Any, errlog: Any = None) -> Any:
        return original_stdio_client(server, errlog=_DEVNULL)

    return wrapper


# import 时一次性包装 adapter 的 stdio_client：所有调用方（会话池 / 连接测试）都要静默，
# 且临时 patch/restore 在并发下会互相恢复错原值，永久包装最简单也无竞态。
sessions.stdio_client = _make_quiet_stdio_client(sessions.stdio_client)


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


def _kill_pids(pids: set[int]) -> None:
    """强制终止指定 PID 集合（仅这些，不波及其它）。

    Unix: SIGTERM → 短暂等待 → SIGKILL 兜底。
    Windows: taskkill /F /PID。
    """
    targets = [p for p in pids if p]
    if not targets:
        return

    if sys.platform == "win32":
        for pid in targets:
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
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    time.sleep(0.3)

    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _kill_child_processes() -> None:
    """SIGKILL 兜底：终止当前进程的**全部**后代。仅进程退出路径可用。"""
    _kill_pids(set(_collect_descendant_pids(os.getpid())))


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
        # 本 manager start 期间新出现的子进程 PID（精确 teardown 用，绝不碰其它池的）
        self._child_pids: set[int] = set()
        # 构建本池所用配置的稳定 hash（供 invalidate 精准判断「是否真变了」）
        self._config_hash: str = ""
        # 各 server 最近一次加载结果：name → {ok, tools?|error?}（mcp.status 广播 / 面板徽标）
        self.server_status: dict[str, dict] = {}

    @property
    def is_started(self) -> bool:
        """会话管理器是否已启动"""
        return self._started

    async def start(self, mcp_config: dict[str, Any]) -> list[StructuredTool]:
        """启动持久会话并加载工具（mcp_config 由调用方分层合并后传入）。

        对于 stdio 类型服务器，创建持久会话保持子进程存活；
        对于其他类型服务器，使用默认的无状态模式加载工具。
        子进程 PID 在 _start_servers 内逐 server 记录——任意时刻被取消
        （配置作废）都不漏杀已 spawn 的子进程。
        """
        if self._started:
            logger.warning("[MCP] SessionManager 已启动，请先 close 再重新 start")
            return self._tools

        if not mcp_config:
            return []

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        interceptors = [ToolArgsInterceptor()]

        # 按传输类型分组
        persistent: dict[str, Any] = {}
        stateless: dict[str, Any] = {}
        for name, config in mcp_config.items():
            target = persistent if _needs_persistent_session(config) else stateless
            target[name] = config

        all_tools: list[StructuredTool] = []
        await self._start_servers(persistent, interceptors, all_tools, persistent=True)
        await self._start_servers(stateless, interceptors, all_tools, persistent=False)

        self._config_hash = _config_hash(mcp_config)
        self._tools = all_tools
        self._started = True
        return all_tools

    async def _start_servers(
        self,
        servers: dict[str, Any],
        interceptors: list[ToolArgsInterceptor],
        out_tools: list[StructuredTool],
        persistent: bool,
    ) -> None:
        """逐个连接 server 并加载工具（persistent=True 建持久会话，否则无状态）。

        超时/异常/状态记录脚手架两类共用——server_status 的形状与文案只此一份。
        """
        kind = "服务器" if persistent else "无状态服务器"
        # 逐 server 记录新 spawn 的子进程 PID（仅 persistent=stdio 会留驻子进程）：
        # 快照 diff 放进 finally——连接中途被取消（配置作废）也已入账，close 的
        # SIGKILL 兜底不漏杀。精确归属依赖 _start_lock 串行化 spawn，故上一轮的
        # after 可直接复用为下一轮的 before，全程 S+1 次进程树遍历而非 2S 次；
        # 每次遍历对每个存活后代同步 spawn 一个 pgrep，须下放线程避免阻塞事件循环
        # （本函数跑在持 _start_lock 的后台加载里，阻塞会拖停所有会话的流式输出）。
        before = (
            set(await asyncio.to_thread(_collect_descendant_pids, os.getpid()))
            if persistent
            else set()
        )
        for server_name, server_config in servers.items():
            try:
                client = MultiServerMCPClient(
                    {server_name: server_config},
                    tool_interceptors=interceptors,
                )
                async with asyncio.timeout(_SERVER_START_TIMEOUT):
                    if persistent:
                        session = await self._exit_stack.enter_async_context(
                            client.session(server_name)
                        )
                        self._sessions[server_name] = session
                        tools = await load_mcp_tools(
                            session,
                            server_name=server_name,
                            tool_interceptors=interceptors,
                        )
                    else:
                        tools = await client.get_tools()
                self._register_tools(server_name, tools, out_tools)
                self.server_status[server_name] = {"ok": True, "tools": len(tools)}
                logger.info(
                    f"[MCP] {kind} {server_name} 已连接，加载了 {len(tools)} 个工具"
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except TimeoutError:
                self.server_status[server_name] = {
                    "ok": False,
                    "error": f"连接超时（{_SERVER_START_TIMEOUT:.0f}s）",
                }
                logger.error(
                    f"[MCP] {kind} {server_name} 连接超时（{_SERVER_START_TIMEOUT}s），"
                    "已跳过：端口被其它程序占用/服务无响应时连接会无限挂起"
                )
            except Exception as e:
                self.server_status[server_name] = {
                    "ok": False,
                    "error": _format_exception_details(e)[:200],
                }
                logger.error(
                    f"[MCP] {kind} {server_name} 加载失败: "
                    f"{_format_exception_details(e)}"
                )
            finally:
                if persistent:
                    after = set(
                        await asyncio.to_thread(_collect_descendant_pids, os.getpid())
                    )
                    self._child_pids |= after - before
                    before = after

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
        """只关闭**本 manager** 的持久会话，不波及其它池。

        优雅 aclose（3s 超时）拆掉本池 exit_stack 里的会话/子进程；随后 SIGKILL
        兜底**仅限本池 start 期间记录的 PID**（应对某些 server 优雅关闭挂起），
        绝不像旧实现那样扫杀整个进程的后代（会误杀别的池）。
        """
        if self._exit_stack is None:
            return

        try:
            await asyncio.wait_for(self._exit_stack.aclose(), timeout=3.0)
        except TimeoutError:
            logger.warning("[MCP] 关闭持久会话超时（3s），强制跳过")
        except RuntimeError:
            # anyio cancel scope 不允许跨 task 退出；下方 SIGKILL 兜底本池子进程
            pass
        except Exception as e:
            logger.error(f"[MCP] 关闭持久会话时出错: {_format_exception_details(e)}")
        finally:
            _kill_pids(self._child_pids)  # 只杀本池自己的子进程
            self._exit_stack = None
            self._sessions.clear()
            self._tools.clear()
            self._tool_server_map.clear()
            self._child_pids.clear()
            self._config_hash = ""
            self._started = False
            logger.info("[MCP] 持久会话已关闭（本池）")


# ── 会话池（按项目分池）──
#
# 全局单例不足以表达两层配置：不同项目 merge 出的 server 集不同，须各自一个池。
# key = str(project_dir)，全局（project_dir=None）用固定 key。

_GLOBAL_POOL_KEY = "__global__"
# 已启动池数上限：超过则优雅淘汰最久未用的池，bound 住长跑 serve 多项目切换的子进程
# 增长。与 Claude Code「连接持进程生命周期」同思路，只是加一个宽松上限防病态无界增长。
_MAX_POOLS = 16
# 串行化 start：保证 start 期间的子进程 PID 快照精确归属本池（不与并发 spawn 交叉），
# 并顺带消除同一池并发首次初始化时重复 start 的竞态。
_start_lock = asyncio.Lock()
# 池加载完成回调（gateway 注册，广播 mcp.status 给绑定该池的连接）
_on_pool_loaded: Callable[[dict], None] | None = None
# 关停闩：close_all_pools 置位后不再受理新加载——清理期间/之后残存的后台任务
# （detached run、迟到的 cron tick）再触发 ensure_loading 会在 SIGKILL 扫尾后
# spawn 无人回收的子进程
_shutting_down = False


class McpPool:
    """单个项目池的全部状态与生命周期：manager、在途加载任务、版本号、LRU 记账。

    生命周期不变量集中在本类，调用方无需各自维护：

    - :meth:`close` 恒取消在途加载、关 manager、换新一代空 manager 并递增
      generation——存活会话在轮首据版本号感知换代并重建工具列表（继续持有已关池
      的工具只会调用失败）。配置作废与 LRU 淘汰共用此路径，不可能漏 bump。
    - 加载任务只清理自己的登记：被 close 取消的旧任务不会误删同 key 后继注册的新任务。

    对象一经创建即长驻 ``_pools``（轻量），generation 随之跨换代存活。
    """

    def __init__(self, key: str) -> None:
        self.key = key
        self.manager = MCPSessionManager()
        # 加载版本号：每次 start 完成 / close 换代 +1。会话在轮首据此感知
        # 「工具已就位/已换代」并重建 context.tools——后台加载不阻塞会话就绪的
        # 另一半拼图（对齐 Claude Code：交互会话从不等 MCP，工具随连接就位动态出现）。
        self.generation = 0
        self.last_used = 0.0  # 最近访问 monotonic 时刻（LRU 淘汰用）
        self._load_task: asyncio.Task | None = None

    @property
    def loading(self) -> bool:
        """是否有在途后台加载。"""
        return self._load_task is not None and not self._load_task.done()

    def ensure_loading(self) -> None:
        """确保本池在后台加载（幂等、不阻塞调用方）。关停闩落下后不再受理。"""
        if _shutting_down or self.manager.is_started or self.loading:
            return
        self._load_task = asyncio.create_task(self._load())

    def cancel_loading(self) -> None:
        """取消在途加载（不清登记、不等退出——那是 close 的事）。"""
        if self._load_task is not None:
            self._load_task.cancel()

    async def wait_ready(self) -> None:
        """等待本池就绪（未触发过则先触发后台加载，再等它完成）。

        暖池零 I/O 直接返回，无 MCP 配置也直接返回。供单发/非交互路径（cron、
        headless、workflow、子代理）经 ``get_tools(wait_mcp=True)`` 在建 agent 前
        等工具就位——它们没有下一轮可自愈。交互路径（bridge）保持非阻塞 + 轮首刷新。
        等待期间池被 close 换代（配置作废）则对新一代重试，加载失败终态不重试。
        """
        while not self.manager.is_started:
            if not self.loading and not _load_merged_mcp_config(
                _key_project_dir(self.key)
            ):
                return
            self.ensure_loading()
            task = self._load_task
            if task is None:
                return  # 关停闩已落，ensure_loading 不再受理：不会再有可等的加载
            manager = self.manager
            # wait 而非 await：任务被取消（配置作废）时不向调用方抛 CancelledError
            await asyncio.wait([task])
            if self.manager is manager:
                return  # 未换代：加载已到终态（成功或失败），失败不重试

    async def _load(self) -> None:
        """后台加载；完成后递增版本号并通知订阅者（含失败的 server 明细）。"""
        task = asyncio.current_task()
        manager = self.manager
        mcp_config = _load_merged_mcp_config(_key_project_dir(self.key))
        try:
            if not mcp_config:
                return
            async with _start_lock:
                # 等锁期间池可能被 close（配置作废换代）：manager 已换新，
                # 对旧 manager start 会 spawn 无人追踪的子进程，必须放弃本次加载
                if self.manager is not manager:
                    return
                if not manager.is_started:
                    await manager.start(mcp_config)
                await _evict_lru_pools(keep=self)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(
                f"加载MCP工具失败: {_format_exception_details(e)}. "
                f"配置的服务器: {list(mcp_config.keys())}"
            )
            return
        finally:
            # 只清理自己的登记：本任务被 close 取消后，同 key 可能已注册新任务
            if self._load_task is task:
                self._load_task = None
        if self.manager is not manager:
            return  # start 期间被 close 换代：版本号与广播由新一代负责
        self.generation += 1
        if _on_pool_loaded is not None:
            _on_pool_loaded(
                {
                    "project": project_wire_key(_key_project_dir(self.key)),
                    "servers": _server_status_list(manager),
                }
            )

    async def close(self) -> None:
        """关池并换代：取消并送走在途加载、关 manager（杀本池子进程）、递增版本号。"""
        # 换 manager 必须在首个 await 之前：wait_ready 的等待者先于本方法被唤醒
        # （同等一个任务，done-callback 按注册序 FIFO 分发），排水期间 ensure_loading
        # 也可能进来——二者全靠 identity 校验感知换代（等待者对新代重试、新加载绑定
        # 新 manager）；若换代滞后到排水之后，它们会把旧 manager 误判为现任：等待者
        # 静默按终态返回（单发路径拿零工具），新加载对着将被关闭的 manager start。
        manager = self.manager
        self.manager = MCPSessionManager()
        self.cancel_loading()
        task = self._load_task
        self._load_task = None
        if task is not None:
            # 必须等被取消的任务退出：取消是异步送达的，_start_servers 的 finally
            # 还没把刚 spawn 的 PID 记进 manager 就关它，会漏杀该子进程
            await asyncio.wait([task])
        await manager.close()
        self.generation += 1


_pools: dict[str, McpPool] = {}


def pool_for(project_dir: Path | None) -> McpPool:
    """取项目池（不存在则建对象，不触发加载）。project_dir=None 为全局池。"""
    key = _project_key(project_dir)
    pool = _pools.get(key)
    if pool is None:
        pool = McpPool(key)
        _pools[key] = pool
    return pool


def set_on_pool_loaded(cb: Callable[[dict], None] | None) -> None:
    """注册池加载完成回调（进程级单一订阅者：BroadcastHub）。"""
    global _on_pool_loaded
    _on_pool_loaded = cb


def pool_generation(project_dir: Path | None) -> int:
    """项目池的加载版本号（未加载过为 0）。"""
    pool = _pools.get(_project_key(project_dir))
    return pool.generation if pool is not None else 0


def _server_status_list(manager: MCPSessionManager) -> list[dict]:
    """server_status → wire 形状（McpServerStatus[]）：RPC 与 mcp.status 广播共用。"""
    return [{"name": n, **st} for n, st in manager.server_status.items()]


def get_pool_status(project_dir: Path | None) -> dict:
    """项目池当前状态（面板徽标 / get_mcp_status RPC）。"""
    pool = _pools.get(_project_key(project_dir))
    if pool is None:
        return {"loading": False, "servers": []}
    return {"loading": pool.loading, "servers": _server_status_list(pool.manager)}


async def _evict_lru_pools(keep: McpPool) -> None:
    """已启动的池数超上限时，优雅关闭最久未用的（keep 除外）。在 _start_lock 内调用。

    close 会递增被淘汰池的版本号：仍绑着它的存活会话轮首感知换代、重建并按需重载。
    """
    while True:
        started = [p for p in _pools.values() if p.manager.is_started]
        if len(started) <= _MAX_POOLS:
            return
        victim = min(
            (p for p in started if p is not keep),
            key=lambda p: p.last_used,
            default=None,
        )
        if victim is None:
            return
        await victim.close()
        logger.info("[MCP] 池数超上限，淘汰最久未用会话池: %s", victim.key)


def _project_key(project_dir: Path | None) -> str:
    return str(project_dir) if project_dir is not None else _GLOBAL_POOL_KEY


def project_wire_key(project_dir: Path | None) -> str:
    """mcp.status 的池 wire 标识（"" = 全局池）。

    广播过滤按字节比对（broadcast._mine：连接的 key == 事件 payload 的 project），
    payload 侧与连接侧必须同源——两侧都经本函数投影，改归一化只改这一处。
    """
    return "" if project_dir is None else str(project_dir)


def _key_project_dir(key: str) -> Path | None:
    """池 key 反解出 project_dir（全局池 → None）。"""
    return None if key == _GLOBAL_POOL_KEY else Path(key)


async def close_all_pools() -> None:
    """进程退出时关闭所有会话池：先 SIGKILL 全部后代兜底（应对优雅关闭挂起的 server），
    再逐池优雅关。仅 shutdown_shared_runtime 调用；日常作废走 invalidate_mcp_pools。
    """
    global _shutting_down
    _shutting_down = True  # 先落闩：清理期间/之后 ensure_loading 一律不再起新加载
    # 再取消全部在途后台加载：否则它可能在清理后继续 spawn 子进程；
    # 逐池收尾统一走 close()（等任务退出 + 换代）
    for pool in _pools.values():
        pool.cancel_loading()
    _kill_child_processes()
    for pool in list(_pools.values()):
        await pool.close()
    _pools.clear()


async def invalidate_mcp_pools(scope: str, project_dir: Path | None = None) -> None:
    """save/delete 后作废**配置真的变了**的会话池，下次加载时以新配置重建。

    借鉴 Claude Code 的 config-hash diff：只关 merged 配置 hash 变了的池，
    没变的（如某项目自己覆盖了被改的全局 server）原样保留、完全不打断。
    close 内取消在途加载、杀本池子进程并递增版本号——存活会话轮首感知换代
    重建工具列表，重建触发新池后台加载。

    ``scope=="global"`` → 逐池重算 merged hash（全局层被所有项目继承）；
    其它 → 只查该项目的池。
    """
    if scope == "global":
        candidates = list(_pools.values())
    else:
        pool = _pools.get(_project_key(project_dir))
        candidates = [pool] if pool is not None else []

    for pool in candidates:
        new_config = _load_merged_mcp_config(_key_project_dir(pool.key))
        if not pool.manager.is_started and not pool.loading:
            # 冷池没有可关的资源，但配置非空时必须换代：绑着它的存活会话只认
            # 版本号比对，不换代则轮首永不重建（首个新增的 server 永不生效）
            if new_config:
                pool.generation += 1
            continue
        if _config_hash(new_config) != pool.manager._config_hash:
            await pool.close()


# ── 公共 API ──


async def _list_all_pages(
    list_page: Callable[..., Awaitable[Any]], attr: str
) -> list[Any]:
    """按 MCP 分页协议取全量：循环 cursor 直到 nextCursor 为空。"""
    items: list[Any] = []
    cursor: str | None = None
    while True:
        page = await list_page(cursor=cursor)
        items.extend(getattr(page, attr))
        if not page.nextCursor:
            return items
        cursor = page.nextCursor


async def _probe_mcp_server(config: dict[str, Any], timeout: float) -> dict[str, Any]:
    """建一次会话完成握手并枚举能力（tools/prompts/resources 按声明的 capability 取）。

    超时从拿到 spawn 锁才起表：后台池加载可长时间持锁（30s/server 串行），
    把排队时间计入预算会把健康 server 误报成超时。
    """
    async with asyncio.timeout(None) as probe_timeout:
        async with AsyncExitStack() as stack:
            # stdio spawn 子进程须与池 start 的 PID 快照互斥（diff 归属正确性依赖快照期间
            # 无别处 spawn），只锁 spawn 一瞬；HTTP/SSE 无子进程不加锁
            guard = _start_lock if config.get("transport") == "stdio" else nullcontext()
            async with guard:
                probe_timeout.reschedule(asyncio.get_running_loop().time() + timeout)
                start = time.monotonic()
                session = await stack.enter_async_context(
                    sessions.create_session(config)
                )
            init = await session.initialize()
            latency_ms = int((time.monotonic() - start) * 1000)
            caps = init.capabilities

            tools = (
                [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.inputSchema,
                    }
                    for t in await _list_all_pages(session.list_tools, "tools")
                ]
                if caps.tools is not None
                else []
            )
            prompts = (
                [
                    {
                        "name": p.name,
                        "description": p.description or "",
                        "arguments": [
                            {
                                "name": a.name,
                                "description": a.description or "",
                                "required": bool(a.required),
                            }
                            for a in (p.arguments or [])
                        ],
                    }
                    for p in await _list_all_pages(session.list_prompts, "prompts")
                ]
                if caps.prompts is not None
                else []
            )
            resources = (
                [
                    {
                        "uri": str(r.uri),
                        "name": r.name or "",
                        "description": r.description or "",
                        "mime_type": r.mimeType or "",
                    }
                    for r in await _list_all_pages(session.list_resources, "resources")
                ]
                if caps.resources is not None
                else []
            )

    return {
        "ok": True,
        "server": {"name": init.serverInfo.name, "version": init.serverInfo.version},
        "latency_ms": latency_ms,
        "tools": tools,
        "prompts": prompts,
        "resources": resources,
    }


async def test_mcp_server(
    server_config: dict[str, Any], timeout: float = 15.0
) -> dict[str, Any]:
    """连接测试：用给定配置临时建一次会话，握手后枚举能力，随即断开。

    超时独立于 _SERVER_START_TIMEOUT：这是交互路径，用户在弹窗前实时等待，
    后台池加载放宽到 30s 的理由不适用。计时从拿到 spawn 锁开始（见 _probe_mcp_server）。

    与常驻会话池完全独立——验证的是「这份配置能不能连上、有什么能力」，
    不动任何已建立的池。配置归一化与加载侧同源（:func:`_normalize_server_config`），
    测试通过 = 会话加载也认。成功返回 ``{ok, server, latency_ms, tools, prompts,
    resources}``，失败返回 ``{ok: False, error}``。
    """
    config = _normalize_server_config(server_config)
    try:
        return await _probe_mcp_server(config, timeout)
    except TimeoutError:
        return {"ok": False, "error": f"连接超时（{timeout:g}s）"}
    except Exception as e:
        return {"ok": False, "error": _format_exception_details(e)}


async def get_mcp_tools(
    filter_names: list[str] | None = None,
    project_dir: Path | None = None,
) -> list[StructuredTool]:
    """获取MCP服务器提供的工具（分层配置：全局 ∪ 会话项目）。

    ``project_dir`` 未显式给定时读 contextvar（由 ``get_tools`` 设置）；缺省即纯全局。
    每个项目一个会话池，池内首次加载后缓存工具；自动为 stdio 服务器创建持久会话。
    本函数恒不阻塞；需要等冷池就位的调用方经 ``get_tools(wait_mcp=True)`` 先等池。
    """
    if project_dir is None:
        project_dir = _current_project_dir.get()

    # 先登记池对象（轻量、不触发加载）再查配置：无配置的项目也要在 _pools 挂名，
    # 否则用户添加首个 server 时 invalidate 找不到池、无从换代
    pool = pool_for(project_dir)
    pool.last_used = time.monotonic()  # LRU 记账

    mcp_config = _load_merged_mcp_config(project_dir)
    if not mcp_config:
        return []

    if not pool.manager.is_started:
        # 后台加载、立即返回空集：MCP 从不阻塞会话就绪/轮次（对齐 Claude Code 的
        # pending 语义）。就位后 generation 变化，会话在轮首重建工具列表。
        pool.ensure_loading()
        logger.warning("[MCP] 池加载中，本轮无 MCP 工具（下一轮自愈）: %s", pool.key)
        return []

    return _filter_tools(pool.manager.get_tools(), filter_names)
