"""Lumi gateway：把 Agent 运行时暴露给各前端 channel（desktop WS / 未来 IM）。

当前承载进程级广播中枢（broadcast）。后续将吸收会话编排（GatewaySession）、
传输适配（channels/）与从 bridge 拆出的可组合服务，使新增 channel 只需实现传输。
"""
