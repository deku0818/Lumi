"""CommandRegistry 属性测试"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from lumi.agents.tools.loader import SkillConfig
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.registry import CommandRegistry

# 生成策略：有效的命令名称、描述和类型
command_names = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)
command_descriptions = st.text(min_size=0, max_size=50)
command_types = st.sampled_from([CommandType.BUILTIN, CommandType.SKILL])


async def _dummy_handler(extra_text: str = "") -> None:
    """测试用的空处理器"""


# Feature: slash-commands, Property 1: 命令注册 round-trip
# **Validates: Requirements 1.1**
@settings(max_examples=100)
@given(
    name=command_names,
    description=command_descriptions,
    command_type=command_types,
)
async def test_register_round_trip(
    name: str, description: str, command_type: CommandType
) -> None:
    """注册后 get(name) 返回相同命令对象"""
    registry = CommandRegistry()
    command = SlashCommand(
        name=name,
        description=description,
        command_type=command_type,
        handler=_dummy_handler,
    )

    result = registry.register(command)
    assert result is True

    retrieved = registry.get(name)
    assert retrieved is command
    assert retrieved.name == name
    assert retrieved.description == description
    assert retrieved.command_type == command_type


# Feature: slash-commands, Property 2: 重复命令注册拒绝
# **Validates: Requirements 1.3**
@settings(max_examples=100)
@given(
    name=command_names,
    desc1=command_descriptions,
    desc2=command_descriptions,
    command_type=command_types,
)
async def test_duplicate_register_rejected(
    name: str, desc1: str, desc2: str, command_type: CommandType
) -> None:
    """同名命令再次注册返回 False，原命令不变"""
    registry = CommandRegistry()
    first_command = SlashCommand(
        name=name,
        description=desc1,
        command_type=command_type,
        handler=_dummy_handler,
    )
    second_command = SlashCommand(
        name=name,
        description=desc2,
        command_type=command_type,
        handler=_dummy_handler,
    )

    # 第一次注册成功
    assert registry.register(first_command) is True

    # 第二次同名注册被拒绝
    assert registry.register(second_command) is False

    # get(name) 仍返回第一次注册的命令
    retrieved = registry.get(name)
    assert retrieved is first_command
    assert retrieved.description == desc1


# 生成策略：唯一命令名称列表
unique_command_names = st.lists(command_names, min_size=1, max_size=10, unique=True)
# 前缀字符串策略：包含空字符串和有效前缀
prefix_strings = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-"),
    min_size=0,
    max_size=8,
)


# Feature: slash-commands, Property 3: 前缀匹配正确性
# **Validates: Requirements 1.4, 1.5**
@settings(max_examples=100)
@given(
    names=unique_command_names,
    descriptions=st.lists(command_descriptions, min_size=1, max_size=10),
    command_type=command_types,
    prefix=prefix_strings,
)
async def test_prefix_match_correctness(
    names: list[str],
    descriptions: list[str],
    command_type: CommandType,
    prefix: str,
) -> None:
    """match(prefix) 返回的命令 name 都以 prefix 开头，且不遗漏"""
    registry = CommandRegistry()

    # 注册所有命令
    for i, name in enumerate(names):
        desc = descriptions[i % len(descriptions)]
        cmd = SlashCommand(
            name=name,
            description=desc,
            command_type=command_type,
            handler=_dummy_handler,
        )
        registry.register(cmd)

    matched = registry.match(prefix)

    # 1. 所有返回的命令名称都以 prefix 开头
    for cmd in matched:
        assert cmd.name.startswith(prefix), (
            f"命令 '{cmd.name}' 不以前缀 '{prefix}' 开头"
        )

    # 2. 没有遗漏：所有已注册且名称以 prefix 开头的命令都在结果中
    matched_names = {cmd.name for cmd in matched}
    for name in names:
        if name.startswith(prefix):
            assert name in matched_names, (
                f"命令 '{name}' 以前缀 '{prefix}' 开头但未出现在匹配结果中"
            )

    # 3. 空前缀返回全部命令
    all_matched = registry.match("")
    assert len(all_matched) == len(names)


# --- Property 4 策略 ---
skill_config_st = st.builds(
    SkillConfig,
    name=command_names,
    description=command_descriptions,
    prompt=st.text(min_size=1, max_size=100),
)
unique_skill_configs = (
    st.lists(skill_config_st, min_size=1, max_size=8)
    .map(lambda configs: list({c.name: c for c in configs}.values()))
    .filter(lambda configs: len(configs) >= 1)
)


# Feature: slash-commands, Property 4: 技能同步完整性
# **Validates: Requirements 2.1, 2.2**
@settings(max_examples=100)
@given(skills=unique_skill_configs)
async def test_sync_skills_completeness(skills: list[SkillConfig]) -> None:
    """sync_skills() 后技能命令与传入列表一一对应"""
    registry = CommandRegistry()

    def make_handler(skill: SkillConfig) -> ...:
        return _dummy_handler

    registry.sync_skills(skills, make_handler)

    # 1. 每个 SkillConfig 都有对应的 SKILL 类型命令，且 description 匹配
    for skill in skills:
        cmd = registry.get(skill.name)
        assert cmd is not None, f"技能 '{skill.name}' 未注册为命令"
        assert cmd.command_type == CommandType.SKILL, (
            f"命令 '{skill.name}' 类型应为 SKILL，实际为 {cmd.command_type}"
        )
        assert cmd.description == skill.description, (
            f"命令 '{skill.name}' 描述不匹配: 期望 '{skill.description}'，实际 '{cmd.description}'"
        )

    # 2. 不存在多余的 SKILL 命令
    skill_commands = [
        cmd for cmd in registry.all_commands if cmd.command_type == CommandType.SKILL
    ]
    skill_names_in_registry = {cmd.name for cmd in skill_commands}
    expected_names = {s.name for s in skills}
    assert skill_names_in_registry == expected_names, (
        f"SKILL 命令集合不匹配: 注册表中 {skill_names_in_registry}，期望 {expected_names}"
    )


# Feature: slash-commands, Property 5: 技能同步增量更新
# **Validates: Requirements 2.3**
@settings(max_examples=100)
@given(
    skills_a=unique_skill_configs,
    skills_b=unique_skill_configs,
    builtin_name=command_names,
)
async def test_sync_skills_incremental_update(
    skills_a: list[SkillConfig],
    skills_b: list[SkillConfig],
    builtin_name: str,
) -> None:
    """两次 sync_skills() 后技能命令精确反映最新列表，内置命令不受影响"""
    registry = CommandRegistry()

    # 注册一个内置命令
    builtin_cmd = SlashCommand(
        name=builtin_name,
        description="内置测试命令",
        command_type=CommandType.BUILTIN,
        handler=_dummy_handler,
    )
    registry.register(builtin_cmd)

    def make_handler(skill: SkillConfig) -> ...:
        return _dummy_handler

    # 第一次同步 skills_a
    registry.sync_skills(skills_a, make_handler)

    # 第二次同步 skills_b
    registry.sync_skills(skills_b, make_handler)

    # 内置命令名称集合（sync_skills 不应覆盖内置命令）
    builtin_names = {
        cmd.name
        for cmd in registry.all_commands
        if cmd.command_type == CommandType.BUILTIN
    }

    # 1. SKILL 命令精确反映 skills_b（排除与内置命令同名的技能）
    skill_commands = [
        cmd for cmd in registry.all_commands if cmd.command_type == CommandType.SKILL
    ]
    skill_names_in_registry = {cmd.name for cmd in skill_commands}
    expected_skill_names = {s.name for s in skills_b} - builtin_names
    assert skill_names_in_registry == expected_skill_names, (
        f"SKILL 命令集合不匹配: 注册表中 {skill_names_in_registry}，期望 {expected_skill_names}"
    )

    # 2. 每个非内置同名的 skills_b 描述正确映射
    for skill in skills_b:
        if skill.name in builtin_names:
            continue
        cmd = registry.get(skill.name)
        assert cmd is not None, f"技能 '{skill.name}' 未注册为命令"
        assert cmd.command_type == CommandType.SKILL
        assert cmd.description == skill.description

    # 3. 内置命令不受影响
    retrieved_builtin = registry.get(builtin_name)
    assert retrieved_builtin is not None, (
        f"内置命令 '{builtin_name}' 在 sync_skills 后丢失"
    )
    assert retrieved_builtin.command_type == CommandType.BUILTIN
    assert retrieved_builtin is builtin_cmd


# --- 单元测试 ---
# **Validates: Requirements 1.2**


async def test_all_commands_returns_tuple() -> None:
    """all_commands 返回 tuple 实例（不可变集合）"""
    registry = CommandRegistry()
    cmd = SlashCommand(
        name="test",
        description="测试命令",
        command_type=CommandType.BUILTIN,
        handler=_dummy_handler,
    )
    registry.register(cmd)

    result = registry.all_commands
    assert isinstance(result, tuple)


async def test_empty_registry_queries() -> None:
    """空注册表：get 返回 None，match 返回空 tuple，all_commands 返回空 tuple"""
    registry = CommandRegistry()

    assert registry.get("nonexistent") is None
    assert registry.match("any") == ()
    assert isinstance(registry.match("any"), tuple)
    assert registry.all_commands == ()
    assert isinstance(registry.all_commands, tuple)
