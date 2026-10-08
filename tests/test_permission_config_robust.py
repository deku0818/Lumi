"""权限配置容错：手写配置的畸形字段不能扩大边界，也不能让引擎构造失败。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lumi.agents.permissions.engine import PermissionEngine


@pytest.mark.parametrize("bad", ["/opt/shared", 5, [None, "", 3]])
def test_malformed_workspaces_are_dropped(tmp_path, bad):
    # 字符串被逐字符展开成 "/"、"o"…，"/" 进了边界；5 / [None] 让构造直接抛 TypeError
    project = tmp_path / "proj"
    (project / ".lumi").mkdir(parents=True)
    (project / ".lumi" / "permissions.json").write_text(json.dumps({"workspaces": bad}))
    engine = PermissionEngine(project, user_config_dir=tmp_path / "home")
    assert engine.get_boundary_violations("write", {"file_path": "/etc/passwd"}) == [
        str(Path("/etc/passwd").resolve())  # macOS 上 /etc → /private/etc
    ]


@pytest.mark.parametrize(
    ("rule", "path"),
    [
        ("write(/secrets/**)", "src/../secrets/k"),  # 相对路径带 ..
        ("write(/secrets/**)", "{proj}/src/../secrets/k"),  # 绝对路径带 ..
        ("write(**/.env)", "/tmp/elsewhere/.env"),  # 项目外路径
    ],
)
def test_deny_path_rules_see_normalized_path(tmp_path, rule, path):
    from lumi.agents.permissions.models import (
        Permission,
        PermissionConfig,
        PermissionDecision,
        PermissionRule,
    )

    project = tmp_path / "proj"
    project.mkdir()
    engine = PermissionEngine(project, user_config_dir=tmp_path / "home")
    engine._config = PermissionConfig(
        permissions=(PermissionRule(tool=rule, permission=Permission.DENY),)
    )
    file_path = path.format(proj=project)
    assert engine.evaluate("write", {"file_path": file_path}) == PermissionDecision.DENY


@pytest.mark.parametrize(
    ("home_rules", "project_rules"),
    [
        ({"deny": ["bash(curl *)"]}, {"allow": ["bash(curl *)"]}),  # 跨层 allow 盖 deny
        ({}, {"deny": ["bash(curl *)"], "ask": ["bash(curl *)"]}),  # 同文件 ask 盖 deny
    ],
)
def test_deny_cannot_be_overridden(tmp_path, home_rules, project_rules):
    from lumi.agents.permissions.models import PermissionDecision

    home = tmp_path / "home"
    home.mkdir()
    (home / "permissions.json").write_text(json.dumps({"permissions": home_rules}))
    project = tmp_path / "proj"
    (project / ".lumi").mkdir(parents=True)
    (project / ".lumi" / "permissions.json").write_text(
        json.dumps({"permissions": project_rules})
    )
    engine = PermissionEngine(project, user_config_dir=home)
    assert engine.evaluate("bash", {"command": "curl x"}) == PermissionDecision.DENY
