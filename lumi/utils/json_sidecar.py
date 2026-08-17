"""按 key 落盘的 JSON sidecar：读（损坏回落空）+ 合并写（空值删键、无变化不写、原子写）。

session_meta（会话用户标记）与 channels/relay（直连绑定）共用这一套读写语义，只有路径不同。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.logger import logger


def load_sidecar(path: Path) -> dict[str, dict]:
    """读取全部条目，缺失或损坏时返回空字典。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("sidecar 读取失败: %s", path, exc_info=True)
        return {}


def update_sidecar(path: Path, key: str, **fields) -> dict:
    """合并更新某 key 的字段；空值（False/""/None）删键保持精简，与现状一致则跳过写盘。"""
    data = load_sidecar(path)
    old = data.get(key, {})
    entry = {**old, **fields}
    entry = {k: v for k, v in entry.items() if v not in (None, "", False)}
    if entry == old:
        return entry
    if entry:
        data[key] = entry
    else:
        data.pop(key, None)
    atomic_write_json(path, data)
    return entry
