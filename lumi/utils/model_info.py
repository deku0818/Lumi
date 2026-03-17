"""OpenRouter 模型信息获取

从 OpenRouter API 获取模型元数据（context_length、pricing 等），
支持模糊匹配用户输入的模型名到正确的 model id。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz

from lumi.utils.logger import logger

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_REQUEST_TIMEOUT = 10.0
_MATCH_THRESHOLD = 60


@dataclass(frozen=True)
class ModelInfo:
    """模型元数据（不可变）"""

    id: str
    context_length: int
    max_completion_tokens: int | None = None


def _match_model(models: list[dict], query: str) -> dict | None:
    """模糊匹配用户输入的模型名到 OpenRouter model id。

    匹配优先级：
    1. 精确匹配 id
    2. 尾部精确匹配（qwen3-max → qwen/qwen3-max）
    3. rapidfuzz 模糊匹配兜底

    Args:
        models: OpenRouter 返回的模型列表。
        query: 用户输入的模型名。

    Returns:
        匹配到的模型字典，未匹配返回 None。
    """
    query_lower = query.lower().strip()

    # 1. 精确匹配
    for m in models:
        if m["id"].lower() == query_lower:
            return m

    # 2. 尾部精确匹配
    for m in models:
        if m["id"].lower().endswith("/" + query_lower):
            return m

    # 3. 模糊匹配兜底
    best_score, best_model = 0.0, None
    for m in models:
        score = fuzz.token_set_ratio(query_lower, m["id"].lower())
        if score > best_score:
            best_score, best_model = score, m

    return best_model if best_score >= _MATCH_THRESHOLD else None


async def fetch_model_info(model_name: str) -> ModelInfo | None:
    """从 OpenRouter API 异步获取模型信息。

    Args:
        model_name: 用户配置的模型名（支持模糊匹配）。

    Returns:
        匹配到的 ModelInfo，请求失败或未匹配时返回 None。
    """
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(_OPENROUTER_MODELS_URL)
            resp.raise_for_status()
    except Exception:
        logger.warning("[ModelInfo] 无法连接 OpenRouter API，将使用配置文件默认值")
        return None

    models = resp.json().get("data", [])
    matched = _match_model(models, model_name)
    if not matched:
        logger.warning("[ModelInfo] 未匹配到模型 %r，将使用配置文件默认值", model_name)
        return None

    ctx_len = matched.get("context_length", 0)
    top = matched.get("top_provider") or {}
    max_comp = top.get("max_completion_tokens")

    logger.info(
        "[ModelInfo] 匹配到模型 %s (context_length=%d)",
        matched["id"],
        ctx_len,
    )
    return ModelInfo(
        id=matched["id"],
        context_length=ctx_len,
        max_completion_tokens=max_comp,
    )
