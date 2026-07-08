"""会话与历史消息领域逻辑 — 由 WS 服务端用于 list_sessions / load_history。

从 LangGraph checkpoint 派生会话列表（``session_store``）、持久化 pin/重命名等
用户标记（``session_meta``），以及消息显示的纯读取逻辑
（``message_text`` / ``message_visibility``——按 ``lumi.items`` 显示声明读取，
不解析正文标签）。无 textual 依赖，可在 headless 服务中直接使用。
"""
