"""直连轮：cc 的 RelayEvent 流折叠成飞书流式卡片（与 outbound.run_turn 同构）。

复用 FeishuStreaming 的全套机制（打字机 / 工具状态行 / 超长截断 / 10 分钟续写），
cc 的 tool_use 映射到同一张工具友好动作表。卡片终态前追加灰字来源行
（``Claude Code · 会话 xxxx``）作直连身份标识——直连期卡片与 Lumi 本体一眼可分。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lumi.gateway.channels.feishu.outbound import tool_activity, turn_closer
from lumi.gateway.channels.feishu.streaming import grey
from lumi.gateway.channels.relay import binding_of, run_claude_turn, update_binding
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.gateway.channels.feishu.channel import FeishuChannel

# cc 工具名（小写后）→ streaming.TOOL_FRIENDLY_ACTIONS 的键；未列出的按小写原名
# 查表，查不到落到表的默认动作短语（"处理任务"）。
_TOOL_ALIASES = {"todowrite": "todos", "task": "agent", "notebookedit": "edit"}


def _tool_key(name: str) -> str:
    lower = name.lower()
    return _TOOL_ALIASES.get(lower, lower)


def _source_note(session_id: str) -> str:
    """卡片尾部的来源行（灰字 footer）：完整 sid，可整段复制去终端 resume 接管。

    resume 只认全 UUID（或标题），短号会报 No sessions match——别为紧凑截断它。
    """
    return "\n\n" + grey(f"⚡ Claude Code · 会话 {session_id or '—'}")


async def run_relay_turn(
    channel: FeishuChannel,
    *,
    chat_id: str,
    thread_id: str,
    reply_to: str,
    prompt: str,
) -> None:
    """驱动一轮 cc 直连，把事件流渲染到飞书（调用方已持本会话运行锁）。

    init / result 携带的 session_id 都即时落盘——每轮 ``--resume`` 派生新 sid，
    续对话恒 resume 最新；init 先于一切产出，中断也不丢续接能力。
    """
    streaming = channel.streaming
    binding = binding_of(thread_id)
    cwd = binding.get("cwd") or channel.config.workspace
    got_text = False
    _end = turn_closer(streaming, chat_id, reply_to)

    turn = run_claude_turn(
        prompt,
        cwd,
        binding.get("session_id", ""),
        model=binding.get("model", ""),
        effort=binding.get("effort", ""),
    )
    try:
        async for event in turn:
            kind = event.kind
            if kind == "delta":
                got_text = True
                await streaming.send_delta(
                    chat_id, event.text, {"message_id": reply_to}
                )
            elif kind in ("tool_start", "tool_end"):
                await tool_activity(
                    streaming,
                    chat_id,
                    reply_to,
                    kind.removeprefix("tool_"),
                    _tool_key(event.name),
                )
            elif kind == "init":
                if event.session_id:
                    update_binding(thread_id, session_id=event.session_id)
            elif kind == "result":
                # result 是 run_claude_turn 的终态事件，就地收尾。
                if event.session_id:
                    update_binding(thread_id, session_id=event.session_id)
                if event.is_error:
                    # 已流出的正文必须保住：正常关卡（flush 尾部 + 挂来源行），再发红卡。
                    # aborted=True 会跳过尾部 flush 与降级发送，等于把答案前半段扔了。
                    if got_text:
                        await streaming.send_delta(
                            chat_id,
                            _source_note(event.session_id),
                            {"message_id": reply_to},
                        )
                    await _end(aborted=not got_text)
                    await channel.send_markdown(
                        chat_id,
                        (event.text or "执行失败")[:500],
                        reply_to=reply_to,
                        title="⚠️ Claude Code 报错",
                        template="red",
                        note="/direct new 开新会话可恢复",
                    )
                else:
                    # 极端情形（无任何 delta 流出）用 result 文本兜底，再挂来源行
                    tail = "" if got_text else event.text
                    await streaming.send_delta(
                        chat_id,
                        tail + _source_note(event.session_id),
                        {"message_id": reply_to},
                    )
                    await _end(aborted=False)
    except Exception as e:
        logger.error(f"Feishu 直连轮异常 chat={chat_id}: {e}", exc_info=True)
        await _end(aborted=True)
        await channel.send_markdown(
            chat_id,
            "请稍后重试。",
            reply_to=reply_to,
            title="⚠️ 直连轮出错",
            template="red",
        )
    finally:
        # 含 /stop 取消路径：aclose 让 run_claude_turn 的 finally 终止子进程组，
        # 再兜底关卡（未显式收尾的路径），避免"生成中"冻死
        await turn.aclose()
        await _end(aborted=True)
