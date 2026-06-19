"""Anthropic prompt 缓存配置。"""

CACHE_CONTROL: dict[str, str] = {"type": "ephemeral", "ttl": "5m"}
