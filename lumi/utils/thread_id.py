"""Thread ID 工具模块

Lumi 的 thread_id 按前缀区分会话来源，各处分流（session 列表、dream 门控）据此判定：

- desktop / API 会话：``t-<uuid hex>``（:func:`generate_thread_id` 默认前缀）
- cron 执行会话：``cron-<uuid hex>``（前缀 :data:`CRON_THREAD_PREFIX`）
- IM 渠道常驻会话：``<渠道前缀><会话 key>``，由 :func:`sanitize_thread_id` 收敛成
  小写字母 / 数字 / ``-``、不超过 63 字符的确定性 id（飞书为 ``feishu-<chat_id>``），
  同一群 / 同一人跨重启落回同一 thread
"""

import re
from uuid import uuid4

from lumi.utils.constants import FEISHU_THREAD_PREFIX

MAX_LENGTH = 63

# cron 执行会话的 thread 前缀：scheduler 生成、session_store 过滤共用此单一定义
CRON_THREAD_PREFIX = "cron"

# IM 渠道常驻会话的 thread 前缀（未来新增渠道在此登记）
_CHANNEL_THREAD_PREFIXES = (FEISHU_THREAD_PREFIX,)


def is_channel_thread(thread_id: str) -> bool:
    """是否是 IM 渠道的常驻长会话 thread（feishu 等，未来企微）。

    渠道会话是「一群 / 一人一个永久 thread」，与 desktop 短会话本质不同——增量式门控
    （N 个新会话等）对它无意义，dream / 维护逻辑据此分流。新增渠道只需在
    ``_CHANNEL_THREAD_PREFIXES`` 登记前缀，各处判定自动跟上。
    """
    return thread_id.startswith(_CHANNEL_THREAD_PREFIXES)


def is_cron_thread(thread_id: str) -> bool:
    """是否是 cron 执行线程（``cron-`` 前缀）。观测直播 / dream 分流据此判定。"""
    return thread_id.startswith(CRON_THREAD_PREFIX)


def generate_thread_id(prefix: str = "t") -> str:
    """生成随机 thread_id：``{prefix}-{uuid_hex}``，如 ``t-7e2fe03e335d4cb8829ef86518c9e232``。"""
    return f"{prefix}-{uuid4().hex}"


def sanitize_thread_id(thread_id: str) -> str:
    """将任意字符串收敛为小写字母 / 数字 / ``-`` 组成、不超过 63 字符的确定性 ID

    转换规则:
    - 转小写
    - 非法字符替换为 `-`
    - 合并连续 `-`，去掉首尾 `-`
    - 数字开头时添加 `t-` 前缀
    - 截断到 63 字符
    - 空串或全非法字符时生成新 ID

    Args:
        thread_id: 原始 thread_id

    Returns:
        收敛后的 ID
    """
    # 转小写，非法字符替换为 -
    sanitized = re.sub(r"[^a-z0-9-]", "-", thread_id.lower())
    # 合并连续 -，去掉首尾 -
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")

    # 空串或全非法字符时生成新 ID
    if not sanitized:
        return generate_thread_id()

    # 数字开头时添加 t- 前缀
    if sanitized[0].isdigit():
        sanitized = f"t-{sanitized}"

    # 截断到 63 字符，确保不以 - 结尾
    sanitized = sanitized[:MAX_LENGTH].rstrip("-")

    return sanitized
