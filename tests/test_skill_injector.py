# Feature: dynamic-skill-loading, Property 3: System-reminder 格式完整性
# Feature: dynamic-skill-loading, Property 4: 消息注入保持原始内容不变
"""SkillInjector 属性测试

Property 3: 验证 format_skill_reminder() 的输出格式完整性
Property 4: 验证 inject_skills_into_message() 注入后原始内容不变
"""

from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import HumanMessage

from lumi.agents.core.preprocessing.skills import (
    format_skill_reminder,
    inject_skills_into_message,
)
from lumi.agents.tools.loader import SkillConfig

# --- 共用策略定义 ---

# 技能名称策略：字母数字下划线，不含冒号和换行
skill_name_st = st.from_regex(r"[a-z][a-z0-9_]{1,20}", fullmatch=True)

# 技能描述策略：可打印字符，不含换行，且首尾无空白（确保 round-trip 一致性）
skill_description_st = (
    st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "Z", "P"),
            blacklist_characters="\n\r",
        ),
        min_size=1,
        max_size=80,
    )
    .map(lambda s: s.strip())
    .filter(lambda s: len(s) > 0)
)

# prompt 策略
skill_prompt_st = st.text(
    alphabet=st.characters(categories=("L", "N", "Z")),
    min_size=1,
    max_size=100,
)


def _build_skill_config(name: str, description: str, prompt: str) -> SkillConfig:
    """构建 SkillConfig 实例。"""
    return SkillConfig(name=name, description=description, prompt=prompt)


# 单个技能策略
skill_config_st = st.builds(
    _build_skill_config,
    name=skill_name_st,
    description=skill_description_st,
    prompt=skill_prompt_st,
)

# 非空技能列表策略（名称唯一）
skill_config_list_st = (
    st.lists(skill_config_st, min_size=1, max_size=8)
    .map(lambda configs: list({c.name: c for c in configs}.values()))
    .filter(lambda configs: len(configs) >= 1)
)

# --- Property 4 策略定义 ---

# 纯文本 content 策略
_text_content_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Z", "P"),
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=100,
)

# 单个 text content block 策略
_text_block_st = _text_content_st.map(lambda t: {"type": "text", "text": t})

# 列表形式的 content 策略（1~5 个 text block）
_list_content_st = st.lists(_text_block_st, min_size=1, max_size=5)

# HumanMessage 策略：content 为字符串或列表
_human_message_st = st.one_of(
    _text_content_st.map(lambda t: HumanMessage(content=t)),
    _list_content_st.map(lambda blocks: HumanMessage(content=blocks)),
)


# --- Property 3 属性测试 ---


# **Validates: Requirements 2.1, 2.3, 5.1, 5.2, 5.3**
@settings(max_examples=100)
@given(skills=skill_config_list_st)
def test_system_reminder_format_completeness(skills: list[SkillConfig]) -> None:
    """验证 format_skill_reminder 输出的格式完整性。

    对任意非空 SkillConfig 列表：
    1. 输出以 <system-reminder> 开头
    2. 输出以 </system-reminder> 结尾
    3. 输出包含 SYSTEM_REMINDER_HEADER
    4. 对每个技能，输出包含 `- {name}: {description}`
    """
    result = format_skill_reminder(skills)

    # 1. 以 <system-reminder> 开头
    assert result.startswith("<system-reminder>"), (
        f"输出应以 <system-reminder> 开头，实际开头: {result[:40]!r}"
    )

    # 2. 以 </system-reminder> 结尾（允许尾部换行）
    assert result.rstrip().endswith("</system-reminder>"), (
        f"输出应以 </system-reminder> 结尾，实际结尾: {result[-40:]!r}"
    )

    # 3. 包含固定说明文本头
    assert "以下技能可用于 skill 工具:" in result, (
        "输出应包含说明文本头: '以下技能可用于 skill 工具:'"
    )

    # 4. 对每个技能，包含 `- name: description`
    for skill in skills:
        expected_entry = f"- {skill.name}: {skill.description}"
        assert expected_entry in result, (
            f"输出应包含技能条目 {expected_entry!r}，实际输出:\n{result}"
        )


# --- Property 4 属性测试 ---


