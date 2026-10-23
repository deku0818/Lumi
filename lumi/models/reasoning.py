"""思考状态只回传给生成它的模型；转换发送副本，不改 checkpoint。"""

from copy import deepcopy
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

ORIGIN = "lumi_reasoning_origin"
DETAIL_DELTAS = "lumi_reasoning_details_deltas"
TOOL_SIGNATURES = "lumi_tool_signature_deltas"
REASONING_FIELDS = (
    "reasoning_content",
    "reasoning",
    "reasoning_details",
    "encrypted_content",
    DETAIL_DELTAS,
)
_BLOCKS = {"thinking", "redacted_thinking", "reasoning"}
_SIGNATURES = {
    "signature",
    "thought_signature",
    "thinkingSignature",
    "encrypted_content",
}


def _without_signatures(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_signatures(v) for v in value]
    if isinstance(value, dict):
        return {
            k: _without_signatures(v)
            if k in {"extras", "extra_content", "google", "function", "tool_calls"}
            else v
            for k, v in value.items()
            if k not in _SIGNATURES
        }
    return value


def prepare_history(
    messages: list[BaseMessage], origin: tuple[str, ...]
) -> list[BaseMessage]:
    """未标记的旧消息也按来源不明处理，正文和工具调用仍保留。"""
    result = []
    for message in messages:
        if not isinstance(message, AIMessage):
            result.append(message)
            continue
        if origin and tuple(message.response_metadata.get(ORIGIN, ())) == origin:
            result.append(message)
            continue
        content = message.content
        if isinstance(content, list):
            content = [
                b
                for b in content
                if not isinstance(b, dict) or b.get("type") not in _BLOCKS
            ]
        if not content and not message.tool_calls:
            continue
        kwargs = {
            k: v
            for k, v in message.additional_kwargs.items()
            if k
            not in (
                *REASONING_FIELDS,
                TOOL_SIGNATURES,
                "__gemini_function_call_thought_signatures__",
            )
        }
        # Responses SDK 会用 id 自动选择 previous_response_id 并裁掉此前历史；
        # 外来响应 ID 必须一并移除，否则服务端会重新载入已被本地过滤的思考。
        metadata = {
            k: v
            for k, v in message.response_metadata.items()
            if k not in {ORIGIN, "id"}
        }
        result.append(
            message.model_copy(
                update={
                    "content": _without_signatures(content),
                    "additional_kwargs": _without_signatures(kwargs),
                    "response_metadata": metadata,
                    "tool_calls": _without_signatures(message.tool_calls),
                }
            )
        )
    return result


class ReplayChatModel(BaseChatModel):
    """所有 invoke 调用共用发送投影和来源标记，包括流式聚合后的回复。"""

    lumi_origin: tuple[str, ...] = Field(default=(), exclude=True)

    def _stamp_message(self, message: AIMessage) -> AIMessage:
        return message.model_copy(
            update={
                "response_metadata": {
                    **message.response_metadata,
                    ORIGIN: self.lumi_origin,
                }
            }
        )

    def _stamp(self, result: ChatResult) -> ChatResult:
        for generation in result.generations:
            generation.message = self._stamp_message(generation.message)
        return result

    def _generate_with_cache(self, messages, *args, **kwargs) -> ChatResult:
        return self._stamp(
            super()._generate_with_cache(
                deepcopy(prepare_history(messages, self.lumi_origin)), *args, **kwargs
            )
        )

    async def _agenerate_with_cache(self, messages, *args, **kwargs) -> ChatResult:
        return self._stamp(
            await super()._agenerate_with_cache(
                deepcopy(prepare_history(messages, self.lumi_origin)), *args, **kwargs
            )
        )

    def stream(self, input, config=None, *, stop=None, **kwargs):
        messages = deepcopy(
            prepare_history(self._convert_input(input).to_messages(), self.lumi_origin)
        )
        for chunk in super().stream(messages, config, stop=stop, **kwargs):
            yield self._stamp_message(chunk)

    async def astream(self, input, config=None, *, stop=None, **kwargs):
        messages = deepcopy(
            prepare_history(self._convert_input(input).to_messages(), self.lumi_origin)
        )
        async for chunk in super().astream(messages, config, stop=stop, **kwargs):
            yield self._stamp_message(chunk)
