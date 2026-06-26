"""把一次 agent run 的 BridgeEvent 流折叠成飞书消息。

这是 Lumi「每会话一 AgentBridge」直驱模型替代 OmniAgent dispatcher 的那层胶水：消费
``bridge.stream_response`` 产出的 :class:`BridgeEvent`，驱动打字机流式卡片，并按既定规则
处理交互——

- ``message.delta`` → 喂流式卡片打字机
- ``tool.start`` / ``tool.complete`` → 驱动"正在…"忙碌状态行
- ``clarify.request``（ask 工具）→ 收尾当前卡片后单独发 ask 询问卡片（保留的唯一交互）
- ``approval.request``（DENY / bypass-immune / 分类器 ask 等泄漏的人工审批）→ **不弹卡片**，
  飞书侧一律自动拒绝，让模型改用无需审批的方式（privileged / auto 两模式通用）
- ``error`` / 异常 / 取消 → 中止卡片并提示

只处理主 agent 事件；子代理（``parent_run_id`` 非空）的内部活动不外显。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from lumi.agents.tools.providers.ask import ASK_CANCELLED
from lumi.gateway.bridge import EventKind
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.gateway.channels.feishu.channel import FeishuChannel

# 飞书会话不支持人工工具审批：泄漏的 approval.request 一律以此理由自动拒绝。
_AUTO_REJECT = {
    "decision": "reject",
    "message": "当前会话不支持工具审批，已自动拒绝；请改用无需审批的方式完成。",
}


async def run_turn(
    channel: FeishuChannel,
    bridge,
    *,
    chat_id: str,
    thread_id: str,
    reply_to: str,
    content: str | list,
    tool_mode: str,
) -> None:
    """驱动一轮 agent run，把事件流渲染到飞书。"""
    streaming = channel.streaming
    ended = False

    async def _end(*, aborted: bool) -> None:
        # 首个终态路径收尾流式卡 buf，置 ended 让后续（含 finally 兜底）空转，不重复分发。
        nonlocal ended
        if ended:
            return
        ended = True
        await streaming.send_delta(
            chat_id,
            "",
            {"_stream_end": True, "_aborted": aborted, "message_id": reply_to},
        )

    try:
        async for evt in bridge.stream_response(content, tool_mode=tool_mode):
            if evt.parent_run_id:
                continue  # 子代理内部活动不外显
            kind = evt.kind
            if kind == EventKind.MESSAGE_DELTA:
                if evt.text:
                    await streaming.send_delta(
                        chat_id, evt.text, {"message_id": reply_to}
                    )
            elif kind == EventKind.TOOL_START:
                await streaming.send_delta(
                    chat_id,
                    "",
                    {
                        "_tool_activity": {"phase": "start", "name": evt.name},
                        "message_id": reply_to,
                    },
                )
            elif kind == EventKind.TOOL_COMPLETE:
                await streaming.send_delta(
                    chat_id,
                    "",
                    {
                        "_tool_activity": {"phase": "end", "name": evt.name},
                        "message_id": reply_to,
                    },
                )
            elif kind == EventKind.CLARIFY:
                # 飞书已禁用 ask 工具，正常不会出现 clarify。防御性兜底：直接按"取消作答"
                # 收尾，让模型自行判断后继续，避免 broker future 永挂、run-lock 永占。
                approval_id = str((evt.data or {}).get("approval_id") or "")
                if approval_id:
                    bridge.resolve_approval(approval_id, ASK_CANCELLED)
            elif kind == EventKind.APPROVAL:
                aid = str((evt.data or {}).get("approval_id") or "")
                if aid:
                    bridge.resolve_approval(aid, dict(_AUTO_REJECT))
            elif kind == EventKind.ERROR:
                await _end(aborted=True)
                await channel.send_markdown(
                    chat_id, f"⚠️ {evt.error}", reply_to=reply_to
                )
            elif kind == EventKind.TURN_COMPLETE:
                await _end(aborted=False)
    except asyncio.CancelledError:
        raise  # finally 兜底收尾
    except Exception as e:
        logger.error(f"Feishu run_turn 异常 chat={chat_id}: {e}", exc_info=True)
        await _end(aborted=True)  # 先关卡再发错误提示，保证顺序
        await channel.send_markdown(
            chat_id, "⚠️ 处理消息时出错，请稍后重试。", reply_to=reply_to
        )
    finally:
        # 兜底：未显式收尾的路径（取消 / 提前 return）在此关掉卡片，避免"生成中"冻死。
        await _end(aborted=True)
