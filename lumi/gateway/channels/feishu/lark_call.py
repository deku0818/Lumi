"""lark-oapi 同步调用的统一错误样板。

飞书所有 SDK 调用都经此封装，集中"吞异常 + 吞 ``success()==False`` + 统一日志"，
避免各处自写 try/except 漂移。``lark_call_classified`` 额外返回飞书错误码与失败原因，
供 streaming（按码换卡 / 退避）与接入体检（按码区分故障、把原因displayed给用户）使用。
"""

from __future__ import annotations

from typing import Any

from lumi.utils.logger import logger

# 请求压根没到开放平台（异常 / 超时 / DNS / 代理）。与 API 拒绝必须可区分：后者说明
# 凭证已被验证过，前者什么都没验证——混为一谈会把断网的用户支去重抄 App Secret。
# 飞书不用负数错误码，故可安全占位。
NETWORK_ERROR = -1


def lark_call_classified(
    op: str, fn: Any, *, level: str = "warning"
) -> tuple[Any, int, str]:
    """``lark_call`` 的带错误码版本：返回 ``(resp | None, code, reason)``。

    成功 → ``(resp, 0, "")``；API 拒绝 → ``(None, resp.code, "code=… msg")``；
    请求未送达 → ``(None, NETWORK_ERROR, 异常文本)``。reason 是给人看的失败原因，
    调用方要展示给用户时取它，只记日志的用 :func:`lark_call` 即可。
    """
    log_fn = logger.error if level == "error" else logger.warning
    try:
        resp = fn()
    except Exception as e:
        log_fn(f"Feishu {op} 异常: {e}", exc_info=True)
        return None, NETWORK_ERROR, str(e)
    if not resp.success():
        reason = f"code={resp.code} {resp.msg}"
        log_fn(f"Feishu {op} 失败: {reason}")
        return None, resp.code, reason
    return resp, 0, ""


def lark_call(op: str, fn: Any, *, level: str = "warning") -> Any:
    """lark-oapi 同步调用的错误样板。

    调用 ``fn()`` 并吞下 ``resp.success() == False`` 与任意异常，两种情况都按
    ``level``（warning / error）记一条 ``Feishu {op} ...`` 日志并返回 ``None``；
    成功返回原始 response（调用方自取 ``resp.data.xxx``）。
    """
    resp, _code, _reason = lark_call_classified(op, fn, level=level)
    return resp
