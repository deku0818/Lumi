"""运行中插话的收件箱：轮在跑时到达的真实用户消息先放这里，由图在工具边界 /
模型想结束时取走、注入当前轮（见 ``nodes.take_interjections``）；没赶上的由调用方
在轮结束后另起一轮发出。

只在真实用户轮期间开放取走（``open``）：后台通知 / 妙记等系统轮里到达的消息
不混进系统轮的回复，等它结束后另起一轮。只挂在会话主 agent 的 context 上，子代理
context 为 None——插话只属于主对话。
"""

from langchain_core.messages import HumanMessage

# astream_events 中浮现该名的 on_custom_event 即一次插话注入；bridge 据此 yield message.injected。
LUMI_INJECT_EVENT = "lumi_interject"

# 待注入消息上限：再多说明用户在刷屏，等这轮回复后再发
MAX_PENDING = 10


class InboxFullError(ValueError):
    """待注入消息已达上限。"""


class Inbox:
    """按到达顺序暂存待插话的用户消息（已按普通用户消息构造好，带 id 与显示声明）。"""

    def __init__(self) -> None:
        self._msgs: list[HumanMessage] = []
        # 是否允许图取走注入：仅真实用户轮期间为 True（bridge 开轮置位、收尾复位）
        self.open = False

    def put(self, msg: HumanMessage) -> None:
        if len(self._msgs) >= MAX_PENDING:
            raise InboxFullError("消息有点多，等我回复后再发。")
        self._msgs.append(msg)

    def take(self) -> list[HumanMessage]:
        """取走全部待插话消息。"""
        msgs, self._msgs = self._msgs, []
        return msgs

    def withdraw(self, message_id: str) -> bool:
        """撤回一条尚未送达的消息；已被取走（或不存在）返回 False。"""
        for i, m in enumerate(self._msgs):
            if m.id == message_id:
                del self._msgs[i]
                return True
        return False

    def pending(self) -> list[HumanMessage]:
        return list(self._msgs)
