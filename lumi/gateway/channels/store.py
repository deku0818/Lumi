"""IM channel 配置持久化 —— ``~/.lumi/channels.json``（含密钥，chmod 600）。

由 desktop UI 经 WS RPC 读写（``get_channels`` / ``save_channel``），与 config.yaml 解耦。
照抄 ``models/provider_store.py`` 的范式：原子写、限权、缺失/损坏返回默认。

    {"feishu": {"enabled": bool, "app_id": str, "app_secret": str,
                "allow_from": [str], "group_policy": "mention|open",
                "tool_mode": "auto|privileged", "workspace": str}}
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.utils.atomic_io import atomic_write_json
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger


def _path() -> Path:
    return GLOBAL_CONFIG_DIR / "channels.json"


def _read() -> dict:
    """读取并解析 channels.json 一次；缺失/损坏返回空 dict。"""
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        logger.warning("channels.json 读取失败: %s", path, exc_info=True)
        return {}


def load_feishu() -> FeishuChannelConfig:
    """读取飞书配置；缺失/非法字段回落各自默认。"""
    raw = _read().get("feishu")
    if not isinstance(raw, dict):
        return FeishuChannelConfig()
    try:
        return FeishuChannelConfig.model_validate(raw)
    except Exception:
        logger.warning("channels.json 飞书段校验失败，使用默认", exc_info=True)
        return FeishuChannelConfig()


def save_feishu(config: dict) -> FeishuChannelConfig:
    """校验并持久化飞书配置（含密钥，chmod 600 原子写），返回规范化后的配置。"""
    validated = FeishuChannelConfig.model_validate(config)
    data = _read()
    data["feishu"] = validated.model_dump()
    atomic_write_json(_path(), data, mode=0o600)
    return validated
