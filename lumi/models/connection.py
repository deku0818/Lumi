"""供应商显式接口配置。"""

COMPATIBILITY = {
    "openai": {
        "standard",
        "qwen",
        "deepseek",
        "kimi",
        "glm",
        "mimo",
        "minimax",
        "openrouter",
        "local",
    },
    "responses": {"standard", "openrouter"},
    "anthropic": {"claude", "minimax", "compatible"},
    "gemini": {"gemini"},
}


def validate_connection(protocol: str, compatibility: str) -> None:
    if protocol not in COMPATIBILITY or compatibility not in COMPATIBILITY[protocol]:
        raise ValueError("接口类型与兼容规则不匹配")
