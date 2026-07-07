"""短哈希——内容指纹的单一实现。

context_inject 的条目 digest 与 memory 索引行的 fallback key 同属一个 marker
体系，截断长度与算法必须同源，否则同一行的 hash 口径不一致会被误判为每轮变更。
"""

from __future__ import annotations

import hashlib


def short_hash(text: str, length: int = 8) -> str:
    """文本的 sha256 十六进制摘要，截断到 ``length`` 位。"""
    return hashlib.sha256(text.encode()).hexdigest()[:length]
