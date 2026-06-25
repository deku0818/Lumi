"""Summary 压缩的辅助：PTL retry / 熔断器 / 图像剥离 / API round 分组。

仅服务于 ``lumi.agents.core.nodes.summarizer`` 节点（串行拓扑
``PreprocessMessages → Summarizer → CallModel``，summary 在关键路径上，故失败需熔断
兜底、自身超长需 PTL 截头重试）；不暴露给业务代码。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import openai
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

_PTL_SUBSTRINGS = (
    "prompt is too long",
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


_MEDIA_REPLACEMENTS = {
    "image": "[image]",
    "image_url": "[image]",
    "document": "[document]",
}


def _has_media_block(content: list) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") in _MEDIA_REPLACEMENTS for b in content
    )


def strip_images_from_messages(msgs: list[BaseMessage]) -> list[BaseMessage]:
    """把 image / document block 替换为 ``[image]`` / ``[document]`` 文本占位。

    summary 不需要看图，剥掉防 summary 调用本身撞 PTL（图片单 block ~2K token）。
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
    """主入口：调 chain → PTL → 截头 → 再调，直到成功或超 ``max_retry``。

    返回 ``(response_content, ptl_retry_count)``；``response_content`` 是 raw
    AIMessage.content，调用方负责 ``extract_ainvoke_content``。
    """
    work = list(messages_to_summarize)
    attempt = 0
    while True:
        try:
            response = await chain.ainvoke(
                {"messages": work + [HumanMessage(content=prompt)]}
            )
            return response.content, attempt
        except Exception as e:
            if attempt >= max_retry or not is_ptl_error(e):
                raise
            truncated = truncate_head_for_ptl_retry(work, drop_ratio)
            if truncated is None:
                raise
            work = truncated
            attempt += 1


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
