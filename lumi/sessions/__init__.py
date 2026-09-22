"""会话与历史消息领域逻辑 — 由 WS 服务端用于 list_sessions / load_history。

从 LangGraph checkpoint 派生会话列表（``session_store``）、持久化 pin/重命名等
用户标记（``session_meta``）。消息显示的纯读取逻辑（``visible_user_text`` /
``should_show_human_message``）随显示声明契约放在 ``lumi.agents.core.meta_message``。
"""
