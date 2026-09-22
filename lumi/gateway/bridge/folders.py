"""Folder / workspace 管理：会话项目切换与本会话临时可访问目录。

临时目录状态（``extra_folders`` / 已通知快照）归本类所有；bridge 反向引用只用来
触达运行时 context（权限引擎）与项目切换的连带动作（MCP 池 / checkpoint 元数据 / shell）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.core.hooks import build_config_hooks
from lumi.agents.runtime.shell_session import get_shell_session_manager
from lumi.models import provider_store

if TYPE_CHECKING:
    from lumi.agents.permissions.engine import PermissionEngine
    from lumi.gateway.bridge.core import AgentBridge


def _enclosing_dir(path: str) -> Path | None:
    """越界路径对应的应授权目录：目录取自身，文件 / 待创建路径取最近的已存在祖先。

    取「最近的已存在祖先」而非直接 parent，是因为越界路径常常整条尾巴都还不存在
    （``write /b-dir/new/deep/x.txt``），拿不存在的 parent 去 add_folder 只会失败。
    一路走到文件系统根仍不存在则放弃——把 ``/`` 加进工作区等于关掉边界，远超出
    「放宽该目录」的授权语义。
    """
    resolved = Path(path).expanduser().resolve()
    root = Path(resolved.root)
    for candidate in (resolved, *resolved.parents):
        if candidate == root:
            return None
        if candidate.is_dir():
            return candidate
    return None


class FolderManager:
    """工作目录切换与本会话临时目录管理。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge
        # 本会话临时添加的额外可访问目录（不持久化，连接断开即失效）
        self.extra_folders: list[str] = []
        # 上次通知模型时的目录快照，用于在下一条用户消息注入增减变更提醒
        self._notified_folders: set[str] = set()

    @property
    def _engine(self) -> PermissionEngine | None:
        """本会话的权限引擎；``initialize()`` 之前为 None——此时只记账不同步引擎，
        建连后的首次 initialize 会按 extra_folders 重放。"""
        context = self._bridge._context
        return context.permission_engine if context is not None else None

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
        self._bridge._config_hooks = build_config_hooks(target)
        engine = self._engine
        if engine is not None:
            engine.rebase(target)

    def add_folder(self, path: str) -> dict:
        """临时把目录加进本会话可访问范围（仅内存，不持久化）。"""
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f"目录不存在: {target}")
        folder = str(target)
        if folder not in self.extra_folders:
            self.extra_folders.append(folder)
            engine = self._engine
            if engine is not None:
                engine.add_ephemeral_workspace(folder)
        return {"folders": list(self.extra_folders)}

    def widen_for_violations(self, violations: list[str]) -> None:
        """批准越界操作 → 把路径所在目录纳入本会话工作区，等价于替用户点「添加文件夹」。

        为什么批准必须连带放宽边界见 docs/architecture/permissions.md。
        """
        for raw in violations:
            directory = _enclosing_dir(raw)
            if directory is not None:
                self.add_folder(str(directory))

    def remove_folder(self, path: str) -> dict:
        """移除临时添加的目录。"""
        folder = str(Path(path).expanduser().resolve())
        if folder in self.extra_folders:
            self.extra_folders.remove(folder)
            engine = self._engine
            if engine is not None:
                engine.remove_ephemeral_workspace(folder)
        return {"folders": list(self.extra_folders)}

    def drain_folder_note(self) -> str:
        """对比上次通知后的额外目录增减，生成 system-reminder 文本（无变更返回空串）。

        与快照做差集：添加后又移除的目录自然抵消，不产生提醒。
        """
        current = set(self.extra_folders)
        added = [f for f in self.extra_folders if f not in self._notified_folders]
        removed = sorted(self._notified_folders - current)
        self._notified_folders = current
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
        b = self._bridge
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
