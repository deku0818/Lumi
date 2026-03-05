"""
LLM 模型管理器

提供统一的模型创建和缓存功能，支持多种模型类型：
- ChatOpenAI: OpenAI系列模型
- ChatAnthropic: Claude系列模型
"""

import hashlib
import json
import os
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from lumi.utils.logger import logger


# 默认模型名称
def get_default_model_name() -> str:
    """延迟读取环境变量，确保 config.yaml 的 env 已注入"""
    return os.getenv("LLM_MODEL_NAME", "qwen3-max")


class ModelManager:
    """LLM模型管理器 - 负责创建、缓存和管理各种LLM实例"""

    def __init__(self):
        """初始化模型管理器"""
        self._cache: dict[str, Any] = {}
        self.anthropic_params = {"temperature": 0.6, "timeout": 120}
        self.openai_params = {
            "temperature": 0.6,
            "timeout": 120,
        }

    def _create_cache_key(self, model_name: str, **params) -> str:
        """根据模型名称和参数创建缓存键"""
        cache_data = {"model_name": model_name, **params}
        cache_str = json.dumps(cache_data, sort_keys=True)
        return hashlib.md5(cache_str.encode()).hexdigest()

    def _detect_model_type(
        self, model_name: str
    ) -> Literal["anthropic", "openai", "bedrock"]:
        """检测模型类型"""
        if not model_name:
            return "openai"
        name = model_name.lower()
        # Bedrock 模型名包含 'anthropic.claude'（如 us.anthropic.claude-*）
        if "anthropic.claude" in name:
            return "bedrock"
        # 直连 Anthropic（claude-* 或 minimax）
        if "claude" in name or "anthropic" in name or "minimax" in name:
            return "anthropic"
        return "openai"

    def create_llm(self, model_name: str = None, use_cache: bool = True, **llm_params):
        """
        创建LLM实例

        Args:
            model_name: 模型名称，如果为None则使用环境变量
            use_cache: 是否使用缓存，默认为True
            **llm_params: LLM的其他参数

        Returns:
            LLM实例 (ChatOpenAI, ChatAnthropic 或 ChatDeepSeek)
        """
        if model_name is None:
            model_name = get_default_model_name()

        # 检测模型类型
        model_type = self._detect_model_type(model_name)

        # 从配置文件读取对应模型类型的参数
        from lumi.utils.read_config import get_config

        config_params = get_config().config.llm_params.get_params_for_model_type(
            model_type
        )

        # 参数优先级: ModelManager默认 < config.yaml < 代码传参
        match model_type:
            case "anthropic" | "bedrock":
                final_params = {
                    **self.anthropic_params,
                    **config_params,
                    "model": model_name,
                }
                final_params.update(llm_params)

            case _:
                final_params = {
                    **self.openai_params,
                    **config_params,
                    "model": model_name,
                }
                final_params.update(llm_params)

        # 检查缓存
        if use_cache:
            cache_key = self._create_cache_key(model_name, **final_params)
            if cache_key in self._cache:
                logger.debug(f"从缓存中获取LLM实例: {model_name}")
                return self._cache[cache_key]

        # 创建LLM实例
        match model_type:
            case "anthropic" | "bedrock":
                logger.debug(f"创建 ChatAnthropic 模型: {model_name}")
                llm = ChatAnthropic(**final_params)
            case _:
                logger.debug(f"创建 ChatOpenAI 模型: {model_name}")
                llm = ChatOpenAI(**final_params)

        # 添加到缓存
        if use_cache:
            cache_key = self._create_cache_key(model_name, **final_params)
            self._cache[cache_key] = llm
            logger.debug(f"LLM实例已缓存，当前缓存数量: {len(self._cache)}")

        return llm

    def clear_cache(self):
        """清空LLM缓存"""
        self._cache.clear()
        logger.info("LLM缓存已清空")

    def get_cached_count(self) -> int:
        """获取当前缓存的LLM实例数量"""
        return len(self._cache)


# 全局模型管理器实例
model_manager = ModelManager()


# 导出的函数
def create_llm(model_name: str = None, use_cache: bool = True, **llm_params):
    """创建LLM实例（支持缓存）"""
    return model_manager.create_llm(model_name, use_cache, **llm_params)


def clear_llm_cache():
    """清空LLM缓存"""
    model_manager.clear_cache()


def get_cached_llm_count() -> int:
    """获取当前缓存的LLM实例数量"""
    return model_manager.get_cached_count()


def detect_model_type(model_name: str) -> Literal["anthropic", "openai", "bedrock"]:
    """检测模型类型"""
    return model_manager._detect_model_type(model_name)
