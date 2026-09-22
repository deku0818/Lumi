"""MCP 配置：全局 ∪ 项目两层合并、归一化、按 mtime 缓存。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lumi.utils.config import get_config
from lumi.utils.hashing import short_hash
from lumi.utils.logger import logger
from lumi.utils.paths import lumi_home


def global_mcp_config_path() -> Path:
    """全局层配置路径 = 该机器固定位置。

    显式指定的配置目录（get_config().discovery.explicit_dir）> ``lumi_home()``
    （即 ``LUMI_CONFIG_DIR`` > ``~/.lumi``）。**刻意跳过 cwd/.lumi 发现**——两层模型下
    「cwd 到底算全局还是某个项目」有歧义，全局层必须是稳定的每机器位置。
    """
    override = get_config().discovery.explicit_dir
    base = Path(override).expanduser().resolve() if override else lumi_home()
    return base / "mcp_server.json"


def project_mcp_config_path(project_dir: Path) -> Path:
    """项目层配置路径。加载侧与写入侧（``gateway.mcp_rpc``）共用，二者一旦分叉
    就是「面板存了却加载不到」。"""
    return project_dir / ".lumi" / "mcp_server.json"


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


def normalize_server_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """单个 server 配置归一化：剥离 Lumi 元字段 ``disabled``、补推缺省 ``transport``/``args``。

    ``disabled`` 绝不能下传给 langchain adapter（它 ``**params`` 全透传，混入未知键会
    TypeError）；``transport`` 缺省按有无 url 推断（Claude Desktop 风格配置不写该键，
    而 adapter 的 create_session 强制要求）；``args`` 同理——命令本身自足（无参数可传）
    时配置里没有该键，但 adapter 硬性要求 stdio 必须带 args，补空列表即可。
    会话池与连接测试共用，两路行为恒一致。
    """
    out = {k: v for k, v in cfg.items() if k != "disabled"}
    if "transport" not in out:
        out["transport"] = "streamable_http" if out.get("url") else "stdio"
    if out["transport"] == "stdio":
        out.setdefault("args", [])
    return out


def _strip_disabled(config: dict[str, Any]) -> dict[str, Any]:
    """丢弃被禁用的 server，其余逐个归一化（见 :func:`normalize_server_config`）。"""
    return {
        name: normalize_server_config(cfg)
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


def load_merged_mcp_config(project_dir: Path | None) -> dict[str, Any]:
    """分层合并的 MCP 配置（全局 ∪ 项目，项目同名覆盖），并剥离 ``disabled``。

    全局层由 :func:`global_mcp_config_path` 决定；项目层为
    ``<project_dir>/.lumi/mcp_server.json``（仅当其路径 ≠ 全局层时叠加）。
    返回可直接下传 adapter 的配置。
    """
    global_path = global_mcp_config_path()
    project_path = (
        project_mcp_config_path(project_dir) if project_dir is not None else None
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


def config_hash(config: dict[str, Any]) -> str:
    """merged 配置的稳定 hash（key 排序 → {a,b} 与 {b,a} 同 hash）。用于判断池是否真变。"""
    return short_hash(json.dumps(config, sort_keys=True, ensure_ascii=False), 16)


EMPTY_CONFIG_HASH = config_hash({})
