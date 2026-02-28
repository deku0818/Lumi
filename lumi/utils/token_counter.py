import tiktoken

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
