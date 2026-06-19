"""Channel 协议：传输无关的帧出口。

GatewaySession 只通过 ``channel.send(frame)`` 把 wire 帧推给前端；具体传输
（desktop WS / 未来 IM）各自实现 Channel，使会话编排与传输彻底解耦。
"""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    async def send(self, frame: dict) -> None: ...
