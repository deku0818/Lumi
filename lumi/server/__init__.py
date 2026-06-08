"""Lumi desktop 后端服务层。

把中立的 AgentBridge 暴露为 JSON-RPC over WebSocket，供 Electron / web 前端连接。
对外事件协议见 protocol.py，WS 端点见 ws.py。
"""
