"""让同一进程里的多个 lark WS 客户端各用各的事件循环。

``lark_oapi.ws.client`` 把事件循环存在**模块级**全局 ``loop`` 上（import 时抓一次），
``Client.start()`` 内部直接拿它跑 ``run_until_complete`` / ``create_task``。单个机器人
时把这个全局换成本线程的 loop 就够用；项目级机器人让一个进程同时跑 N 个客户端、各有
各的 daemon 线程和 loop，**N 个线程写同一个全局，后写的赢**——先连上那个客户端的协程
转眼挂到别人的 loop 上：

    receive message loop exit, err: ... got Future attached to a different loop
    connect failed, err: This event loop is already running   # 每 5 秒重连一次，永远失败

净效果是只有最后启动的机器人活着，其余全死。这里把那个全局换成按线程分发的代理：每个
WS 线程登记自己的 loop，SDK 里的 ``loop.xxx`` 就各归各的。未登记的线程退回原本那个
loop，行为与打补丁前一致。

**已知边界**：代理不是 loop 本身，``x is loop`` 这类身份比较会失配。SDK 里只有
``lark_oapi.channel``（另一套 API）这么用，Lumi 走的是 ``lark.ws.Client``，不经过它。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_local = threading.local()
_install_lock = threading.Lock()


class _PerThreadLoop:
    """按调用线程分发的事件循环代理。"""

    def __init__(self, fallback: Any) -> None:
        self._fallback = fallback

    def __getattr__(self, name: str) -> Any:
        # __getattr__ 只在常规查找落空时触发，故代理自身的属性不会被转发出去
        return getattr(getattr(_local, "loop", None) or self._fallback, name)


def use_loop_in_this_thread(loop: asyncio.AbstractEventLoop) -> None:
    """把本线程的 loop 登记为 lark WS 在本线程使用的事件循环（首次调用时装代理）。

    在调用 ``Client.start()`` 之前、于该客户端自己的 WS 线程内调用。
    """
    import lark_oapi.ws.client as ws_client

    with _install_lock:
        if not isinstance(ws_client.loop, _PerThreadLoop):
            ws_client.loop = _PerThreadLoop(ws_client.loop)
    _local.loop = loop
