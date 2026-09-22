"""LumiAgent 桥接层包（`lumi.gateway.bridge`）。

AgentBridge 是流式 + 会话生命周期核心；folder / 文件级 checkpoint 是其子模块，
审批富化与供应商 CRUD 是无状态函数（approval / providers）。
"""

from __future__ import annotations

from lumi.gateway.bridge.core import (
    AgentBridge,
    BridgeEvent,
    EventKind,
    build_skill_command_blocks,
    shutdown_shared_runtime,
)

__all__ = [
    "AgentBridge",
    "BridgeEvent",
    "EventKind",
    "build_skill_command_blocks",
    "shutdown_shared_runtime",
]
