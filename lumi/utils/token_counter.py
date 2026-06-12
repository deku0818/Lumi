import tiktoken
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from lumi.utils.constants import IMAGE_TOKEN_ESTIMATE
from lumi.utils.logger import logger

# 在模块加载时预初始化编码器，避免在异步上下文中触发阻塞调用
# tiktoken 加载 BPE 文件时会调用 tempfile.gettempdir()，后者内部调用 os.getcwd()
_encoder = tiktoken.encoding_for_model("gpt-4")


def _get_encoder():
    """获取缓存的编码器实例"""
    return _encoder


def str_token_counter(text: str) -> int:
    """
    计算单个文本的token数量。

    Args:
        text: 文本字符串

    Returns:
        int: token数量
    """
    enc = _get_encoder()
    return len(enc.encode(text))


def list_token_counter(texts: list[str]) -> list[int]:
    """
    计算多个文本的token数量。

    Args:
        texts: 文本字符串列表

    Returns:
        List[int]: 对应token数量列表
    """
    enc = _get_encoder()
    return [len(enc.encode(text)) for text in texts]


def truncate_str_to_max_tokens(text, max_tokens: int = 4096) -> str:
    """
    将字符串截断到指定的最大token数量。

    Args:
        text: 输入文本，会被转换为字符串
        max_tokens: 最大允许的token数量，默认4096

    Returns:
        str: 截断后的字符串

    Raises:
        ValueError: 当 max_tokens 小于等于 0 时抛出异常
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens 必须大于 0")

    # 确保输入是字符串类型
    if text is None:
        return ""

    text_str = str(text)

    if not text_str:
        return text_str

    enc = _get_encoder()

    # 先检查是否需要截断
    current_tokens = len(enc.encode(text_str))
    if current_tokens <= max_tokens:
        return text_str

    # 需要截断，先编码后截取前max_tokens个token
    tokens = enc.encode(text_str)
    truncated_tokens = tokens[:max_tokens]

    # 解码回字符串
    return enc.decode(truncated_tokens)


def truncate_docs_to_max_tokens(
    docs: list[str] | str, max_tokens: int = 10000
) -> list[str] | str:
    """截取字符串或字符串列表到指定的最大 token 数量。

    单个字符串按 token 截断；列表只保留能完整放下的项目，不截断单项。
    """
    if isinstance(docs, str):
        return truncate_str_to_max_tokens(docs, max_tokens)

    truncated_items = []
    current_tokens = 0
    for item in docs:
        item_tokens = str_token_counter(item if isinstance(item, str) else str(item))
        # 单项超限直接跳过；加入当前项会超限则停止
        if item_tokens > max_tokens:
            continue
        if current_tokens + item_tokens > max_tokens:
            break
        truncated_items.append(item)
        current_tokens += item_tokens

    logger.debug(
        f"内容处理完成 - 原始数量: {len(docs)}, "
        f"截取后数量: {len(truncated_items)}, "
        f"总token数: {current_tokens}/{max_tokens}"
    )
    return truncated_items


def _count_content_tokens(content) -> int:
    """计算消息 content 的 token 数（图片使用固定估算值，不对 base64 数据进行 tokenize）"""
    if isinstance(content, str):
        return str_token_counter(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    total += str_token_counter(item.get("text", ""))
                elif item.get("type") in ("image", "image_url"):
                    total += IMAGE_TOKEN_ESTIMATE
                else:
                    total += str_token_counter(str(item))
            else:
                total += str_token_counter(str(item))
        return total
    return str_token_counter(str(content))


def tiktoken_counter(messages: list[BaseMessage]) -> int:
    """估算消息列表的 token 总数

    支持 str 和多模态 list content（图片使用固定估算值 IMAGE_TOKEN_ESTIMATE）。
    """
    num_tokens = 3  # every reply is primed with <|start|>assistant<|message|>
    tokens_per_message = 3
    tokens_per_name = 1
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, ToolMessage):
            role = "tool"
        elif isinstance(msg, SystemMessage):
            role = "system"
        else:
            raise ValueError(f"Unsupported messages type {msg.__class__}")
        content_tokens = _count_content_tokens(msg.content)
        num_tokens += tokens_per_message + str_token_counter(role) + content_tokens
        if msg.name:
            num_tokens += tokens_per_name + str_token_counter(msg.name)
    return num_tokens
