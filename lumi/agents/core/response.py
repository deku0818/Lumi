import asyncio
import json

import httpx
from langchain_core.load import dumpd
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from lumi.models.manager import detect_protocol, get_default_model_name
from lumi.utils.image import download_image_as_base64
from lumi.utils.logger import logger


def extract_ainvoke_content(content) -> str:
    """从 LLM 响应中提取文本内容

    Args:
        content: LLM 响应的 content 字段，可能是 str 或 list[dict]

    Returns:
        提取的文本内容
    """
    if isinstance(content, list) and len(content) > 0:
        # 遍历查找包含 text 字段的项（跳过 thinking 等）
        for item in content:
            if isinstance(item, dict) and "text" in item:
                text_content = item.get("text", "")
                if text_content:
                    return text_content
        # 回退：取第一个元素
        first_item = content[0]
        if isinstance(first_item, dict):
            return first_item.get("text", str(first_item))
        return str(first_item)
    elif isinstance(content, str):
        return content
    else:
        logger.error(f"extract_ainvoke_content 出现未知类型: {type(content)}")
        return str(content) if content else ""


async def astream_raw_events(
    graph: CompiledStateGraph,
    state,
    config: RunnableConfig,
    context=None,
):
    """LangGraph 原始事件流式响应生成器

    直接输出 astream_events 的所有事件，使用 dumpd 序列化为 JSON。
    不做任何过滤或转换。
    """
    try:
        async for event in graph.astream_events(
            state, config, stream_mode="updates", context=context
        ):
            event_json = dumpd(event)
            yield f"data: {json.dumps(event_json, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error(f"LangGraph SSE stream error: {e}", exc_info=True)
        error_json = {"status": "error", "error": str(e)}
        yield f"data: {json.dumps(error_json, ensure_ascii=False)}\n\n"


_EXPECTED_DOWNLOAD_ERRORS = (
    httpx.HTTPStatusError,
    httpx.TimeoutException,
    httpx.ConnectError,
    ValueError,
    OSError,
)


async def message_transform(
    question: str | list[dict],
    model_name: str = None,
) -> str | list[dict]:
    """转换用户问题的 content 内容（按目标模型 provider 归一化多模态图片块）

    处理流程：
    1. 字符串直接返回
    2. 非 Anthropic 模型 → 转换为 OpenAI 图片格式
    3. Anthropic + Bedrock 模型 → URL 图片异步下载转 base64
    4. 直连 Anthropic 模型 → 保持原格式

    Args:
        question: 用户问题内容，支持 str 或 list[dict] (Anthropic content blocks)
        model_name: 模型名称，如果为 None 则从环境变量获取

    Returns:
        转换后的 content（str 或 list[dict]）
    """
    if isinstance(question, str):
        return question

    if model_name is None:
        model_name = get_default_model_name()

    # Bedrock（us.anthropic.claude-*）不支持 URL 图片，需下载转 base64
    if "anthropic.claude" in model_name.lower():
        return await _convert_content_images_for_bedrock(question)
    if detect_protocol(model_name) == "openai":
        return _convert_content_to_openai_format(question, model_name)
    # anthropic: 保持原格式
    return question


def _convert_content_to_openai_format(
    content: list[dict], model_name: str
) -> list[dict]:
    """将 Anthropic content blocks 转换为 OpenAI 格式。

    - text / image_url block: 原样保留
    - image block (url / base64): → image_url (raw URL / data URL)
    """
    converted = []

    for item in content:
        if not isinstance(item, dict):
            converted.append(item)
            continue

        item_type = item.get("type")

        if item_type == "text":
            converted.append(item)
        elif item_type == "image_url":
            converted.append(item)
        elif item_type == "image":
            source = item.get("source", {})
            if isinstance(source, dict) and source.get("type") == "url":
                converted.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": source.get("url", "")},
                    }
                )
            elif isinstance(source, dict) and source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                converted.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                )
            else:
                logger.warning(f"未知的 image source 格式: {source}")
                converted.append(item)
        else:
            converted.append(item)

    logger.debug(f"消息格式已从 Anthropic 转换为 OpenAI 格式 (模型: {model_name})")
    return converted


async def _convert_content_images_for_bedrock(
    content: list[dict],
) -> list[dict]:
    """将 content blocks 中的 URL 图片转为 base64（用于 Bedrock 模型）

    Bedrock 不支持 URL 图片源，下载失败时直接抛出异常。
    """
    # 收集需要下载的图片位置和 URL
    download_tasks: list[tuple[int, str]] = []

    for idx, item in enumerate(content):
        if (
            isinstance(item, dict)
            and item.get("type") == "image"
            and isinstance(item.get("source"), dict)
            and item["source"].get("type") == "url"
            and item["source"].get("url")
        ):
            download_tasks.append((idx, item["source"]["url"]))

    if not download_tasks:
        return content

    results = await asyncio.gather(
        *(download_image_as_base64(url) for _, url in download_tasks),
        return_exceptions=True,
    )

    # 区分预期异常和非预期异常
    converted = list(content)
    failed_urls: list[str] = []
    for (idx, url), result in zip(download_tasks, results):
        if isinstance(result, Exception):
            if not isinstance(result, _EXPECTED_DOWNLOAD_ERRORS):
                raise result
            logger.error(f"Bedrock 图片下载失败: {url} - {result}")
            failed_urls.append(url)
            continue
        converted[idx] = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": result.media_type,
                "data": result.data,
            },
        }

    if failed_urls:
        raise ValueError(
            f"Bedrock 图片下载失败（Bedrock 不支持 URL 图片源，必须转为 base64）: "
            f"{', '.join(failed_urls)}"
        )

    logger.debug(f"Bedrock 图片转换完成: {len(download_tasks)} 张图片")
    return converted
