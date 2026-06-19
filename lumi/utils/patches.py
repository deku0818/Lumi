"""
第三方库运行时补丁

所有 patch 函数在应用程序启动时统一调用，集中管理对第三方库的修复。
"""

from contextvars import ContextVar

from lumi.utils.logger import logger

# 每个 asyncio Task / 线程独立的缓存，避免并发流互相覆盖
_stream_cached_usage: ContextVar = ContextVar("_stream_cached_usage", default=None)


def patch_langchain_anthropic_stream_usage():
    """修复 langchain-anthropic 流式模式下 input_tokens 始终为 0 的 bug

    Bug 原因：
        Anthropic 流式 API 在 message_start 事件返回 input_tokens，
        在 message_delta 事件返回 output_tokens。
        langchain-anthropic 在 message_start 时跳过了 usage_metadata，
        仅在 message_delta 时读取，而 message_delta 不含 input_tokens，
        导致聚合后 input_tokens 永远为 0。

    修复方式：
        缓存 message_start 中的 input_tokens，在 message_delta 阶段
        检查 input_tokens 是否为 0，是则从缓存补上。
        这样既能修复 Anthropic 原生 API 的 bug，又不会在
        已正确返回 input_tokens 的兼容 API（如 MiniMax）上重复计算。

    影响版本：langchain-anthropic <= 1.3.5
    """
    try:
        from langchain_anthropic import chat_models
    except ImportError:
        logger.debug("[patch] langchain-anthropic 未安装，跳过 stream usage 补丁")
        return

    try:
        _create_usage_metadata = chat_models._create_usage_metadata
    except AttributeError:
        logger.warning(
            "[patch] langchain-anthropic 内部 API 已变更（_create_usage_metadata 缺失），"
            "stream usage 补丁未能应用。"
        )
        return

    def _apply_patch(event, stream_usage, message_chunk):
        """在 message_start 时缓存 usage，在 message_delta 时按需补上 input_tokens

        使用 ContextVar 保证每个 asyncio Task / 线程有独立的缓存，
        避免并发流（如 call_model 与 summarizer 并行）互相覆盖。
        """
        if event.type == "message_start" and stream_usage:
            usage = getattr(event.message, "usage", None)
            if usage is not None:
                _stream_cached_usage.set(usage)
        elif (
            event.type == "message_delta" and stream_usage and message_chunk is not None
        ):
            um = getattr(message_chunk, "usage_metadata", None)
            if um is not None and um.get("input_tokens", 0) == 0:
                cached = _stream_cached_usage.get(None)
                if cached is not None:
                    # 用 message_start 的完整 usage 重建 metadata（含 cache 详情）
                    input_meta = _create_usage_metadata(cached)
                    output_tokens = um.get("output_tokens", 0)
                    input_meta["output_tokens"] = output_tokens
                    input_meta["total_tokens"] = (
                        input_meta["input_tokens"] + output_tokens
                    )
                    message_chunk.usage_metadata = input_meta

    # >= 1.3.5: 实例方法在 ChatAnthropic 类上
    chat_cls = getattr(chat_models, "ChatAnthropic", None)
    if chat_cls and hasattr(chat_cls, "_make_message_chunk_from_anthropic_event"):
        original_method = chat_cls._make_message_chunk_from_anthropic_event
        if getattr(original_method, "_lumi_patched", False):
            return  # 已 patch，跳过

        def _patched_method(
            self,
            event,
            *,
            stream_usage=True,
            coerce_content_to_string,
            block_start_event=None,
        ):
            message_chunk, block_start_event = original_method(
                self,
                event,
                stream_usage=stream_usage,
                coerce_content_to_string=coerce_content_to_string,
                block_start_event=block_start_event,
            )
            _apply_patch(event, stream_usage, message_chunk)
            return message_chunk, block_start_event

        _patched_method._lumi_patched = True
        chat_cls._make_message_chunk_from_anthropic_event = _patched_method
        logger.debug(
            "[patch] langchain-anthropic stream usage_metadata 已修复（实例方法）"
        )
        return

    # <= 1.3.4: 模块级函数
    original_fn = getattr(chat_models, "_make_message_chunk_from_anthropic_event", None)
    if original_fn is not None:
        if getattr(original_fn, "_lumi_patched", False):
            return  # 已 patch，跳过

        def _patched_fn(
            event,
            *,
            stream_usage=True,
            coerce_content_to_string,
            block_start_event=None,
        ):
            message_chunk, block_start_event = original_fn(
                event,
                stream_usage=stream_usage,
                coerce_content_to_string=coerce_content_to_string,
                block_start_event=block_start_event,
            )
            _apply_patch(event, stream_usage, message_chunk)
            return message_chunk, block_start_event

        _patched_fn._lumi_patched = True
        chat_models._make_message_chunk_from_anthropic_event = _patched_fn
        logger.debug(
            "[patch] langchain-anthropic stream usage_metadata 已修复（模块函数）"
        )
        return

    logger.warning(
        "[patch] langchain-anthropic 内部 API 已变更，stream usage 补丁未能应用。"
        "input_tokens 在流式模式下可能为 0。"
    )


def apply_all():
    """应用所有补丁"""
    patch_langchain_anthropic_stream_usage()
