"""会话标题自动生成 — 对齐 claude-code 的 sessionTitle 机制。

第 1 条可见用户消息发出时即从消息文本生成（不等本轮跑完，几秒内上屏）；
第 3 条用户消息时用对话全文尾部再生成一次（纠正话题漂移）后定稿。触发与
写入守卫在 GatewaySession（session.py），本模块只负责素材提取与 LLM 调用。

模型来自 providers 分区的 titler 指针（resolve_pointer("titler")，未配则跟随
会话 active 模型）。结果写入 session_meta sidecar 的 auto_title 字段，展示
优先级为 手动 title > 渠道 channel_title > auto_title > 首条消息。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lumi.models.chain import structured_output
from lumi.models.provider_store import resolve_pointer
from lumi.sessions.message_text import extract_text_content, visible_user_text
from lumi.sessions.message_visibility import should_show_human_message

# 对话素材取末尾 1000 字符：话题漂移时近期内容优先（对齐 CC extractConversationText）
_TAIL_CHARS = 1000

_SYSTEM = (
    "Generate a concise title (3-7 words) that captures the main topic or goal "
    "of this conversation. The title should be clear enough that the user "
    "recognizes the conversation in a session list. Use the same language as "
    "the conversation; in English use sentence case (capitalize only the first "
    "word and proper nouns).\n\n"
    "Good examples:\n"
    '{"title": "Fix login button on mobile"}\n'
    '{"title": "修复移动端登录按钮"}\n'
    '{"title": "Add OAuth authentication"}\n\n'
    'Bad (too vague): {"title": "Code changes"}\n'
    'Bad (too long): {"title": "Investigate and fix the issue where the login '
    'button does not respond on mobile devices"}'
)


class _Title(BaseModel):
    title: str = Field(description="会话标题")


def refresh_digest(messages: list, current_text: str) -> str:
    """第 3 条可见用户消息时的标题刷新素材；未到刷新点返回空串。

    current_text 为刚发出的消息，一律按「快照尚未包含」计数与拼接——本轮刚起跑，
    快照几乎总是缺它；若碰巧已落 checkpoint，只是提早一条触发刷新、素材末尾多一段
    重复文本，均无害。反向按文本相等判重会把「消息恰好重复上一条」误判为已包含，
    悄悄推迟定稿。
    """
    users = 0
    parts: list[str] = []
    for m in messages:
        kind = getattr(m, "type", None)
        if kind == "human" and should_show_human_message(m):
            text = visible_user_text(m)
            users += bool(text)
        elif kind == "ai":
            text = extract_text_content(m.content)
        else:
            continue
        if text:
            parts.append(text)
    if users + 1 < 3:
        return ""
    parts.append(current_text)
    return "\n".join(parts)[-_TAIL_CHARS:]


async def generate_title(conversation: str) -> str:
    """根据对话素材生成标题；素材为空返回空串（模型失败向上抛，由调用方记录）。"""
    if not conversation.strip():
        return ""
    titler = resolve_pointer("titler")
    chain = structured_output(
        template="{conversation}",
        structure=_Title,
        system_prompt=_SYSTEM,
        model_name=titler.model,
        **titler.conn_kwargs(),
    )
    result: _Title = await chain.ainvoke({"conversation": conversation})
    return result.title.strip()
