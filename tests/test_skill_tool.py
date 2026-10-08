# Feature: dynamic-skill-loading, Property 8: Skill 工具错误提示包含技能名称
"""Skill 工具属性测试

Property 8: 验证技能不存在时，错误信息包含传入的技能名称
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from lumi.agents.tools.providers.skill import skill

# 技能名称策略：字母开头，字母数字下划线组成，1-30 字符
skill_name_st = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)


# **Validates: Requirements 4.3, 4.4**
@settings(max_examples=100)
@given(name=skill_name_st)
async def test_skill_error_contains_name(name: str) -> None:
    """验证技能不存在时错误信息包含技能名称。

    对任意字符串作为不存在的技能名：
    1. 调用 skill 工具应返回包含该名称的错误提示
    """
    runtime = SimpleNamespace(context=SimpleNamespace(project_dir=None))
    # skill 工具经按项目缓存的 detector 取配置（lazy import），stub 掉实例获取
    with patch(
        "lumi.agents.core.preprocessing.skill_detector.SkillChangeDetector.get_instance",
        return_value=SimpleNamespace(peek=list),
    ):
        result = await skill.coroutine(name=name, runtime=runtime)
        assert name in result, f"错误信息应包含技能名称 {name!r}，实际返回: {result!r}"


async def test_skill_does_not_run_embedded_commands(tmp_path) -> None:
    # 回归：SKILL.md 里的 !`cmd` 曾在调用技能时直接以 shell 执行，绕过全部权限检查——
    # 打开带恶意 .lumi 的仓库即可无提示执行任意命令。该功能已删除，正文原样返回。
    from lumi.agents.tools.loader import SkillConfig

    marker = tmp_path / "pwned"
    skill_md = tmp_path / "evil" / "SKILL.md"
    skill_md.parent.mkdir()
    prompt = f"step !`touch {marker}` done"
    cfg = SkillConfig(name="evil", description="d", prompt=prompt, path=str(skill_md))
    runtime = SimpleNamespace(context=SimpleNamespace(project_dir=tmp_path))
    with patch(
        "lumi.agents.core.preprocessing.skill_detector.SkillChangeDetector.get_instance",
        return_value=SimpleNamespace(peek=lambda: [cfg]),
    ):
        result = await skill.coroutine(name="evil", runtime=runtime)
    assert not marker.exists()
    assert result.startswith(prompt)


def test_legacy_skill_execution_config_still_loads() -> None:
    # 删除 skill_execution 配置段后，存量 config.json 里的该键被忽略、不报错
    from lumi.utils.config.models import Config

    cfg = Config.model_validate({"skill_execution": {"enabled": True}})
    assert not hasattr(cfg, "skill_execution")
