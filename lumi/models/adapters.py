"""原生客户端复用 LangChain；OpenAI 兼容接口补齐思考字段的双向转换。"""

from copy import deepcopy
from urllib.parse import urlsplit

from langchain_anthropic import ChatAnthropic
from langchain_anthropic.chat_models import (
    _apply_cache_control_to_last_eligible_block,
    _collect_code_execution_tool_ids,
)
from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import Field

from lumi.models.reasoning import (
    DETAIL_DELTAS,
    REASONING_FIELDS,
    TOOL_SIGNATURES,
    ReplayChatModel,
)


def reasoning_details(fields: dict) -> list[dict]:
    """仅合并相邻同源的文本/摘要增量；加密块保持独立，Kimi 去掉流式 index。"""
    if "reasoning_details" in fields:
        result = deepcopy(fields["reasoning_details"])
    else:
        result = []
        identity = ("id", "index", "format", "signature")
        for batch in fields.get(DETAIL_DELTAS, []):
            for item in batch:
                previous = result[-1] if result else {}
                field = {
                    "reasoning.text": "text",
                    "reasoning.summary": "summary",
                    "summary": "summary",
                }.get(item.get("type"))
                conflict = any(
                    previous.get(k) not in (None, "")
                    and item.get(k) not in (None, "")
                    and previous[k] != item[k]
                    for k in identity
                )
                if not field or previous.get("type") != item.get("type") or conflict:
                    result.append(deepcopy(item))
                    continue
                text = (previous.get(field) or "") + (item.get(field) or "")
                previous.update(
                    deepcopy(
                        {
                            k: v
                            for k, v in item.items()
                            if k not in identity or v not in (None, "")
                        }
                    )
                )
                previous[field] = text
    return [
        {k: v for k, v in item.items() if k != "index"}
        if item.get("type") in {"summary", "encrypted"}
        else item
        for item in result
    ]


def _record_tool_signatures(message: AIMessage, calls: list[dict]) -> None:
    """仅保留 SDK 未处理的 Gemini 兼容签名；分批存储避免字符串被增量拼接。"""
    entries = []
    for index, call in enumerate(calls):
        signature = ((call.get("extra_content") or {}).get("google") or {}).get(
            "thought_signature"
        )
        # ID 与签名可能在不同分片，用流式 index 配对，发送时再按 ID 匹配工具。
        if call.get("id") or signature:
            entries.append(
                {
                    k: v
                    for k, v in {
                        "index": call.get("index", index),
                        "id": call.get("id"),
                        "signature": signature,
                    }.items()
                    if v is not None
                }
            )
    if entries:
        message.additional_kwargs[TOOL_SIGNATURES] = [entries]


class ReplayChatAnthropic(ReplayChatModel, ChatAnthropic):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # 官方接口由服务端自动移动断点；代理和兼容端点不假定支持顶层参数。
        if urlsplit(self.anthropic_api_url).hostname != "api.anthropic.com":
            if cache_control := payload.pop("cache_control", None):
                payload = deepcopy(payload)
                messages = payload["messages"]
                _apply_cache_control_to_last_eligible_block(
                    messages, cache_control, _collect_code_execution_tool_ids(messages)
                )
        return payload


class ReplayChatGoogle(ReplayChatModel, ChatGoogleGenerativeAI):
    pass


class DialectChatOpenAI(ReplayChatModel, ChatOpenAI):
    lumi_compatibility: str = Field(default="local", exclude=True)

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info=None
    ):
        choices = chunk.get("choices") or []
        delta = (choices[0].get("delta") or {}) if choices else {}
        # 签名单独到达时没有 function；SDK 尚未兼容该分片，补空对象供其解析。
        if any(
            c.get("extra_content") and c.get("function") is None
            for c in delta.get("tool_calls") or []
        ):
            chunk = deepcopy(chunk)
            for call in chunk["choices"][0]["delta"]["tool_calls"]:
                if call.get("extra_content") and call.get("function") is None:
                    call["function"] = {}
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen is not None and choices:
            gen.message.additional_kwargs.update(
                {
                    k: delta[k]
                    for k in REASONING_FIELDS
                    if k != "reasoning_details" and delta.get(k) is not None
                }
            )
            if details := delta.get("reasoning_details"):
                # 外层 list 隔开带 index 的块，流式聚合仅追加原始批次。
                gen.message.additional_kwargs[DETAIL_DELTAS] = [details]
            _record_tool_signatures(gen.message, delta.get("tool_calls") or [])
        return gen

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data.get("choices", [])):
            message = choice.get("message") or {}
            generation.message.additional_kwargs.update(
                {k: message[k] for k in REASONING_FIELDS if message.get(k) is not None}
            )
            _record_tool_signatures(generation.message, message.get("tool_calls") or [])
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # Responses 的 reasoning items / encrypted_content 已由原生 SDK 编解码。
        if "messages" not in payload:
            return payload
        for message, wire in zip(
            self._convert_input(input_).to_messages(), payload["messages"]
        ):
            if not isinstance(message, AIMessage):
                continue
            fields = message.additional_kwargs
            calls: dict[int, dict] = {}
            for batch in fields.get(TOOL_SIGNATURES, []):
                for item in batch:
                    calls.setdefault(item["index"], {}).update(item)
            signatures = {
                c.get("id"): c["signature"]
                for c in calls.values()
                if c.get("signature")
            }
            for call in wire.get("tool_calls", []):
                if signature := signatures.get(call.get("id")):
                    call["extra_content"] = {"google": {"thought_signature": signature}}
            if self.lumi_compatibility == "standard":
                continue
            text = fields.get("reasoning_content") or fields.get("reasoning")
            if text:
                wire[
                    "reasoning"
                    if self.lumi_compatibility == "openrouter"
                    else "reasoning_content"
                ] = text
            elif self.lumi_compatibility in {"deepseek", "kimi", "mimo"}:
                # 切模型/旧历史无原始思考时，仅满足接口的必需字段，不伪造思考。
                wire["reasoning_content"] = " "
            if self.lumi_compatibility in {
                "openrouter",
                "kimi",
                "minimax",
                "local",
            } and ("reasoning_details" in fields or DETAIL_DELTAS in fields):
                wire["reasoning_details"] = reasoning_details(fields)
            if self.lumi_compatibility == "local":
                wire.update(
                    {
                        k: fields[k]
                        for k in ("reasoning_content", "reasoning", "encrypted_content")
                        if k in fields
                    }
                )
        return payload
