"""把一次 agent run 的 BridgeEvent 流折叠成飞书消息。

消费 ``bridge.stream_response`` 产出的 :class:`BridgeEvent`，驱动打字机流式卡片
（:class:`FeishuStreaming`），并按既定规则处理交互——

- ``message.delta`` → 喂流式卡片打字机
- ``message.start`` / ``message.retry`` → 记正文边界 / 回滚畸形响应
- ``tool.start`` / ``tool.complete`` → 驱动"正在…"忙碌状态行
- ``clarify.request``（ask 工具，IM 会话已禁用）→ 防御性按"取消作答"收尾
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
    reply_to: str,
    content: str | list,
    message_meta: dict | None = None,
    synthetic: bool = False,
    attachments: list[str] | None = None,
    command: tuple[str, str] | None = None,
) -> None:
    """驱动一轮 agent run，把事件流渲染到飞书。

    synthetic=True 标记系统合成轮（后台任务通知），注入文本不作为用户消息呈现。
    attachments 为下载好的文件路径，交 bridge 统一注入标签块 + 写 items.files。
    command=(name, extra_text) 时走 bridge.stream_command（斜杠命令轮，content 不使用）。
    终态收尾 ``streaming.end`` 幂等：首个终态路径收尾，finally 兜底重调空转。
    """
    streaming = channel.streaming
    tool_mode = channel.config.tool_mode

    if command:
        stream = bridge.stream_command(
            command[0], command[1], tool_mode=tool_mode, message_meta=message_meta
        )
    else:
        stream = bridge.stream_response(
            content,
            tool_mode=tool_mode,
            message_meta=message_meta,
            synthetic=synthetic,
            attachments=attachments,
        )
    try:
        async for evt in stream:
            if evt.parent_run_id:
                continue  # 子代理内部活动不外显
            kind = evt.kind
            if kind == EventKind.MESSAGE_DELTA:
                await streaming.append(chat_id, evt.text, reply_to)
            elif kind == EventKind.MESSAGE_START:
                streaming.mark(chat_id)
            elif kind == EventKind.MESSAGE_RETRY:
                await streaming.reset(chat_id, reply_to)
            elif kind == EventKind.TOOL_START:
                await streaming.tool_activity(chat_id, "start", evt.name, reply_to)
            elif kind == EventKind.TOOL_COMPLETE:
                await streaming.tool_activity(chat_id, "end", evt.name, reply_to)
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
                await streaming.end(chat_id, aborted=True, reply_to=reply_to)
                await channel.send_markdown(
                    chat_id,
                    str(evt.error),
                    reply_to=reply_to,
                    title="⚠️ 出错了",
                    template="red",
                )
            elif kind == EventKind.TURN_COMPLETE:
                await streaming.end(chat_id, aborted=False, reply_to=reply_to)
    except asyncio.CancelledError:
        # /stop 硬取消：与 desktop 同一收尾——确定性关图 + 中断残留写回
        # （shield 已内置于方法），卡片上已显示的内容不再从历史里消失
        await bridge.finalize_cancelled_stream(stream)
        raise  # finally 兜底收尾
    except Exception as e:
        logger.error(f"Feishu run_turn 异常 chat={chat_id}: {e}", exc_info=True)
        await bridge.finalize_cancelled_stream(stream)  # 确定性关图，不留 GC 竞争
        # 先关卡再发错误提示，保证顺序
        await streaming.end(chat_id, aborted=True, reply_to=reply_to)
        await channel.send_markdown(
            chat_id,
            "处理消息时出错，请稍后重试。",
            reply_to=reply_to,
            title="⚠️ 出错了",
            template="red",
        )
    finally:
        # 兜底：未显式收尾的路径（取消 / 提前 return）在此关掉卡片，避免"生成中"冻死。
        await streaming.end(chat_id, aborted=True, reply_to=reply_to)
