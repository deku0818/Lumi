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
    get_pool_status,
    global_mcp_config_path,
    invalidate_mcp_pools,
    project_mcp_config_path,
    test_mcp_server,
)
from lumi.utils.atomic_io import atomic_write_json


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
        return project_mcp_config_path(project_dir)
    # 全局层写入位置须与加载侧同源（同样尊重 LUMI_CONFIG_DIR / 显式配置目录），
    # 否则「存了却加载不到」
    return global_mcp_config_path()


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


def upsert_server(
    scope: str, project_dir: Path | None, name: str, config: dict | None
) -> tuple[Path, dict]:
    """单个 server 的写入（``config=None`` 即删除）：严格读防抹除 + 原子写 0o600
    （env/headers 可含密钥）。desktop RPC 与 `lumi mcp` CLI 的唯一写入路径；
    返回 (落盘路径, 写后的全部 server)。"""
    path = server_config_path(scope, project_dir)
    servers = read_servers(path)  # 损坏则抛错，避免抹掉全部配置
    if config is None:
        servers.pop(name, None)
    else:
        servers[name] = config
    atomic_write_json(path, servers, mode=0o600)
    return path, servers


def _scope_of(params: dict) -> tuple[str, Path | None]:
    scope = params.get("scope") or "global"
    return scope, resolve_project_dir(scope, params.get("project") or "")


async def _test(params: dict) -> dict:
    # 连接测试：直接用前端传来的配置临时连一次，与 scope/写盘无关
    return await test_mcp_server(params.get("config") or {})


async def _status(params: dict) -> dict:
    # 项目池的最近加载状态（面板徽标）：project 空 = 全局池。
    # 复用 resolve_project_dir 保证路径归一化与建池/作废一个口径
    return get_pool_status(resolve_project_dir("project", params.get("project") or ""))


async def _list(params: dict) -> dict:
    scope, project_dir = _scope_of(params)
    path = server_config_path(scope, project_dir)
    # 附带该 scope 配置文件的绝对路径供面板原样展示——前端拼 ~/.lumi 既
    # 看不懂又会在 LUMI_CONFIG_DIR / 显式配置目录下说谎
    return {"servers": read_servers_lenient(path), "path": str(path)}


async def _save(params: dict) -> dict:
    name = params.get("name") or ""
    if not name:
        raise ValueError("MCP server 缺少 name")
    scope, project_dir = _scope_of(params)
    _, servers = upsert_server(scope, project_dir, name, params.get("config") or {})
    await invalidate_mcp_pools(scope, project_dir)
    return {"servers": servers}


async def _delete(params: dict) -> dict:
    scope, project_dir = _scope_of(params)
    _, servers = upsert_server(scope, project_dir, params.get("name") or "", None)
    await invalidate_mcp_pools(scope, project_dir)
    return {"servers": servers}


HANDLERS = {
    "list_mcp_servers": _list,
    "save_mcp_server": _save,
    "delete_mcp_server": _delete,
    "test_mcp_server": _test,
    "get_mcp_status": _status,
}
