"""工具权限控制系统 - 权限引擎

核心入口，协调配置加载、规则匹配、工作区边界检查。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.agents.permissions.boundary import WorkspaceBoundary
from lumi.agents.permissions.config_loader import ConfigLoader
from lumi.agents.tools.capability import split_compound_command
from lumi.agents.permissions.matcher import (
    COMMAND_ARG_KEYS,
    COMMAND_TOOLS,
    RuleMatcher,
    extract_arg,
)
from lumi.agents.permissions.models import (
    Permission,
    PermissionConfig,
    PermissionDecision,
    PermissionRule,
    ToolCallInfo,
)
from lumi.agents.permissions.workspace import (
    add_authorized_directory,
    set_authorized_directory,
)
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
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error("权限配置加载失败 (%s)，回退到无规则状态", e, exc_info=True)
            if not hasattr(self, "_loader"):
                self._loader = ConfigLoader(project_dir, user_config_dir)
            self._config = PermissionConfig()

        # 构建工作区边界检查器并同步到 filesystem 层
        self._rebuild_boundary()

    @property
    def config(self) -> PermissionConfig:
        """当前权限配置。"""
        return self._config

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
        set_authorized_directory(self._project_dir)
        for wp in workspace_paths[1:]:
            add_authorized_directory(wp)

    def evaluate(self, tool_name: str, tool_args: dict) -> PermissionDecision:
        """评估单个工具调用的权限决策。

        评估顺序：先检查 deny 规则，再检查 allow 规则。
        未匹配任何规则返回 unmatched。
        对 bash 复合命令（含 &&、||、;、|），拆分后逐个子命令评估。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            权限决策结果
        """
        # 参数验证
        if not isinstance(tool_name, str) or not tool_name:
            logger.error(f"[PermissionEngine.evaluate] tool_name 无效：{tool_name!r}")
            return PermissionDecision.UNMATCHED

        if not isinstance(tool_args, dict):
            logger.error(
                f"[PermissionEngine.evaluate] tool_args 类型异常：{type(tool_args)}"
            )
            return PermissionDecision.UNMATCHED

        if self._config is None:
            logger.warning("[PermissionEngine.evaluate] 配置未加载，返回 UNMATCHED")
            return PermissionDecision.UNMATCHED

        # bash 复合命令：拆分后逐个子命令评估，取最严格结果
        if tool_name in COMMAND_TOOLS:
            command = extract_arg(tool_args, COMMAND_ARG_KEYS)
            if command:
                subs = split_compound_command(command)
                if len(subs) > 1:
                    return self._evaluate_compound(tool_name, subs)

        return self._evaluate_single(tool_name, tool_args)

    # 权限严格度：数值越小越严格
    _STRICTNESS: dict[Permission, int] = {
        Permission.DENY: 0,
        Permission.ASK: 1,
        Permission.ALLOW: 2,
    }
    _TO_DECISION: dict[Permission, PermissionDecision] = {
        Permission.DENY: PermissionDecision.DENY,
        Permission.ASK: PermissionDecision.ASK,
        Permission.ALLOW: PermissionDecision.ALLOW,
    }

    def _evaluate_single(self, tool_name: str, tool_args: dict) -> PermissionDecision:
        """评估单条命令（不拆分复合命令）。

        单次遍历规则列表，取最严格的匹配结果。
        优先级：deny > ask > allow > unmatched。
        """
        best_priority = 3  # UNMATCHED 哨兵值
        best_decision = PermissionDecision.UNMATCHED

        for rule in self._config.permissions:
            priority = self._STRICTNESS[rule.permission]
            if priority < best_priority and RuleMatcher.match_rule(
                rule, tool_name, tool_args
            ):
                best_priority = priority
                best_decision = self._TO_DECISION[rule.permission]
                if best_priority == 0:  # DENY: 不可能更严格，立即短路
                    return PermissionDecision.DENY

        return best_decision

    def _evaluate_compound(
        self, tool_name: str, sub_commands: list[str]
    ) -> PermissionDecision:
        """评估复合命令：逐个子命令评估，取最严格结果。

        严格度：DENY > ASK > UNMATCHED > ALLOW。
        ANY deny → DENY；ANY ask → ASK；ANY unmatched → UNMATCHED；ALL allow → ALLOW。
        """
        has_unmatched = False
        has_ask = False
        for sub in sub_commands:
            decision = self._evaluate_single(tool_name, {"command": sub})
            if decision == PermissionDecision.DENY:
                return PermissionDecision.DENY
            if decision == PermissionDecision.ASK:
                has_ask = True
            elif decision == PermissionDecision.UNMATCHED:
                has_unmatched = True

        if has_ask:
            return PermissionDecision.ASK
        if has_unmatched:
            return PermissionDecision.UNMATCHED
        return PermissionDecision.ALLOW

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
        # 边界检查器未初始化时保守拒绝
        if self._boundary is None:
            logger.error(f"[PermissionEngine] 边界检查器未初始化，拒绝工具 {tool_name}")
            return False

        try:
            paths = self._boundary.extract_paths_from_tool_call(tool_name, tool_args)
        except Exception as e:
            logger.error(
                f"[PermissionEngine] 工具 {tool_name} 路径提取失败：{e}",
                exc_info=True,
            )
            return False  # 保守策略：无法提取路径时拒绝执行

        if not paths:
            # 无法提取路径，记录调试信息但视为边界内
            logger.debug(f"[PermissionEngine] 工具 {tool_name} 未包含可提取的路径参数")
            return True

        for p in paths:
            try:
                # 相对路径基于项目目录解析
                resolved = p if p.is_absolute() else self._project_dir / p
                if not self._boundary.is_within_boundary(resolved):
                    logger.warning(
                        f"[PermissionEngine] 工具 {tool_name} 超出工作区边界：{resolved}"
                    )
                    return False
            except Exception as e:
                logger.error(
                    f"[PermissionEngine] 工具 {tool_name} 边界检查异常 (路径：{p}): {e}",
                    exc_info=True,
                )
                return False  # 保守策略：检查异常时拒绝执行

        return True

    def get_boundary_violations(self, tool_name: str, tool_args: dict) -> list[str]:
        """获取超出工作区边界的路径列表。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            超出边界的路径字符串列表
        """
        try:
            paths = self._boundary.extract_paths_from_tool_call(tool_name, tool_args)
        except Exception as e:
            logger.error(
                "[PermissionEngine] get_boundary_violations 路径提取失败 (%s): %s",
                tool_name,
                e,
                exc_info=True,
            )
            return []

        violations: list[str] = []
        for p in paths:
            try:
                resolved = p if p.is_absolute() else self._project_dir / p
                if not self._boundary.is_within_boundary(resolved):
                    violations.append(str(resolved))
            except Exception as e:
                logger.error(
                    "[PermissionEngine] 边界检查异常 (路径: %s): %s",
                    p,
                    e,
                    exc_info=True,
                )
        return violations

    def add_allow_rule(self, tool_expr: str) -> None:
        """将 allow 规则追加到项目本地配置并更新内存。

        已存在相同表达式的 allow 规则时跳过，避免重复。

        Args:
            tool_expr: 工具表达式，如 "bash(ls -la)" 或 "bash(ls *)"
        """
        # 去重：内存中已有相同 allow 规则则跳过
        for rule in self._config.permissions:
            if rule.tool == tool_expr and rule.permission == Permission.ALLOW:
                return

        new_rule = PermissionRule(tool=tool_expr, permission=Permission.ALLOW)

        # 更新内存中的配置
        self._config = PermissionConfig(
            workspaces=self._config.workspaces,
            permissions=(*self._config.permissions, new_rule),
        )

        # 持久化到本地配置文件
        try:
            local_cfg = self._loader.load_single(self._loader.local_config_path)
            if local_cfg is None:
                local_cfg = PermissionConfig()
            # 文件中也做去重检查
            existing = {
                r.tool
                for r in local_cfg.permissions
                if r.permission == Permission.ALLOW
            }
            if tool_expr in existing:
                return
            updated = PermissionConfig(
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
                workspaces=(*local_cfg.workspaces, directory),
                permissions=local_cfg.permissions,
            )
            self._loader.save_local(updated)
        except Exception:
            logger.error("持久化工作区配置失败: %s", directory, exc_info=True)

    def add_ephemeral_rules(self, allow_exprs: list[str]) -> None:
        """添加临时 allow 规则（仅内存，不持久化）。

        用于 CLI --allow 参数传入的会话级规则。

        Args:
            allow_exprs: 工具表达式列表，如 ["bash(npm *)", "edit"]
        """
        new_rules = []
        existing = {
            r.tool for r in self._config.permissions if r.permission == Permission.ALLOW
        }
        for expr in allow_exprs:
            if expr and expr not in existing:
                new_rules.append(PermissionRule(tool=expr, permission=Permission.ALLOW))
        if new_rules:
            self._config = PermissionConfig(
                workspaces=self._config.workspaces,
                permissions=(*self._config.permissions, *new_rules),
            )

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
