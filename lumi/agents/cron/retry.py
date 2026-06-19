"""重试判定与退避计算：cron 任务失败后的瞬态错误识别与退避延迟。

- ``is_transient_error``：判断异常是否为可重试的瞬态错误。
- ``backoff_delay``：按连续失败次数计算退避延迟秒数。
"""

from __future__ import annotations

import asyncio

import httpx

from lumi.utils.constants import CRON_BACKOFF_INTERVALS


def is_transient_error(exc: BaseException) -> bool:
    """判断异常是否为瞬态错误，瞬态错误可触发重试。

    瞬态错误包括：
    - asyncio.TimeoutError（网络超时）
    - httpx.HTTPStatusError 且状态码为 429 或 5xx
    - ConnectionError、OSError（网络连接问题）
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500

    return isinstance(exc, (ConnectionError, OSError))


def backoff_delay(consecutive_errors: int) -> int:
    """按连续失败次数返回退避延迟秒数（夹取到 ``CRON_BACKOFF_INTERVALS`` 末位）。"""
    idx = min(consecutive_errors - 1, len(CRON_BACKOFF_INTERVALS) - 1)
    return CRON_BACKOFF_INTERVALS[idx]
