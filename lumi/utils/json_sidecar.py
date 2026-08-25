"""按 key 落盘的 JSON sidecar：读（损坏回落空）+ 合并写（空值删键、无变化不写、原子写）。

session_meta（会话用户标记）与 channels/relay（直连绑定）共用这一套读写语义，只有路径不同。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.logger import logger

# path → (mtime_ns, data)：sidecar 被高频读取（直连绑定每条消息、模型覆盖每轮），
# mtime 不变即复用上次解析结果；写入走 atomic 换文件，mtime 变化自然失效。
# 约定：调用方把返回值当只读（写一律经 _commit，见其注释）。
_cache: dict[Path, tuple[int, dict]] = {}


def load_sidecar(path: Path) -> dict[str, dict]:
    """读取全部条目，缺失或损坏时返回空字典。"""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("sidecar 读取失败: %s", path, exc_info=True)
        return {}
    _cache[path] = (mtime, data)
    return data


def _commit(path: Path, data: dict) -> None:
    """落盘 + 回填缓存（省掉下次读的整文件重解析）。**两个写口的唯一提交点。**

    传进来的必须是新字典：就地改缓存里那本，写盘失败时缓存已经变了、mtime 又没变，
    这份分歧会在本进程内一直活到重启。
    """
    atomic_write_json(path, data)
    _cache[path] = (path.stat().st_mtime_ns, data)


def delete_sidecar(path: Path, key: str) -> None:
    """删除整条 key（不存在则不写盘）。"""
    data = load_sidecar(path)
    if key not in data:
        return
    _commit(path, {k: v for k, v in data.items() if k != key})


def update_sidecar(path: Path, key: str, **fields) -> dict:
    """合并更新某 key 的字段；空值（False/""/None）删键保持精简，与现状一致则跳过写盘。"""
    data = load_sidecar(path)
    old = data.get(key, {})
    entry = {**old, **fields}
    entry = {k: v for k, v in entry.items() if v not in (None, "", False)}
    if entry == old:
        return entry
    if entry:
        _commit(path, {**data, key: entry})
    else:
        delete_sidecar(path, key)
    return entry
