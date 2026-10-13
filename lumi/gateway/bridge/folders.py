"""会话项目切换与 Ultra 模式提醒。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.core.graph import build_system_prompt
from lumi.agents.core.hooks import build_config_hooks
from lumi.agents.runtime.shell_session import get_shell_session_manager
from lumi.models import provider_store

if TYPE_CHECKING:
    from lumi.agents.permissions.engine import PermissionEngine
    from lumi.gateway.bridge.core import AgentBridge

# 真实用户消息 additional_kwargs 上的 marker：本轮起模型已知的 Ultra 状态
REMINDED_KEY = "lumi_reminded"


class FolderManager:
    """会话工作目录切换与 Ultra 模式提醒。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    @property
    def _engine(self) -> PermissionEngine | None:
        """本会话的权限引擎，initialize 之前为 None。"""
        context = self._bridge._context
        return context.permission_engine if context is not None else None

    async def set_workspace(self, path: str) -> dict:
        """把本会话（bridge）的项目切到 path——项目随会话绑定。

        只 rebase 本 bridge 的权限引擎、重载本会话项目的 config hooks、重建系统提示词
        （项目层 SOUL/AGENTS + 记忆目录）、更新 checkpoint 元数据、重置本会话当前
        thread 的持久 shell（原地改项目时它仍驻留旧目录）。
        **不动进程 cwd、不碰其它会话与进程级 hooks**——多会话各绑各项目，
        互不影响。
        """
        b = self._bridge
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f"目录不存在: {target}")
        self.rebase_workspace(target)
        b.mark_workspace_bound()
        b.retarget_mcp(target)
        if b._context is not None:
            b._context.system_prompt = build_system_prompt(
                target, enable_memory=b._context.memory_enabled
            )
        # checkpoint 元数据跟随新项目（下一轮 checkpoint 用新目录）
        if b._config is not None:
            b._config.setdefault("metadata", {})["workspace_dir"] = b.workspace_dir
        # 本会话当前 thread 的持久 shell 仍驻留旧目录，关闭后惰性重建到新项目
        await get_shell_session_manager().close_session(b.current_thread_id)
        return {"workspace": str(target)}

    def rebase_workspace(self, target: Path) -> None:
        """重建本会话的权限引擎和 config hooks，不改变进程 cwd。"""
        self._bridge._config_hooks = build_config_hooks(target)
        engine = self._engine
        if engine is not None:
            engine.rebase(target)

    def reminded_state(self) -> dict:
        """本轮起模型应知的 Ultra 状态（挂在注入提醒的用户消息上作 marker）。"""
        return {"ultra": self._ultra_active()}

    def ultra_note(self, known: bool) -> str:
        """Ultra 档位开/关切换时的边沿编排提醒（缓存安全）。

        与模型已知的档位状态做差：off→ultra 注入「已开启」，ultra→off 注入「已关闭」，
        无变化返回空串——reminder 一旦前置进某轮用户消息即长驻历史，无需每轮重复。
        workflow 工具本身常驻（不增删，缓存前缀恒定）。
        """
        current = self._ultra_active()
        if current == known:
            return ""
        body = (
            "Ultra 编排模式已开启：对实质性的多步 / 需全面覆盖 / 需多视角交叉验证的任务，"
            "优先用 workflow 工具拆解并扇出子代理；琐碎或单步任务仍直接处理，不要为其套用 workflow。"
            if current
            else "Ultra 编排模式已关闭：回到常规处理，不再主动用 workflow 编排。"
        )
        return "<system-reminder>\n" + body + "\n</system-reminder>\n"

    def _ultra_active(self) -> bool:
        ctx = self._bridge._context
        # 档位取值须与 call_model 逐字同链（nodes.call_model → create_llm）：显式覆盖
        # （context.effort 非 None）优先，否则按**本会话当前模型**反查 profile 档位。
        # 回退到无参 resolve()（全局 active）会在会话模型 ≠ 全局 active 时报错档位——
        # /model 切过的渠道会话、或渠道配了模型没配档位，提醒与实际生效档位就对不上。
        effective = (
            ctx.effort
            if ctx.effort is not None
            else provider_store.resolve(ctx.model_name, ctx.provider).effort
        )
        return effective == "ultra"
