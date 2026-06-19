# Feature: dynamic-skill-loading, Property 8: Skill 工具错误提示包含技能名称
"""Skill 工具属性测试

Property 8: 验证技能不存在时，错误信息包含传入的技能名称
"""

from __future__ import annotations

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
    with patch("lumi.agents.tools.providers.skill.load_skills", return_value=[]):
        result = await skill.ainvoke({"name": name})
        assert name in result, f"错误信息应包含技能名称 {name!r}，实际返回: {result!r}"
