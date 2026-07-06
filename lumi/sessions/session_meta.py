"""会话用户元数据 sidecar — pin / 重命名等不属于 checkpoint 的用户标记。

会话列表本身由 LangGraph checkpoint 派生（见 session_store.py），但「置顶」「自定义
标题」是用户施加的元数据，不存在于 checkpoint 中。本模块用一个 JSON 文件按 thread_id
持久化这些标记，与 checkpoints.db 同目录（共享生命周期）。

存储形如 {"<thread_id>": {"pinned": true, "title": "自定义名"}}；仅写入非默认值，
保持文件精简。除用户标记外也承载派生标题（channel_title 渠道自动名、auto_title
模型生成标题及其定稿标记 auto_title_final，展示优先级 title > channel_title >
auto_title）。无 textual 依赖，可在 headless 服务中直接使用。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.config.global_manager import GlobalConfigManager
from lumi.utils.logger import logger


def _meta_path() -> Path:
    return GlobalConfigManager.load().get_checkpoint_dir() / "session_meta.json"


def load_all() -> dict[str, dict]:
    """读取全部会话元数据，缺失或损坏时返回空字典。"""
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("会话元数据读取失败: %s", path, exc_info=True)
        return {}


def _save_all(data: dict[str, dict]) -> None:
    atomic_write_json(_meta_path(), data)


def update_meta(thread_id: str, **fields) -> dict:
    """更新某会话的元数据字段；清理空值（False/""/None）以保持精简。

    合并后与现状一致则跳过写盘——高频调用方（飞书入站每条消息同步群名）
    据此免每消息一次全文件写，且删除后的重建能如实重写（无内存缓存可失效）。
    """
    data = load_all()
    old = data.get(thread_id, {})
    entry = {**old, **fields}
    entry = {k: v for k, v in entry.items() if v not in (None, "", False)}
    if entry == old:
        return entry
    if entry:
        data[thread_id] = entry
    else:
        data.pop(thread_id, None)
    _save_all(data)
    return entry


def delete_meta(thread_id: str) -> None:
    """删除某会话的元数据条目（会话被删除时调用）。"""
    data = load_all()
    if data.pop(thread_id, None) is not None:
        _save_all(data)
