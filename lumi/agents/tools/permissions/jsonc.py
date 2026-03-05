"""JSONC 解析器 - 支持 // 单行注释和 /* */ 块注释

提供 strip_jsonc_comments 和 parse_jsonc 两个函数，
用于解析带注释的 JSON 配置文件（JSONC 格式）。

注意：JSON 字符串内的注释标记不会被去除。
"""

from __future__ import annotations

import json
from typing import Any


def strip_jsonc_comments(text: str) -> str:
    """去除 JSONC 文本中的注释，保留字符串内的注释标记。

    支持两种注释格式：
    - // 单行注释（到行尾）
    - /* */ 块注释（可跨行）

    Args:
        text: 包含注释的 JSONC 文本

    Returns:
        去除注释后的纯 JSON 文本
    """
    result: list[str] = []
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]

        # 处理 JSON 字符串（双引号包裹）
        if ch == '"':
            # 收集整个字符串，包括转义字符
            result.append(ch)
            i += 1
            while i < length:
                ch = text[i]
                result.append(ch)
                if ch == "\\":
                    # 转义字符，跳过下一个字符
                    i += 1
                    if i < length:
                        result.append(text[i])
                elif ch == '"':
                    break
                i += 1
            i += 1

        # 处理 // 单行注释
        elif ch == "/" and i + 1 < length and text[i + 1] == "/":
            # 跳过到行尾
            i += 2
            while i < length and text[i] != "\n":
                i += 1

        # 处理 /* */ 块注释
        elif ch == "/" and i + 1 < length and text[i + 1] == "*":
            i += 2
            while i + 1 < length:
                if text[i] == "*" and text[i + 1] == "/":
                    i += 2
                    break
                i += 1
            else:
                # 未闭合的块注释，跳到末尾
                i = length

        else:
            result.append(ch)
            i += 1

    return "".join(result)


def parse_jsonc(text: str) -> Any:
    """解析 JSONC 格式文本为 Python 对象。

    先去除注释，再使用 json.loads 解析。

    Args:
        text: JSONC 格式文本

    Returns:
        解析后的 Python 对象（通常为 dict）

    Raises:
        json.JSONDecodeError: JSON 语法错误
    """
    stripped = strip_jsonc_comments(text)
    return json.loads(stripped)
