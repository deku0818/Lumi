"""IM channel 配置持久化 —— ``~/.lumi/lumi.json`` 的 "channels" 分区（含密钥，chmod 600）。

由 desktop UI 经 WS RPC 读写（``get_channels`` / ``save_channel``），与 config.json 解耦。
经 ``user_store`` section-patch 原子写；缺失/损坏返回默认。

    {"feishu": {"enabled": bool, "app_id": str, "app_secret": str,
                "allow_from": [str], "group_policy": "mention|open",
                "tool_mode": "auto|privileged", "workspace": str}}
"""

from __future__ import annotations

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.utils.config import user_store
from lumi.utils.logger import logger


def _read() -> dict:
    """读取 lumi.json 的 "channels" 分区一次；缺失/损坏返回空 dict。"""
    return user_store.read_section("channels", {})


def config_path() -> str:
    """配置（含密钥）落盘的绝对路径，供面板原样展示。

    「存在哪」这件事归本模块管：调用方各自去问 user_store 的话，日后换存储位置
    就得同时改好几处，而面板显示的路径与真实写入位置分家是最不该发生的一种谎。
    """
    return str(user_store.CONFIG_FILE)


def load_feishu() -> FeishuChannelConfig:
    """读取飞书配置；缺失/非法字段回落各自默认。"""
    raw = _read().get("feishu")
    if not isinstance(raw, dict):
        return FeishuChannelConfig()
    try:
        return FeishuChannelConfig.model_validate(raw)
    except Exception:
        logger.warning("channels 分区飞书段校验失败，使用默认", exc_info=True)
        return FeishuChannelConfig()


def save_feishu(config: dict) -> FeishuChannelConfig:
    """校验并持久化飞书配置（含密钥，chmod 600 原子写），返回规范化后的配置。"""
    validated = FeishuChannelConfig.model_validate(config)
    data = _read()
    data["feishu"] = validated.model_dump()
    user_store.write_section("channels", data)
    return validated
