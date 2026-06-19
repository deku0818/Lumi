"""Folder / workspace 管理（从 AgentBridge 拆出的职责子模块）。

folder 状态（_extra_folders / _notified_folders）仍归属 AgentBridge；本类持
bridge 反向引用，逻辑逐字照搬自原 AgentBridge。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.core.hooks import load_hooks, reset_hooks
from lumi.agents.runtime.shell_session import get_shell_session_manager
from lumi.models import provider_store
from lumi.utils.workspace_id import get_workspace_dir

if TYPE_CHECKING:
    from lumi.gateway.bridge.core import AgentBridge


class FolderManager:
    """工作目录切换与本会话临时目录管理。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    async def set_workspace(self, path: str) -> dict:
        """切换进程级工作目录（项目切换的后端入口）。

        chdir 后系统信息注入、新建 checkpoint 的 workspace 元数据、会话列表过滤
        全部跟随新目录；所有存活 bridge 的权限边界一并重建为新目录，共享 shell
        会话重置使下一条 bash 命令在新目录启动。前端切项目后会另开新会话。
        """
        from lumi.gateway.bridge.core import _active_bridges

        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f"目录不存在: {target}")
        os.chdir(target)
        # cwd 是进程级单一状态：让每个存活 bridge 的引擎都重建到新目录，
        # 避免其它会话的引擎边界与 cwd 脱节（split-state）。各自保留本会话的临时目录。
        for bridge in list(_active_bridges):
            bridge._rebase_workspace(target)
        # hooks 是进程全局且只加载一次（_LOADED 守卫）——切项目时同步重载，
        # 否则新项目的 .lumi/hooks.json 永不生效、旧项目 hook 继续对新工作区触发。
        reset_hooks()
        load_hooks(target)
        # bash 工具共用 "default" shell 会话，仍驻留旧目录，关闭后惰性重建
        await get_shell_session_manager().close_session("default")
        return {"workspace": get_workspace_dir()}

    def rebase_workspace(self, target: Path) -> None:
        """把本 bridge 的权限引擎重建到 target，并重新挂上本会话的临时目录。

        rebase 会从新项目重载配置、丢弃内存里的临时目录，故重建后重新加回——
        既保住本会话的「添加文件夹」，又使 _notified_folders 仍与实际一致（不产生
        虚假的「已移除」提醒）。
        """
        b = self._bridge
        engine = b._context.permission_engine if b._context else None
        if engine is None:
            return
        engine.rebase(target)
        for folder in b._extra_folders:
            engine.add_ephemeral_workspace(folder)

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

    @staticmethod
    def ultra_note() -> str:
        """Ultra 档位激活时的轮内编排提醒（缓存安全）。

        active 模型档位 = ultra 时返回 system-reminder，鼓励对实质性多步任务用
        workflow 拆解；否则空串。workflow 工具本身常驻（不增删，缓存前缀恒定）。
        """
        if provider_store.resolve().effort != "ultra":
            return ""
        return (
            "<system-reminder>\n"
            "Ultra 编排模式已开启：对实质性的多步 / 需全面覆盖 / 需多视角交叉验证的任务，"
            "优先用 workflow 工具拆解并扇出子代理；琐碎或单步任务仍直接处理，不要为其套用 workflow。\n"
            "</system-reminder>\n"
        )

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
