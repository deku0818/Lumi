# Feature: dynamic-skill-loading, Property 1: Digest 确定性与敏感性
"""SkillChangeDetector 属性测试

验证 _compute_digest() 的确定性（相同输入 → 相同 digest）
和敏感性（不同输入 → 不同 digest）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector

# --- 策略定义 ---

# 技能名称策略：生成合法的目录名
skill_name_st = st.from_regex(r"[a-z][a-z0-9_]{1,15}", fullmatch=True)

# 文件大小策略：通过控制 prompt 内容长度间接控制文件大小
prompt_content_st = st.text(
    alphabet=st.characters(categories=("L", "N", "Z")),
    min_size=1,
    max_size=200,
)

# 单个技能元数据策略
skill_entry_st = st.tuples(skill_name_st, prompt_content_st)

# 技能列表策略：至少 1 个技能，名称唯一
skill_list_st = (
    st.lists(skill_entry_st, min_size=1, max_size=8)
    .map(lambda entries: list({name: content for name, content in entries}.items()))
    .filter(lambda entries: len(entries) >= 1)
)


def _create_skill_file(base_dir: Path, name: str, content: str) -> Path:
    """在 base_dir 下创建 skill_name/SKILL.md 文件，返回文件路径。"""
    skill_dir = base_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n{content}",
        encoding="utf-8",
    )
    return skill_file


# --- 属性测试 ---


# **Validates: Requirements 1.1, 1.4**
@settings(max_examples=100)
@given(skills=skill_list_st)
def test_digest_determinism(skills: list[tuple[str, str]]) -> None:
    """相同目录状态下，_compute_digest() 应产生相同的 digest。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, content in skills:
            _create_skill_file(tmp_dir, name, content)

        detector = SkillChangeDetector(skills_dir=tmp_dir)
        digest_a = detector._compute_digest()
        digest_b = detector._compute_digest()

        assert digest_a == digest_b, (
            f"相同目录状态下 digest 不一致: {digest_a!r} != {digest_b!r}"
        )
        # digest 非空（目录有文件时）
        assert digest_a != "", "目录包含 SKILL.md 时 digest 不应为空"


# **Validates: Requirements 1.1, 1.4**
@settings(max_examples=100)
@given(skills=skill_list_st)
def test_digest_sensitivity_on_size_change(skills: list[tuple[str, str]]) -> None:
    """修改任一 SKILL.md 的文件大小后，digest 应发生变化。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for name, content in skills:
            p = _create_skill_file(tmp_dir, name, content)
            paths.append(p)

        detector = SkillChangeDetector(skills_dir=tmp_dir)
        digest_before = detector._compute_digest()

        # 修改第一个文件的内容（改变 size）
        target = paths[0]
        original = target.read_text(encoding="utf-8")
        target.write_text(original + "\nextra content to change size", encoding="utf-8")

        digest_after = detector._compute_digest()

        assert digest_before != digest_after, "修改文件大小后 digest 应发生变化"


# **Validates: Requirements 1.1, 1.4**
@settings(max_examples=100)
@given(skills=skill_list_st)
def test_digest_sensitivity_on_mtime_change(skills: list[tuple[str, str]]) -> None:
    """修改任一 SKILL.md 的 mtime 后，digest 应发生变化。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for name, content in skills:
            p = _create_skill_file(tmp_dir, name, content)
            paths.append(p)

        detector = SkillChangeDetector(skills_dir=tmp_dir)
        digest_before = detector._compute_digest()

        # 修改第一个文件的 mtime（向过去偏移 10 秒）
        target = paths[0]
        stat = target.stat()
        new_mtime = stat.st_mtime - 10
        os.utime(target, (stat.st_atime, new_mtime))

        digest_after = detector._compute_digest()

        assert digest_before != digest_after, "修改文件 mtime 后 digest 应发生变化"


# Feature: dynamic-skill-loading, Property 2: 缓存正确性
"""
验证 SkillChangeDetector.check() 的缓存行为：
- 连续两次 check() 且未修改文件时，第二次返回 changed=False
- 修改文件后 check() 返回 changed=True
"""


# 描述策略：生成合法的 YAML 安全描述文本
description_st = st.from_regex(r"[a-z][a-z0-9 ]{0,30}", fullmatch=True)

# 单个技能策略（名称 + 描述 + prompt 内容）
skill_full_entry_st = st.tuples(skill_name_st, description_st, prompt_content_st)

# 技能列表策略：至少 1 个技能，名称唯一
skill_full_list_st = (
    st.lists(skill_full_entry_st, min_size=1, max_size=5)
    .map(
        lambda entries: list(
            {name: (name, desc, content) for name, desc, content in entries}.values()
        )
    )
    .filter(lambda entries: len(entries) >= 1)
)


def _create_skill_file_full(
    base_dir: Path, name: str, description: str, content: str
) -> Path:
    """在 base_dir 下创建 skill_name/SKILL.md 文件（含完整 YAML frontmatter）。"""
    skill_dir = base_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f'---\nname: "{name}"\ndescription: "{description}"\n---\n{content}',
        encoding="utf-8",
    )
    return skill_file


# **Validates: Requirements 1.2, 1.3**
@settings(max_examples=100)
@given(skills=skill_full_list_st)
def test_cache_correctness_no_change(
    skills: list[tuple[str, str, str]],
) -> None:
    """连续两次 check() 且未修改文件时，第二次应返回 changed=False，技能列表一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, desc, content in skills:
            _create_skill_file_full(tmp_dir, name, desc, content)

        detector = SkillChangeDetector(skills_dir=tmp_dir)

        # 第一次 check：初始缓存为空，应返回 changed=True
        skills_first, changed_first = detector.check()
        assert changed_first is True, "首次 check() 应返回 changed=True"
        assert len(skills_first) == len(skills), (
            f"首次 check() 返回的技能数量不匹配: {len(skills_first)} != {len(skills)}"
        )

        # 第二次 check：未修改文件，应返回 changed=False
        skills_second, changed_second = detector.check()
        assert changed_second is False, (
            "未修改文件时第二次 check() 应返回 changed=False"
        )

        # 技能列表内容应一致
        first_names = sorted(s.name for s in skills_first)
        second_names = sorted(s.name for s in skills_second)
        assert first_names == second_names, (
            f"两次 check() 返回的技能名称不一致: {first_names} != {second_names}"
        )


# **Validates: Requirements 1.2, 1.3**
@settings(max_examples=100)
@given(skills=skill_full_list_st)
def test_cache_correctness_after_modification(
    skills: list[tuple[str, str, str]],
) -> None:
    """修改文件后 check() 应返回 changed=True，技能列表反映最新状态。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for name, desc, content in skills:
            p = _create_skill_file_full(tmp_dir, name, desc, content)
            paths.append(p)

        detector = SkillChangeDetector(skills_dir=tmp_dir)

        # 第一次 check：建立缓存
        detector.check()

        # 修改第一个文件的内容（改变 size 和 mtime）
        target = paths[0]
        original = target.read_text(encoding="utf-8")
        target.write_text(original + "\n额外内容改变文件大小", encoding="utf-8")

        # 第二次 check：文件已修改，应返回 changed=True
        skills_after, changed_after = detector.check()
        assert changed_after is True, "修改文件后 check() 应返回 changed=True"
        assert len(skills_after) == len(skills), (
            f"修改后技能数量不应变化: {len(skills_after)} != {len(skills)}"
        )
