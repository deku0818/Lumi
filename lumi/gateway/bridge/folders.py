"""Folder / workspace 管理（从 AgentBridge 拆出的职责子模块）。

folder 状态（_extra_folders / _notified_folders）仍归属 AgentBridge；本类持
bridge 反向引用，逻辑逐字照搬自原 AgentBridge。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.core.hooks import build_config_hooks
from lumi.agents.runtime.shell_session import get_shell_session_manager
from lumi.models import provider_store

if TYPE_CHECKING:
    from lumi.gateway.bridge.core import AgentBridge


class FolderManager:
    """工作目录切换与本会话临时目录管理。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    async def set_workspace(self, path: str) -> dict:
        """把本会话（bridge）的项目切到 path——项目随会话绑定。

        只 rebase 本 bridge 的权限引擎、重载本会话项目的 config hooks、更新 checkpoint
        元数据、重置本会话当前 thread 的持久 shell（原地改项目时它仍驻留旧目录）。
        **不动进程 cwd、不重建其它会话的边界、不碰进程级 hooks**——多会话各绑各项目，
        互不影响。
        """
        b = self._bridge
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f"目录不存在: {target}")
        self.rebase_workspace(target)
        b.mark_workspace_bound()
        b.retarget_mcp(target)
        # checkpoint 元数据跟随新项目（下一轮 checkpoint 用新目录）
        if b._config is not None:
            b._config.setdefault("metadata", {})["workspace_dir"] = b.workspace_dir
        # 本会话当前 thread 的持久 shell 仍驻留旧目录，关闭后惰性重建到新项目
        await get_shell_session_manager().close_session(b.current_thread_id)
        return {"workspace": str(target)}

    def rebase_workspace(self, target: Path) -> None:
        """把本 bridge 的权限引擎 + config hooks 重建到 target（会话级，不动进程）。

        engine.rebase 只重载新项目的持久化配置，会话级 ephemeral workspace
        （「添加文件夹」）存于引擎独立字段、跨 rebase 自动保留，无需重新加回。
        config hooks 与引擎独立，随项目切换重载（下一轮 _stream 注入 per-run）。
        """
        b = self._bridge
        b._config_hooks = build_config_hooks(target)
        engine = b._context.permission_engine if b._context else None
        if engine is None:
            return
        engine.rebase(target)

    def add_folder(self, path: str) -> dict:
        """临时把目录加进本会话可访问范围（仅内存，不持久化）。"""
        b = self._bridge
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f"目录不存在: {target}")
        folder = str(target)
        if folder not in b._extra_folders:
            b._extra_folders.append(folder)
            if b._context is not None and b._context.permission_engine is not None:
                b._context.permission_engine.add_ephemeral_workspace(folder)
        return {"folders": list(b._extra_folders)}

    def remove_folder(self, path: str) -> dict:
        """移除临时添加的目录。"""
        b = self._bridge
        folder = str(Path(path).expanduser().resolve())
        if folder in b._extra_folders:
            b._extra_folders.remove(folder)
            if b._context is not None and b._context.permission_engine is not None:
                b._context.permission_engine.remove_ephemeral_workspace(folder)
        return {"folders": list(b._extra_folders)}

    def drain_folder_note(self) -> str:
        """对比上次通知后的额外目录增减，生成 system-reminder 文本（无变更返回空串）。

        与快照做差集：添加后又移除的目录自然抵消，不产生提醒。
        """
        b = self._bridge
        current = set(b._extra_folders)
        added = [f for f in b._extra_folders if f not in b._notified_folders]
        removed = sorted(b._notified_folders - current)
        b._notified_folders = current
        if not added and not removed:
            return ""
        lines: list[str] = []
        if added:
            lines.append("用户已将以下目录添加到本会话可访问范围：")
            lines.extend(f"- {f}" for f in added)
        if removed:
            lines.append("用户已将以下目录从本会话可访问范围移除：")
            lines.extend(f"- {f}" for f in removed)
        return "<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>\n"

    def drain_ultra_note(self) -> str:
        """Ultra 档位开/关切换时的边沿编排提醒（缓存安全）。

        与上次通知的档位状态做差：off→ultra 注入「已开启」，ultra→off 注入「已关闭」，
        无变化返回空串——reminder 一旦前置进某轮用户消息即长驻历史，无需每轮重复。
        workflow 工具本身常驻（不增删，缓存前缀恒定）。
        """
        b = self._bridge
        # 渠道会话有档位覆盖（context.effort 非 None）时以它为准；desktop 会话为 None，
        # 回退到全局 active 的 profile 档位——保证 ultra 的 workflow 提醒与实际生效档位一致。
        override = b._context.effort
        effective = override if override is not None else provider_store.resolve().effort
        current = effective == "ultra"
        if current == b._notified_ultra:
            return ""
        b._notified_ultra = current
        body = (
            "Ultra 编排模式已开启：对实质性的多步 / 需全面覆盖 / 需多视角交叉验证的任务，"
            "优先用 workflow 工具拆解并扇出子代理；琐碎或单步任务仍直接处理，不要为其套用 workflow。"
            if current
            else "Ultra 编排模式已关闭：回到常规处理，不再主动用 workflow 编排。"
        )
        return "<system-reminder>\n" + body + "\n</system-reminder>\n"

    def add_workspace(self, directory: str) -> None:
        """持久化工作区目录到权限引擎"""
        from lumi.utils.logger import logger

        b = self._bridge
        if b._context and b._context.permission_engine:
            b._context.permission_engine.add_workspace(directory)
        else:
            logger.warning(
                "[Bridge] add_workspace 跳过: 权限引擎不可用 (dir=%s)", directory
            )
