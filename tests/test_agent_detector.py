# Feature: dynamic-agent-loading, Property 1: Digest 确定性与敏感性
"""AgentChangeDetector 属性测试

镜像 SkillChangeDetector：验证 _compute_digest() 的确定性与敏感性，
以及 peek() 的缓存语义（变更注入语义已由 context_inject 的消息级 marker 承担）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector

# --- 策略定义 ---

agent_name_st = st.from_regex(r"[a-z][a-z0-9_]{1,15}", fullmatch=True)
prompt_content_st = st.text(
    alphabet=st.characters(categories=("L", "N", "Z")),
    min_size=1,
    max_size=200,
)
agent_entry_st = st.tuples(agent_name_st, prompt_content_st)
agent_list_st = (
    st.lists(agent_entry_st, min_size=1, max_size=8)
    .map(lambda entries: list({name: content for name, content in entries}.items()))
    .filter(lambda entries: len(entries) >= 1)
)


def _create_agent_file(base_dir: Path, name: str, content: str) -> Path:
    """在 base_dir 下创建 <name>.md 文件（带 YAML frontmatter），返回文件路径。"""
    agent_file = base_dir / f"{name}.md"
    agent_file.write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n{content}",
        encoding="utf-8",
    )
    return agent_file


# --- 属性测试: digest ---


@settings(max_examples=50)
@given(agents=agent_list_st)
def test_digest_determinism(agents: list[tuple[str, str]]) -> None:
    """相同目录状态下，_compute_digest() 应产生相同的 digest。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, content in agents:
            _create_agent_file(tmp_dir, name, content)

        detector = AgentChangeDetector(agents_dir=tmp_dir)
        digest_a = detector._compute_digest()
        digest_b = detector._compute_digest()

        assert digest_a == digest_b, (
            f"相同目录状态下 digest 不一致: {digest_a!r} != {digest_b!r}"
        )
        assert digest_a != "", "目录包含 *.md 时 digest 不应为空"


@settings(max_examples=50)
@given(agents=agent_list_st)
def test_digest_sensitivity_on_size_change(agents: list[tuple[str, str]]) -> None:
    """修改任一 *.md 的文件大小后，digest 应发生变化。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for name, content in agents:
            paths.append(_create_agent_file(tmp_dir, name, content))

        detector = AgentChangeDetector(agents_dir=tmp_dir)
        digest_before = detector._compute_digest()

        target = paths[0]
        original = target.read_text(encoding="utf-8")
        target.write_text(original + "\nextra content to change size", encoding="utf-8")

        assert digest_before != detector._compute_digest(), (
            "修改文件大小后 digest 应变化"
        )


@settings(max_examples=50)
@given(agents=agent_list_st)
def test_digest_sensitivity_on_mtime_change(agents: list[tuple[str, str]]) -> None:
    """修改任一 *.md 的 mtime 后，digest 应发生变化。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for name, content in agents:
            paths.append(_create_agent_file(tmp_dir, name, content))

        detector = AgentChangeDetector(agents_dir=tmp_dir)
        digest_before = detector._compute_digest()

        target = paths[0]
        stat = target.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime - 10))

        assert digest_before != detector._compute_digest(), (
            "修改文件 mtime 后 digest 应变化"
        )


# --- 缓存语义 ---


def test_cache_correctness_after_modification() -> None:
    """新增用户 agent 文件后 peek() 应反映最新列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _create_agent_file(tmp_dir, "alpha", "alpha 提示词")

        detector = AgentChangeDetector(agents_dir=tmp_dir)
        assert "alpha" in {a.name for a in detector.peek()}

        # 新增一个 agent 文件
        _create_agent_file(tmp_dir, "beta", "beta 提示词")
        assert {"alpha", "beta"} <= {a.name for a in detector.peek()}
