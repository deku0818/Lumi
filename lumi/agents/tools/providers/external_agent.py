"""External agent 工具 —— 经 ACP 把任务委派给外部编程 agent（首发 Claude Code）。

与进程内子 agent 工具（providers/agent.py）**同形状**：backend 从「进程内 LumiAgent」
换成「进程外 ACP 子进程」。外部 agent 的每条 session/update 经 ``adispatch_custom_event``
回流——因发生在工具 callback 上下文，LangChain 自动带 parent_ids，bridge 据此映射成
子卡片事件（见 gateway/bridge/core.py 的 LUMI_ACP_EVENT 分支）。

权限（PR3）与 fs（PR4）回调尚未接入：当前 AcpClient 对外部 agent 的工具调用一律拒绝，
故本工具目前最适合分析 / 问答类任务。
"""

# 注意：本模块**不能**加 `from __future__ import annotations`（PR3 起会注入
# `runtime: ToolRuntime`，字符串化注解会破坏注入；见 registry 的加载期守卫）。

import json

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.acp import AcpClient
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# bridge 在 astream_events 里据此名把外部 agent 的 session/update 映射成子卡片事件。
LUMI_ACP_EVENT = "lumi_acp"

# 无 acp_agents.json 配置时的默认 worker：当前维护版 adapter，复用本机 Claude 登录态。
_DEFAULT_COMMAND = "npx"
_DEFAULT_ARGS = ("-y", "@agentclientprotocol/claude-agent-acp")

_DELEGATE_DESCRIPTION = """把一个完整的编程任务委派给 Claude Code（外部 agent）自主完成，返回它的最终结论。

如何调用：
- task：交给 Claude Code 的完整任务描述（它有独立上下文，需自包含）
- cwd：它的工作目录（绝对路径），每次显式指定

Claude Code 的中间过程（思考、工具调用、回复）会实时回流，在前端以子卡片展示。"""


def _content_text(content: object) -> str:
    """取 ACP content block 的文本（非文本块返回空串）。"""
    return content.text if getattr(content, "type", None) == "text" else ""


def _normalize_acp_update(update: object) -> dict | None:
    """把一条 ACP session/update 归一化成 bridge 能直接映射的 payload；无关更新返回 None。

    归一化在 agents 层完成（此处懂 ACP schema），bridge 只按 kind 映射 EventKind，
    无需 import acp schema。
    """
    kind = getattr(update, "session_update", None)
    if kind == "agent_message_chunk":
        text = _content_text(update.content)
        return {"kind": "message", "text": text} if text else None
    if kind == "agent_thought_chunk":
        text = _content_text(update.content)
        return {"kind": "thought", "text": text} if text else None
    if kind == "tool_call":  # ToolCallStart
        return {
            "kind": "tool_start",
            "name": update.title,
            "tool_call_id": update.tool_call_id,
        }
    if kind == "tool_call_update" and update.status in ("completed", "failed"):
        return {
            "kind": "tool_complete",
            "name": update.title or "",
            "tool_call_id": update.tool_call_id,
            "is_error": update.status == "failed",
        }
    return None


def _claude_code_spec() -> tuple[str, tuple[str, ...], dict | None]:
    """claude-code 的 spawn 规格：有 acp_agents.json 配置用之，否则用默认 npx adapter。"""
    cfg = _load_acp_config().get("claude-code", {})
    command = cfg.get("command")
    if not command:
        return _DEFAULT_COMMAND, _DEFAULT_ARGS, None
    return command, tuple(cfg.get("args", [])), cfg.get("env")


def _load_acp_config() -> dict:
    """加载 .lumi/acp_agents.json（与 mcp_server.json 对称）；缺失 / 损坏返回空 dict。"""
    path = get_config().acp_config_path
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("ACP 配置加载失败 %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


class DelegateInput(BaseModel):
    """delegate_to_claude 的输入参数。"""

    task: str = Field(description="交给 Claude Code 执行的完整任务描述（需自包含）")
    cwd: str = Field(description="Claude Code 的工作目录（绝对路径），每次显式指定")


@tool(description=_DELEGATE_DESCRIPTION, args_schema=DelegateInput)
async def delegate_to_claude(task: str, cwd: str) -> str:
    """经 ACP 委派任务给 Claude Code，流式回流中间过程，返回最终文本。"""
    command, args, env = _claude_code_spec()
    client = AcpClient(command, *args, env=env)

    async def on_update(session_id: str, update: object) -> None:
        payload = _normalize_acp_update(update)
        if payload is not None:
            await adispatch_custom_event(LUMI_ACP_EVENT, payload)

    result = await client.run(task, cwd=cwd, on_update=on_update)
    return result.text or f"(Claude Code 未返回文本，stop_reason={result.stop_reason})"
