"""线程安全的懒加载缓存：缓存命中不调 API，未命中交给传入的 fetch 函数批量解析。

通用 ``K → V`` 缓存，不绑定任何具体数据源——fetch 与 fallback 由调用方
（``FeishuDirectory``）按数据源注入。典型用法::

    cache = CachingDirectory()
    out = await cache.resolve(ids, fetch_missing, fallback)

线程安全：缓存读写加锁，允许 WebSocket 线程与事件循环线程并发读写。fetch 在
executor 里跑，避开阻塞事件循环。``resolve`` 只把成功解析的写回缓存；失败的
key 用 ``fallback`` 兜底但**不**写缓存，下次仍有机会重试。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable


class CachingDirectory[K, V]:
    """通用线程安全缓存：prime 写入 + 缓存优先的批量 resolve。"""

    def __init__(self) -> None:
        self._cache: dict[K, V] = {}
        self._lock = threading.Lock()

    def prime(self, key: K, value: V) -> None:
        """手动写入一条映射（如把 bot 自身写成"机器人"）。"""
        with self._lock:
            self._cache[key] = value

    def prime_many(self, mapping: dict[K, V]) -> None:
        """批量写入映射（启动预热 / 整群补全用）；空映射直接跳过。"""
        if not mapping:
            return
        with self._lock:
            self._cache.update(mapping)

    async def resolve(
        self,
        keys: list[K],
        fetch_missing: Callable[[list[K]], dict[K, V]],
        fallback: Callable[[K], V],
    ) -> dict[K, V]:
        """解析一批 key：未命中的交给 ``fetch_missing``（在 executor 里跑）。

        ``fetch_missing`` 成功解析的写回缓存；它没返回的 key 用 ``fallback`` 兜底
        且**不**写缓存，以便下次重试。
        """
        unique = [k for k in dict.fromkeys(keys) if k]
        if not unique:
            return {}

        with self._lock:
            missing = [k for k in unique if k not in self._cache]

        if missing:
            resolved = await asyncio.get_running_loop().run_in_executor(
                None, fetch_missing, missing
            )
            if resolved:
                with self._lock:
                    self._cache.update(resolved)

        with self._lock:
            out: dict[K, V] = {}
            for k in unique:
                hit = self._cache.get(k)
                out[k] = hit if hit is not None else fallback(k)
        return out
