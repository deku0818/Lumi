"""LumiAgent 桥接层包（`lumi.gateway.bridge`）。

公共 API 与导入路径与拆包前完全一致：AgentBridge 仍是流式 + 会话生命周期核心，
Provider CRUD / 审批富化 / checkpoint / folder 等职责拆到 service 子模块。
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
