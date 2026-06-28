"""每轮上下文块（Claude Code 式：稳定前缀，免疫截断）。

把 env / agent 列表 / skill 列表 / 记忆索引 / LUMI.md 组装成一段文本，由 ``call_model``
每轮经 ``tool_call_chain(turn_context=...)`` 作为**一条 HumanMessage 插在静态 system 之后**
（见 ``tool_call_chain`` / ``_turn_context_inserter``）——不进持久历史、不进 checkpoint。
作 human 消息（CC 同构）而非 system：① 在 ``my_trim_messages`` **之后**插入，故不被
``strategy="last"`` 截掉；② 不是连续第二条 system，避开兼容 provider 的兼容问题；
③ 静态 system 保持纯净，成为所有 provider 都能命中的独立缓存单元。

取代原先「注入进历史某条消息 + 压缩时重注入 + first_message 门控」三处分散逻辑：
「注入时机」这一维度被消除，故 skill 漏首条 / 记忆丢压缩 / detector 单例 changed 失真
一并不存在。

**确定性是缓存正确性的前提**：内容不变时本块必须逐字节一致——它在缓存前缀里，字节变则
其后历史的缓存失效。故 agent/skill 按名排序、不含任何时间/每轮变化字段；仅当内容真的
变化（写记忆 / 改 skill / 切项目）时破一次——正是该让模型看到变化的时刻。
"""

from __future__ import annotations

from typing import Any

from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector
from lumi.agents.core.preprocessing.agents import format_agent_reminder
from lumi.agents.core.preprocessing.memory import format_memory_reminder
from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector
from lumi.agents.core.preprocessing.skills import format_skill_reminder
from lumi.agents.core.preprocessing.system_info import format_system_reminder
from lumi.agents.permissions.workspace import get_authorized_directory


def _has_agent_tool(tools: list) -> bool:
    """当前 agent 是否持有 agent 工具——决定是否注入「可用 agent 列表」。"""
    return any(getattr(t, "name", None) == "agent" for t in tools)


def build_turn_context_text(memory_enabled: bool, has_agent_tool: bool) -> str:
    """组装上下文块文本。

    顺序固定：env → agent → skill → 记忆/LUMI.md；列表按名排序保证确定性
    （不依赖 glob 的文件系统顺序），便于字节稳定性断言。
    """
    parts = [format_system_reminder()]

    if has_agent_tool:
        agents = sorted(
            AgentChangeDetector.get_instance().check()[0], key=lambda a: a.name
        )
        if agents:
            parts.append(format_agent_reminder(agents))

    skills = sorted(SkillChangeDetector.get_instance().check()[0], key=lambda s: s.name)
    if skills:
        parts.append(format_skill_reminder(skills))

    mem = format_memory_reminder(get_authorized_directory(), memory_enabled)
    if mem:
        parts.append(mem)

    return "".join(parts)


def build_turn_context(runtime: Any) -> str:
    """从 runtime 组装每轮上下文块文本（env 恒在，故始终非空）。"""
    return build_turn_context_text(
        memory_enabled=runtime.context.memory_enabled,
        has_agent_tool=_has_agent_tool(runtime.context.tools),
    )
