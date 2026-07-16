"""Summary 压缩的辅助：PTL retry / 熔断器 / 图像剥离 / API round 分组 + 离线强制压缩。

主体服务于 ``lumi.agents.core.nodes.summarizer`` 节点（串行拓扑
``Summarizer → PreprocessMessages → CallModel``，summary 在关键路径上，故失败需熔断
兜底、自身超长需 PTL 截头重试）。文件末尾另有**离线强制压缩**入口
（``select_for_compaction`` / ``build_compacted_update``），供 ``AgentBridge.compact_thread``
/ ``/compact`` 命令 / IM 每日整理对空闲会话主动压缩，绕开节点专属的阈值门与熔断器。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import openai
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

from lumi.agents.core.preprocessing.summary import build_summary_carrier

_PTL_SUBSTRINGS = (
    "prompt is too long",
    "input is too long",  # Bedrock: "Input is too long for requested model"
    "context_length_exceeded",
    "maximum context length",
    "context window",
)


def is_ptl_error(exc: BaseException) -> bool:
    """识别 prompt-too-long 错误。

    多 provider 错误类型不统一（Anthropic / OpenAI / Bedrock 各家串都不同），
    取保守白名单：substring + (BadRequest 类型 或 400/413 状态码)。
    """
    msg = str(exc).lower()
    if not any(s in msg for s in _PTL_SUBSTRINGS):
        return False
    if isinstance(exc, (anthropic.BadRequestError, openai.BadRequestError)):
        return True
    status = getattr(exc, "status_code", None)
    return status in (400, 413)


def split_into_rounds(msgs: list[BaseMessage]) -> list[list[BaseMessage]]:
    """按 API round 分组：每个 AIMessage + 紧随其后的 ToolMessage 算一组。

    首段在第一条 AIMessage 之前的 Human/System 单独成前导组。保护工具调用配对
    完整性——截头时整组丢弃，不会留下孤立 tool_use。
    """
    if not msgs:
        return []
    rounds: list[list[BaseMessage]] = []
    cur: list[BaseMessage] = []
    for m in msgs:
        if isinstance(m, AIMessage) and cur:
            rounds.append(cur)
            cur = []
        cur.append(m)
    if cur:
        rounds.append(cur)
    return rounds


def truncate_head_for_ptl_retry(
    msgs: list[BaseMessage], drop_ratio: float
) -> list[BaseMessage] | None:
    """按 round 从头部丢弃 ``drop_ratio`` 比例的 round。

    返回 None 表示无法再截（剩 ≤1 round，再截就空了）。至少丢 1 组，至多丢
    ``len(rounds) - 1`` 组。
    """
    rounds = split_into_rounds(msgs)
    if len(rounds) < 2:
        return None
    n_drop = max(1, int(len(rounds) * drop_ratio))
    n_drop = min(n_drop, len(rounds) - 1)
    return [m for r in rounds[n_drop:] for m in r]


# CallModel 撞 PTL 反应式压缩时保留的尾部 round 数：保住进行中的工具轮。
# 配合单轮工具结果聚合上限（round_tool_max_bytes ≈ 60K token），2 个尾 round +
# system/tools + 摘要 carrier 仍在 200K 窗口内。
_PTL_KEEP_TAIL_ROUNDS = 2


def select_for_ptl_compaction(
    messages: list[BaseMessage], keep_rounds: int = _PTL_KEEP_TAIL_ROUNDS
) -> tuple[list[BaseMessage], list[BaseMessage]] | None:
    """PTL 反应式压缩选材：返回 ``(to_summarize, tail)``，不可压返回 ``None``。

    与 summarizer 节点不同，此路径可发生在工具循环中段（末条是 ToolMessage），
    故按 API round 切组、保留尾部 ``keep_rounds`` 组，其余进摘要。头部
    SystemMessage 不参与（调用方原位保留不删）。rounds ≤ keep_rounds + 1 时
    头部只剩前导组（往往就是当前用户消息），压缩有害无益，返回 None。
    """
    body = (
        messages[1:]
        if messages and isinstance(messages[0], SystemMessage)
        else list(messages)
    )
    rounds = split_into_rounds(body)
    if len(rounds) <= keep_rounds + 1:
        return None
    to_summarize = [m for r in rounds[:-keep_rounds] for m in r]
    tail = [m for r in rounds[-keep_rounds:] for m in r]
    return to_summarize, tail


_MEDIA_REPLACEMENTS = {
    "image": "[image]",
    "image_url": "[image]",
    "document": "[document]",
}


def _has_media_block(content: list) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") in _MEDIA_REPLACEMENTS for b in content
    )


def messages_have_media(msgs: list[BaseMessage]) -> bool:
    """列表中是否有任一消息含 image / document block。"""
    return any(
        isinstance(m.content, list) and _has_media_block(m.content) for m in msgs
    )


def strip_images_from_messages(msgs: list[BaseMessage]) -> list[BaseMessage]:
    """把 image / document block 替换为 ``[image]`` / ``[document]`` 文本占位。

    仅在 summary 调用撞 PTL 时作为**第一档缓解**（保全文字、只丢图，比截头损失小），
    不再无条件预剥——预剥会让 messages 偏离主循环写下的滚动缓存断点、砸掉在线
    summarizer 本可命中的热缓存读（见 :func:`summarize_with_ptl_retry`）。
    无图消息原样放行不复制；只对真有图/文档的消息做 model_copy。
    """
    result: list[BaseMessage] = []
    for m in msgs:
        if not isinstance(m.content, list) or not _has_media_block(m.content):
            result.append(m)
            continue
        new_content = [
            (
                {"type": "text", "text": _MEDIA_REPLACEMENTS[b["type"]]}
                if isinstance(b, dict) and b.get("type") in _MEDIA_REPLACEMENTS
                else b
            )
            for b in m.content
        ]
        result.append(m.model_copy(update={"content": new_content}))
    return result


async def summarize_with_ptl_retry(
    messages_to_summarize: list[BaseMessage],
    prompt: str,
    chain,
    *,
    max_retry: int,
    drop_ratio: float,
) -> tuple[object, int]:
    """主入口：调 chain → PTL →（先剥图，再截头）→ 再调，直到成功或超 ``max_retry``。

    首次尝试带原图，让在线 summarizer 命中主循环的热消息缓存；仅当撞 PTL 时才逐档
    缓解：第一档剥图（保全文字、只丢图，仅一次），仍 PTL 再按 round 截头。

    返回 ``(response_content, ptl_retry_count)``；``response_content`` 是 raw
    AIMessage.content，调用方负责 ``extract_ainvoke_content``。
    """
    work = list(messages_to_summarize)
    attempt = 0
    stripped = False
    while True:
        try:
            response = await chain.ainvoke(
                {"messages": work + [HumanMessage(content=prompt)]}
            )
            return response.content, attempt
        except Exception as e:
            if attempt >= max_retry or not is_ptl_error(e):
                raise
            if not stripped and messages_have_media(work):
                work = strip_images_from_messages(work)
                stripped = True
                attempt += 1
                continue
            truncated = truncate_head_for_ptl_retry(work, drop_ratio)
            if truncated is None:
                raise
            work = truncated
            attempt += 1


async def run_summary(
    messages: list,
    prompt: str,
    *,
    tools,
    system_prompt: str,
    model_name: str,
    max_retry: int,
    drop_ratio: float,
) -> tuple[str, int]:
    """跑一次摘要：缓存安全的 tool_call_chain → 带原图调用 → PTL 时先剥图再截头 → 提取文本。

    summarizer 节点与离线 ``AgentBridge.compact_thread`` 共用这段（缓存安全的分叉：与主对话
    相同的 system_prompt + tools 前缀复用 Prompt Caching，摘要本身不调工具）。首次带原图，
    使在线 summarizer 命中主循环写下的滚动消息缓存（字节一致才读得到）；图仅在撞 PTL 时才由
    ``summarize_with_ptl_retry`` 剥除。**不含**节点专属的熔断 / 阈值——调用方按需包裹。
    返回 ``(summary_text, ptl_retries)``。

    多模态 block 与 ``call_model`` 同法 ``message_transform``（按 provider 归一化图片
    格式）：对直连 Anthropic 是恒等（内容不变、缓存字节不受影响），对 OpenAI/Bedrock
    转成各自格式——既发得对，又与主循环发出的字节一致、同样命中缓存。
    """
    # 函数级 import 避开 compact（早被 nodes import）→ chain/response 的潜在环
    from lumi.agents.core.response import extract_ainvoke_content, message_transform
    from lumi.models.chain import tool_call_chain

    transformed: list = []
    for m in messages:
        if isinstance(m, HumanMessage) and isinstance(m.content, list):
            new_content = await message_transform(m.content, model_name=model_name)
            transformed.append(m.model_copy(update={"content": new_content}))
        else:
            transformed.append(m)

    chain = tool_call_chain(
        tools, system_prompt=system_prompt, model_name=model_name, streaming=False
    )
    raw_content, ptl_retries = await summarize_with_ptl_retry(
        transformed, prompt, chain, max_retry=max_retry, drop_ratio=drop_ratio
    )
    return extract_ainvoke_content(raw_content), ptl_retries


# ---------------------------------------------------------------------------
# 熔断器：同一 thread summary 连续失败超阈值后短暂放行 CallModel，不再重试
# ---------------------------------------------------------------------------


@dataclass
class _CircuitState:
    fail_count: int = 0
    opened_at: float | None = None


class _CircuitStore:
    """进程内 per-thread 熔断状态。

    长服务下 thread_id 会持续累积，``record`` 时顺手清理过期条目避免无限增长。
    """

    _PRUNE_EVERY = 64  # 每 N 次失败做一次扫描清理

    def __init__(self) -> None:
        self._states: dict[str, _CircuitState] = {}
        self._record_count = 0

    def is_open(self, thread_id: str, threshold: int, reset_sec: int) -> bool:
        s = self._states.get(thread_id)
        if s is None or s.opened_at is None:
            return False
        if time.time() - s.opened_at > reset_sec:
            self._states.pop(thread_id, None)
            return False
        return s.fail_count >= threshold

    def record_failure(self, thread_id: str, reset_sec: int) -> int:
        s = self._states.setdefault(thread_id, _CircuitState())
        s.fail_count += 1
        s.opened_at = time.time()
        self._record_count += 1
        if self._record_count % self._PRUNE_EVERY == 0:
            self._prune(reset_sec)
        return s.fail_count

    def reset(self, thread_id: str) -> None:
        self._states.pop(thread_id, None)

    def clear_all(self) -> None:
        self._states.clear()
        self._record_count = 0

    def _prune(self, reset_sec: int) -> None:
        now = time.time()
        expired = [
            tid
            for tid, s in self._states.items()
            if s.opened_at is None or now - s.opened_at > reset_sec
        ]
        for tid in expired:
            self._states.pop(tid, None)


_circuits = _CircuitStore()


def is_circuit_open(thread_id: str, threshold: int, reset_sec: int) -> bool:
    """同一 thread summary 连续失败 ``threshold`` 次后熔断 ``reset_sec`` 秒。"""
    return _circuits.is_open(thread_id, threshold, reset_sec)


def record_circuit_failure(thread_id: str, reset_sec: int = 600) -> int:
    """记录一次失败，返回当前 fail_count。"""
    return _circuits.record_failure(thread_id, reset_sec)


def reset_circuit(thread_id: str) -> None:
    """summary 成功后清零计数。"""
    _circuits.reset(thread_id)


def clear_all_circuits() -> None:
    """清空所有熔断状态（仅供测试 / 运维使用）。"""
    _circuits.clear_all()


# ---------------------------------------------------------------------------
# 离线强制压缩：对空闲会话的完整历史做一次压缩（供 AgentBridge.compact_thread /
# /compact 命令 / IM 每日整理调用）。与上面的 summarizer 节点共用压缩核，但绕开节点专属的
# 阈值门 / "末条必须 HumanMessage" / 熔断器——那些只在即将溢出的当轮调用里才有意义。
# ---------------------------------------------------------------------------


def select_for_compaction(messages: list) -> tuple[list, AIMessage] | None:
    """判定是否可压缩，返回 ``(to_summarize, last_ai)`` 或 ``None``。

    不设大小门——有历史就压。仅保留两条**结构性**前提（非阈值）：
    - 头部 SystemMessage 不参与、保留不动；
    - 末条须是**无 tool_calls 的干净 AIMessage**（= 已完成一轮的空闲会话），规避半截
      工具轮与压缩后的连续同角色；
    - 末条之外须至少有一条带 id 的消息可压（否则无可压缩、白跑一次摘要）。
    """
    if not messages:
        return None
    body = messages[1:] if isinstance(messages[0], SystemMessage) else messages
    if not body:
        return None
    last = body[-1]
    if not isinstance(last, AIMessage) or last.tool_calls:
        return None
    to_summarize = body[:-1]
    if not any(m.id for m in to_summarize):
        return None
    return to_summarize, last


def build_compacted_update(
    to_summarize: list, last: AIMessage, summary_text: str
) -> dict:
    """构造 ``aupdate_state`` 的 ``messages`` 更新：删除整段 body、重建为单条摘要 carrier。

    删除 ``to_summarize + last`` 全部（含末条 AI），只留 ``Human(<summary>)``。
    下一条真实用户消息到来时序列为 ``[Human(<summary>), Human(用户)]``（连续
    human 各 provider 均接受），context_inject hook 扫不到 marker 即注入全量——
    与在线 summarizer 压缩后的形态同构。头部 SystemMessage 未被删、留在原位。
    """
    carrier = build_summary_carrier(summary_text)
    removes = [RemoveMessage(id=m.id) for m in [*to_summarize, last] if m.id]
    return {"messages": [*removes, carrier]}
