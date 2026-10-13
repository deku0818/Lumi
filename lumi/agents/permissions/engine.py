"""工具权限控制系统 - 权限引擎

核心入口，协调配置加载、规则匹配。
"""

from __future__ import annotations

from pathlib import Path

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
)
from lumi.agents.tools.capability import split_compound_command
from lumi.utils.logger import logger


class PermissionEngine:
    """权限引擎 - 协调配置加载与权限评估

    负责加载权限配置、评估工具调用权限。
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
        self._loader = ConfigLoader(project_dir, user_config_dir)
        self._config = self._loader.load()

    @property
    def config(self) -> PermissionConfig:
        """当前权限配置。"""
        return self._config

    @property
    def project_dir(self) -> Path:
        """本引擎绑定的项目根目录（会话级，随 rebase 变化）。"""
        return self._project_dir

    def rebase(self, project_dir: Path) -> None:
        """切换项目根目录：重载新目录的权限配置。"""
        self._project_dir = project_dir.resolve()
        self._loader = ConfigLoader(self._project_dir, self._user_config_dir)
        self._config = self._loader.load()

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

    def reload(self) -> None:
        """重新加载配置文件（仅在文件变更时）。"""
        if not self._loader.needs_reload():
            return

        try:
            new_config = self._loader.load()
        except Exception:
            logger.error("重新加载权限配置失败", exc_info=True)
            return

        self._config = new_config
