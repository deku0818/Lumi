"""MCP RPC：desktop WS 的 MCP 服务器管理方法实现。

作用范围两层：
- ``global`` → 该机器的 ``~/.lumi/mcp_server.json``（跨项目共享）
- ``project`` → ``<project>/.lumi/mcp_server.json``（叠加/覆盖全局同名 server）

读写的是**原始** dict（含 ``disabled`` 元字段供 UI 置灰）；分层合并 + 剥离 disabled
发生在加载侧（``mcp._load_merged_mcp_config``）。save/delete 写盘后作废配置真变了的
会话池，下次新会话加载时以新配置重建（没变的池完全不打断）。

项目根统一 ``expanduser().resolve()``，与 bridge 建池时（core.py 的 initialize）的池
key 口径一致——否则 symlink 路径（如 macOS /tmp→/private/tmp）会导致作废 pop 不中、
面板改动对该项目静默不生效。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.agents.tools.providers.mcp import (
    _global_mcp_config_path,
    get_pool_status,
    invalidate_mcp_pools,
    test_mcp_server,
)
from lumi.utils.atomic_io import atomic_write_json

MCP_METHODS = frozenset(
    {
        "list_mcp_servers",
        "save_mcp_server",
        "delete_mcp_server",
        "test_mcp_server",
        "get_mcp_status",
    }
)


def resolve_project_dir(scope: str, project: str) -> Path | None:
    """作用范围的项目根（global → None）；resolve 以对齐池 key（RPC 与 CLI 共用）。"""
    if scope == "project" and project:
        return Path(project).expanduser().resolve()
    return None


def server_config_path(scope: str, project_dir: Path | None) -> Path:
    """按 scope 解析目标 mcp_server.json 路径（desktop RPC 与 `lumi mcp` CLI 共用）。"""
    if scope == "project":
        if project_dir is None:
            raise ValueError("项目级 MCP 操作缺少 project 路径")
        return project_dir / ".lumi" / "mcp_server.json"
    # 全局层写入位置须与加载侧同源（同样尊重 --config-dir / LUMI_CONFIG_DIR），否则「存了却加载不到」
    return _global_mcp_config_path()


def read_servers(path: Path) -> dict:
    """严格读（写前必经）：缺失=正常空 dict；损坏/非 dict 则**抛错中止**，
    绝不用 {} 覆盖抹掉已有配置。列表等宽松场景由调用方自行捕获 ValueError。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"{path} 解析失败，已中止写入以免覆盖现有配置：{e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层不是 JSON 对象，已中止写入")
    return data


def read_servers_lenient(path: Path) -> dict:
    """宽松读（列表/展示用）：损坏当空处理，不阻断面板与查看（写路径才严格防抹除）。"""
    try:
        return read_servers(path)
    except ValueError:
        return {}


def write_servers(path: Path, servers: dict) -> None:
    """原子写 + 0o600（env/headers 可含密钥，与 channels 一致）；与 read_servers 成对。"""
    atomic_write_json(path, servers, mode=0o600)


async def dispatch_mcp(method: str, params: dict) -> dict:
    """执行一个 MCP RPC 方法（method 已确认属于 MCP_METHODS）。"""
    if method == "test_mcp_server":
        # 连接测试：直接用前端传来的配置临时连一次，与 scope/写盘无关
        return await test_mcp_server(params.get("config") or {})

    if method == "get_mcp_status":
        # 项目池的最近加载状态（面板徽标）：project 空 = 全局池。
        # 复用 resolve_project_dir 保证路径归一化与建池/作废一个口径
        return get_pool_status(
            resolve_project_dir("project", params.get("project") or "")
        )

    scope = params.get("scope") or "global"
    project = params.get("project") or ""
    project_dir = resolve_project_dir(scope, project)
    path = server_config_path(scope, project_dir)

    if method == "list_mcp_servers":
        # 附带该 scope 配置文件的绝对路径供面板原样展示——前端拼 ~/.lumi 既
        # 看不懂又会在 --config-dir 时说谎
        return {"servers": read_servers_lenient(path), "path": str(path)}

    servers = read_servers(path)  # 损坏则抛错，避免 save/delete 抹掉全部配置

    if method == "save_mcp_server":
        name = params.get("name") or ""
        if not name:
            raise ValueError("MCP server 缺少 name")
        servers[name] = params.get("config") or {}
    else:  # delete_mcp_server
        servers.pop(params.get("name") or "", None)

    write_servers(path, servers)
    await invalidate_mcp_pools(scope, project_dir)
    return {"servers": servers}
