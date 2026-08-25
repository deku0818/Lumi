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

from pathlib import Path

from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.config.global_manager import GlobalConfigManager
from lumi.utils.json_sidecar import load_sidecar, update_sidecar


def _meta_path() -> Path:
    return GlobalConfigManager.load().get_checkpoint_dir() / "session_meta.json"


def load_all() -> dict[str, dict]:
    """读取全部会话元数据，缺失或损坏时返回空字典。"""
    return load_sidecar(_meta_path())


def _save_all(data: dict[str, dict]) -> None:
    atomic_write_json(_meta_path(), data)


def update_meta(thread_id: str, **fields) -> dict:
    """更新某会话的元数据字段；清理空值（False/""/None）以保持精简。

    合并后与现状一致则跳过写盘——高频调用方（飞书入站每条消息同步群名）
    据此免每消息一次全文件写，且删除后的重建能如实重写（无内存缓存可失效）。
    """
    return update_sidecar(_meta_path(), thread_id, **fields)


def delete_meta(thread_id: str) -> None:
    """删除某会话的元数据条目（会话被删除时调用）。"""
    data = load_all()
    if data.pop(thread_id, None) is not None:
        _save_all(data)


def get_model_override(thread_id: str) -> tuple[str, str]:
    """读某会话的模型覆盖 (provider, model)（IM 渠道 /model 命令设定）；未设定返回空串对。

    与 goal 同理：会话级、跨轮跨重启存活的用户设定，与 pinned/title 同居一条 meta，
    「清空/删除会话」的 delete_meta 天然连它一并清，新会话不继承旧覆盖。
    """
    meta = load_all().get(thread_id, {})
    return meta.get("model_provider", ""), meta.get("model", "")


def set_model_override(thread_id: str, provider: str, model: str) -> None:
    """写会话级模型覆盖；空值即清除（update_meta 语义，保留 pin/title 等其他标记）。"""
    update_meta(thread_id, model_provider=provider, model=model)


def get_goal(thread_id: str) -> str:
    """读某会话当前生效的 goal 条件（/goal 命令设定）；未设定返回空串。

    goal 是 session 级 Stop hook 条件（见 hooks/goal.py），跨轮跨重启存活。存
    sidecar 而非 LangGraph state，是为让 goal_stop_hook 达成时能"清条件 + 返回
    None"、不短路后续的 auto_dream_stop_hook。清除用 ``update_meta(tid, goal="")``
    （空值自动过滤，保留 pin/rename），不用 delete_meta（会连带清掉用户标记）。
    """
    return load_all().get(thread_id, {}).get("goal", "")
