"""lark-oapi 同步调用的统一错误样板。

飞书所有 SDK 调用都经此封装，集中"吞异常 + 吞 ``success()==False`` + 统一日志"，
避免各处自写 try/except 漂移。``_lark_call_classified`` 额外返回飞书错误码，供
streaming 等需按码决定恢复策略（换卡 / 退避）的调用方使用。
"""

from __future__ import annotations

from typing import Any

from lumi.utils.logger import logger


def lark_call_classified(
    op: str, fn: Any, *, level: str = "warning"
) -> tuple[Any, int]:
    """``lark_call`` 的带错误码版本：返回 ``(resp | None, raw_code)``。

    成功 → ``(resp, 0)``；失败/异常 → ``(None, code)``（异常用 ``0``）。
    """
    log_fn = logger.error if level == "error" else logger.warning
    try:
        resp = fn()
    except Exception as e:
        log_fn(f"Feishu {op} 异常: {e}", exc_info=True)
        return None, 0
    if not resp.success():
        log_fn(f"Feishu {op} 失败: code={resp.code}, msg={resp.msg}")
        return None, resp.code
    return resp, 0


def lark_call(op: str, fn: Any, *, level: str = "warning") -> Any:
    """lark-oapi 同步调用的错误样板。

    调用 ``fn()`` 并吞下 ``resp.success() == False`` 与任意异常，两种情况都按
    ``level``（warning / error）记一条 ``Feishu {op} ...`` 日志并返回 ``None``；
    成功返回原始 response（调用方自取 ``resp.data.xxx``）。
    """
    resp, _code = lark_call_classified(op, fn, level=level)
    return resp
