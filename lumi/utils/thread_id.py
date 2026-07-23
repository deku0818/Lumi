"""Thread ID 工具模块

生成和验证符合 DNS-1035 规范的 thread_id，用于 Kubernetes Service 名称等场景。

DNS-1035 规范要求:
- 必须以小写字母开头
- 只能包含小写字母、数字和连字符（-）
- 必须以字母或数字结尾
- 长度不能超过 63 个字符
"""

import re
from uuid import uuid4

from lumi.utils.constants import FEISHU_THREAD_PREFIX

# DNS-1035 正则表达式
DNS_1035_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")
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


class InvalidThreadIdError(ValueError):
    """无效的 thread_id 错误"""


def generate_thread_id(prefix: str = "t") -> str:
    """生成符合 DNS-1035 规范的 thread_id

    格式: {prefix}-{uuid_hex}
    例如: t-7e2fe03e335d4cb8829ef86518c9e232

    Args:
        prefix: 前缀，默认为 "t"，必须以小写字母开头

    Returns:
        符合 DNS-1035 规范的 thread_id
    """
    uuid_hex = uuid4().hex
    thread_id = f"{prefix}-{uuid_hex}"
    return thread_id


def sanitize_thread_id(thread_id: str) -> str:
    """将任意字符串转为 DNS-1035 合规的 ID

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
        符合 DNS-1035 规范的 ID
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


def valid_thread_id(thread_id: str) -> str:
    """验证 thread_id 是否符合 DNS-1035 规范

    Args:
        thread_id: 待验证的 thread_id

    Returns:
        验证通过时返回原 thread_id

    Raises:
        InvalidThreadIdError: thread_id 不符合 DNS-1035 规范
    """
    if not thread_id:
        raise InvalidThreadIdError("thread_id 不能为空")

    if len(thread_id) > MAX_LENGTH:
        raise InvalidThreadIdError(
            f"thread_id 长度不能超过 {MAX_LENGTH} 个字符，当前长度: {len(thread_id)}"
        )

    if not DNS_1035_PATTERN.match(thread_id):
        errors = []
        if not thread_id[0].isalpha() or not thread_id[0].islower():
            errors.append("必须以小写字母开头")
        if not thread_id[-1].isalnum() or (
            thread_id[-1].isalpha() and not thread_id[-1].islower()
        ):
            errors.append("必须以小写字母或数字结尾")
        if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in thread_id):
            errors.append("只能包含小写字母、数字和连字符（-）")

        error_msg = f"thread_id '{thread_id}' 不符合 DNS-1035 规范"
        if errors:
            error_msg += f": {'; '.join(errors)}"
        raise InvalidThreadIdError(error_msg)

    return thread_id
