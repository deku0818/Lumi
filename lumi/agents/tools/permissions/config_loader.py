"""工具权限控制系统 - 配置加载器

负责三级配置文件的发现、解析（支持 JSONC）、合并和持久化。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from lumi.agents.tools.permissions.jsonc import parse_jsonc
from lumi.agents.tools.permissions.models import (
    DEFAULT_RULES,
    Permission,
    PermissionConfig,
    PermissionRule,
)
from lumi.utils.logger import logger


def _parse_rules(raw: dict[str, Any]) -> tuple[PermissionRule, ...]:
    """将 JSON permissions 字段解析为 PermissionRule 元组。

    格式: {"allow": ["read", "bash(npm *)"], "deny": ["bash(rm -rf *)"]}

    跳过格式不正确的规则并记录警告。

    Args:
        raw: permissions 字段的原始值（字典）

    Returns:
        解析后的规则元组
    """
    if not isinstance(raw, dict):
        logger.warning("permissions 字段应为对象，已跳过: %s", type(raw).__name__)
        return ()

    rules: list[PermissionRule] = []
    for perm_str in ("allow", "deny", "ask"):
        tool_list = raw.get(perm_str, [])
        if not isinstance(tool_list, list):
            logger.warning("permissions.%s 应为数组，已跳过", perm_str)
            continue
        try:
            permission = Permission(perm_str)
        except ValueError:
            logger.warning("未知权限类型: %s", perm_str)
            continue
        for tool_expr in tool_list:
            if isinstance(tool_expr, str) and tool_expr:
                rules.append(PermissionRule(tool=tool_expr, permission=permission))
            else:
                logger.warning("跳过无效工具表达式: %s", tool_expr)

    return tuple(rules)


def _config_from_dict(data: dict[str, Any]) -> PermissionConfig:
    """从字典构建 PermissionConfig。

    Args:
        data: 解析后的 JSON 字典

    Returns:
        PermissionConfig 实例
    """
    workspaces = tuple(data.get("workspaces", []))
    raw_permissions = data.get("permissions", {})
    permissions = _parse_rules(raw_permissions) if raw_permissions else ()
    return PermissionConfig(
        workspaces=workspaces,
        permissions=permissions,
    )


def _config_to_dict(config: PermissionConfig) -> dict[str, Any]:
    """将 PermissionConfig 序列化为字典（新格式）。

    Args:
        config: 权限配置

    Returns:
        可 JSON 序列化的字典
    """
    allow_list: list[str] = []
    deny_list: list[str] = []
    ask_list: list[str] = []
    for r in config.permissions:
        if r.permission == Permission.ALLOW:
            allow_list.append(r.tool)
        elif r.permission == Permission.DENY:
            deny_list.append(r.tool)
        elif r.permission == Permission.ASK:
            ask_list.append(r.tool)

    permissions: dict[str, list[str]] = {}
    if allow_list:
        permissions["allow"] = allow_list
    if deny_list:
        permissions["deny"] = deny_list
    if ask_list:
        permissions["ask"] = ask_list

    return {
        "workspaces": list(config.workspaces),
        "permissions": permissions,
    }


def _merge_configs(configs: list[PermissionConfig]) -> PermissionConfig:
    """合并多级配置，高优先级覆盖低优先级同工具规则。

    合并策略：按优先级从低到高遍历，同一工具表达式的规则以最后出现的为准。
    最终追加 DEFAULT_RULES 中未被覆盖的规则。

    Args:
        configs: 按优先级从低到高排列的配置列表

    Returns:
        合并后的最终配置
    """
    # 使用 dict 保持插入顺序，后插入的覆盖先插入的
    rule_map: dict[str, PermissionRule] = {}
    all_workspaces: list[str] = []
    seen_workspaces: set[str] = set()

    for cfg in configs:
        for ws in cfg.workspaces:
            if ws not in seen_workspaces:
                all_workspaces.append(ws)
                seen_workspaces.add(ws)
        for rule in cfg.permissions:
            rule_map[rule.tool] = rule

    # 追加默认规则（仅当未被用户规则覆盖时）
    for rule in DEFAULT_RULES:
        if rule.tool not in rule_map:
            rule_map[rule.tool] = rule

    return PermissionConfig(
        workspaces=tuple(all_workspaces),
        permissions=tuple(rule_map.values()),
    )


class ConfigLoader:
    """权限配置加载器 - 三级配置文件加载与合并

    配置文件优先级（从低到高）：
    1. 用户全局: ~/.lumi/permissions.json
    2. 项目共享: {project}/.lumi/permissions.json
    3. 项目本地: {project}/.lumi/permissions.local.json
    """

    def __init__(
        self,
        project_dir: Path,
        user_config_dir: Path | None = None,
    ) -> None:
        """初始化配置加载器。

        Args:
            project_dir: 项目根目录
            user_config_dir: 用户配置目录，默认 ~/.lumi
        """
        self._project_dir = project_dir.resolve()
        self._user_config_dir = (
            user_config_dir.resolve() if user_config_dir else Path.home() / ".lumi"
        )

        # 三级配置文件路径（按优先级从低到高）
        self._config_paths: tuple[Path, ...] = (
            self._user_config_dir / "permissions.json",
            self._project_dir / ".lumi" / "permissions.json",
            self._project_dir / ".lumi" / "permissions.local.json",
        )

        # mtime 缓存，用于检测文件变更
        self._mtimes: dict[Path, float] = {}

    @property
    def local_config_path(self) -> Path:
        """项目本地配置文件路径。"""
        return self._config_paths[-1]

    def load(self) -> PermissionConfig:
        """加载并合并所有层级的配置，返回最终配置。

        Returns:
            合并后的 PermissionConfig
        """
        configs: list[PermissionConfig] = []
        for path in self._config_paths:
            cfg = self.load_single(path)
            if cfg is not None:
                configs.append(cfg)

        # 更新 mtime 缓存
        self._update_mtimes()

        if not configs:
            return PermissionConfig(permissions=DEFAULT_RULES)

        return _merge_configs(configs)

    def load_single(self, path: Path) -> PermissionConfig | None:
        """加载单个配置文件。

        Args:
            path: 配置文件路径

        Returns:
            解析后的 PermissionConfig，失败返回 None
        """
        if not path.exists():
            return None

        try:
            text = path.read_text(encoding="utf-8")
            data = parse_jsonc(text)
            if not isinstance(data, dict):
                logger.warning("权限配置文件格式错误（非对象）: %s", path)
                return None
            return _config_from_dict(data)
        except json.JSONDecodeError as e:
            logger.warning("权限配置文件 JSON 语法错误 %s: %s", path, e)
            return None
        except OSError as e:
            logger.warning("读取权限配置文件失败 %s: %s", path, e)
            return None

    def save_local(self, config: PermissionConfig) -> None:
        """将配置写入项目本地配置文件（原子写入）。

        目录不存在时自动创建。

        Args:
            config: 要写入的配置
        """
        target = self.local_config_path
        target.parent.mkdir(parents=True, exist_ok=True)

        data = _config_to_dict(config)
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        tmp_path: Path | None = None
        try:
            # 原子写入：先写临时文件再 rename
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=target.parent,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            tmp_path.replace(target)
        except OSError as e:
            logger.error("写入权限配置文件失败 %s: %s", target, e)
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def needs_reload(self) -> bool:
        """检查配置文件是否有变更（基于 mtime）。

        Returns:
            True 表示有文件变更，需要重新加载
        """
        for path in self._config_paths:
            try:
                current_mtime = path.stat().st_mtime
                cached_mtime = self._mtimes.get(path)
                if cached_mtime is None or current_mtime != cached_mtime:
                    return True
            except FileNotFoundError:
                if path in self._mtimes:
                    # 文件被删除
                    return True
            except OSError as e:
                logger.warning("检查配置文件变更失败 %s: %s，触发重新加载", path, e)
                return True
        return False

    def _update_mtimes(self) -> None:
        """更新 mtime 缓存。"""
        self._mtimes.clear()
        for path in self._config_paths:
            try:
                if path.exists():
                    self._mtimes[path] = path.stat().st_mtime
            except OSError as e:
                logger.warning("读取配置文件 mtime 失败 %s: %s", path, e)
