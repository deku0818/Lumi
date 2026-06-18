"""会话与历史消息领域逻辑 — 由 WS 服务端用于 list_sessions / load_history。

从 LangGraph checkpoint 派生会话列表（``session_store``）、持久化 pin/重命名等
用户标记（``session_meta``），以及消息内容的纯解析/清理/可见性判定
（``message_text`` / ``text_cleaning`` / ``message_visibility``）。无 textual 依赖，
可在 headless 服务中直接使用。
"""
