"""文本大小度量的单一收口。

两类用途：

1. 阈值类（工具结果是否过大、read 是否超限）——用 ``text_size`` / ``content_size``
   的 **UTF-8 字节大小**衡量：又快又简单，对中英混合比"字符数 / token 数"更稳定
   （实测字节/token 跨度约 1.8×，字符/token 约 5×）。

2. 上下文窗口预算（summary 触发、trim）——硬上限真在 token。优先读模型响应的真实
   ``usage_metadata``（``context_window_tokens``），仅对其后新增消息用 ``estimate_tokens``
   按字节粗估；trim 这类需对任意子集计数的场景直接用 ``content_size`` + ``estimate_tokens``。

不再用本地 tokenizer（错配的 gpt-4 tiktoken 已移除）。
"""

from __future__ import annotations

from typing import Any

from lumi.utils.constants import IMAGE_TOKEN_ESTIMATE

# 字节 → token 粗换算比值。实测每 token 字节数：中文 ~2.8、英文/代码 ~4.7、混合 ~3.4。
# 取 3（偏向中文下界）：CJK 仅低估 ~8%，英文/代码偏高估——确保上下文预算宁可早压缩
# 也不低估导致真实 token 溢出模型窗口（溢出=API 400，比早压缩代价大）。
BYTES_PER_TOKEN = 3

# 多模态 block 在估算中的固定字节当量（绝不对 base64 计长，否则估算爆炸触发误删）。
# 沿用历史 token 估算语义（image≈IMAGE_TOKEN_ESTIMATE / document≈3000 tok）× BYTES_PER_TOKEN。
IMAGE_SIZE_BYTES = IMAGE_TOKEN_ESTIMATE * BYTES_PER_TOKEN
DOCUMENT_SIZE_BYTES = 3000 * BYTES_PER_TOKEN


def text_size(text: str) -> int:
    """文本的 UTF-8 字节大小——唯一的"大小"度量。"""
    return len(text.encode("utf-8"))


def _block_size(block: Any) -> int:
    """单个 content block 的字节大小；image/document 用固定当量（不对 base64 计长）。"""
    if isinstance(block, dict):
        t = block.get("type")
        if t == "text":
            return text_size(block.get("text", ""))
        if t in ("image", "image_url"):
            return IMAGE_SIZE_BYTES
        if t == "document":
            return DOCUMENT_SIZE_BYTES
        return text_size(str(block))
    if isinstance(block, str):
        return text_size(block)
    return text_size(str(block))


def content_size(content: Any) -> int:
    """消息 content 的字节大小：str 直接量；list[block] 逐块累加。"""
    if isinstance(content, str):
        return text_size(content)
    if isinstance(content, list):
        return sum(_block_size(b) for b in content)
    return text_size(str(content))


def estimate_tokens(size_bytes: int) -> int:
    """字节 → token 的保守估算（仅在拿不到权威 usage 时使用）。"""
    return size_bytes // BYTES_PER_TOKEN


def truncate_text_to_max_bytes(text: Any, max_bytes: int) -> str:
    """将文本截断到指定的最大 UTF-8 字节数，按字符边界切（不产生半个字符）。"""
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须大于 0")
    text_str = "" if text is None else str(text)
    encoded = text_str.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text_str
    # 在 max_bytes 处截断，可能落在多字节字符中间——用 errors="ignore" 丢弃尾部残字节
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def truncate_docs_to_max_bytes(
    docs: list[str] | str, max_bytes: int
) -> list[str] | str:
    """截取字符串或字符串列表到指定的最大 UTF-8 字节数。

    单个字符串按字节截断；列表只保留能完整放下的项目，不截断单项。
    """
    if isinstance(docs, str):
        return truncate_text_to_max_bytes(docs, max_bytes)

    truncated_items: list[str] = []
    current_bytes = 0
    for item in docs:
        item_str = item if isinstance(item, str) else str(item)
        item_bytes = text_size(item_str)
        # 单项超限直接跳过；加入当前项会超限则停止
        if item_bytes > max_bytes:
            continue
        if current_bytes + item_bytes > max_bytes:
            break
        truncated_items.append(item)
        current_bytes += item_bytes
    return truncated_items


def _usage_window_tokens(msg: Any) -> int | None:
    """从一条消息的 ``usage_metadata`` 取真实上下文窗口大小。

    langchain-anthropic 的 ``input_tokens`` 已含 cache（read + creation），故窗口 =
    ``total_tokens``（= input + output）；不可再加 cache 明细（会双重计数）。
    非 AIMessage / 无 usage 的消息返回 None。
    """
    usage = getattr(msg, "usage_metadata", None)
    if not usage:
        return None
    total = usage.get("total_tokens")
    if total is not None:
        return total
    return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)


def context_window_tokens(messages: list) -> int:
    """当前上下文窗口的 token 数（summary 触发判断用）。

    取最近一条带 usage 的消息的真实窗口，加上其后新增消息的字节估算；没有任何
    usage（首轮 / sub-agent 首调）时整体退化为字节估算。
    """
    # 从尾部反向扫一遍：累计"锚点之后"消息的字节；遇到第一条带 usage 的消息即结算。
    tail_bytes = 0
    for i in range(len(messages) - 1, -1, -1):
        win = _usage_window_tokens(messages[i])
        if win is not None:
            return win + estimate_tokens(tail_bytes)
        tail_bytes += content_size(getattr(messages[i], "content", ""))
    return estimate_tokens(tail_bytes)
