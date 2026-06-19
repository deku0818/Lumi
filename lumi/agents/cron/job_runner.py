"""任务执行辅助：从 Agent 响应中提取纯文本输出。"""

from __future__ import annotations


def extract_output(response: dict) -> str:
    """从 Agent 响应中提取纯文本输出。"""
    messages = response.get("messages", [])
    if not messages:
        raise ValueError("Agent 响应中无消息")
    last_msg = messages[-1]
    raw_content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    if isinstance(raw_content, list):
        # 仅取 text 块，跳过 thinking 等非文本块（否则会拼出空行）
        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(raw_content)
