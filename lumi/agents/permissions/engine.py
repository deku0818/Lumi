"""工具权限控制系统 - 权限引擎

核心入口，协调配置加载、规则匹配、工作区边界检查。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.agents.permissions.boundary import WorkspaceBoundary
from lumi.agents.permissions.config_loader import ConfigLoader
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
)
from lumi.agents.permissions.workspace import (
    add_authorized_directory,
    set_authorized_directory,
)
from lumi.agents.tools.capability import split_compound_command
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
        # 保存用户配置目录：rebase() 切项目时需复用，否则会退回默认 ~/.lumi，
        # 把自定义目录下的用户级权限规则悄悄丢掉
        self._user_config_dir = user_config_dir
        # 会话级临时工作区（「添加文件夹」）。独立于 _config——后者会被
        # reload()/rebase() 从磁盘整体覆盖，若把 ephemeral 混进去，配置文件一变更
        # 用户本会话加的目录就被悄悄撤销。单独存字段使其跨 reload/rebase 存活。
        self._ephemeral_workspaces: list[Path] = []
        self._loader = ConfigLoader(project_dir, user_config_dir)
        self._config = self._load_config()
        # 构建工作区边界检查器并同步到 filesystem 层
        self._rebuild_boundary()

    def _load_config(self) -> PermissionConfig:
        """从磁盘加载配置；失败回退到无规则状态（所有调用返回 unmatched）。"""
        try:
            return self._loader.load()
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error("权限配置加载失败 (%s)，回退到无规则状态", e, exc_info=True)
            return PermissionConfig()

    @property
    def config(self) -> PermissionConfig:
        """当前权限配置。"""
        return self._config

    @property
    def project_dir(self) -> Path:
        """本引擎绑定的项目根目录（会话级，随 rebase 变化）。"""
        return self._project_dir

    def _rebuild_boundary(self) -> None:
        """重建工作区边界检查器并同步到 filesystem 授权目录。

        边界 = 项目根 + 配置 workspaces + 会话级 ephemeral workspaces。
        ephemeral 不存在 _config 里，故 reload()/rebase() 重载配置后仍保留。
        """
        workspace_paths = [self._project_dir]
        for ws in self._config.workspaces:
            p = Path(ws)
            if p.is_absolute():
                workspace_paths.append(p)
            else:
                workspace_paths.append(self._project_dir / p)
        workspace_paths.extend(self._ephemeral_workspaces)
        # 持久记忆目录（~/.lumi/memory/projects/<本项目>/）纳入边界：使 write/edit 能
        # 写到项目外的记忆目录（validate_path 与边界检查同时放行）。免审批另由 routing 短路。
        from lumi.agents.memory import memory_dir

        workspace_paths.append(memory_dir(self._project_dir))
        self._boundary = WorkspaceBoundary(workspace_paths)

        # 同步到 filesystem 层的授权目录列表
        set_authorized_directory(self._project_dir)
        for wp in workspace_paths[1:]:
            add_authorized_directory(wp)

    def authorized_directories(self) -> list[Path]:
        """本引擎当前授权目录（项目根在首位 + 配置 workspaces + 会话级 ephemeral）。

        供 bridge 在每轮 run 起点注入到 filesystem 层的 per-run 授权上下文，
        使同进程多会话并发时各 run 的工具按本会话边界校验路径，互不串扰。

        boundary.workspaces 属性已返回新列表（保护内部状态），直接返回即可、不再 wrap。
        """
        return self._boundary.workspaces

    def rebase(self, project_dir: Path) -> None:
        """切换项目根目录：重载新目录的权限配置并重建工作区边界。"""
        self._project_dir = project_dir.resolve()
        self._loader = ConfigLoader(self._project_dir, self._user_config_dir)
        self._config = self._load_config()
        self._rebuild_boundary()

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
                rule, tool_name, tool_args, self._project_dir
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

    def check_workspace_boundary(self, tool_name: str, tool_args: dict) -> bool:
        """工具调用是否在工作区边界内（提取不到路径视为边界内）。"""
        violations = self.get_boundary_violations(tool_name, tool_args)
        if violations:
            logger.warning(
                "[PermissionEngine] 工具 %s 超出工作区边界：%s", tool_name, violations
            )
        return not violations

    def get_boundary_violations(self, tool_name: str, tool_args: dict) -> list[str]:
        """超出工作区边界的路径列表（相对路径基于项目目录解析）。"""
        paths = self._boundary.extract_paths_from_tool_call(tool_name, tool_args)
        resolved = [p if p.is_absolute() else self._project_dir / p for p in paths]
        return [str(p) for p in resolved if not self._boundary.is_within_boundary(p)]

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

    def add_ephemeral_workspace(self, directory: str) -> None:
        """临时把目录加入工作区（仅内存，不持久化；会话级「添加文件夹」用）。

        存独立的 _ephemeral_workspaces 而非 _config.workspaces——后者会被
        reload()/rebase() 从磁盘整体覆盖，导致用户本会话添加的目录在权限配置
        文件变更（如审批「总是允许」写入 local 配置）后被悄悄撤销。
        """
        resolved = Path(directory).resolve()
        if resolved in self._ephemeral_workspaces:
            return
        self._ephemeral_workspaces.append(resolved)
        self._rebuild_boundary()

    def remove_ephemeral_workspace(self, directory: str) -> None:
        """移除临时加入的工作区目录并重建边界。"""
        resolved = Path(directory).resolve()
        if resolved not in self._ephemeral_workspaces:
            return
        self._ephemeral_workspaces.remove(resolved)
        self._rebuild_boundary()

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
