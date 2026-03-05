"""工具权限控制系统 - 权限引擎

核心入口，协调配置加载、规则匹配、工作区边界检查。
"""

from __future__ import annotations

import os
from pathlib import Path

from lumi.agents.tools.permissions.boundary import WorkspaceBoundary
from lumi.agents.tools.permissions.config_loader import ConfigLoader
from lumi.agents.tools.permissions.matcher import RuleMatcher
from lumi.agents.tools.permissions.models import (
    Permission,
    PermissionConfig,
    PermissionDecision,
    PermissionRule,
    ToolCallInfo,
)
from lumi.agents.tools.workspace import add_authorized_directory
from lumi.utils.logger import logger


class PermissionEngine:
    """权限引擎 - 协调配置加载与权限评估

    负责加载权限配置、评估工具调用权限、管理工作区边界。
    初始化失败时回退到无规则状态（所有调用返回 unmatched）。
    """

    def __init__(
        self,
        project_dir: Path,
        user_config_dir: Path | None = None,
    ) -> None:
        """初始化权限引擎。

        Args:
            project_dir: 项目根目录
            user_config_dir: 用户配置目录，默认 ~/.lumi
        """
        self._project_dir = project_dir.resolve()

        try:
            self._loader = ConfigLoader(project_dir, user_config_dir)
            self._config = self._loader.load()
        except Exception:
            logger.error("权限引擎初始化失败，回退到无规则状态", exc_info=True)
            if not hasattr(self, "_loader"):
                self._loader = ConfigLoader(project_dir, user_config_dir)
            self._config = PermissionConfig()

        # 构建工作区边界检查器并同步到 filesystem 层
        self._rebuild_boundary()

        # 特权模式检查
        if self.is_privileged:
            logger.warning("权限引擎已启用特权模式，所有审批检查将被跳过")

    @property
    def config(self) -> PermissionConfig:
        """当前权限配置。"""
        return self._config

    @property
    def is_privileged(self) -> bool:
        """是否处于特权模式。

        通过环境变量 LUMI_PRIVILEGED=true 或配置文件 privileged: true 启用。
        """
        env_val = os.environ.get("LUMI_PRIVILEGED", "").lower()
        return self._config.privileged or env_val == "true"

    def _rebuild_boundary(self) -> None:
        """重建工作区边界检查器并同步到 filesystem 授权目录。"""
        workspace_paths = [self._project_dir]
        for ws in self._config.workspaces:
            p = Path(ws)
            if p.is_absolute():
                workspace_paths.append(p)
            else:
                workspace_paths.append(self._project_dir / p)
        self._boundary = WorkspaceBoundary(workspace_paths)

        # 同步到 filesystem 层的授权目录列表
        for wp in workspace_paths[1:]:
            add_authorized_directory(wp)

    def evaluate(self, tool_name: str, tool_args: dict) -> PermissionDecision:
        """评估单个工具调用的权限决策。

        评估顺序：先检查 deny 规则，再检查 allow 规则。
        未匹配任何规则返回 unmatched。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            权限决策结果
        """
        # 先检查 deny 规则
        for rule in self._config.permissions:
            if rule.permission == Permission.DENY:
                if RuleMatcher.match_rule(rule, tool_name, tool_args):
                    return PermissionDecision.DENY

        # 再检查 allow 规则
        for rule in self._config.permissions:
            if rule.permission == Permission.ALLOW:
                if RuleMatcher.match_rule(rule, tool_name, tool_args):
                    return PermissionDecision.ALLOW

        return PermissionDecision.UNMATCHED

    def evaluate_batch(
        self, tool_calls: list[ToolCallInfo]
    ) -> list[PermissionDecision]:
        """批量评估多个工具调用（各自独立）。

        Args:
            tool_calls: 工具调用信息列表

        Returns:
            对应的权限决策列表
        """
        return [self.evaluate(tc.name, tc.args) for tc in tool_calls]

    def check_workspace_boundary(self, tool_name: str, tool_args: dict) -> bool:
        """检查工具调用是否在工作区边界内。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            True 表示在边界内（或无法提取路径），False 表示超出边界
        """
        paths = self._boundary.extract_paths_from_tool_call(tool_name, tool_args)
        if not paths:
            # 无法提取路径，视为边界内
            return True

        for p in paths:
            # 相对路径基于项目目录解析
            resolved = p if p.is_absolute() else self._project_dir / p
            if not self._boundary.is_within_boundary(resolved):
                return False
        return True

    def get_boundary_violations(self, tool_name: str, tool_args: dict) -> list[str]:
        """获取超出工作区边界的路径列表。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            超出边界的路径字符串列表
        """
        paths = self._boundary.extract_paths_from_tool_call(tool_name, tool_args)
        violations: list[str] = []
        for p in paths:
            resolved = p if p.is_absolute() else self._project_dir / p
            if not self._boundary.is_within_boundary(resolved):
                violations.append(str(resolved))
        return violations

    def add_allow_rule(self, tool_expr: str) -> None:
        """将 allow 规则追加到项目本地配置并更新内存。

        Args:
            tool_expr: 工具表达式，如 "bash(ls -la)" 或 "bash(ls *)"
        """
        new_rule = PermissionRule(tool=tool_expr, permission=Permission.ALLOW)

        # 更新内存中的配置
        self._config = PermissionConfig(
            privileged=self._config.privileged,
            workspaces=self._config.workspaces,
            permissions=(*self._config.permissions, new_rule),
        )

        # 持久化到本地配置文件
        try:
            local_cfg = self._loader.load_single(self._loader.local_config_path)
            if local_cfg is None:
                local_cfg = PermissionConfig()
            updated = PermissionConfig(
                privileged=local_cfg.privileged,
                workspaces=local_cfg.workspaces,
                permissions=(*local_cfg.permissions, new_rule),
            )
            self._loader.save_local(updated)
        except Exception:
            logger.error(
                "持久化 allow 规则失败，规则仅保留在内存中: %s",
                tool_expr,
                exc_info=True,
            )

    def add_workspace(self, directory: str) -> None:
        """将目录添加到工作区列表并持久化。

        Args:
            directory: 目录绝对路径
        """
        if directory in self._config.workspaces:
            return

        # 更新内存
        self._config = PermissionConfig(
            privileged=self._config.privileged,
            workspaces=(*self._config.workspaces, directory),
            permissions=self._config.permissions,
        )

        # 重建边界检查器并同步到 filesystem 层
        self._rebuild_boundary()
        try:
            local_cfg = self._loader.load_single(self._loader.local_config_path)
            if local_cfg is None:
                local_cfg = PermissionConfig()
            updated = PermissionConfig(
                privileged=local_cfg.privileged,
                workspaces=(*local_cfg.workspaces, directory),
                permissions=local_cfg.permissions,
            )
            self._loader.save_local(updated)
        except Exception:
            logger.error("持久化工作区配置失败: %s", directory, exc_info=True)

    def reload(self) -> None:
        """重新加载配置文件（仅在文件变更时）。"""
        if not self._loader.needs_reload():
            return

        try:
            new_config = self._loader.load()
        except Exception:
            logger.error("重新加载权限配置失败", exc_info=True)
            return

        old_config = self._config
        self._config = new_config
        try:
            self._rebuild_boundary()
        except Exception:
            logger.error("重建工作区边界失败，回滚配置", exc_info=True)
            self._config = old_config