# **Validates: Requirements 2.2, 2.5**
@settings(max_examples=100)
@given(message=_human_message_st, skills=skill_config_list_st)
def test_inject_prepends_reminder_and_preserves_original(
    message: HumanMessage,
    skills: list[SkillConfig],
) -> None:
    """验证消息注入在原始内容前插入 system-reminder 且保持原始内容不变。

    对任意 HumanMessage 和非空技能列表：
    1. 原始消息的 content 在注入后不被修改
    2. 新消息第一个 block 是包含 <system-reminder> 的 text block
    3. 新消息后续 block 与原始 content block 一致（作为后缀保留）
    4. 只有第一个 block 包含 system-reminder
    """
    # 深拷贝原始消息用于后续比较
    original_content = copy.deepcopy(message.content)

    # 执行注入
    new_message = inject_skills_into_message(message, skills)

    # 1. 原始消息 content 未被修改
    assert message.content == original_content, "原始消息的 content 不应被修改"

    # 将原始 content 统一为 block 列表形式以便比较
    if isinstance(original_content, str):
        expected_suffix = [{"type": "text", "text": original_content}]
    else:
        expected_suffix = list(original_content)

    new_blocks = new_message.content
    assert isinstance(new_blocks, list), "注入后 content 应为列表"

    # 2. 第一个 block 是 system-reminder
    suffix_len = len(expected_suffix)
    assert len(new_blocks) == suffix_len + 1, (
        f"新消息应有 {suffix_len + 1} 个 block（1 reminder + 原始 {suffix_len}），"
        f"实际 {len(new_blocks)}"
    )
    prepended_block = new_blocks[0]
    assert isinstance(prepended_block, dict), "插入的 block 应为字典"
    assert prepended_block.get("type") == "text", "插入的 block type 应为 text"
    assert "<system-reminder>" in prepended_block.get("text", ""), (
        "第一个 block 应包含 <system-reminder>"
    )
    assert "</system-reminder>" in prepended_block.get("text", ""), (
        "第一个 block 应包含 </system-reminder>"
    )

    # 3. 后续 block 与原始 content block 一致
    assert new_blocks[1:] == expected_suffix, (
        "system-reminder 之后的 block 应与原始 content block 一致"
    )

    # 4. 只有第一个 block 包含 system-reminder
    for block in new_blocks[1:]:
        if isinstance(block, dict):
            assert "<system-reminder>" not in block.get("text", ""), (
                "只有第一个 block 应包含 <system-reminder>"
            )


# --- Property 5 策略定义 ---

# Feature: dynamic-skill-loading, Property 5: 触发条件包含


class SkillConfigWithTrigger(SkillConfig):
    """带 trigger 字段的 SkillConfig 子类，用于测试触发条件格式化。"""

    trigger: str | None = None


# 触发条件文本策略：非空、无换行
_trigger_text_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Z", "P"),
        blacklist_characters="\n\r",
    ),
    min_size=1,
    max_size=60,
).filter(lambda s: s.strip() != "")


def _build_skill_with_trigger(
    name: str, description: str, prompt: str, trigger: str
) -> SkillConfigWithTrigger:
    """构建带 trigger 的 SkillConfig 实例。"""
    return SkillConfigWithTrigger(
        name=name, description=description, prompt=prompt, trigger=trigger
    )


# 带 trigger 的单个技能策略
_skill_with_trigger_st = st.builds(
    _build_skill_with_trigger,
    name=skill_name_st,
    description=skill_description_st,
    prompt=skill_prompt_st,
    trigger=_trigger_text_st,
)

# 带 trigger 的非空技能列表策略（名称唯一）
_skill_with_trigger_list_st = (
    st.lists(_skill_with_trigger_st, min_size=1, max_size=8)
    .map(lambda configs: list({c.name: c for c in configs}.values()))
    .filter(lambda configs: len(configs) >= 1)
)


# --- Property 5 属性测试 ---


# **Validates: Requirements 2.4**
@settings(max_examples=100)
@given(skills=_skill_with_trigger_list_st)
def test_trigger_condition_included(
    skills: list[SkillConfigWithTrigger],
) -> None:
    """验证触发条件包含在格式化输出中。

    对任意带 trigger 的 SkillConfig 列表：
    1. 输出中每个技能条目应包含其 trigger 文本
    2. 触发条件格式为 `（触发条件：{trigger}）`
    """
    result = format_skill_reminder(skills)

    for skill in skills:
        # 1. 输出包含 trigger 文本
        assert skill.trigger in result, (
            f"输出应包含触发条件文本 {skill.trigger!r}，实际输出:\n{result}"
        )

        # 2. 触发条件格式正确
        expected_trigger_fmt = f"（触发条件：{skill.trigger}）"
        assert expected_trigger_fmt in result, (
            f"输出应包含格式化的触发条件 {expected_trigger_fmt!r}，实际输出:\n{result}"
        )
