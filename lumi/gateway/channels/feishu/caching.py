"""懒加载缓存：缓存命中不调 API，未命中交给传入的 fetch 函数批量解析。

通用 ``K → V`` 缓存，不绑定任何具体数据源——fetch 与 fallback 由调用方
（``FeishuDirectory``）按数据源注入。缓存只在主事件循环上读写（fetch 在 executor
里跑，但只经返回值写回），无需加锁。``resolve`` 只把成功解析的写回缓存；失败的
key 用 ``fallback`` 兜底但**不**写缓存，下次仍有机会重试。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class CachingDirectory[K, V]:
    """通用缓存：prime_many 写入 + 缓存优先的批量 resolve。"""

    def __init__(self) -> None:
        self._cache: dict[K, V] = {}

    def prime_many(self, mapping: dict[K, V]) -> None:
        """批量写入映射（启动预热用）。"""
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
        missing = [k for k in unique if k not in self._cache]
        if missing:
            resolved = await asyncio.get_running_loop().run_in_executor(
                None, fetch_missing, missing
            )
            self._cache.update(resolved)
        return {k: self._cache.get(k) or fallback(k) for k in unique}
